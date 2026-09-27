"""``account_requests_reconcile`` -- act on accounts that now exist, hourly.

The ONE scheduled writer of fulfillment: each open request whose email now
resolves to exactly one active user is stamped, an ``enrollment`` gets its
membership, and unverified public rows past the horizon are purged. The
queue card's "Reconcile now" button runs the same function on demand. Then,
when ``JIRA_ENABLED``, it learns help-desk ticket keys and refreshes their status.

Writes only the database (the ticket pass only *reads* the tracker), so no
``dry_run`` branch: the runner's rollback is complete coverage. Never a tracker
write: ``JIRA_WRITE_ENABLED`` is not in the CronJob. Design:
docs/plans/implemented/ACCOUNT_REGISTRATION.md, docs/plans/TICKET_PROVIDER.md.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import DEFAULT_TZ, Hourly, to_local_naive
from scheduling.tasks._notice_common import positive_int_env

#: The minute is cosmetic: the CronJob wakes at :07 and the runner claims the
#: last occurrence, so this slot runs at the NEXT wake, in registry order
#: before xras_sweep. Rows the sweep writes reconcile one wake later.
SCHEDULE = Hourly(minute=20)

#: An unverified public row older than this is a stranger's typo or a bot;
#: overridable via ``$SAM_TASKS_ACCOUNT_PURGE_DAYS``.
DEFAULT_PURGE_DAYS = 7


#: Tracker reads per pass (learn and refresh each); ``$SAM_TASKS_TICKET_LOOKUP_MAX``.
DEFAULT_TICKET_LOOKUP_MAX = 50


def purge_days(env: Optional[dict] = None) -> int:
    """Read per run, like every task knob."""
    return positive_int_env('SAM_TASKS_ACCOUNT_PURGE_DAYS', DEFAULT_PURGE_DAYS, env)


def ticket_lookup_max(env: Optional[dict] = None) -> int:
    return positive_int_env('SAM_TASKS_TICKET_LOOKUP_MAX', DEFAULT_TICKET_LOOKUP_MAX, env)


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

    # Fail-open: a tracker outage is a line in the detail, never a red Job.
    from sam.integration.tickets.learn import describe, sync_tickets
    tickets = sync_tickets(ctx.sam_session, clock=clock, limit=ticket_lookup_max())
    ctx.logger.info('account requests: %s', describe(tickets))
    return TaskResult(
        detail={**counts, 'purge_days': days, 'clock': clock.isoformat(),
                'tickets': tickets},
        # An enrollment failure is a data condition a human fixes on the card
        # (the row stays in the queue with its reason); it is not a red Job.
        message=(f"{counts['fulfilled']} fulfilled, {counts['enrolled']} "
                 f"enrolled, {counts['enroll_failed']} enrollment failure(s), "
                 f"{counts['purged']} purged; {describe(tickets)}"),
    )
