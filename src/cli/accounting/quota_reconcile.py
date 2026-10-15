"""`sam-admin accounting --reconcile-quotas`: allocations against filesystem quota truth."""
from cli.core.output import output_json
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
from cli.accounting.fileset_resolver import FilesetResolver
from cli.accounting.quota_readers import get_quota_reader, QuotaEntry
from cli.accounting.path_verifier import (
    PathVerificationError, auto_detect_verifier,
)
from sam.manage.transaction import management_transaction
from sam.manage.allocations import update_allocation


# Reconcile tolerance: allocations within this fraction of quota truth are
# treated as matched (ignores rounding noise from prior TiB<->byte conversions).
QUOTA_TOLERANCE = 0.01  # 1%


def _delta_pct(sam_tib: float, quota_bytes: int) -> float:
    quota_tib = quota_bytes / BYTES_PER_TIB
    return (sam_tib - quota_tib) / quota_tib * 100.0 if quota_tib else 0.0


def _fileset_item(qe: QuotaEntry, owner: Optional[str] = None) -> dict:
    """One quota entry as a plain dict; ``owner`` is the projcode it attached to."""
    return {
        'fileset': qe.fileset_name, 'path': qe.path, 'projcode': owner,
        'limit_bytes': qe.limit_bytes, 'usage_bytes': qe.usage_bytes,
        'files': qe.file_count, 'utilization': qe.utilization, 'exists': None,
    }


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
        """Reconcile ``resource_name``'s allocations against its quota file; read-only by default.

        ``update_accounting_system`` applies mismatched amounts and ``deactivate_orphaned``
        deactivates orphans, independently; ``force`` overrides the live-path gate for the latter.
        """
        from sam.resources.resources import Resource
        from sam.accounting.accounts import Account
        from sam.accounting.allocations import Allocation
        from sam.projects.projects import Project, ProjectDirectory
        from sqlalchemy import func

        # JSON is report-only (src/cli/README.md); its messages go to stderr.
        json_mode = self.ctx.output_format == 'json'
        if json_mode:
            if update_accounting_system or deactivate_orphaned:
                output_json({
                    'kind': 'quota_reconcile', 'error': 'json_unsupported_for_writes',
                    'message': '--format json cannot be combined with '
                               '--update-accounting-system or --deactivate-orphaned',
                })
                return EXIT_ERROR
            self.console = self.ctx.stderr_console

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
        # Active directories per project: context for the Orphaned display.
        dirs_by_projcode: dict[str, list[str]] = {}
        for pd, proj in (
            self.session.query(ProjectDirectory, Project)
            .join(Project, Project.project_id == ProjectDirectory.project_id)
            .filter(ProjectDirectory.is_currently_active)
        ):
            dirs_by_projcode.setdefault(proj.projcode, []).append(pd.directory_name)

        # The disk ingest's rule decides which project owns each fileset. A project
        # can own several filesets: quotas are a list per projcode, summed at roll-up.
        resolver = FilesetResolver(self.session, resource)
        labels = {id(qe): qe.fileset_name.upper() for qe in quota_entries}
        resolver.learn_labels((qe.path, labels[id(qe)]) for qe in quota_entries)
        own_quota: dict[str, list[QuotaEntry]] = {}
        seen_filesets: set[str] = set()
        unmapped: list[QuotaEntry] = []
        known_unowned: list[QuotaEntry] = []
        unlinked: list[dict] = []
        for qe in quota_entries:
            key = qe.path or qe.fileset_name
            if key in seen_filesets:
                continue
            seen_filesets.add(key)
            res = resolver.resolve(qe.path, labels[id(qe)])
            if res.project is None:
                (known_unowned if res.reason == 'known_unowned' else unmapped).append(qe)
                continue
            own_quota.setdefault(res.project.projcode, []).append(qe)
            if res.reason == 'unlinked_directory':
                unlinked.append({**_fileset_item(qe, res.project.projcode),
                                 'action': res.action})

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
        def _project_item(proj, alloc, expected_bytes, contributors) -> dict:
            sam_tib = float(alloc.amount)
            return {
                'projcode': proj.projcode, 'allocation_id': alloc.allocation_id,
                'sam_tib': sam_tib, 'quota_bytes': expected_bytes,
                'quota_tib': expected_bytes / BYTES_PER_TIB,
                'delta_pct': _delta_pct(sam_tib, expected_bytes),
                'filesets': [_fileset_item(qe, owner) for owner, qe in contributors],
            }

        matched: list[dict] = []
        mismatched: list[dict] = []
        orphaned: list[dict] = []
        for projcode, (proj, alloc) in by_projcode.items():
            expected_bytes, contributors = _rollup(proj)
            if expected_bytes == 0:
                orphaned.append({
                    'projcode': projcode, 'allocation_id': alloc.allocation_id,
                    'sam_tib': float(alloc.amount),
                    'directories': [{'path': d, 'exists': None}
                                    for d in dirs_by_projcode.get(projcode, [])],
                })
                continue
            sam_bytes = float(alloc.amount) * BYTES_PER_TIB
            delta_frac = abs(sam_bytes - expected_bytes) / expected_bytes
            item = _project_item(proj, alloc, expected_bytes, contributors)
            (mismatched if delta_frac > QUOTA_TOLERANCE else matched).append(item)

        # ---- 6a. Filesets no allocation covers --------------------------------
        # A quota whose owner has no active allocation on itself or an ancestor
        # is in no subtree above. Report it, split by whether the project ever
        # had one here: retention lags removal, so expired filesets linger.
        covered = set(by_projcode) | {
            p.projcode for p in (
                self.session.query(Project)
                .join(Account, Account.project_id == Project.project_id)
                .join(Allocation, Allocation.account_id == Account.account_id)
                .filter(Account.resource_id == resource.resource_id)
                .filter(Account.deleted == False)  # noqa: E712
                .filter(Allocation.is_active)
                .filter(Allocation.parent_allocation_id.isnot(None))
            )
        }
        allocated_by_root: dict[int, list] = {}
        for pc in by_projcode:
            p = projects_by_code[pc]
            if p.tree_root is not None:
                allocated_by_root.setdefault(p.tree_root, []).append(p)
        last_end = dict(
            self.session.query(Account.project_id, func.max(Allocation.end_date))
            .join(Allocation, Allocation.account_id == Account.account_id)
            .filter(Account.resource_id == resource.resource_id)
            .filter(Allocation.deleted == False)  # noqa: E712
            .group_by(Account.project_id)
        )
        now = datetime.now()
        after_expiry: list[dict] = []
        never_allocated: list[dict] = []
        for projcode, entries in own_quota.items():
            proj = projects_by_code[projcode]
            if projcode in covered or any(
                a.is_ancestor_of(proj) for a in allocated_by_root.get(proj.tree_root, ())
            ):
                continue
            ended = last_end.get(proj.project_id)
            for qe in entries:
                item = {**_fileset_item(qe, projcode), 'project_active': bool(proj.active),
                        'last_end_date': ended}
                if ended is not None and ended <= now:
                    item['days_since_end'] = (now.date() - ended.date()).days
                    after_expiry.append(item)
                else:
                    never_allocated.append(item)
        after_expiry.sort(key=lambda i: (-i['days_since_end'], i['projcode']))
        never_allocated.sort(key=lambda i: (-i['usage_bytes'], i['projcode']))
        unlinked.sort(key=lambda i: (i['projcode'], i['path'] or ''))

        report = {
            'kind': 'quota_reconcile',
            'resource': resource_name,
            'source': quota_path,
            'snapshot_date': reader.snapshot_date,
            'verified_paths': verifier is not None,
            'verify_mode': verify_mode_banner,
            'inheriting_skipped': n_inheriting_skipped,
            'categories': {
                'matched': matched,
                'quota_mismatch': mismatched,
                'allocation_without_quota': orphaned,
                'quota_after_expiry': after_expiry,
                'quota_without_allocation': never_allocated,
                'unlinked_directory': unlinked,
                'no_project': [_fileset_item(qe) for qe in unmapped],
                'known_unowned': [_fileset_item(qe) for qe in known_unowned],
            },
        }
        report['counts'] = {k: len(v) for k, v in report['categories'].items()}

        # ---- 6b. Path verification (optional) ---------------------------------
        if verifier is not None:
            items = [d for o in orphaned for d in o['directories']]
            items += [f for f in report['categories']['no_project'] if f['path']]
            try:
                path_exists = verifier.check(sorted({i['path'] for i in items}))
            except PathVerificationError as exc:
                self.console.print(f"[bold red]{exc}[/bold red]")
                return EXIT_ERROR
            for i in items:
                i['exists'] = path_exists.get(i['path'], False)

        # ---- 7. Report (always) -----------------------------------------------
        if json_mode:
            output_json(report)
            return EXIT_SUCCESS
        display_quota_reconcile_plan(self.ctx, report)

        # ---- 8. Apply (only when explicitly opted in) -------------------------
        # The two write flags are independent: --update-accounting-system
        # applies mismatched amounts, --deactivate-orphaned deactivates orphan
        # allocations, neither means report-only, and --force (with the latter)
        # overrides the live-path gate.
        counts = report['counts']
        if not update_accounting_system and not deactivate_orphaned:
            display_quota_reconcile_summary(
                self.ctx, counts, report_only=True,
                will_apply_updates=False, will_deactivate_orphans=False,
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
                self.ctx, counts, report_only=False,
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
                    for item in mismatched:
                        n_contrib = len(item['filesets'])
                        try:
                            update_allocation(
                                self.session,
                                item['allocation_id'],
                                admin_user_id,
                                amount=item['quota_tib'],
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
                                f"[red]Failed to update {item['projcode']}: {exc}[/red]"
                            )

                if deactivate_orphaned:
                    for item in orphaned:
                        projcode, dirs = item['projcode'], item['directories']
                        # Safety gate: if path verification says every
                        # ProjectDirectory is still live on disk, don't
                        # silently deactivate — require --force.
                        live = bool(dirs) and all(d['exists'] for d in dirs)
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
                                item['allocation_id'],
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
            self.ctx, counts,
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
