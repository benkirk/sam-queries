"""``unix_group_masks`` — the hourly watch over the operators' unix group masks
(docs/plans/UNIX_GROUP_MASK.md section 4): expire the overdue, confirm an adhoc mask the
directory now agrees with, re-assert a drifted entry row, count the rest.

Reads the directory and the ticket links; files and comments nothing. Transactional, so
``--dry-run`` is the runner's rollback. Prod ships it named in ``helm/values.yaml``
``SAM_TASKS_DISABLED`` (the list is fail-open); dev runs it.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import Hourly, to_local_naive

NAME = 'unix_group_masks'
#: Masks and entry rows are naive Mountain (the app clock).
TZ = 'America/Denver'
SCHEDULE = Hourly(minute=40)


@task(name=NAME,
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the lease (max(3x, 900 s) must exceed the CronJob's activeDeadlineSeconds).
      expected_runtime=timedelta(minutes=5),
      misfire_grace=timedelta(hours=6),
      description='Expire, confirm and re-assert the unix group masks against the directory')
def unix_group_masks(ctx) -> TaskResult:
    """One pass over the open masks."""
    # Deferred: the CLI's --list path must not pay for the ORM or ldap3.
    from sam.integration.directory.registry import reader_from_environment
    from sam.manage.groups import sweep_headline, sweep_masks

    reader = reader_from_environment()
    now = to_local_naive(ctx.occurrence, ZoneInfo(TZ))
    counts = sweep_masks(ctx.sam_session, reader if reader.configured else None,
                         now=now, by=f'task:{NAME}')
    counts['as_of'] = now.isoformat()
    headline = sweep_headline(counts)
    ctx.logger.info('unix group masks: %s', headline)
    return TaskResult(detail=counts, message=headline)
