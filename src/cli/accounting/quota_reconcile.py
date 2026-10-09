"""`sam-admin accounting --reconcile-quotas`: allocations against filesystem quota truth."""
from cli.core.utils import EXIT_ERROR, EXIT_SUCCESS
from sam.summaries.disk_summaries import BYTES_PER_TIB
import getpass
import os
from datetime import date, datetime, timedelta
from typing import Optional

from cli.accounting.display import (
    display_quota_reconcile_plan,
    display_quota_reconcile_summary,
)
from cli.accounting.quota_readers import get_quota_reader, QuotaEntry
from cli.accounting.path_verifier import (
    PathVerificationError, auto_detect_verifier,
)
from sam.manage.transaction import management_transaction
from sam.manage.allocations import update_allocation


# Reconcile tolerance: allocations within this fraction of quota truth are
# treated as matched (ignores rounding noise from prior TiB<->byte conversions).
QUOTA_TOLERANCE = 0.01  # 1%


class QuotaReconcileMixin:
    """The --reconcile-quotas path of `AccountingAdminCommand`."""

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

          * ``update_accounting_system``  -> apply mismatched-amount updates
          * ``deactivate_orphaned``       -> also deactivate orphan allocations
            (requires ``update_accounting_system``)
          * ``force``                     -> override the live-path safety
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
            return EXIT_ERROR

        resource = Resource.get_by_name(self.session, resource_name)
        if resource is None:
            self.console.print(
                f"Error: resource {resource_name!r} not found in SAM",
                style="bold red",
            )
            return EXIT_ERROR

        try:
            reader = get_quota_reader(resource_name, quota_path)
        except NotImplementedError as exc:
            self.console.print(f"Error: {exc}", style="bold red")
            return EXIT_ERROR

        try:
            quota_entries = reader.read()
        except (OSError, ValueError) as exc:
            self.console.print(
                f"Error reading quota file {quota_path!r}: {exc}",
                style="bold red",
            )
            return EXIT_ERROR

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
                return EXIT_ERROR
            self.console.print(
                f"[dim]Path verification: {verify_mode_banner}[/dim]"
            )

        # ---- 2. Load active allocations to reconcile ---------------------------
        # Inheriting allocations (non-NULL parent_allocation_id) are shadows of
        # their master: update_allocation forbids mutating them directly and any
        # cascade flows from the master. Skip them in SQL, but surface the count.
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
        # Every project that might own a fileset, not just those with an
        # allocation here, so child quotas roll up into a parent's expected
        # value. No active filter -- a deactivated project can still own a
        # fileset the reconcile must account for.
        all_projects = self.session.query(Project).all()
        projects_by_code = {p.projcode: p for p in all_projects}

        # ---- 4. Map quota entries <-> projects (over ALL projects) --------------
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

        # A project can own multiple filesets, so quotas are a list per projcode,
        # summed at roll-up. Dedupe by fileset_name or a quota matching via both
        # projcode AND ProjectDirectory path is counted twice.
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
        # NestedSetMixin: P's subtree is every Q sharing P.tree_root with
        # P.tree_left <= Q.tree_left and P.tree_right >= Q.tree_right: the kernel's
        # subtree join, computed in Python over the preloaded set to avoid a
        # per-project round-trip.
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
            -> multiple QuotaEntry contributions), so we flatten the
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
            sam_bytes = sam_tib * BYTES_PER_TIB
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
                return EXIT_ERROR

        # ---- 7. Report (always) -----------------------------------------------
        display_quota_reconcile_plan(
            self.ctx, resource_name,
            matched, mismatched, orphaned, unmapped,
            path_exists=path_exists if verifier is not None else None,
        )

        # ---- 8. Apply (only when explicitly opted in) -------------------------
        # The two write flags are independent: --update-accounting-system
        # applies mismatched amounts, --deactivate-orphaned deactivates orphan
        # allocations, neither means report-only, and --force (with the latter)
        # overrides the live-path gate.
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
            return EXIT_SUCCESS

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
            return EXIT_SUCCESS

        # Resolve the admin user for the audit trail
        admin_user_id = self._resolve_admin_user_id()
        if admin_user_id is None:
            return EXIT_ERROR

        n_updated = 0
        n_deactivated = 0
        n_errors = 0
        # End_date must be YESTERDAY at 23:59:59, precisely, for two reasons.
        # normalize_end_date promotes a midnight end_date to 23:59:59 of the
        # SAME day, so `today` at midnight leaves the allocation active until
        # end-of-day and a same-day re-run still reports it as an orphan. And
        # validate_allocation_dates runs on the INPUT value, before
        # normalize_end_date is reached, so yesterday-at-midnight fails
        # validation against any start_date later in yesterday. The audit trail
        # timestamps the real moment, so the 1-day backdate loses nothing.
        effective_end = (
            datetime.combine(date.today(), datetime.min.time())
            - timedelta(seconds=1)
        )

        try:
            with management_transaction(self.session):
                if update_accounting_system:
                    for projcode, sam_tib, expected_bytes, contributors in mismatched:
                        _, alloc = by_projcode[projcode]
                        new_tib = expected_bytes / BYTES_PER_TIB
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
            return EXIT_ERROR

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
        return EXIT_SUCCESS if n_errors == 0 else EXIT_ERROR

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
