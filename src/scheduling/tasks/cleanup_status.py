"""``cleanup_status_snapshots`` — prune old `system_status` rows nightly.

⚠️ **A task computes from ``ctx.occurrence``, never from the wall clock.**
That is what makes a run dispatched five hours late produce exactly the same
result as a punctual one, which is in turn what makes the whole scheme
deterministic and re-runnable. It is the single easiest thing for a task author
to get wrong, so it is stated here, at the top of the first task anyone will
copy.

The retention policy itself is not here — it lives in
``system_status.retention`` and is shared with the hand-run
``scripts/cleanup_status_data.py``, so a manual prune and the nightly one
cannot disagree. See ``docs/plans/implemented/SCHEDULED_TASKS.md`` § 3.1 and § 6.2.
"""

from __future__ import annotations

import os
from datetime import timedelta

from scheduling.registry import TaskResult, task
from scheduling.schedules import Daily
from system_status.retention import DEFAULT_RETENTION_DAYS, cleanup_old_data

#: How long the ledger keeps its own history. The task whose job is bounding
#: growth bounds it too — four lines, and no second CronJob.
TASK_RUN_RETENTION_DAYS = 180

#: 02:15 Mountain. Deliberately inside the window where both DST transitions
#: bite: the fold and gap rules are written down and tested
#: (`tests/unit/test_schedule_predicates.py`), and moving a nightly prune to
#: dodge a tested code path is superstition.
SCHEDULE = Daily(2, 15, tz='America/Denver')


def retention_days(env: dict | None = None) -> int:
    """The window, from ``$STATUS_RETENTION_DAYS`` or the policy default.

    Read per run rather than at import so a `values.yaml` change takes effect
    on the next dispatch rather than the next pod restart.
    """
    raw = (env or os.environ).get('STATUS_RETENTION_DAYS')
    if raw is None or not str(raw).strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    # A zero or negative window would delete everything, including rows
    # written seconds ago. Refuse rather than obey.
    return days if days > 0 else DEFAULT_RETENTION_DAYS


@task(name='cleanup_status_snapshots',
      schedule=SCHEDULE,
      needs=('status',),
      expected_runtime=timedelta(minutes=2),
      description='Prune system_status snapshot rows past the retention window')
def cleanup_status_snapshots(ctx) -> TaskResult:
    """Delete snapshot rows older than the retention window, and old ledger rows."""
    days = retention_days()
    cutoff = ctx.occurrence - timedelta(days=days)      # NOT utcnow()

    ctx.logger.info('pruning system_status before %s (%d-day window)',
                    cutoff.isoformat(), days)

    deleted = cleanup_old_data(
        retention_days=days,
        cutoff=cutoff,
        dry_run=ctx.dry_run,
        session=ctx.status_session,
    )

    # The ledger lives on the same bind, so it is pruned in the same
    # transaction. Guarded on `finished_at IS NOT NULL`, which is what stops
    # this run from deleting its own still-`running` row.
    from scheduling.ledger import TaskLedger
    pruned = 0
    if not ctx.dry_run:
        ledger = TaskLedger(lambda: _NonClosingSession(ctx.status_session))
        pruned = ledger.prune(
            older_than=ctx.occurrence - timedelta(days=TASK_RUN_RETENTION_DAYS))

    return TaskResult(
        detail={'deleted': deleted,
                'task_run_pruned': pruned,
                'cutoff': cutoff.isoformat(),
                'retention_days': days},
        message=f'{sum(deleted.values())} snapshot rows, {pruned} ledger rows')


class _NonClosingSession:
    """Lends the task's session to the ledger without letting it be closed.

    ``TaskLedger`` is built for its own short-lived sessions — it commits and
    closes each one, which is right when mail or a claim must survive a
    caller's rollback. Pruning is the opposite case: it is ordinary bulk
    deletion that belongs in the task's transaction, and a ledger that closed
    the session out from under the runner would break the commit that follows.
    """

    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False            # never closes; the runner owns the session

    def commit(self):
        self._inner.flush()     # the runner commits, once, at the end

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._inner, name)
