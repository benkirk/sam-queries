"""`sam-admin accounting --disk`: a disk-usage snapshot into disk_charge_summary and disk_activity."""
from cli.core.utils import EXIT_ERROR, EXIT_SUCCESS
from sam.summaries.disk_summaries import BYTES_PER_TIB
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from cli.core.output import output_json
from cli.core.display_utils import progress as progress_bar
from cli.accounting.display import (
    display_disk_dry_run_table,
    display_disk_import_report,
)
from cli.accounting.disk_usage import get_disk_usage_reader, DiskUsageEntry
from sam.manage.summaries import (
    resolve_user,
    upsert_disk_charge_summary,
    upsert_disk_activity, upsert_disk_charge,
)
from sam.manage.transaction import management_transaction
from sam.summaries.disk_summaries import (
    DISK_CHARGING_TIB_EPOCH, mark_disk_snapshot_current, tib_years,
)


# Some feeds (e.g. Quasar) ship one rollup row per project, with the literal
# sentinel `'total'` for a username. Keep it as the `act_username` audit label
# and attribute the row to the project lead, matching the `<unidentified>`
# gap-row convention, so user resolution and charging math succeed.
_DISK_ROLLUP_USERNAMES = frozenset({'total'})

# Fileset labels HSG reports that no SAM project owns (scratch, test and
# hackathon areas). Rows under them with an unlinked path are skipped, not
# charged and not errors. Grow it here; it is not config.
KNOWN_UNOWNED_PROJCODES = frozenset({'ROOT', 'RISC', 'NGIC'})

# Disk-import report categories. Only UNEXPECTED ones make the run exit 2;
# the rest are reported and exit 0. See docs/plans/DISK_INGEST_TRIAGE.md.
DISK_UNEXPECTED_CATEGORIES = ('no_project', 'no_account', 'unknown_user')
DISK_REPORT_CATEGORIES = DISK_UNEXPECTED_CATEGORIES + (
    'known_unowned', 'unlinked_directory', 'expired_directory',
)
# --reconcile-directories writes these; 'review' (path owned by another project) never.
DIRECTORY_AUTO_ACTIONS = ('reopen', 'rename', 'create')


@dataclass(frozen=True)
class _DiskResolution:
    """How a disk row resolved; ``reason`` is None or a report category."""
    project: Optional[object] = None
    account: Optional[object] = None
    via: Optional[str] = None       # 'path' | 'projcode' | 'label'
    reason: Optional[str] = None
    action: Optional[str] = None    # unlinked_directory only: DIRECTORY_AUTO_ACTIONS or 'review'
    directory_id: Optional[int] = None   # the row a reopen/rename acts on

    @property
    def ok(self) -> bool:
        return self.account is not None


class _DirectoryIndex:
    """Every project_directory row, ended ones included, to pick a reconcile action."""

    def __init__(self, rows):
        self.by_path = defaultdict(list)
        self.by_case = defaultdict(list)
        for pd in rows:
            self.by_path[pd.directory_name].append(pd)
            self.by_case[(pd.project_id, pd.directory_name.lower())].append(pd)

    @staticmethod
    def _latest(rows):
        return max(rows, key=lambda r: (r.end_date is None, r.end_date or datetime.min))

    def action_for(self, project_id: int, path: str) -> tuple[str, Optional[int]]:
        same = [r for r in self.by_path.get(path, ()) if r.project_id == project_id]
        if same:
            return 'reopen', self._latest(same).project_directory_id
        if self.by_path.get(path):
            return 'review', None
        variants = self.by_case.get((project_id, path.lower()))
        if variants:
            return 'rename', self._latest(variants).project_directory_id
        return 'create', None


def _build_disk_import_report(entries, resolve, known_usernames: set,
                              skipped_system: dict) -> dict:
    """Classify normal (non-gap) rows into report categories; plain dict out.

    A row that did not resolve is counted under its reason only. An unknown
    user is checked on resolved rows, so a row can be both ``unlinked_directory``
    (informational) and ``unknown_user``. Rollup ``total`` rows have no user.
    """
    buckets: dict[str, dict] = {c: {} for c in DISK_REPORT_CATEGORIES}
    rows = dict.fromkeys(DISK_REPORT_CATEGORIES, 0)

    for e in entries:
        if e.user_override is not None:
            continue
        res = resolve(e)
        if res.reason is not None:
            rows[res.reason] += 1
            item = buckets[res.reason].setdefault((e.projcode, e.directory_path), {
                'projcode': e.projcode, 'path': e.directory_path,
                'sam_projcode': res.project.projcode if res.project else None,
                'project_id': res.project.project_id if res.project else None,
                'rows': 0, 'bytes': 0,
            })
            if res.reason == 'unlinked_directory':
                item.update(action=res.action, project_directory_id=res.directory_id)
            item['rows'] += 1
            item['bytes'] += e.bytes
        if not res.ok or e.username in _DISK_ROLLUP_USERNAMES:
            continue
        if e.username.lower() not in known_usernames:
            rows['unknown_user'] += 1
            item = buckets['unknown_user'].setdefault(e.username, {
                'username': e.username, 'projcodes': set(), 'rows': 0, 'bytes': 0,
            })
            item['projcodes'].add(res.project.projcode)
            item['rows'] += 1
            item['bytes'] += e.bytes

    categories = {}
    for cat, bucket in buckets.items():
        items = list(bucket.values())
        if cat == 'unknown_user':
            for item in items:
                item['projcodes'] = sorted(item['projcodes'])
            items.sort(key=lambda i: i['username'])
        else:
            items.sort(key=lambda i: (i['projcode'], i['path'] or ''))
        categories[cat] = items

    counts = dict(rows)
    counts['system_account_rows'] = sum(skipped_system.values())
    counts['unexpected'] = sum(rows[c] for c in DISK_UNEXPECTED_CATEGORIES)
    return {
        'counts': counts,
        'categories': categories,
        'system_accounts': dict(sorted(skipped_system.items())),
    }


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


class DiskIngestMixin:
    """The --disk path of `AccountingAdminCommand`."""

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
        epoch: Optional[date] = None,
        reconcile_directories: bool = False,
    ) -> int:
        """Import a per-user-per-project disk usage snapshot into ``disk_charge_summary``.

        See ``docs/plans/implemented/DISK_CHARGING.md`` for the design. High-level flow:

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

        # JSON mode: stdout carries only the envelope; progress and notes go to stderr.
        json_mode = self.ctx.output_format == 'json'
        if json_mode:
            self.console = self.ctx.stderr_console

        # ---- 1. Validate inputs ----------------------------------------
        if not resource_name:
            self.console.print(
                "Error: --disk requires --resource", style="bold red"
            )
            return EXIT_ERROR
        if not user_usage_path:
            self.console.print(
                "Error: --disk requires --user-usage <path>", style="bold red"
            )
            return EXIT_ERROR
        if reconcile_gap and not quotas_path:
            self.console.print(
                "Error: --reconcile-quota-gap requires --quotas <path>",
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

        # ---- 2. Parse the per-user file -------------------------------
        try:
            reader = get_disk_usage_reader(resource_name, user_usage_path)
        except NotImplementedError as exc:
            self.console.print(f"Error: {exc}", style="bold red")
            return EXIT_ERROR
        try:
            entries = reader.read()
        except (OSError, ValueError) as exc:
            self.console.print(
                f"Error reading {user_usage_path!r}: {exc}", style="bold red"
            )
            return EXIT_ERROR

        if not entries:
            self.console.print(
                f"[yellow]No usage rows in {user_usage_path}[/yellow]"
            )
            return EXIT_SUCCESS

        snap_date = reader.snapshot_date
        if snap_date is None:
            self.console.print(
                "Error: cannot determine snapshot date from "
                f"{user_usage_path!r}", style="bold red",
            )
            return EXIT_ERROR

        # ---- 3. Date assertion (--date safety check) -------------------
        # --date collapses to start_date == end_date, and the file's snapshot
        # date must then match it exactly -- catching "wrong file fed to wrong
        # date" before any DB write.
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
                return EXIT_ERROR

        # ---- 4. Cutover-epoch enforcement ------------------------------
        # --epoch overrides the constant for known-safe backfills; the error
        # still names DISK_CHARGING_TIB_EPOCH so operators know what it replaces.
        effective_epoch = epoch or DISK_CHARGING_TIB_EPOCH
        if snap_date < effective_epoch:
            self.console.print(
                f"Error: snapshot date {snap_date} is before the "
                f"DISK_CHARGING_TIB_EPOCH ({effective_epoch}). "
                "This command only writes post-epoch TiB-year rows; "
                "pre-epoch legacy rows are not rewritten. "
                "Pass --epoch YYYY-MM-DD to override for a known-safe "
                "backfill.",
                style="bold red",
            )
            return EXIT_ERROR

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
                return EXIT_ERROR
            entries.extend(gap_rows)

        # ---- 6. Charging math ------------------------------------------
        for e in entries:
            e.terabyte_years = tib_years(e.bytes, reporting_interval)
            e.charges = e.terabyte_years

        # ---- 6b. Resolve projcode for normal rows ----------------------
        # acct.glade column 3 is a fileset label ('cesm', 'cgd'), not a SAM
        # projcode. Resolve as legacy did, directory_path -> ProjectDirectory ->
        # Project: path lookup first, then projcode-as-label, then the one project
        # this file's linked rows give the same label (/quasar/rda_dr rides with
        # /quasar/rda, as tier-3 grouping by label already charges it). A row that
        # fails gets a report category (_DiskResolution.reason) instead of an error.
        from sam.projects.projects import ProjectDirectory, Project
        from sam.accounting.allocations import Allocation
        pd_path_to_project: dict[str, "Project"] = {
            pd.directory_name: proj
            for pd, proj in (
                self.session.query(ProjectDirectory, Project)
                .join(Project, Project.project_id == ProjectDirectory.project_id)
                .filter(ProjectDirectory.is_currently_active)
                .all()
            )
        }
        directory_index = _DirectoryIndex(self.session.query(ProjectDirectory).all())
        funded_accounts = {
            account_id for (account_id,) in (
                self.session.query(Allocation.account_id)
                .join(Account, Account.account_id == Allocation.account_id)
                .filter(Account.resource_id == resource.resource_id, Allocation.is_active)
                .distinct()
            )
        }
        label_projects: dict[str, dict] = {}
        for e in entries:
            proj = pd_path_to_project.get(e.directory_path) if e.directory_path else None
            if proj is not None:
                label_projects.setdefault(e.projcode, {})[proj.project_id] = proj
        resolution_cache: dict[tuple, _DiskResolution] = {}
        account_cache: dict[int, Optional["Account"]] = {}

        def _resolve_for_row(row) -> _DiskResolution:
            """Resolve a normal row (gap rows carry overrides); memoized per (path, projcode)."""
            key = (row.directory_path, row.projcode)
            if key not in resolution_cache:
                resolution_cache[key] = _resolve_uncached(row)
            return resolution_cache[key]

        def _resolve_uncached(row) -> _DiskResolution:
            project = pd_path_to_project.get(row.directory_path) if row.directory_path else None
            via = 'path'
            if project is None:
                if row.projcode in KNOWN_UNOWNED_PROJCODES:
                    return _DiskResolution(reason='known_unowned')
                project = Project.get_by_projcode(self.session, row.projcode)
                via = 'projcode'
            if project is None:
                siblings = label_projects.get(row.projcode, {})
                if len(siblings) != 1:
                    return _DiskResolution(reason='no_project')
                (project,) = siblings.values()
                via = 'label'

            if project.project_id not in account_cache:
                account_cache[project.project_id] = Account.get_by_project_and_resource(
                    self.session, project.project_id, resource.resource_id,
                    exclude_deleted=not include_deleted_accounts,
                )
            acct = account_cache[project.project_id]
            if acct is None:
                return _DiskResolution(project=project, via=via, reason='no_account')
            if via == 'path' or not row.directory_path:
                return _DiskResolution(project=project, account=acct, via=via)
            if acct.account_id not in funded_accounts:
                return _DiskResolution(project=project, account=acct, via=via,
                                       reason='expired_directory')
            action, directory_id = directory_index.action_for(
                project.project_id, row.directory_path)
            return _DiskResolution(project=project, account=acct, via=via,
                                   reason='unlinked_directory', action=action,
                                   directory_id=directory_id)

        # ---- 6c. Classify every row before any write (reads only) ------
        known_usernames = self._known_usernames(entries)
        report = _build_disk_import_report(
            entries, _resolve_for_row, known_usernames, reader.skipped_system,
        )
        unexpected = report['counts']['unexpected']
        envelope = {
            'resource': resource_name, 'snapshot_date': snap_date,
            'dry_run': dry_run, 'skip_errors': skip_errors, 'written': False,
        }

        # ---- 7. Verbose dry-run table ----------------------------------
        if self.ctx.verbose and not json_mode:
            display_disk_dry_run_table(
                self.ctx, entries, resource_name, dry_run=dry_run,
            )

        if dry_run:
            self._emit_disk_report(report, envelope)
            return EXIT_ERROR if unexpected else EXIT_SUCCESS
        # Without --skip-errors an unexpected gap refuses the whole file, before
        # the first write, so a partial load never happens by accident.
        if unexpected and not skip_errors:
            self._emit_disk_report(report, envelope)
            self.console.print(
                "[bold red]Unresolved rows; nothing written. Fix them or pass "
                "--skip-errors to load the rest.[/bold red]"
            )
            return EXIT_ERROR

        # ---- 6d. --reconcile-directories: fix the links the report found -----
        if reconcile_directories:
            try:
                with management_transaction(self.session):
                    envelope['directories'] = self._reconcile_directories(
                        report['categories']['unlinked_directory'])
            except Exception as exc:  # noqa: BLE001
                self.console.print(
                    f"[bold red]Directory reconcile failed: {exc}[/bold red]")
                return EXIT_ERROR

        # ---- 7. Register the snapshot date BEFORE any tier-3 insert.
        # disk_charge_summary.activity_date FKs disk_charge_summary_status, and
        # InnoDB checks FKs at statement time, so the parent status row must
        # exist first. Marking it after the inserts instead would rely on a
        # prior same-date import having seeded the row -- true for the co-dated
        # GPFS feeds, but not for an off-cadence date from another feed (Destor's
        # Monday tick against a Saturday-cadence status table).
        try:
            with management_transaction(self.session):
                mark_disk_snapshot_current(self.session, snap_date)
        except Exception as exc:  # noqa: BLE001
            self.console.print(
                f"[bold red]Failed to register snapshot {snap_date}: {exc}[/bold red]"
            )
            return EXIT_ERROR

        # ---- 7a. Tier-1 / Tier-2: populate disk_activity + disk_charge.
        # Per-fileset granularity that disk_charge_summary can't carry.
        # Iterates raw entries (pre-_group_disk_entries) so multi-fileset
        # projects produce one disk_activity row per (directory, user).
        try:
            n_act, n_ch, n_skip_tier12 = self._write_disk_activity_and_charge(
                entries,
                snap_date=snap_date,
                resource_name=resource_name,
                resolve_for_row=_resolve_for_row,
                chunk_size=chunk_size,
                skip_errors=skip_errors,
            )
        except ValueError as exc:
            self.console.print(
                f"[bold red]Tier-1/Tier-2 write aborted: {exc}[/bold red]"
            )
            return EXIT_ERROR
        if self.ctx.verbose:
            self.console.print(
                f"[dim]Wrote {n_act} disk_activity / {n_ch} disk_charge "
                f"row(s) for {resource_name} on {snap_date} "
                f"({n_skip_tier12} unresolved → tier-1 only).[/dim]"
            )

        # ---- 7b. Idempotency: delete pre-existing rows for this
        # (resource, snapshot_date) so a re-run replaces rather than duplicates.
        # The legacy ingest left `act_username` / `act_projcode` NULL, so the
        # natural-key UPDATE cannot match those rows -- they would accumulate
        # alongside the new ones and double-count on roll-up.
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
            return EXIT_ERROR
        if n_deleted_legacy:
            self.console.print(
                f"[dim]Cleared {n_deleted_legacy} pre-existing "
                f"disk_charge_summary row(s) for "
                f"{resource_name} on {snap_date} before re-import.[/dim]"
            )

        # ---- 7c. Aggregate per-(user, fileset) rows into per-(user, project)
        # totals before upsert: the input ships one row per (user, directory)
        # and the upsert natural key omits directory_path, so multi-fileset
        # projects would silently UPDATE-overwrite. See `_group_disk_entries`.
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

        chunks = [
            entries_to_upsert[i:i + chunk_size]
            for i in range(0, len(entries_to_upsert), chunk_size)
        ]

        with progress_bar(self.ctx) as progress:
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
                                # Normal rows carry the parsed username and
                                # projcode, which the resolver needs. Gap rows
                                # (`<unidentified>`) carry the audit label in
                                # row.act_username and set row.user_override,
                                # so the resolver is skipped entirely.
                                user_for_upsert = row.user_override
                                if row.user_override is not None:
                                    act_uname = row.act_username
                                    act_pcode = None
                                    project_for_upsert = None
                                    account_for_upsert = row.account_override
                                else:
                                    act_uname = row.username
                                    # Rows the report already counts (unresolved
                                    # or an unknown user) are skipped, not errors.
                                    res = _resolve_for_row(row)
                                    if not res.ok or (
                                        act_uname not in _DISK_ROLLUP_USERNAMES
                                        and act_uname.lower() not in known_usernames
                                    ):
                                        continue
                                    project_for_upsert = res.project
                                    account_for_upsert = res.account
                                    # The audit column carries the SAM-canonical
                                    # projcode, not the input's umbrella label.
                                    act_pcode = project_for_upsert.projcode

                                    # Project-rollup feeds ship no per-user
                                    # breakdown, so attribute to the project
                                    # lead and keep the sentinel as the audit
                                    # username -- the `<unidentified>` gap-row
                                    # convention.
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
                    return EXIT_ERROR

        # ---- 9. Re-stamp: legacy SAM's disk_charge triggers set this date
        # current=FALSE on each insert/delete in 7a, and legacy's Quartz recompute
        # then rewrites the whole day in its own shape (HARD_DELETE_AUDIT.md §3).
        try:
            with management_transaction(self.session):
                mark_disk_snapshot_current(self.session, snap_date)
        except Exception as exc:  # noqa: BLE001
            self.console.print(
                f"[bold red]Failed to re-stamp snapshot {snap_date}: {exc}[/bold red]"
            )
            return EXIT_ERROR

        envelope.update(written=True, created=n_created, updated=n_updated, errors=n_errors)
        self._emit_disk_report(report, envelope)
        return EXIT_ERROR if (unexpected or n_errors) else EXIT_SUCCESS

    def _reconcile_directories(self, items: list) -> dict:
        """Apply the report's reopen/rename/create actions; returns counts per action."""
        from sam.projects.projects import ProjectDirectory
        counts = dict.fromkeys(DIRECTORY_AUTO_ACTIONS, 0)
        done = set()
        for item in items:
            key = (item['project_id'], item['path'])
            if item['action'] not in DIRECTORY_AUTO_ACTIONS or key in done:
                continue
            done.add(key)
            if item['action'] == 'create':
                ProjectDirectory.create(self.session, project_id=item['project_id'],
                                        directory_name=item['path'])
            else:
                pd = self.session.get(ProjectDirectory, item['project_directory_id'])
                if item['action'] == 'rename':
                    pd.update(directory_name=item['path'])
                pd.reopen()
            counts[item['action']] += 1
        return counts

    def _known_usernames(self, entries) -> set:
        """Lowercased usernames of normal rows that exist in SAM (one query)."""
        from sam.core.users import User
        names = {
            e.username for e in entries
            if e.user_override is None and e.username not in _DISK_ROLLUP_USERNAMES
        }
        if not names:
            return set()
        found = self.session.query(User.username).filter(User.username.in_(names))
        return {u.lower() for (u,) in found}

    def _emit_disk_report(self, report: dict, envelope: dict) -> None:
        if self.ctx.output_format == 'json':
            output_json({'kind': 'disk_import', **envelope, **report})
        else:
            display_disk_import_report(self.ctx, {**envelope, **report})

    def _write_disk_activity_and_charge(
        self,
        entries: list,
        *,
        snap_date: date,
        resource_name: str,
        resolve_for_row,
        chunk_size: int,
        skip_errors: bool,
    ) -> tuple[int, int, int]:
        """Tier-1/Tier-2 importer: populate ``disk_activity`` and
        ``disk_charge`` from per-fileset entries.

        Idempotent per ``(resource_name, snap_date)``: pre-existing rows
        for this slice are deleted (two-step, since the prod FK has no
        ``ON DELETE CASCADE``) before re-insert. Skips synthetic gap rows
        (``user_override`` set) and rollup-sentinel rows
        (``_DISK_ROLLUP_USERNAMES``) — both are tier-3-only by
        construction.

        Unresolved rows still get a ``disk_activity`` row with
        ``processing_status=False`` and ``error_comment`` set; ``disk_charge``
        is skipped for those (audit trail without tier-2 noise).

        Returns ``(n_activity, n_charge, n_unresolved)``.
        """
        from sam.activity.disk import DiskActivity, DiskCharge

        now = datetime.now()
        n_activity = 0
        n_charge = 0
        n_unresolved = 0

        # ---- Filter out tier-1-skip rows ------------------------------
        # Compute writable BEFORE any DB access so the rollup-only /
        # gap-only fast path is a true no-op (no probe SELECT).
        writable = [
            e for e in entries
            if e.user_override is None
            and e.username not in _DISK_ROLLUP_USERNAMES
        ]
        if not writable:
            return 0, 0, 0

        # ---- Idempotency: two-step delete (FK has no CASCADE).
        # Combined with the chunked write into ONE management_transaction
        # so nothing churns MySQL SAVEPOINTs in xdist test mode beyond
        # what's strictly necessary.
        chunks = [
            writable[i:i + chunk_size]
            for i in range(0, len(writable), chunk_size)
        ]

        with progress_bar(self.ctx) as progress:
            task = progress.add_task(
                f"Writing {resource_name} disk_activity/disk_charge...",
                total=len(writable),
            )
            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    with management_transaction(self.session):
                        if chunk_idx == 1:
                            # Idempotency delete piggybacks the first chunk's
                            # transaction. Two-step (FK has no CASCADE).
                            existing_ids = [
                                row[0] for row in (
                                    self.session.query(
                                        DiskActivity.disk_activity_id
                                    )
                                    .filter(
                                        DiskActivity.activity_date == snap_date,
                                        DiskActivity.resource_name == resource_name,
                                    )
                                    .all()
                                )
                            ]
                            if existing_ids:
                                self.session.query(DiskCharge).filter(
                                    DiskCharge.disk_activity_id.in_(existing_ids)
                                ).delete(synchronize_session=False)
                                self.session.query(DiskActivity).filter(
                                    DiskActivity.disk_activity_id.in_(existing_ids)
                                ).delete(synchronize_session=False)
                        for e in chunk:
                            progress.advance(task)
                            # Resolve project/account first; failure is
                            # captured as audit metadata on the tier-1 row.
                            res = resolve_for_row(e)
                            account = res.account
                            user = None
                            err = None
                            if not res.ok:
                                err = (
                                    f"unresolved({res.reason}): projcode={e.projcode!r} "
                                    f"path={e.directory_path!r}"
                                )
                            else:
                                try:
                                    user = resolve_user(
                                        self.session, e.username, None,
                                    )
                                except ValueError as uexc:
                                    err = str(uexc)

                            try:
                                activity, _ = upsert_disk_activity(
                                    self.session,
                                    directory_name=e.directory_path,
                                    username=e.username,
                                    projcode=e.projcode,
                                    activity_date=e.activity_date,
                                    reporting_interval=e.reporting_interval,
                                    bytes=e.bytes,
                                    number_of_files=e.number_of_files,
                                    resource_name=resource_name,
                                    load_date=now,
                                    disk_cos_id=0,
                                    error_comment=err,
                                    processing_status=err is None,
                                )
                                n_activity += 1
                            except Exception as exc:  # noqa: BLE001
                                if not skip_errors:
                                    raise
                                if self.ctx.verbose:
                                    self.console.print(
                                        f"[yellow]Skip disk_activity: {exc}[/yellow]"
                                    )
                                continue

                            if err is not None:
                                n_unresolved += 1
                                continue

                            try:
                                upsert_disk_charge(
                                    self.session,
                                    disk_activity_id=activity.disk_activity_id,
                                    account_id=account.account_id,
                                    user_id=user.user_id,
                                    charge_date=now,
                                    activity_date=e.activity_date,
                                    terabyte_year=e.terabyte_years,
                                    charge=e.charges,
                                )
                                n_charge += 1
                            except Exception as exc:  # noqa: BLE001
                                if not skip_errors:
                                    raise
                                if self.ctx.verbose:
                                    self.console.print(
                                        f"[yellow]Skip disk_charge: {exc}[/yellow]"
                                    )
                except Exception as exc:  # noqa: BLE001
                    self.console.print(
                        f"[bold red]Tier-1/Tier-2 chunk {chunk_idx} aborted: {exc}[/bold red]"
                    )
                    raise

        return n_activity, n_charge, n_unresolved

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
          b. fileset path matches a path observed in user_entries -> that
             row's projcode
          c. fileset path matches a ProjectDirectory.path -> that project's projcode
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

        # Build path -> projcode fallback from ProjectDirectory.
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
                f"with unattributed bytes; total {total_gap_bytes / BYTES_PER_TIB:.2f} TiB "
                "attributed to project leads with audit label "
                f"{unidentified_label!r}.[/cyan]"
            )
        return gap_rows
