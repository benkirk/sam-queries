"""``account_requests_reconcile`` -- act on accounts that now exist, every 15 minutes.

The ONE scheduled writer of fulfillment: each open request whose email now
resolves to exactly one active user is stamped, an ``enrollment`` gets its
membership, and unverified public rows past the horizon are purged. The
queue card's "Reconcile now" button runs the same function on demand. Then,
when ``JIRA_ENABLED``, it learns help-desk ticket keys and refreshes their status.

Then, behind ``XRAS_SUBMIT_ANONYMOUS_ENABLED`` and ``XRAS_WRITE_ENABLED`` in tasks.env,
it merges each fulfilled ``/request`` placeholder into its account in XRAS
(``merge_fulfilled_visitors``): its one XRAS write, skipped by ``--dry-run``. The merge
reads committed rows, so a row this pass fulfilled merges on the next.
Otherwise the database alone, which the runner's rollback covers. Never a tracker
write: it reaches the tracker through ``read_providers`` alone. Design:
docs/plans/implemented/ACCOUNT_REGISTRATION.md, docs/plans/implemented/TICKET_PROVIDER.md,
docs/plans/implemented/XRAS_SUBMISSION_SPRINT2.md 3b.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import DEFAULT_TZ, CronExpr, to_local_naive
from scheduling.tasks._notice_common import positive_int_env

#: Every quarter hour: a new account is usable on its project within ~15 min
#: of the LDAP mirror landing it, not ~60. The CronJob wakes at :07/:22/:37/:52
#: (helm tasks.schedule), so each slot is claimed about seven minutes later.
SCHEDULE = CronExpr('*/15 * * * *', tz='UTC')

#: An unverified public row older than this is a stranger's typo or a bot;
#: overridable via ``$SAM_TASKS_ACCOUNT_PURGE_DAYS``.
DEFAULT_PURGE_DAYS = 7


#: The audit actor of the placeholder merge (a ``TASK_WRITABLE_OPERATIONS`` write).
OPERATOR = 'task:account_requests_reconcile'

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

    # Fail-open: the pass handles a tracker outage itself; anything else (a JSON
    # shape, a DB without external_ticket) must not roll back the stamps above.
    from sam.integration.tickets.learn import describe, sync_tickets
    try:
        tickets = sync_tickets(ctx.sam_session, clock=clock, limit=ticket_lookup_max())
    except Exception as exc:                          # noqa: BLE001
        ctx.logger.exception('account requests: ticket pass failed')
        tickets = {'skipped': True, 'reason': f'{type(exc).__name__}: {exc}'}
    ctx.logger.info('account requests: %s', describe(tickets))
    merges = _merge_visitors(ctx)
    return TaskResult(
        detail={**counts, 'purge_days': days, 'clock': clock.isoformat(),
                'tickets': tickets, 'merges': merges},
        # An enrollment failure is a data condition a human fixes on the card
        # (the row stays in the queue with its reason); it is not a red Job.
        message=(f"{counts['fulfilled']} fulfilled, {counts['enrolled']} "
                 f"enrolled, {counts['enroll_failed']} enrollment failure(s), "
                 f"{counts['purged']} purged; {describe(tickets)}"
                 + (f"; {len(merges['merged'])} placeholder(s) merged"
                    if merges.get('merged') else '')),
    )


def _merge_visitors(ctx) -> dict:
    """The /request placeholder merge, fail-open like the ticket pass; ``skipped`` says why not."""
    from sam.integration.xras_api.config import XrasApiConfig
    from sam.manage.xras_remediation import merge_fulfilled_visitors
    from scheduling.tasks._notice_common import new_sam_session
    if not XrasApiConfig.from_environment().visitor_merge_configured:
        return {'skipped': 'lever off'}
    if ctx.dry_run:
        return {'skipped': 'dry run'}
    try:
        out = merge_fulfilled_visitors(lambda: new_sam_session(ctx.sam_session),
                                       operator=OPERATOR)
    except Exception as exc:                          # noqa: BLE001
        ctx.logger.exception('account requests: the placeholder merge failed')
        return {'skipped': f'{type(exc).__name__}: {exc}'}
    ctx.logger.info('account requests: placeholders %d due, %d merged, %d waiting, %d failed',
                    out['due'], len(out['merged']), out['waiting'], out['failed'])
    return {**out, 'merged': [list(m[:2]) for m in out['merged']]}
