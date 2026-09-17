"""``account_queue_digest`` -- mail NUSD the open account requests, weekly.

Monday 08:00 America/Denver, one message to ``$NOTIFY_ACCOUNT_QUEUE_TO``,
keyed on the day so the queue card's Send button and this task cannot both
send it. The reconcile pass runs first so a row whose account already exists
never reaches NUSD, and every row that left is stamped ``requested_at``.

Unlike ``expiration_notices`` there is no per-run summary mail: the digest IS
the one message, and the ledger row is its record. A queue with nothing in
it sends nothing -- NUSD asked for a worklist, not a heartbeat -- and the
ledger detail says so. Design: docs/plans/ACCOUNT_REGISTRATION.md section 3.2.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import Weekly, to_local_naive
from scheduling.tasks._notice_common import (
    new_sam_session as _new_sam_session,
    positive_int_env,
    raise_if_disabled,
)
from scheduling.tasks.mail_guards import EmailCapExceeded

#: Monday 08:00 Mountain: on NUSD's desk when the week starts.
SCHEDULE = Weekly(0, 8, 0, tz='America/Denver')

#: Runaway guard on the digest's row count, overridable via
#: ``$SAM_TASKS_ACCOUNT_MAX``. A digest listing thousands of people is a
#: selection bug, not a workshop.
DEFAULT_ACCOUNT_MAX = 500


def account_max(env: Optional[dict] = None) -> int:
    return positive_int_env('SAM_TASKS_ACCOUNT_MAX', DEFAULT_ACCOUNT_MAX, env)


def digest_recipient(env: Optional[dict] = None) -> str:
    """Where the digest goes. Empty means the task is inert, by design."""
    return ((env or os.environ).get('NOTIFY_ACCOUNT_QUEUE_TO') or '').strip()


def queue_url(env: Optional[dict] = None) -> str:
    """The link the digest carries, from ``$NOTIFY_ACCOUNT_QUEUE_URL``.

    ``NOTIFY_``-prefixed so the CronJob receives it by prefix, beside the
    recipient. Empty means the digest carries no link.
    """
    return ((env or os.environ).get('NOTIFY_ACCOUNT_QUEUE_URL') or '').strip()


@task(name='account_queue_digest',
      schedule=SCHEDULE,
      needs=('sam',),
      # A lease knob, not a runtime estimate: max(3x, 900s) must exceed the
      # CronJob's activeDeadlineSeconds (3000s), or a killed send is
      # reclaimed mid-flight and NUSD is mailed twice. The drift test pins it.
      expected_runtime=timedelta(minutes=20),
      # A late digest is byte-identical (the key is the slot's day).
      misfire_grace=timedelta(hours=24),
      description='Mail NUSD the weekly digest of open HPC account requests')
def account_queue_digest(ctx) -> TaskResult:
    """Reconcile, then send the open queue to NUSD as one message."""
    from sam.manage.account_requests import reconcile_account_requests
    from sam.notify import Notifier
    from sam.notify.ledger import NotificationLedger
    from sam.queries.account_requests import build_queue_summary, queue_requests

    session = ctx.sam_session
    occurrence = to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz))

    # 1. Reconcile first: a row whose account exists must not reach NUSD.
    reconciled = reconcile_account_requests(session, clock=occurrence)
    rows = queue_requests(session)
    detail = {
        'occurrence_local': occurrence.isoformat(),
        'reconciled': reconciled,
        'selected': len(rows),
        'new': sum(1 for r in rows if r.requested_at is None),
        'waiting': sum(1 for r in rows if r.requested_at is not None),
        'sent': 0,
    }

    recipient = digest_recipient()
    if not recipient:
        detail['reason'] = 'NOTIFY_ACCOUNT_QUEUE_TO is unset'
        ctx.logger.info('NOTIFY_ACCOUNT_QUEUE_TO is unset; no digest sent')
        return TaskResult(detail=detail,
                          message=f'{len(rows)} open; no digest recipient configured')

    notifier = Notifier(ledger=NotificationLedger(
        # The ledger's OWN sessions: mail cannot be un-sent by a rollback.
        lambda: _new_sam_session(session)))

    # 2. Guards, before any transport is touched.
    raise_if_disabled(notifier)
    cap = account_max()
    if len(rows) > cap:
        reason = (f'{len(rows)} open requests exceed SAM_TASKS_ACCOUNT_MAX={cap}; '
                  f'nothing was sent')
        raise EmailCapExceeded(reason, audience=len(rows), cap=cap)

    if not rows:
        detail['reason'] = 'queue empty'
        ctx.logger.info('no open account requests; no digest sent')
        return TaskResult(detail=detail, message='queue empty; nothing sent')

    message = build_queue_summary(
        session, rows, recipient=recipient, occurrence=occurrence,
        requested_by=f'task:{ctx.task_name}', queue_url=queue_url())

    # 3. Send.
    if ctx.dry_run:
        notifier.preview(message)            # writes NO ledger row
        return TaskResult(detail={**detail, 'dry_run': True},
                          message=f'{len(rows)} open; digest previewed, not sent')

    result = notifier.send(message)
    detail.update({'status': result.status, 'sent': int(result.ok),
                   'failed': int(not result.ok), 'error': result.detail})
    if result.status in ('sent', 'redirected'):
        # Send first, record second: the stamp says NUSD was told.
        for row in rows:
            row.mark_requested(occurrence)
        session.flush()
    elif result.status == 'suppressed':
        ctx.logger.info('digest for %s already sent today; not re-sent',
                        occurrence.date())
    return TaskResult(
        detail=detail,
        message=f"{len(rows)} open; digest {result.status}",
        partial_failures=int(not result.ok and result.status != 'suppressed'))
