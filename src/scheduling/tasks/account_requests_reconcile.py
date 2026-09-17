"""``account_requests_reconcile`` -- act on accounts that now exist, hourly.

The ONE scheduled writer of fulfillment: each open request whose email now
resolves to exactly one active user is stamped, an ``enrollment`` gets its
membership, and unverified public rows past the horizon are purged. The
queue card's "Reconcile now" button runs the same function on demand.

DB-only, so no ``dry_run`` branch: the runner's rollback is complete coverage
(``TaskContext.dry_run``). Design: docs/plans/ACCOUNT_REGISTRATION.md.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import DEFAULT_TZ, Hourly, to_local_naive
from scheduling.tasks._notice_common import positive_int_env

#: Twenty past, so it never shares a minute with the sweep's :00 pass that
#: feeds it rows.
SCHEDULE = Hourly(minute=20)

#: An unverified public row older than this is a stranger's typo or a bot;
#: overridable via ``$SAM_TASKS_ACCOUNT_PURGE_DAYS``.
DEFAULT_PURGE_DAYS = 7


def purge_days(env: Optional[dict] = None) -> int:
    """Read per run, like every task knob."""
    return positive_int_env('SAM_TASKS_ACCOUNT_PURGE_DAYS', DEFAULT_PURGE_DAYS, env)


@task(name='account_requests_reconcile',
      schedule=SCHEDULE,
      needs=('sam',),
      expected_runtime=timedelta(minutes=2),
      description='Fulfill account requests whose accounts now exist; '
                  'purge stale unverified ones')
def account_requests_reconcile(ctx) -> TaskResult:
    """Stamp fulfilled requests, enroll them, purge the stale unverified."""
    from sam.manage.account_requests import reconcile_account_requests

    # Rows are stamped naive-Mountain; ctx.occurrence is naive UTC.
    clock = to_local_naive(ctx.occurrence, ZoneInfo(DEFAULT_TZ))
    days = purge_days()
    counts = reconcile_account_requests(ctx.sam_session, clock=clock,
                                        purge_unverified_days=days)
    ctx.logger.info('account requests: %(checked)d checked, %(fulfilled)d '
                    'fulfilled, %(enrolled)d enrolled, %(enroll_failed)d '
                    'enrollment failure(s), %(purged)d purged', counts)
    return TaskResult(
        detail={**counts, 'purge_days': days, 'clock': clock.isoformat()},
        # An enrollment failure is a data condition a human fixes on the card
        # (the row stays in the queue with its reason); it is not a red Job.
        message=(f"{counts['fulfilled']} fulfilled, {counts['enrolled']} "
                 f"enrolled, {counts['enroll_failed']} enrollment failure(s), "
                 f"{counts['purged']} purged"),
    )
