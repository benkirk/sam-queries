"""
Accounting commands for SAM.

AccountingAdminCommand — bridges hpc-usage-queries data into comp_charge_summary.
AccountingSearchCommand — queries comp_charge_summary for user inspection.
"""
import getpass
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

from cli.core.base import BaseCommand
from cli.core.output import output_json
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, MofNCompleteColumn
from cli.accounting.display import (
    display_dry_run_table,
    display_import_summary,
    display_charge_summary_table,
    display_quota_reconcile_plan,
    display_quota_reconcile_summary,
)
from cli.accounting.quota_readers import get_quota_reader, QuotaEntry
from cli.accounting.path_verifier import (
    PathVerificationError, auto_detect_verifier,
)
from sam.manage.summaries import upsert_comp_charge_summary
from sam.manage.transaction import management_transaction
from sam.manage.allocations import update_allocation
from sam.plugins import HPC_USAGE_QUERIES


# Reconcile tolerance: allocations within this fraction of quota truth are
# treated as matched (ignores rounding noise from prior TiB⇄byte conversions).
QUOTA_TOLERANCE = 0.01  # 1%

# Threshold: GPU hours must be at least this fraction of total compute hours
# to classify a row as a GPU resource charge rather than CPU.
# Avoids misclassifying CPU jobs that happened to touch a GPU queue briefly.
GPU_FRACTION_THRESHOLD = 0.01  # 1%

# PBS assigns ephemeral names like R5184776 (reservation), M2498882 (maintenance),
# S870294 (standing reservation) to individual reservations.  SAM has a single
# canonical 'reservation' queue per resource that covers all of them.
_RESERVATION_QUEUE_RE = re.compile(r'^[RMS]\d')


def normalize_queue_name(queue_name: str) -> str:
    """Map ephemeral PBS reservation queue names to the canonical 'reservation' queue.

    Known limitation: if the same user/project has jobs in two different PBS
    reservations on the same day, both rows normalize to the same SAM natural key
    and the second upsert overwrites the first (charges are not summed).  This
    edge case is considered rare enough not to warrant pre-aggregation today.
    """
    if _RESERVATION_QUEUE_RE.match(queue_name):
        return 'reservation'
    return queue_name


def adapt_jobstats_row(row: dict, machine: str) -> Optional[tuple]:
    """
    Classify an hpc-usage-queries daily summary row into SAM resource and charge fields.

    This function is the single place where machine-specific billing rules live.
    It handles:
      - Which SAM resource name to use (e.g. "Derecho" vs "Derecho GPU")
      - What core_hours and charges to post
      - Sanity checks for anomalous data (e.g. 1M CPU-h + 10 GPU-h → CPU, not GPU)

    Args:
        row: Dict from JobQueries.daily_summary_report() with keys:
             date, user, account, queue,
             job_count, cpu_hours, gpu_hours, memory_hours
        machine: 'derecho' or 'casper'

    Returns:
        (resource_name, machine_name, core_hours, charges) to pass to
        upsert_comp_charge_summary(), or None to silently skip the row (e.g.
        zero-charge row).  machine_name is always explicit so that resources
        with multiple SAM machines (e.g. Casper) don't trigger auto-detection.

    Raises:
        ValueError: For rows with an unknown machine name.
    """
    cpu_h = row["cpu_hours"] or 0.0
    gpu_h = row["gpu_hours"] or 0.0
    cpu_c = row["cpu_charges"] or 0.0
    gpu_c = row["gpu_charges"] or 0.0
    total = cpu_h + gpu_h

    if total <= 0.0:
        return None  # Skip zero-charge rows

    gpu_fraction = gpu_h / total

    if machine == "derecho":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Meaningful GPU usage → Derecho GPU resource
            # core_hours = GPU hours (the Derecho GPU billing metric)
            # TODO: confirm whether charges = gpu_hours or a weighted formula
            return "Derecho GPU", "derecho-gpu", gpu_h, gpu_c
        # Pure CPU job (or anomalous GPU ratio → treat as CPU)
        # core_hours = CPU core-hours (numnodes * 128 * wall_hours)
        # TODO: confirm charges formula (queue_factor multiplier?)
        return "Derecho", "derecho", cpu_h, cpu_c

    elif machine == "casper":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Casper GPU resource
            # TODO: confirm Casper GPU resource name and charges formula
            return "Casper GPU", "Casper-gpu", gpu_h, gpu_c
        # Casper CPU resource
        # TODO: confirm Casper charges formula (cpu_hours + memory_hours?)
        return "Casper", "Casper", cpu_h, cpu_c

    else:
        raise ValueError(f"Unknown machine: {machine!r}. Add a case to adapt_jobstats_row().")


class AccountingAdminCommand(BaseCommand):
    """Posts daily charge summaries from hpc-usage-queries into SAM."""

    def execute(
        self,
        *,
        comp: bool = False,
        disk: bool = False,
        archive: bool = False,
        reconcile_quotas: Optional[str] = None,
        resource: Optional[str] = None,
        machine: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        dry_run: bool = False,
        update_accounting_system: bool = False,
        deactivate_orphaned: bool = False,
        force: bool = False,
        verify_paths: bool = False,
        verify_host: Optional[str] = None,
        skip_errors: bool = False,
        create_queues: bool = False,
        chunk_size: int = 500,
        include_deleted_accounts: bool = False,
    ) -> int:
        if reconcile_quotas is not None:
            return self._run_reconcile_quotas(
                resource_name=resource,
                quota_path=reconcile_quotas,
                update_accounting_system=update_accounting_system,
                deactivate_orphaned=deactivate_orphaned,
                force=force,
                verify_paths=verify_paths,
                verify_host=verify_host,
            )
        if comp:
            return self._run_comp(
                machine, start_date, end_date,
                dry_run=dry_run,
                skip_errors=skip_errors,
                create_queues=create_queues,
                chunk_size=chunk_size,
                include_deleted_accounts=include_deleted_accounts,
            )
        if disk:
            self.console.print("[yellow]--disk: not yet implemented[/yellow]")
            return 0
        if archive:
            self.console.print("[yellow]--archive: not yet implemented[/yellow]")
            return 0
        self.console.print(
            "Error: specify --comp, --disk, --archive, or --reconcile-quotas",
            style="bold red",
        )
        return 1

    def _run_comp(self, machine: str, start_date: date, end_date: date, **kwargs) -> int:
        """Query hpc-usage-queries and post results to comp_charge_summary."""
        # --- 1. Load job_history plugin (graceful error if not installed) ---
        mod = self.require_plugin(HPC_USAGE_QUERIES)
        if mod is None:
            return 2
        jh_get_session = mod.get_session
        JobQueries = mod.JobQueries

        # --- 2. Open hpc-usage-queries session ---
        try:
            jh_session = jh_get_session(machine)
        except Exception as exc:
            self.console.print(f"[bold red]Error opening job_history session for {machine!r}: {exc}[/bold red]")
            return 2

        # --- 3. Fetch daily summary rows ---
        try:
            rows = list(JobQueries(jh_session).daily_summary_report(start=start_date, end=end_date))
        except Exception as exc:
            self.console.print(f"[bold red]Error fetching daily summary: {exc}[/bold red]")
            return 2
        finally:
            jh_session.close()

        # --- 4. Validate rows exist ---
        if not rows:
            self.console.print(
                f"[yellow]No data found for {machine} between {start_date} and {end_date}[/yellow]"
            )
            return 0

        self.console.print(
            f"Found [bold]{len(rows)}[/bold] rows for [bold]{machine}[/bold] "
            f"({start_date} → {end_date})"
        )

        # --- 5. Verbose: show charge-row table (independent of dry-run) ---
        if self.ctx.verbose:
            display_dry_run_table(
                self.ctx, rows, machine, adapt_jobstats_row, normalize_queue_name,
                dry_run=kwargs.get("dry_run", False),
            )

        # --- 5b. Dry-run: skip insertion ---
        if kwargs.get("dry_run"):
            return 0

        # --- 6-7. Chunk and post rows ---
        n_created = 0
        n_updated = 0
        n_errors = 0
        n_skipped = 0

        chunks = [rows[i:i + kwargs["chunk_size"]] for i in range(0, len(rows), kwargs["chunk_size"])]

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(f"Posting {machine} charges...", total=len(rows))

            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    with management_transaction(self.session):
                        for row in chunk:
                            result = adapt_jobstats_row(row, machine)
                            progress.advance(task)
                            if result is None:
                                n_skipped += 1
                                continue

                            resource_name, machine_name, core_hours, charges = result

                            # Warn on anomalous GPU fraction (proceeded as CPU)
                            cpu_h = row["cpu_hours"] or 0.0
                            gpu_h = row["gpu_hours"] or 0.0
                            if gpu_h > 0 and resource_name in ("Derecho", "Casper"):
                                gpu_fraction = gpu_h / (cpu_h + gpu_h)
                                if self.ctx.verbose:
                                    self.console.print(
                                        f"[yellow]Warning: low GPU fraction ({gpu_fraction:.1%}) "
                                        f"for {row['user']}/{row['account']} on {row['date']} "
                                        f"— posting to {resource_name}[/yellow]"
                                    )

                            try:
                                _, action = upsert_comp_charge_summary(
                                    self.session,
                                    activity_date=date.fromisoformat(str(row["date"])),
                                    act_username=row["user"],
                                    act_projcode=row["account"],
                                    act_unix_uid=None,
                                    resource_name=resource_name,
                                    machine_name=machine_name,
                                    queue_name=normalize_queue_name(row["queue"]),
                                    num_jobs=row["job_count"],
                                    core_hours=core_hours,
                                    charges=charges,
                                    create_queue_if_missing=kwargs["create_queues"],
                                    include_deleted_accounts=kwargs["include_deleted_accounts"],
                                )
                                if action == "created":
                                    n_created += 1
                                else:
                                    n_updated += 1
                            except ValueError as exc:
                                n_errors += 1
                                if not kwargs["skip_errors"]:
                                    raise
                                if self.ctx.verbose:
                                    self.console.print(f"[yellow]Skip: {exc}[/yellow]")

                except ValueError as exc:
                    # Chunk-level failure (skip_errors=False): re-raised from inner loop
                    self.console.print(f"[bold red]Chunk {chunk_idx} aborted: {exc}[/bold red]")
                    return 2

        # --- 8. Summary ---
        display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)

        # --- 9. Exit code ---
        return 0 if n_errors == 0 else 2


    # ------------------------------------------------------------------
    # Quota reconciliation
    # ------------------------------------------------------------------

    def _run_reconcile_quotas(
        self,
        *,
        resource_name: Optional[str],
        quota_path: str,
        update_accounting_system: bool = False,
        deactivate_orphaned: bool = False,
        force: bool = False,
        verify_paths: bool = False,
        verify_host: Optional[str] = None,
    ) -> int:
        """Reconcile SAM allocations for ``resource_name`` against a
        storage-system-specific quota file.

        Always reports the full plan (matched / mismatched / orphaned /
        unmapped tables, snapshot banner, narrative captions). Writes
        are gated behind explicit opt-in flags:

          * ``update_accounting_system``  → apply mismatched-amount updates
          * ``deactivate_orphaned``       → also deactivate orphan allocations
            (requires ``update_accounting_system``)
          * ``force``                     → override the live-path safety
            gate (requires ``deactivate_orphaned``)

        Without any of these the tool is read-only — same code path,
        no DB mutations.
        """
        from sam.resources.resources import Resource
        from sam.accounting.accounts import Account
        from sam.accounting.allocations import Allocation
        from sam.projects.projects import Project, ProjectDirectory

        # ---- 1. Validate inputs ------------------------------------------------
        if not resource_name:
            self.console.print(
                "Error: --reconcile-quotas requires --resource",
                style="bold red",
            )
            return 2

        resource = Resource.get_by_name(self.session, resource_name)
        if resource is None:
            self.console.print(
                f"Error: resource {resource_name!r} not found in SAM",
                style="bold red",
            )
            return 2

        try:
            reader = get_quota_reader(resource_name, quota_path)
        except NotImplementedError as exc:
            self.console.print(f"Error: {exc}", style="bold red")
            return 2

        try:
            quota_entries = reader.read()
        except (OSError, ValueError) as exc:
            self.console.print(
                f"Error reading quota file {quota_path!r}: {exc}",
                style="bold red",
            )
            return 2

        # ---- 1b. Snapshot-age banner ------------------------------------------
        self._display_snapshot_banner(reader)

        # ---- 1c. Path verification setup (optional) ---------------------------
        verifier = None
        verify_mode_banner = None
        if verify_paths:
            try:
                verifier, verify_mode_banner = auto_detect_verifier(
                    mount_root=reader.mount_root,
                    mount_hosts=reader.mount_hosts,
                    explicit_host=verify_host,
                )
            except PathVerificationError as exc:
                self.console.print(f"[bold red]{exc}[/bold red]")
                return 2
            self.console.print(
                f"[dim]Path verification: {verify_mode_banner}[/dim]"
            )

        # ---- 2. Load active allocations to reconcile ---------------------------
        # Inheriting (child) allocations — those with a non-NULL
        # parent_allocation_id — are shadows of their master. Direct
        # mutation is forbidden by update_allocation; any reconcile
        # cascade flows from the master automatically. So skip them at
        # the SQL level, and surface the count for admin awareness.
        # On Campaign_Store this currently affects exactly one pair
        # (NCGD0009 ↔ P03010039 sharing /gpfs/csfs1/cgd/amp); the
        # pattern is more common on HPC resources.
        alloc_rows = (
            self.session.query(Project, Allocation)
            .join(Account, Account.project_id == Project.project_id)
            .join(Allocation, Allocation.account_id == Account.account_id)
            .filter(Account.resource_id == resource.resource_id)
            .filter(Account.deleted == False)  # noqa: E712
            .filter(Allocation.is_active)
            .filter(Allocation.parent_allocation_id.is_(None))
            .all()
        )
        n_inheriting_skipped = (
            self.session.query(Allocation)
            .join(Account, Account.account_id == Allocation.account_id)
            .filter(Account.resource_id == resource.resource_id)
            .filter(Account.deleted == False)  # noqa: E712
            .filter(Allocation.is_active)
            .filter(Allocation.parent_allocation_id.isnot(None))
            .count()
        )
        if n_inheriting_skipped:
            self.console.print(
                f"[dim]Skipping {n_inheriting_skipped} inheriting "
                f"(shared) allocation{'s' if n_inheriting_skipped != 1 else ''} "
                f"— reconciled via the master allocation.[/dim]"
            )
        by_projcode = {proj.projcode: (proj, alloc) for proj, alloc in alloc_rows}

        # ---- 3. Load ALL projects for tree traversal ---------------------------
        # Need every project that might own a fileset — not just those with
        # Campaign_Store allocations — so child quotas can roll up into a
        # parent's expected value (e.g. NMMM0003's /mmm subtree, NCIS0001's
        # /cisl subtree). No active filter: a deactivated project can still
        # own a GPFS fileset the reconcile needs to account for.
        all_projects = self.session.query(Project).all()
        projects_by_code = {p.projcode: p for p in all_projects}

        # ---- 4. Map quota entries ↔ projects (over ALL projects) --------------
        dir_to_projcode: dict[str, str] = {}
        dir_rows = (
            self.session.query(ProjectDirectory, Project)
            .join(Project, Project.project_id == ProjectDirectory.project_id)
            .filter(ProjectDirectory.is_currently_active)
            .all()
        )
        for pd, proj in dir_rows:
            dir_to_projcode.setdefault(pd.directory_name, proj.projcode)

        # Also build a per-project list of active directory names — handy
        # context for the Orphaned display (explains what a deactivated
        # allocation used to map to on disk).
        dirs_by_projcode: dict[str, list[str]] = {}
        for pd, proj in dir_rows:
            dirs_by_projcode.setdefault(proj.projcode, []).append(pd.directory_name)

        # A project can own multiple filesets (e.g. P43713000 / rda has
        # several ProjectDirectory rows mapping to distinct GPFS filesets);
        # store quotas as a list per projcode and sum at roll-up time.
        # Dedupe by fileset_name so a quota that matches via both projcode
        # AND ProjectDirectory path isn't counted twice.
        own_quota: dict[str, list[QuotaEntry]] = {}
        seen_filesets: dict[str, set[str]] = {}
        unmapped: list[QuotaEntry] = []

        def _attach(projcode: str, qe: QuotaEntry) -> None:
            seen = seen_filesets.setdefault(projcode, set())
            if qe.fileset_name in seen:
                return
            seen.add(qe.fileset_name)
            own_quota.setdefault(projcode, []).append(qe)

        for qe in quota_entries:
            projcode = qe.fileset_name.upper()
            if projcode in projects_by_code:
                _attach(projcode, qe)
                continue
            if qe.path and qe.path in dir_to_projcode:
                _attach(dir_to_projcode[qe.path], qe)
                continue
            unmapped.append(qe)

        # ---- 5. Subtree roll-up via MPPT containment --------------------------
        # Project tree uses NestedSetMixin: a project P's subtree is every
        # project Q with P.tree_root == Q.tree_root, P.tree_left <= Q.tree_left,
        # P.tree_right >= Q.tree_right. Mirrors Project.get_subtree_charges()
        # (projects.py:720-747), just computed in Python over the preloaded
        # set (no per-project DB round-trip).
        quota_projects = [
            projects_by_code[pc] for pc in own_quota
            if pc in projects_by_code
        ]
        # Bucket by tree_root so NestedSetMixin's is_ancestor_of() only
        # compares nodes in the same forest (tree_left/tree_right values
        # repeat across different roots, so cross-forest checks are unsafe).
        by_root: dict[int, list] = {}
        for qp in quota_projects:
            if qp.tree_root is not None:
                by_root.setdefault(qp.tree_root, []).append(qp)

        def _rollup(proj) -> tuple[int, list]:
            """Return (expected_bytes, contributors) for `proj`'s subtree.

            Uses NestedSetMixin.is_ancestor_of (src/sam/base.py:305-311)
            which encodes the MPPT containment check. Each tree node
            may carry multiple filesets (multiple ProjectDirectory rows
            → multiple QuotaEntry contributions), so we flatten the
            per-node fileset lists into a single contributor sequence.
            """
            candidates = by_root.get(proj.tree_root, ()) if proj.tree_root else ()
            descendants = [qp for qp in candidates if proj.is_ancestor_of(qp)]
            descendants.sort(key=lambda q: q.tree_left)  # depth-first for display
            self_node = [proj] if own_quota.get(proj.projcode) else []
            contrib_nodes = self_node + descendants
            contributors: list[tuple[str, QuotaEntry]] = []
            for q in contrib_nodes:
                for qe in own_quota.get(q.projcode, ()):
                    contributors.append((q.projcode, qe))
            total = sum(qe.limit_bytes for _, qe in contributors)
            return total, contributors

        # ---- 6. Classify each SAM allocation -----------------------------------
        # Record shapes:
        #   matched, mismatched: (projcode, sam_tib, expected_bytes, contributors)
        #   orphaned:            (projcode, sam_tib, directories: list[str])
        matched: list[tuple[str, float, int, list]] = []
        mismatched: list[tuple[str, float, int, list]] = []
        orphaned: list[tuple[str, float, list[str]]] = []

        for projcode, (proj, alloc) in by_projcode.items():
            sam_tib = float(alloc.amount)
            expected_bytes, contributors = _rollup(proj)
            if expected_bytes == 0:
                orphaned.append(
                    (projcode, sam_tib, dirs_by_projcode.get(projcode, []))
                )
                continue
            sam_bytes = sam_tib * (1024 ** 4)
            delta_frac = abs(sam_bytes - expected_bytes) / expected_bytes
            record = (projcode, sam_tib, expected_bytes, contributors)
            if delta_frac > QUOTA_TOLERANCE:
                mismatched.append(record)
            else:
                matched.append(record)

        # ---- 6b. Path verification (optional) ---------------------------------
        path_exists: dict[str, bool] = {}
        if verifier is not None:
            paths_to_check: set[str] = set()
            for _, _, dirs in orphaned:
                paths_to_check.update(dirs)
            for qe in unmapped:
                if qe.path:
                    paths_to_check.add(qe.path)
            try:
                path_exists = verifier.check(sorted(paths_to_check))
            except PathVerificationError as exc:
                self.console.print(f"[bold red]{exc}[/bold red]")
                return 2

        # ---- 7. Report (always) -----------------------------------------------
        display_quota_reconcile_plan(
            self.ctx, resource_name,
            matched, mismatched, orphaned, unmapped,
            path_exists=path_exists if verifier is not None else None,
        )

        # ---- 8. Apply (only when explicitly opted in) -------------------------
        # The two write flags are independent — use either, both, or
        # neither:
        #   - no flags                       → report-only (here we return).
        #   - --update-accounting-system     → apply mismatched amount updates.
        #   - --deactivate-orphaned          → deactivate orphan allocations.
        #   - both                           → both.
        #   - +--force (with --deactivate-orphaned) → override live-path gate.
        if not update_accounting_system and not deactivate_orphaned:
            display_quota_reconcile_summary(
                self.ctx,
                matched=len(matched), mismatched=len(mismatched),
                orphaned=len(orphaned), unmapped=len(unmapped),
                report_only=True,
                will_apply_updates=False,
                will_deactivate_orphans=False,
            )
            self._print_action_hints(mismatched, orphaned, applied_updates=False,
                                     applied_deactivations=False)
            return 0

        # If the admin's flags don't intersect with anything actionable
        # (e.g. --deactivate-orphaned but no orphans), short-circuit
        # before opening a transaction.
        will_update = update_accounting_system and bool(mismatched)
        will_deactivate = deactivate_orphaned and bool(orphaned)
        if not will_update and not will_deactivate:
            self.console.print(
                "[green]Nothing to reconcile — no actionable changes for the "
                "selected flags.[/green]"
            )
            display_quota_reconcile_summary(
                self.ctx,
                matched=len(matched), mismatched=len(mismatched),
                orphaned=len(orphaned), unmapped=len(unmapped),
                report_only=False,
                will_apply_updates=update_accounting_system,
                will_deactivate_orphans=deactivate_orphaned,
            )
            self._print_action_hints(mismatched, orphaned,
                                     applied_updates=update_accounting_system,
                                     applied_deactivations=deactivate_orphaned)
            return 0

        # Resolve the admin user for the audit trail
        admin_user_id = self._resolve_admin_user_id()
        if admin_user_id is None:
            return 2

        n_updated = 0
        n_deactivated = 0
        n_errors = 0
        # Set end_date to YESTERDAY-23:59:59 so the deactivated
        # allocation drops out of `is_active` immediately. Two things
        # going on here:
        #
        # 1. SAM's normalize_end_date validator promotes a midnight
        #    end_date to 23:59:59 of the SAME day. Passing `today` at
        #    midnight would normalize to today 23:59:59 — leaving the
        #    allocation active until end-of-day. A same-day re-run of
        #    the tool would then still show those rows as orphans.
        #
        # 2. update_allocation's validate_allocation_dates runs on the
        #    INPUT value before normalize_end_date kicks in (the
        #    validator normalizes only when the column is assigned).
        #    So we must pre-supply yesterday at 23:59:59 directly,
        #    not yesterday at midnight (which would fail validation
        #    against any start_date later in yesterday).
        #
        # Audit-trail transaction timestamps capture the precise moment
        # of the change, so a 1-day backdate of end_date doesn't lose
        # information.
        effective_end = (
            datetime.combine(date.today(), datetime.min.time())
            - timedelta(seconds=1)
        )

        try:
            with management_transaction(self.session):
                if update_accounting_system:
                    for projcode, sam_tib, expected_bytes, contributors in mismatched:
                        _, alloc = by_projcode[projcode]
                        new_tib = expected_bytes / (1024 ** 4)
                        n_contrib = len(contributors)
                        try:
                            update_allocation(
                                self.session,
                                alloc.allocation_id,
                                admin_user_id,
                                amount=new_tib,
                                comment=(
                                    f"Reconciled with current fileset quota "
                                    f"from {quota_path} (subtree of {n_contrib} "
                                    f"fileset{'s' if n_contrib != 1 else ''})"
                                ),
                            )
                            n_updated += 1
                        except Exception as exc:  # noqa: BLE001
                            n_errors += 1
                            self.console.print(
                                f"[red]Failed to update {projcode}: {exc}[/red]"
                            )

                if deactivate_orphaned:
                    for projcode, sam_tib, dirs in orphaned:
                        _, alloc = by_projcode[projcode]
                        # Safety gate: if path verification says every
                        # ProjectDirectory is still live on disk, don't
                        # silently deactivate — require --force.
                        live = bool(dirs) and all(
                            path_exists.get(d, False) for d in dirs
                        ) if verifier is not None else False
                        if live and not force:
                            self.console.print(
                                f"[yellow]Skipping {projcode}: all "
                                f"ProjectDirectory paths are live on disk. "
                                "Re-run with --force to deactivate anyway.[/yellow]"
                            )
                            continue
                        note = (
                            " (warning: paths still present on disk)"
                            if live else ""
                        )
                        try:
                            update_allocation(
                                self.session,
                                alloc.allocation_id,
                                admin_user_id,
                                end_date=effective_end,
                                comment=(
                                    f"Deactivated: no fileset quota in "
                                    f"project subtree (source {quota_path})"
                                    f"{note}"
                                ),
                            )
                            n_deactivated += 1
                        except Exception as exc:  # noqa: BLE001
                            n_errors += 1
                            self.console.print(
                                f"[red]Failed to deactivate {projcode}: {exc}[/red]"
                            )
        except Exception as exc:  # noqa: BLE001
            self.console.print(
                f"[bold red]Transaction aborted: {exc}[/bold red]"
            )
            return 2

        display_quota_reconcile_summary(
            self.ctx,
            matched=len(matched), mismatched=len(mismatched),
            orphaned=len(orphaned), unmapped=len(unmapped),
            updated=n_updated, deactivated=n_deactivated,
            errors=n_errors,
            report_only=False,
            will_apply_updates=update_accounting_system,
            will_deactivate_orphans=deactivate_orphaned,
        )
        self._print_action_hints(mismatched, orphaned,
                                 applied_updates=update_accounting_system,
                                 applied_deactivations=deactivate_orphaned)
        return 0 if n_errors == 0 else 2

    def _print_action_hints(
        self,
        mismatched: list,
        orphaned: list,
        *,
        applied_updates: bool,
        applied_deactivations: bool,
    ) -> None:
        """Footer hints that nudge the admin to the next opt-in flag.

        The reconcile tool is intentionally informative-by-default; this
        line tells the admin exactly what flag would turn each pending
        section into a write.
        """
        hints: list[str] = []
        if mismatched and not applied_updates:
            hints.append(
                f"[dim]→ pass [bold]--update-accounting-system[/bold] "
                f"to apply {len(mismatched)} amount update"
                f"{'s' if len(mismatched) != 1 else ''}.[/dim]"
            )
        if orphaned and not applied_deactivations:
            hints.append(
                f"[dim]→ pass [bold]--deactivate-orphaned[/bold] "
                f"to deactivate {len(orphaned)} orphan"
                f"{'s' if len(orphaned) != 1 else ''}.[/dim]"
            )
        for line in hints:
            self.console.print(line)

    def _display_snapshot_banner(self, reader) -> None:
        """Print a one-line banner describing the quota snapshot's age."""
        snap = getattr(reader, 'snapshot_date', None)
        if snap is None:
            self.console.print(
                "[dim]Quota snapshot: date unknown[/dim]"
            )
            return
        age = (datetime.now() - snap).days
        if age > 7:
            style, tag = "bold yellow", f"⚠ {age} days old — may be stale"
        else:
            style, tag = "dim", f"{age} days old"
        self.console.print(
            f"[{style}]Quota snapshot: {snap:%Y-%m-%d %H:%M} ({tag})[/{style}]"
        )

    def _resolve_admin_user_id(self) -> Optional[int]:
        """Look up the shell user in SAM for audit-trail attribution."""
        from sam.core.users import User

        username = (
            os.environ.get('SAM_ADMIN_USER')
            or os.environ.get('USER')
            or (getpass.getuser() if hasattr(getpass, 'getuser') else None)
        )
        if not username:
            self.console.print(
                "Error: cannot determine current user for audit trail. "
                "Set $SAM_ADMIN_USER or $USER.",
                style="bold red",
            )
            return None
        user = User.get_by_username(self.session, username)
        if user is None:
            self.console.print(
                f"Error: admin user {username!r} not found in SAM. "
                "Set $SAM_ADMIN_USER to a valid SAM username.",
                style="bold red",
            )
            return None
        return user.user_id


class AccountingSearchCommand(BaseCommand):
    """Query comp_charge_summary — no plugin required."""

    def execute(
        self,
        *,
        start_date,
        end_date,
        username: Optional[str] = None,
        projcode: Optional[str] = None,
        resource: Optional[str] = None,
        queue: Optional[str] = None,
        machine: Optional[str] = None,
    ) -> int:
        from sam.queries.charges import query_comp_charge_summaries

        rows = query_comp_charge_summaries(
            self.session, start_date, end_date,
            username=username,
            projcode=projcode,
            resource=resource,
            queue=queue,
            machine=machine,
            per_day=self.ctx.verbose,
        )

        if not rows:
            if self.ctx.output_format == 'json':
                output_json({
                    'kind': 'comp_charge_summary',
                    'start_date': start_date,
                    'end_date': end_date,
                    'count': 0,
                    'rows': [],
                })
                return 1
            self.console.print("[yellow]No charge records found for the given filters.[/yellow]")
            return 1

        if self.ctx.output_format == 'json':
            output_json({
                'kind': 'comp_charge_summary',
                'start_date': start_date,
                'end_date': end_date,
                'per_day': self.ctx.verbose,
                'count': len(rows),
                'rows': rows,
            })
            return 0

        display_charge_summary_table(self.ctx, rows, start_date, end_date)
        return 0
