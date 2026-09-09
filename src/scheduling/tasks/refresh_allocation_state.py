"""``refresh_allocation_state`` — the hourly writer of the allocation read-model.

Rebuilds ``account_allocation_state`` from the same batched computation the
dashboards run, so the table cannot drift from the live answer. Readers
consult it through the freshness gate in ``sam.queries.allocation_state``
and fall back to the live path otherwise. Design: ``docs/plans/READ_MODEL.md``.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from scheduling.registry import TaskResult, task
from scheduling.schedules import Hourly

#: Top of every hour, UTC. The dispatcher wakes at :07, so rows land ~:07.
SCHEDULE = Hourly(minute=0)


@task(name='refresh_allocation_state',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the LEASE, not a timeout: max(3x, 900s) = 3600s must exceed the
      # CronJob's activeDeadlineSeconds (3000s), or a killed run is reclaimed
      # while its predecessor's rows are still being written.
      expected_runtime=timedelta(minutes=20),
      description='Rebuild the allocation/usage read-model (account_allocation_state)')
def refresh_allocation_state(ctx) -> TaskResult:
    """Project every candidate allocation and make the table equal to it."""
    from sam.queries.allocation_state import project_allocation_state
    from sam.summaries.allocation_state import AccountAllocationState

    # The wall clock, deliberately -- the one task that must NOT compute from
    # ctx.occurrence. The rows describe the database as it is being read, and
    # `refreshed_at` is compared against SAM's naive-Mountain app-clock
    # timestamps by the freshness gate. Taken BEFORE the projection so a
    # charge landing mid-run is never claimed early. Whole seconds: DATETIME.
    refreshed_at = datetime.now().replace(microsecond=0)
    started = time.monotonic()

    session = ctx.sam_session
    rows = project_allocation_state(session, now=refreshed_at)
    if not rows:
        exc = RuntimeError('projection produced no rows; table left untouched')
        exc.task_detail = {'refreshed_at': refreshed_at.isoformat(), 'rows': 0}
        raise exc

    # No commit: the runner owns the transaction (deactivate_expired.py).
    counts = AccountAllocationState.bulk_replace(session, rows,
                                                 refreshed_at=refreshed_at)

    detail = {
        'refreshed_at': refreshed_at.isoformat(),
        'projects': len({r['project_id'] for r in rows}),
        'rows': len(rows),
        'current': sum(1 for r in rows if r['is_current']),
        **counts,
        'elapsed_s': round(time.monotonic() - started, 1),
    }
    ctx.logger.info('read-model: %d row(s) for %d project(s) in %.1fs '
                    '(+%d ~%d -%d)', detail['rows'], detail['projects'],
                    detail['elapsed_s'], counts['inserted'], counts['updated'],
                    counts['deleted'])
    return TaskResult(detail=detail,
                      message=f"{detail['rows']} row(s) refreshed")
