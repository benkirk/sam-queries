"""``xras_request_tickets`` — one NUSD ticket per XRAS New or Renewal still refused.

Every quarter hour. It reads the refused posts in ``xras_action_log``
(``sam.queries.xras_request_tickets.ticket_episodes``) and, per request:
- opens the ticket once the first refusal is older than the grace period;
- comments when the latest refusal's blocker list is new;
- comments once a post applies.
Comments are internal and NUSD closes the ticket. The webapp only answers posts.

``XRAS_REQUEST_TICKETS_ENABLED`` off, or ``--dry-run``: report, write nothing (the
ledger and link rows commit on their own sessions, so the runner's rollback cannot
undo them). On: file through ``TICKET_PROVIDER``, raising when it cannot write;
samuel-dev runs ``dry-run``. ``SAM_TASKS_XRAS_TICKETS_SINCE`` (an ISO date) is the
arming floor: a request refused before it never gets a ticket opened.
WARNING: the CronJob's one ticket writer.
Design: docs/plans/XRAS_REQUEST_TICKETS.md.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import CronExpr, to_local_naive
from scheduling.tasks._notice_common import new_sam_session, positive_int_env

#: The CronJob wakes at :07/:22/:37/:52, so a ticket opens 10-25 minutes after
#: the first refusal.
SCHEDULE = CronExpr('*/15 * * * *', tz='UTC')

#: Blast-radius bound on the first arming, not a retry budget.
LOOKBACK_DAYS = 30
OPERATOR = 'task:xras_request_tickets'
ENABLED_ENV = 'XRAS_REQUEST_TICKETS_ENABLED'
GRACE_ENV = 'SAM_TASKS_XRAS_TICKETS_GRACE_MINUTES'
MAX_ENV = 'SAM_TASKS_XRAS_TICKETS_MAX'
#: Opens per wake; past it the run raises before any write.
DEFAULT_MAX = 10
#: The Remediations card's public URL; empty leaves the link out.
CARD_URL_ENV = 'NOTIFY_XRAS_REQUESTS_URL'
_MAX_REPORTED = 100
_TZ = ZoneInfo('America/Denver')


class TicketWritesUnavailable(RuntimeError):
    """Armed, but ``TICKET_PROVIDER`` names nothing that can write."""


class TicketCapExceeded(RuntimeError):
    """More opens than ``SAM_TASKS_XRAS_TICKETS_MAX``. Nothing was filed."""

    def __init__(self, message: str, *, opens: int, cap: int) -> None:
        super().__init__(message)
        self.task_detail = {'opens': opens, 'cap': cap, 'aborted_before_filing': True}


def armed() -> bool:
    from sam.integration._config import config_bool
    return config_bool(ENABLED_ENV, False)


def plan(episode, ticket_key: Optional[str], ledger, *, until: datetime,
         grace: timedelta) -> str:
    """What this wake owes one request: ``open``, ``comment``, ``applied``, or a
    no-op state (``waiting``, ``unchanged``, ``done``, ``fixed``)."""
    from sam.queries import xras_request_tickets as q
    if ticket_key is None:
        if episode.applied_at is not None:
            return 'fixed'
        return 'open' if until - episode.first_refused >= grace else 'waiting'
    if episode.applied_at is not None:
        return 'done' if ledger.already_sent(q.applied_key(episode.request_id)) else 'applied'
    if ledger.already_sent(q.content_key(episode.request_id, episode.messages)):
        return 'unchanged'
    return 'comment'


@task(name='xras_request_tickets', schedule=SCHEDULE, needs=('sam',),
      expected_runtime=timedelta(minutes=2),     # lease knob: the 900 s floor
      description='Open and comment on NUSD tickets for XRAS requests still refused')
def xras_request_tickets(ctx) -> TaskResult:
    from sam.integration.tickets import XRAS_REQUEST_KIND, TicketDraft
    from sam.integration.tickets.filing import LinkTarget, file_once
    from sam.integration.tickets.queries import tickets_for
    from sam.notify import Notifier
    from sam.notify.base import Channel, Message, Recipient
    from sam.notify.ledger import NotificationLedger
    from sam.queries import xras_request_tickets as q

    until = to_local_naive(ctx.occurrence, _TZ)
    since = until - timedelta(days=LOOKBACK_DAYS)
    grace = timedelta(minutes=positive_int_env(GRACE_ENV, q.GRACE_MINUTES))
    floor = q.ticket_floor()
    writing = armed() and not ctx.dry_run

    def factory():
        return new_sam_session(ctx.sam_session)

    ledger = NotificationLedger(factory)
    episodes = q.ticket_episodes(ctx.sam_session, since=since, until=until)
    linked = tickets_for(ctx.sam_session, q.ENTITY_TYPE, [e.request_id for e in episodes])
    plans = [(e, linked[e.request_id][-1].ticket_key if e.request_id in linked else None)
             for e in episodes]
    # The floor gates opens only: a linked ticket keeps its comments.
    predates = (q.refused_before(ctx.sam_session,
                                 [e.request_id for e, key in plans if key is None], floor)
                if floor else set())
    plans = [(e, key, 'predates_floor' if key is None and e.request_id in predates
              else plan(e, key, ledger, until=until, grace=grace)) for e, key in plans]

    detail: Dict[str, Any] = {
        'since': since.isoformat(), 'until': until.isoformat(),
        'floor': floor.isoformat() if floor else None,
        'grace_minutes': int(grace.total_seconds() // 60), 'armed': armed(),
        'writing': writing, 'provider': None, 'episodes': len(episodes),
        'open': 0, 'comment': 0, 'applied': 0, 'waiting': 0, 'unchanged': 0,
        'done': 0, 'fixed': 0, 'predates_floor': 0, 'failed': 0, 'sample': [],
    }

    def note(state: str, episode, key: Optional[str], **extra) -> None:
        detail[state] += 1
        if len(detail['sample']) < _MAX_REPORTED:
            detail['sample'].append({'request_id': episode.request_id,
                                     'request_number': episode.request_number,
                                     'state': state, 'ticket': key, **extra})

    if not writing:
        for episode, key, state in plans:
            note(state, episode, key)
        verb = 'dry run' if ctx.dry_run else 'shadow'
        return TaskResult(detail=detail, message=(
            f"{verb}: would open {detail['open']}, comment {detail['comment']}, "
            f"close out {detail['applied']} of {len(episodes)}"))

    # Guards, before any write.
    from sam.integration.tickets.registry import provider_from_environment
    provider = provider_from_environment(interactive=False)
    if provider is None or not provider.write_configured:
        raise TicketWritesUnavailable(
            f'{ENABLED_ENV} is on but TICKET_PROVIDER cannot write '
            f'({provider.name if provider else "mail"}); nothing was filed')
    detail['provider'] = provider.name
    opens = sum(1 for _, _, state in plans if state == 'open')
    cap = positive_int_env(MAX_ENV, DEFAULT_MAX)
    if opens > cap:
        raise TicketCapExceeded(f'{opens} tickets to open exceed {MAX_ENV}={cap}; '
                                'nothing was filed', opens=opens, cap=cap)

    notifier = Notifier(ledger=ledger)
    card_url = (os.environ.get(CARD_URL_ENV) or '').strip()

    def message(episode, key: str, context=None) -> Message:
        return Message(kind=q.LEDGER_KIND,
                       recipient=Recipient(provider.destination, name='NUSD',
                                           role='operator', channel=Channel.TICKET),
                       subject=q.ticket_summary(episode), context=context or {},
                       entity=(q.ENTITY_TYPE, episode.request_id),
                       projcode=episode.projcode, dedup_key=key, requested_by=OPERATOR)

    def comment(episode, ticket: str, key: str, text: str) -> bool:
        try:
            provider.comment(ticket, text, internal=True)
        except Exception as exc:                        # noqa: BLE001
            ledger.record(message(episode, key), status='failed', transport=provider.name,
                          detail=f'{type(exc).__name__}: {exc}')
            ctx.logger.warning('request %s: comment on %s failed: %s',
                               episode.request_id, ticket, exc)
            return False
        ledger.record(message(episode, key), status='sent', transport=provider.name,
                      detail=ticket)
        return True

    for episode, ticket, state in plans:
        rid = episode.request_id
        if state == 'open':
            filed = message(episode, q.open_key(rid),
                            q.ticket_context(ctx.sam_session, episode, card_url=card_url))
            handle = q.ticket_handle(rid)
            filing = file_once(
                provider, ledger, filed, handle=handle,
                render=lambda m=filed: notifier.preview(m),
                draft=lambda rendered, m=filed, h=handle: TicketDraft(
                    handle=h, summary=m.subject, body=rendered.text,
                    kind=XRAS_REQUEST_KIND, link_url=card_url),
                target=LinkTarget(q.ENTITY_TYPE, rid, OPERATOR),
                session_factory=factory, label=f'request {rid}')
            if not filing.ok:
                note('failed', episode, None, error=str(filing.error))
                continue
            if filing.status == 'suppressed':
                # Filed earlier but never linked: noted in the detail, not refiled.
                note('unchanged', episode, None, filing='suppressed')
                continue
            key = filing.ref.key if filing.ref else None
            ledger.record(message(episode, q.content_key(rid, episode.messages)),
                          status='sent', transport=provider.name, detail=key)
            note('open', episode, key, filing=filing.status)
        elif state == 'comment':
            if comment(episode, ticket, q.content_key(rid, episode.messages),
                       q.changed_comment(episode)):
                note('comment', episode, ticket)
            else:
                note('failed', episode, ticket)
        elif state == 'applied':
            if comment(episode, ticket, q.applied_key(rid), q.applied_comment(episode)):
                note('applied', episode, ticket, projcode=episode.projcode)
            else:
                note('failed', episode, ticket)
        else:
            note(state, episode, ticket)

    return TaskResult(detail=detail, partial_failures=detail['failed'], message=(
        f"{detail['open']} opened, {detail['comment']} commented, "
        f"{detail['applied']} closed out, {detail['failed']} failed "
        f"of {len(episodes)} through {provider.name}"))
