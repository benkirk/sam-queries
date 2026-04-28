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
    display_disk_dry_run_table,
    display_import_summary,
    display_charge_summary_table,
    display_quota_reconcile_plan,
    display_quota_reconcile_summary,
)
from cli.accounting.quota_readers import get_quota_reader, QuotaEntry
from cli.accounting.disk_usage import get_disk_usage_reader, DiskUsageEntry
from cli.accounting.path_verifier import (
    PathVerificationError, auto_detect_verifier,
)
from sam.manage.summaries import (
    upsert_comp_charge_summary, upsert_disk_charge_summary,
)
from sam.manage.transaction import management_transaction
from sam.manage.allocations import update_allocation
from sam.plugins import HPC_USAGE_QUERIES
from sam.summaries.disk_summaries import (
    DISK_CHARGING_TIB_EPOCH, mark_disk_snapshot_current, tib_years,
)


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

# Per-user-disk-usage feeds for some resources (e.g. Quasar) only ship a
# single rollup row per project — username is the literal sentinel
# `'total'`. Treat these as project-wide aggregates: keep `act_username`
# as the audit label, attribute the row to the project lead (matching
# the `<unidentified>` gap-row convention) so user resolution and
# downstream charging math succeed.
_DISK_ROLLUP_USERNAMES = frozenset({'total'})


def _group_disk_entries(entries: list[DiskUsageEntry]) -> list[DiskUsageEntry]:
    """Aggregate per-(user, fileset) rows into per-(user, project) totals.

    The disk-usage input (`acct.glade.YYYY-MM-DD`) ships one row per
    (user, directory). Two filesets on the same project for the same
    user collide on the upsert natural key
    ``(activity_date, act_username, act_projcode, account_id)`` —
    `directory_path` is not in the key — and silently UPDATE-overwrite,
    losing every fileset except the last.

    Sum bytes / files / terabyte_years / charges in Python before the
    upsert so each (user, project) lands as one row, matching legacy
    SAM's ``calculateDiskChargeSummaries`` named query
    (``legacy_sam/.../AccountingNamedQuery.xml``):

        GROUP BY (date, user, uid, user_id, projcode, account_id)
        SUM(bytes), SUM(files), SUM(terabyte_year), SUM(charge)

    Synthetic gap rows (``user_override`` set, e.g. ``<unidentified>``)
    pass through unchanged — they already carry pre-resolved entities
    and a unique ``act_username`` so they don't collide on the natural
    key.
    """
    grouped: dict[tuple, DiskUsageEntry] = {}
    pass_through: list[DiskUsageEntry] = []

    for e in entries:
        if e.user_override is not None:
            pass_through.append(e)
            continue
        key = (e.activity_date, e.projcode, e.username)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = DiskUsageEntry(
                activity_date=e.activity_date,
                projcode=e.projcode,
                username=e.username,
                number_of_files=e.number_of_files,
                bytes=e.bytes,
                directory_path=e.directory_path,
                reporting_interval=e.reporting_interval,
                cos=e.cos,
                act_username=e.act_username,
                terabyte_years=e.terabyte_years,
                charges=e.charges,
            )
        else:
            existing.number_of_files += e.number_of_files
            existing.bytes += e.bytes
            existing.terabyte_years += e.terabyte_years
            existing.charges += e.charges

    return list(grouped.values()) + pass_through


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
        # --disk specific
        user_usage_path: Optional[str] = None,
        quotas_path: Optional[str] = None,
        reporting_interval: int = 7,
        unidentified_label: str = '<unidentified>',
        reconcile_quota_gap: bool = False,
        gap_tolerance_bytes: int = 1024 ** 3,    # 1 GiB
        gap_tolerance_frac: float = 0.01,        # 1%
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
            return self._run_disk(
                resource_name=resource,
                user_usage_path=user_usage_path,
                quotas_path=quotas_path,
                reporting_interval=reporting_interval,
                unidentified_label=unidentified_label,
                reconcile_gap=reconcile_quota_gap,
                gap_tolerance_bytes=gap_tolerance_bytes,
                gap_tolerance_frac=gap_tolerance_frac,
                start_date=start_date,
                end_date=end_date,
                dry_run=dry_run,
                skip_errors=skip_errors,
                chunk_size=chunk_size,
                include_deleted_accounts=include_deleted_accounts,
            )
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
    # Disk charge summary import
    # ------------------------------------------------------------------

    def _run_disk(
        self,
        *,
        resource_name: Optional[str],
        user_usage_path: Optional[str],
        quotas_path: Optional[str],
        reporting_interval: int,
        unidentified_label: str,
        reconcile_gap: bool,
        gap_tolerance_bytes: int,
        gap_tolerance_frac: float,
        start_date: Optional[date],
        end_date: Optional[date],
        dry_run: bool,
        skip_errors: bool,
        chunk_size: int,
        include_deleted_accounts: bool,
    ) -> int:
        """Import a per-user-per-project disk usage snapshot into ``disk_charge_summary``.

        See ``docs/plans/DISK_CHARGING.md`` for the design. High-level flow:

          1. Parse the per-user file via a registered DiskUsageReader.
          2. Validate snapshot date against the requested window AND the
             cutover epoch (post-epoch only — pre-epoch rows stay legacy).
          3. Optionally reconcile per-project FILESET totals against the
             user-row sum and emit ``<unidentified>`` gap rows attributed
             to each project's lead.
          4. Compute terabyte_years/charges in TiB-years.
          5. Chunked upsert via ``upsert_disk_charge_summary``.
          6. Mark this date as the current snapshot in
             ``disk_charge_summary_status``.
        """
        from sam.resources.resources import Resource
        from sam.accounting.accounts import Account

        # ---- 1. Validate inputs ----------------------------------------
        if not resource_name:
            self.console.print(
                "Error: --disk requires --resource", style="bold red"
            )
            return 2
        if not user_usage_path:
            self.console.print(
                "Error: --disk requires --user-usage <path>", style="bold red"
            )
            return 2
        if reconcile_gap and not quotas_path:
            self.console.print(
                "Error: --reconcile-quota-gap requires --quotas <path>",
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

        # ---- 2. Parse the per-user file -------------------------------
        try:
            reader = get_disk_usage_reader(resource_name, user_usage_path)
        except NotImplementedError as exc:
            self.console.print(f"Error: {exc}", style="bold red")
            return 2
        try:
            entries = reader.read()
        except (OSError, ValueError) as exc:
            self.console.print(
                f"Error reading {user_usage_path!r}: {exc}", style="bold red"
            )
            return 2

        if not entries:
            self.console.print(
                f"[yellow]No usage rows in {user_usage_path}[/yellow]"
            )
            return 0

        snap_date = reader.snapshot_date
        if snap_date is None:
            self.console.print(
                "Error: cannot determine snapshot date from "
                f"{user_usage_path!r}", style="bold red",
            )
            return 2

        # ---- 3. Date assertion (--date safety check) -------------------
        # The CLI collapses --date to start_date == end_date == expected.
        # If the operator supplied --date, the file's snapshot date must
        # equal it exactly. This catches "wrong file fed to wrong date"
        # mistakes early, before any DB writes.
        if start_date is not None and end_date is not None:
            if not (start_date <= snap_date <= end_date):
                if start_date == end_date:
                    self.console.print(
                        f"Error: snapshot date {snap_date} does not match "
                        f"--date {start_date}",
                        style="bold red",
                    )
                else:
                    self.console.print(
                        f"Error: snapshot date {snap_date} falls outside the "
                        f"requested window {start_date}..{end_date}",
                        style="bold red",
                    )
                return 2

        # ---- 4. Cutover-epoch enforcement ------------------------------
        if snap_date < DISK_CHARGING_TIB_EPOCH:
            self.console.print(
                f"Error: snapshot date {snap_date} is before the "
                f"DISK_CHARGING_TIB_EPOCH ({DISK_CHARGING_TIB_EPOCH}). "
                "This command only writes post-epoch TiB-year rows; "
                "pre-epoch legacy rows are not rewritten.",
                style="bold red",
            )
            return 2

        # ---- 5. Optional gap reconciliation ----------------------------
        if reconcile_gap and quotas_path:
            try:
                gap_rows = self._build_unidentified_disk_rows(
                    resource=resource,
                    user_entries=entries,
                    quotas_path=quotas_path,
                    snapshot_date=snap_date,
                    unidentified_label=unidentified_label,
                    reporting_interval=reporting_interval,
                    gap_tolerance_bytes=gap_tolerance_bytes,
                    gap_tolerance_frac=gap_tolerance_frac,
                    include_deleted_accounts=include_deleted_accounts,
                )
            except (OSError, ValueError) as exc:
                self.console.print(
                    f"Error reading quotas {quotas_path!r}: {exc}",
                    style="bold red",
                )
                return 2
            entries.extend(gap_rows)

        # ---- 6. Charging math ------------------------------------------
        for e in entries:
            e.terabyte_years = tib_years(e.bytes, reporting_interval)
            e.charges = e.terabyte_years

        # ---- 6b. Resolve projcode for normal rows ----------------------
        # The acct.glade column 3 is a fileset label (e.g. 'cesm', 'cgd')
        # not a SAM projcode (e.g. 'CESM0001', 'NCGD0009'). The legacy
        # Java ingest resolved this via directory_path → ProjectDirectory
        # → Project. Mirror that here: prefer the path-based lookup, fall
        # back to projcode-as-label, give up if neither works (the row is
        # then skipped if --skip-errors, else aborts the chunk).
        from sam.projects.projects import ProjectDirectory, Project
        from sam.accounting.accounts import Account
        pd_path_to_project: dict[str, "Project"] = {
            pd.directory_name: proj
            for pd, proj in (
                self.session.query(ProjectDirectory, Project)
                .join(Project, Project.project_id == ProjectDirectory.project_id)
                .filter(ProjectDirectory.is_currently_active)
                .all()
            )
        }
        # Cache resolved Account per project_id for this resource.
        account_cache: dict[int, "Account"] = {}

        def _resolve_for_row(row) -> tuple[bool, Optional["Project"], Optional["Account"]]:
            """Return (resolved_ok, project, account). For normal rows only —
            gap rows already carry user/account overrides."""
            project = None
            if row.directory_path and row.directory_path in pd_path_to_project:
                project = pd_path_to_project[row.directory_path]
            if project is None:
                project = Project.get_by_projcode(self.session, row.projcode)
            if project is None:
                return False, None, None
            acct = account_cache.get(project.project_id)
            if acct is None:
                acct = Account.get_by_project_and_resource(
                    self.session, project.project_id, resource.resource_id,
                    exclude_deleted=not include_deleted_accounts,
                )
                if acct is None:
                    return False, project, None
                account_cache[project.project_id] = acct
            return True, project, acct

        # ---- 7. Verbose dry-run table ----------------------------------
        if self.ctx.verbose:
            display_disk_dry_run_table(
                self.ctx, entries, resource_name, dry_run=dry_run,
            )

        if dry_run:
            return 0

        # ---- 7b. Idempotency: delete pre-existing rows for this
        # (resource, snapshot_date) so a re-run replaces rather than
        # duplicates. Necessary because the legacy ingest left
        # `act_username` / `act_projcode` as NULL, which means our
        # natural-key UPDATE wouldn't match those rows — they'd accumulate
        # alongside the new ones, double-counting on roll-up. Same delete
        # also cleans up the prior post-epoch run on this date.
        from sam.summaries.disk_summaries import DiskChargeSummary
        n_deleted_legacy = 0
        try:
            with management_transaction(self.session):
                deleted = (
                    self.session.query(DiskChargeSummary)
                    .filter(DiskChargeSummary.activity_date == snap_date)
                    .filter(
                        DiskChargeSummary.account_id.in_(
                            self.session.query(Account.account_id).filter(
                                Account.resource_id == resource.resource_id,
                            )
                        )
                    )
                    .delete(synchronize_session=False)
                )
                n_deleted_legacy = int(deleted)
        except Exception as exc:  # noqa: BLE001
            self.console.print(
                f"[bold red]Failed to clear existing rows for {snap_date}: {exc}[/bold red]"
            )
            return 2
        if n_deleted_legacy:
            self.console.print(
                f"[dim]Cleared {n_deleted_legacy} pre-existing "
                f"disk_charge_summary row(s) for "
                f"{resource_name} on {snap_date} before re-import.[/dim]"
            )

        # ---- 7c. Aggregate per-(user, fileset) rows into per-(user,
        # project) totals before upsert. The acct.glade input ships one
        # row per (user, directory); the upsert natural key omits
        # directory_path, so multi-fileset projects would silently
        # UPDATE-overwrite if we fed raw entries through. Mirrors legacy
        # SAM's `calculateDiskChargeSummaries` SUM-by-(date, user,
        # account) — see `_group_disk_entries`.
        entries_to_upsert = _group_disk_entries(entries)
        if self.ctx.verbose and len(entries_to_upsert) != len(entries):
            self.console.print(
                f"[dim]Aggregated {len(entries)} per-fileset rows into "
                f"{len(entries_to_upsert)} per-(user, project) rows for "
                f"upsert (multi-fileset projects rolled up).[/dim]"
            )

        # ---- 8. Chunked upsert -----------------------------------------
        n_created = 0
        n_updated = 0
        n_errors = 0
        n_skipped = 0

        chunks = [
            entries_to_upsert[i:i + chunk_size]
            for i in range(0, len(entries_to_upsert), chunk_size)
        ]

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                f"Posting {resource_name} disk charges...",
                total=len(entries_to_upsert),
            )
            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    with management_transaction(self.session):
                        for row in chunk:
                            progress.advance(task)
                            try:
                                # For normal rows: act_username = parsed
                                # username, act_projcode = parsed projcode
                                # (the resolver needs these). The legacy
                                # Java pipeline stored these as NULL, but
                                # the current upsert tests already write
                                # them — we follow the test convention.
                                #
                                # For gap rows (`<unidentified>`):
                                # row.act_username carries the audit
                                # label and row.user_override is set, so
                                # the resolver is skipped entirely.
                                user_for_upsert = row.user_override
                                if row.user_override is not None:
                                    act_uname = row.act_username
                                    act_pcode = None
                                    project_for_upsert = None
                                    account_for_upsert = row.account_override
                                else:
                                    act_uname = row.username
                                    # Resolve project from directory_path
                                    # (umbrella filesets like 'cgd' map to
                                    # specific SAM projects via
                                    # ProjectDirectory).
                                    ok, project_for_upsert, account_for_upsert = _resolve_for_row(row)
                                    if not ok:
                                        raise ValueError(
                                            f"Could not resolve project/account for "
                                            f"row projcode={row.projcode!r} "
                                            f"path={row.directory_path!r} on "
                                            f"resource {resource_name!r}"
                                        )
                                    # Stash the resolved projcode in the
                                    # audit column so the row carries the
                                    # SAM-canonical name, not the umbrella
                                    # label from the input file.
                                    act_pcode = project_for_upsert.projcode

                                    # Project-rollup feeds (Quasar's
                                    # `'total'` rows): no per-user
                                    # breakdown is shipped, so attribute
                                    # the row to the project lead and
                                    # keep the rollup sentinel as the
                                    # audit username. Mirrors the
                                    # `<unidentified>` gap-row
                                    # convention (see
                                    # `_build_unidentified_disk_rows`).
                                    if act_uname in _DISK_ROLLUP_USERNAMES:
                                        user_for_upsert = project_for_upsert.lead
                                        if user_for_upsert is None:
                                            raise ValueError(
                                                f"rollup row username={act_uname!r} for "
                                                f"projcode={act_pcode!r} but project has "
                                                f"no lead — cannot attribute"
                                            )
                                _, action = upsert_disk_charge_summary(
                                    self.session,
                                    activity_date=row.activity_date,
                                    act_username=act_uname,
                                    act_projcode=act_pcode,
                                    act_unix_uid=None,
                                    resource_name=resource_name,
                                    charges=row.charges,
                                    number_of_files=row.number_of_files,
                                    bytes=row.bytes,
                                    terabyte_years=row.terabyte_years,
                                    user=user_for_upsert,
                                    project=project_for_upsert,
                                    account=account_for_upsert,
                                    include_deleted_accounts=include_deleted_accounts,
                                )
                                if action == 'created':
                                    n_created += 1
                                else:
                                    n_updated += 1
                            except ValueError as exc:
                                n_errors += 1
                                if not skip_errors:
                                    raise
                                if self.ctx.verbose:
                                    self.console.print(f"[yellow]Skip: {exc}[/yellow]")
                except ValueError as exc:
                    self.console.print(
                        f"[bold red]Chunk {chunk_idx} aborted: {exc}[/bold red]"
                    )
                    return 2

            # ---- 9. Mark this snapshot as current --------------------
            try:
                with management_transaction(self.session):
                    mark_disk_snapshot_current(self.session, snap_date)
            except Exception as exc:  # noqa: BLE001
                self.console.print(
                    f"[bold red]Failed to mark snapshot current: {exc}[/bold red]"
                )
                # Don't fail the whole import for this — data is in.

        display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)
        return 0 if n_errors == 0 else 2

    def _build_unidentified_disk_rows(
        self,
        *,
        resource,
        user_entries: list,
        quotas_path: str,
        snapshot_date: date,
        unidentified_label: str,
        reporting_interval: int,
        gap_tolerance_bytes: int,
        gap_tolerance_frac: float,
        include_deleted_accounts: bool,
    ) -> list:
        """Build synthetic ``<unidentified>`` gap rows from FILESET vs Σuser_bytes.

        For every projcode where the FILESET total exceeds the sum of
        per-user acct rows by more than ``gap_tolerance_bytes`` AND
        ``gap_tolerance_frac`` of the FILESET total, emit one DiskUsageEntry
        with:
          - ``act_username = unidentified_label``
          - ``user_override = project.lead``
          - ``account_override`` resolved from (project, resource)
        Skips projects where lead or account cannot be resolved (with a
        per-project warning).

        FILESET key resolution precedence:
          a. fileset name uppercased matches a SAM projcode directly
          b. fileset path matches a path observed in user_entries → that
             row's projcode
          c. fileset path matches a ProjectDirectory.path → that project's projcode
          d. otherwise unmappable; logged & skipped (does NOT create gap)
        """
        from sam.projects.projects import Project, ProjectDirectory
        from sam.accounting.accounts import Account
        from cli.accounting.quota_readers import get_quota_reader

        reader = get_quota_reader(resource.resource_name, quotas_path)
        quota_entries = reader.read()  # already in bytes (KiB×1024)

        # Snapshot-date sanity: cs_usage.json `date` field is a free-form
        # string so the reader sets snapshot_date best-effort. Just warn
        # if the quotas date drifts more than 24h from the user-usage one.
        quota_snap = getattr(reader, 'snapshot_date', None)
        if quota_snap is not None:
            quota_d = quota_snap.date() if hasattr(quota_snap, 'date') else quota_snap
            if abs((quota_d - snapshot_date).days) > 1:
                self.console.print(
                    f"[yellow]Warning: quotas snapshot {quota_d} differs from "
                    f"user-usage snapshot {snapshot_date} by more than 1 day.[/yellow]"
                )

        # Sum per-user bytes per projcode (from already-parsed acct entries).
        user_bytes: dict[str, int] = {}
        path_to_projcode: dict[str, str] = {}
        for e in user_entries:
            user_bytes[e.projcode] = user_bytes.get(e.projcode, 0) + e.bytes
            if e.directory_path:
                path_to_projcode.setdefault(e.directory_path, e.projcode)

        # Build path → projcode fallback from ProjectDirectory.
        dir_rows = (
            self.session.query(ProjectDirectory, Project)
            .join(Project, Project.project_id == ProjectDirectory.project_id)
            .filter(ProjectDirectory.is_currently_active)
            .all()
        )
        pd_path_to_projcode = {
            pd.directory_name: proj.projcode for pd, proj in dir_rows
        }

        # Map each FILESET entry to a projcode + accumulate bytes.
        fileset_bytes: dict[str, int] = {}
        unmapped: list = []
        for qe in quota_entries:
            projcode = qe.fileset_name.upper()
            project = Project.get_by_projcode(self.session, projcode)
            if project is None:
                if qe.path and qe.path in path_to_projcode:
                    projcode = path_to_projcode[qe.path]
                elif qe.path and qe.path in pd_path_to_projcode:
                    projcode = pd_path_to_projcode[qe.path]
                else:
                    unmapped.append(qe)
                    continue
            fileset_bytes[projcode] = fileset_bytes.get(projcode, 0) + qe.usage_bytes

        if unmapped and self.ctx.verbose:
            self.console.print(
                f"[dim]Gap-reconcile: {len(unmapped)} fileset(s) had no SAM "
                "project mapping (skipped).[/dim]"
            )

        # Build gap rows.
        gap_rows: list = []
        for projcode, q_bytes in fileset_bytes.items():
            sum_user = user_bytes.get(projcode, 0)
            gap = q_bytes - sum_user
            if gap <= 0:
                continue
            min_tol = max(gap_tolerance_bytes, int(q_bytes * gap_tolerance_frac))
            if gap < min_tol:
                continue

            project = Project.get_by_projcode(self.session, projcode)
            if project is None:
                continue
            lead = project.lead
            if lead is None:
                self.console.print(
                    f"[yellow]Skipping gap for {projcode}: project has no lead.[/yellow]"
                )
                continue
            account = Account.get_by_project_and_resource(
                self.session, project.project_id, resource.resource_id,
                exclude_deleted=not include_deleted_accounts,
            )
            if account is None:
                self.console.print(
                    f"[yellow]Skipping gap for {projcode}: no account on "
                    f"{resource.resource_name}.[/yellow]"
                )
                continue

            gap_rows.append(DiskUsageEntry(
                activity_date=snapshot_date,
                projcode=projcode,
                username=lead.username,
                number_of_files=0,
                bytes=gap,
                directory_path=None,
                reporting_interval=reporting_interval,
                cos=0,
                act_username=unidentified_label,
                user_override=lead,
                account_override=account,
            ))

        if gap_rows:
            total_gap_bytes = sum(r.bytes for r in gap_rows)
            self.console.print(
                f"[cyan]Gap reconciliation: {len(gap_rows)} project(s) "
                f"with unattributed bytes; total {total_gap_bytes / (1024**4):.2f} TiB "
                "attributed to project leads with audit label "
                f"{unidentified_label!r}.[/cyan]"
            )
        return gap_rows


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
