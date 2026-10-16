"""``directory_report`` — the nightly sweep of the UCAR directory against SAM, counts only.

Read-only, so ``--dry-run`` is the same run (``detail`` says so). ``detail`` carries the
per-kind counts with capped key lists, ``message`` the headline. An unreadable directory
fails the run; an unconfigured one succeeds with nothing to compare. Prod ships it named in
``helm/values.yaml`` ``SAM_TASKS_DISABLED`` (the list is fail-open); dev runs it.
Record: docs/plans/DIRECTORY_CLIENT_NEXT.md section 4.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import Daily, to_local_naive

#: 03:00 Mountain: after the 02:15 status prune, before the working day.
SCHEDULE = Daily(3, 0, tz='America/Denver')


@task(name='directory_report',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the lease (max(3x, 900 s) must exceed the CronJob's activeDeadlineSeconds).
      expected_runtime=timedelta(minutes=5),
      misfire_grace=timedelta(hours=6),
      description='Compare every directory record with SAM: counts by kind and category')
def directory_report(ctx) -> TaskResult:
    """Sweep the directory and record the drift counts."""
    # Deferred: the CLI's --list path must not pay for the ORM or ldap3.
    from sam.integration.directory.registry import reader_from_environment
    from sam.queries.directory_sweep import sweep

    reader = reader_from_environment()
    if not reader.configured:
        ctx.logger.warning('no directory is configured (DIRECTORY_URL is empty); nothing to compare')
        return TaskResult(detail={'configured': False}, message='directory not configured')
    # The open-ness reference for the compare comes from the occurrence, never the wall clock.
    now = to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz))
    report = sweep(ctx.sam_session, reader, now=now)
    ctx.logger.info('directory report: %s', report.headline())
    detail = report.as_dict()
    detail['dry_run'] = bool(ctx.dry_run)
    return TaskResult(detail=detail, message=report.headline())
