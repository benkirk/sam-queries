"""Accounting commands for SAM.

AccountingAdminCommand: `sam-admin accounting`; its --comp, --disk and
--reconcile-quotas paths live in comp_ingest.py, disk_ingest.py and
quota_reconcile.py as mixins.
AccountingSearchCommand / AccountingJobsCommand: `sam-search accounting [--jobs]`.
"""
import os
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from datetime import date
from typing import Optional

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.accounting.display import (
    display_charge_summary_table,
    display_jobs_table,
)
from cli.accounting.comp_ingest import (  # noqa: F401  (re-exported for tests and callers)
    GPU_FRACTION_THRESHOLD, CompIngestMixin, adapt_jobstats_row, classify_comp_resource,
    normalize_queue_name,
)
from cli.accounting.disk_ingest import DiskIngestMixin
from cli.accounting.quota_reconcile import QUOTA_TOLERANCE, QuotaReconcileMixin  # noqa: F401
from sam.plugins import HPC_USAGE_QUERIES


class AccountingAdminCommand(CompIngestMixin, DiskIngestMixin, QuotaReconcileMixin,
                             BaseCommand):
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
        reconcile_directories: bool = False,
        # --comp/--disk epoch override (default: hard-coded constant per mode)
        epoch: Optional[date] = None,
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
                epoch=epoch,
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
                epoch=epoch,
                reconcile_directories=reconcile_directories,
            )
        if archive:
            self.console.print("[yellow]--archive: not yet implemented[/yellow]")
            return EXIT_SUCCESS
        self.console.print(
            "Error: specify --comp, --disk, --archive, or --reconcile-quotas",
            style="bold red",
        )
        return EXIT_ERROR


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

        if self.ctx.output_format == 'json':
            output_json({
                'kind': 'comp_charge_summary',
                'start_date': start_date,
                'end_date': end_date,
                'per_day': self.ctx.verbose,
                'count': len(rows),
                'rows': rows,
            })
        elif rows:
            display_charge_summary_table(self.ctx, rows, start_date, end_date)
        else:
            self.console.print("[yellow]No charge records found for the given filters.[/yellow]")
        return EXIT_SUCCESS if rows else EXIT_NOT_FOUND


# Machines whose hpc-usage-queries databases the CLI may open. Keep in
# lockstep with webapp's _VALID_MACHINES (src/webapp/jobs/routes.py) and the
# plugin's job_history.database.session.VALID_MACHINES.
VALID_JOB_MACHINES = {'derecho', 'casper'}

# Default number of jobs for --recent when no selector is given.
DEFAULT_RECENT_JOBS = 50

# Columns requested from the plugin's jobs_search(). Identity/display fields
# plus the four the GPU/CPU classifier needs (cpu/gpu hours + charges). We
# deliberately never request memory columns — we track but don't bill memory.
JOB_COLUMNS = (
    'job_id', 'account', 'user', 'queue', 'qos', 'qos_factor', 'exit_status',
    'submit', 'start', 'end', 'elapsed', 'walltime',
    'numnodes', 'numcpus', 'numgpus', 'cputype', 'gputype',
    'cpu_hours', 'gpu_hours', 'cpu_charges', 'gpu_charges',
)


def _configured_job_machines() -> list:
    """Machines to query when --machine is not given.

    Sourced from $JOB_HISTORY_MACHINES (comma-separated), defaulting to
    derecho,casper — the same source webapp/config.py uses.
    """
    raw = os.environ.get('JOB_HISTORY_MACHINES', 'derecho,casper')
    return [m.strip().lower() for m in raw.split(',') if m.strip()]


def _end_sort_key(row: dict):
    """Chronological sort key for a job row's end time (None sorts first/oldest).

    Coerces datetimes to ISO strings so mixed datetime/str/None values never
    raise during comparison.
    """
    e = row.get('end')
    if e is None:
        return ''
    return e.isoformat() if hasattr(e, 'isoformat') else str(e)


class AccountingJobsCommand(BaseCommand):
    """List individual jobs from hpc-usage-queries (sam-search accounting --jobs)."""

    def execute(
        self,
        *,
        start_date: date,
        end_date: date,
        username: Optional[str] = None,
        projcode: Optional[str] = None,
        queue: Optional[str] = None,
        qos: Optional[str] = None,
        machine: Optional[str] = None,
        recent: Optional[int] = None,
        largest: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> int:
        json_mode = self.ctx.output_format == 'json'

        # --- Selection mode + limit ---
        # `--job-id` is its own single-result selector and cannot combine with
        # `--recent N` / `--largest N`: it skips list-mode validation and runs
        # one no-sort, no-limit query per machine.
        if job_id is not None:
            if recent is not None or largest is not None:
                self.console.print(
                    "[bold red]--job-id cannot be combined with --recent or "
                    "--largest (job-id is itself the selector).[/bold red]"
                )
                return EXIT_ERROR
            mode, limit = 'job_id', None
        else:
            if recent is not None and largest is not None:
                msg = "--recent and --largest are mutually exclusive."
                self.console.print(f"[bold red]{msg}[/bold red]")
                return EXIT_ERROR
            if largest is not None:
                mode, limit = 'largest', largest
            else:
                mode, limit = 'recent', (recent if recent is not None else DEFAULT_RECENT_JOBS)
            if limit < 1:
                self.console.print("[bold red]Job count must be >= 1.[/bold red]")
                return EXIT_ERROR

        # --- Resolve machines to query ---
        if machine:
            m = machine.strip().lower()
            if m not in VALID_JOB_MACHINES:
                valid = ', '.join(sorted(VALID_JOB_MACHINES))
                self.console.print(
                    f"[bold red]Unknown machine {machine!r}. Valid: {valid}.[/bold red]"
                )
                return EXIT_ERROR
            machines = [m]
        else:
            machines = _configured_job_machines()

        # --- Load plugin ---
        mod = self.require_plugin(HPC_USAGE_QUERIES)
        if mod is None:
            return EXIT_ERROR
        JobQueries = mod.JobQueries
        jh_get_session = mod.get_session

        # --- Account filter (comma-separated -> list of exact codes) ---
        if projcode and ',' in projcode:
            account = [p.strip() for p in projcode.split(',') if p.strip()]
        else:
            account = projcode or None

        base_filters = dict(
            account=account,
            user=username,
            queue=queue,
            qos=qos,
            start=start_date,
            end=end_date,
            columns=list(JOB_COLUMNS),
        )

        merged: list = []
        for mach in machines:
            try:
                jh_session = jh_get_session(mach)
            except Exception as exc:
                self.console.print(
                    f"[bold red]Error opening job_history session for {mach!r}: {exc}[/bold red]"
                )
                return EXIT_ERROR
            try:
                jq = JobQueries(jh_session, machine=mach)
                if mode == 'job_id':
                    # One query per machine; the plugin-side input-shape
                    # classifier (exact vs prefix LIKE) handles scalar, array
                    # and array-element forms uniformly. No sort or limit --
                    # typically 1 row, at most a parent plus its elements.
                    rows = list(jq.jobs_search(
                        **base_filters, job_id=job_id,
                    ))
                elif mode == 'largest':
                    # No combined-charge sort key upstream yet: union the top-N
                    # by cpu_charges and by gpu_charges, then re-rank by the
                    # classified charge below.
                    rows = list(jq.jobs_search(
                        **base_filters, sort_by='cpu_charges', sort_dir='desc', limit=limit,
                    ))
                    rows += list(jq.jobs_search(
                        **base_filters, sort_by='gpu_charges', sort_dir='desc', limit=limit,
                    ))
                else:
                    rows = list(jq.jobs_search(
                        **base_filters, sort_by='end', sort_dir='desc', limit=limit,
                    ))
            except Exception as exc:
                self.console.print(
                    f"[bold red]Error fetching jobs for {mach!r}: {exc}[/bold red]"
                )
                return EXIT_ERROR
            finally:
                jh_session.close()

            for row in rows:
                row['machine'] = mach
            merged.extend(rows)

        # --- Dedup (largest unions two queries) + classify + derive fields ---
        seen = set()
        rows: list = []
        for row in merged:
            key = (row.get('machine'), row.get('job_id'))
            if key in seen:
                continue
            seen.add(key)
            resource, _machine_name, comp_hours, charges = classify_comp_resource(
                row.get('machine'),
                row.get('queue'),
                row.get('cpu_hours'),
                row.get('gpu_hours'),
                row.get('cpu_charges'),
                row.get('gpu_charges'),
            )
            row['resource'] = resource
            row['comp_hours'] = comp_hours
            row['charges'] = charges
            rows.append(row)

        # --- Global re-rank + truncate ---
        if mode == 'largest':
            rows.sort(key=lambda r: r.get('charges') or 0.0, reverse=True)
        else:
            # Both 'recent' and 'job_id' modes show newest-first (job_id
            # typically returns one row, but the parent + array elements
            # case can return many — newest-end at the top is the sensible
            # default).
            rows.sort(key=_end_sort_key, reverse=True)
        if limit is not None:
            rows = rows[:limit]

        if not rows:
            if json_mode:
                output_json({
                    'kind': 'comp_jobs',
                    'start_date': start_date,
                    'end_date': end_date,
                    'mode': mode,
                    'machines': machines,
                    'count': 0,
                    'rows': [],
                })
                return EXIT_NOT_FOUND
            self.console.print("[yellow]No jobs found for the given filters.[/yellow]")
            return EXIT_NOT_FOUND

        if json_mode:
            output_json({
                'kind': 'comp_jobs',
                'start_date': start_date,
                'end_date': end_date,
                'mode': mode,
                'machines': machines,
                'count': len(rows),
                'rows': rows,
            })
            return EXIT_SUCCESS

        display_jobs_table(
            self.ctx, rows, start_date, end_date,
            mode=mode, multi_machine=len(machines) > 1,
        )
        return EXIT_SUCCESS
