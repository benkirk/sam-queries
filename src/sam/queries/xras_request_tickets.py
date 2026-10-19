"""Reads for the one NUSD ticket a refused XRAS New or Renewal earns
(docs/plans/XRAS_REQUEST_TICKETS.md): the 422's pointer and state lines, the
ticket state the webapp shows without any outbound call, and the episodes, keys
and text the ``xras_request_tickets`` task files from.

SQLAlchemy only; the task that files lives in ``scheduling/tasks/xras_request_tickets.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from sam.integration.tickets.models import ExternalTicket
from sam.integration.xras import XrasActionLog
from sam.notify.models import NotificationLog
from sam.queries.xras_actions import _split_errors
from sam.queries.xras_requests import message_kind, person_username

#: ``external_ticket.entity_type``; ``entity_id`` is the XRAS ``requestId``,
#: which survives the rename.
ENTITY_TYPE = 'xras_request'
#: The ``notification_log`` kind of every ticket write (open, comment, applied).
LEDGER_KIND = 'xras_request_ticket'
#: Action types that open a ticket; a refused Supplement or Extension does not.
TICKET_ACTION_TYPES = ('New', 'Renewal')
#: Minutes a request may stay refused before its ticket opens; the task's
#: ``SAM_TASKS_XRAS_TICKETS_GRACE_MINUTES`` overrides it. The CronJob's
#: quarter-hourly wake adds up to 15 more.
GRACE_MINUTES = 10
#: The arming floor (an ISO date or datetime, naive Mountain): a request first refused
#: before it never gets a ticket. The task and the 422 read the same key.
FLOOR_KEY = 'SAM_TASKS_XRAS_TICKETS_SINCE'

#: One line per blocker kind present, in this order. Extra lines after the
#: legacy strings, which stay byte-identical.
POINTERS = (
    ('contract', 'To fix a contract: create it from the seeded form (Admin > Contracts), '
                 "or waive it with the request's contract override."),
    ('mnemonic', 'To fix a mnemonic: link the organization or institution to a code '
                 "(Admin > Mnemonic Codes), or set the request's mnemonic override."),
    ('placeholder', 'To fix a placeholder: merge it into the real account '
                    '(Allocations > XRAS > Pending Users).'),
    ('person', 'To fix a person: create or reactivate the account, or drop the member '
               'from the request in XRAS.'),
)

_REFUSED = 'This action cannot yet succeed'


def ticket_handle(request_id: int) -> str:
    """``SAM-XR-<requestId>``: the ticket summary's lookup token."""
    return f'SAM-XR-{request_id}'


def pointer_lines(messages: Sequence[str]) -> List[str]:
    kinds = {message_kind(m) for m in messages}
    return [line for kind, line in POINTERS if kind in kinds]


def ticket_floor() -> Optional[datetime]:
    """The arming floor, or ``None``; a malformed value raises, visibly."""
    from sam.integration._config import config_str
    raw = config_str(FLOOR_KEY, '').strip()
    return datetime.fromisoformat(raw) if raw else None


@dataclass(frozen=True)
class TicketState:
    """``pending`` (no ticket yet), ``open`` (``key`` set), ``failed`` (the
    task's last attempt to open one failed) or ``floored`` (first refused before
    the arming floor, so none will open)."""

    status: str
    key: str = ''


def request_ticket_state(session: Session, request_id: int, *,
                         floor: Optional[datetime] = None) -> TicketState:
    ticket = (session.query(ExternalTicket)
              .filter(ExternalTicket.entity_type == ENTITY_TYPE,
                      ExternalTicket.entity_id == request_id)
              .order_by(ExternalTicket.creation_time.desc(),
                        ExternalTicket.external_ticket_id.desc())
              .first())
    if ticket is not None:
        return TicketState('open', ticket.ticket_key)
    if floor is not None and refused_before(session, [request_id], floor):
        return TicketState('floored')
    last = (session.query(NotificationLog.status)
            .filter(NotificationLog.kind == LEDGER_KIND,
                    NotificationLog.entity_type == ENTITY_TYPE,
                    NotificationLog.entity_id == request_id)
            .order_by(NotificationLog.creation_time.desc(),
                      NotificationLog.notification_log_id.desc())
            .first())
    if last is not None and last.status == 'failed':
        return TicketState('failed')
    return TicketState('pending')


def state_line(state: TicketState, *, grace_minutes: int = GRACE_MINUTES) -> str:
    if state.status == 'open':
        return f'{_REFUSED}, see {state.key}.'
    if state.status == 'failed':
        return f'{_REFUSED}. Opening a ticket failed; NUSD has not been notified.'
    if state.status == 'floored':
        return f'{_REFUSED}.'
    return (f'{_REFUSED}. If it is still refused in about {grace_minutes + 5} minutes, '
            'a ticket opens with NUSD.')


def reply_lines(messages: Sequence[str], *, state: Optional[TicketState] = None,
                grace_minutes: int = GRACE_MINUTES) -> List[str]:
    """The 422's list: the state line (when tickets are armed), the blockers
    verbatim, then one pointer per blocker kind."""
    head = [state_line(state, grace_minutes=grace_minutes)] if state is not None else []
    return head + list(messages) + pointer_lines(messages)


# -- What the task reads --------------------------------------------------------


@dataclass(frozen=True)
class Episode:
    """One request's refusals from its first in the window, and the post that
    applied after them, if any."""

    request_id: int
    request_number: str
    action_type: str
    first_refused: datetime
    refusals: int
    latest_log_id: int
    latest_refused: datetime
    messages: Tuple[str, ...]
    payload: Mapping[str, Any]
    applied_at: Optional[datetime] = None
    projcode: Optional[str] = None


def _refused():
    return (XrasActionLog.status == 'failed',
            XrasActionLog.http_status == 422,
            XrasActionLog.action_type.in_(TICKET_ACTION_TYPES),
            XrasActionLog.request_id.is_not(None))


def refused_before(session: Session, request_ids: Iterable[int], floor: datetime) -> Set[int]:
    """The requests among *request_ids* with a refused New/Renewal before *floor*."""
    ids = sorted(set(request_ids))
    if not ids:
        return set()
    rows = (session.query(XrasActionLog.request_id).distinct()
            .filter(*_refused(), XrasActionLog.request_id.in_(ids),
                    XrasActionLog.received_time < floor)
            .all())
    return {r.request_id for r in rows}


def ticket_episodes(session: Session, *, since: datetime, until: datetime) -> List[Episode]:
    """Refused New/Renewal posts received in ``[since, until]``, one per
    ``requestId``. Refusals after the post that applied are not part of it."""
    refusals = (session.query(XrasActionLog)
                .filter(*_refused(),
                        XrasActionLog.received_time >= since,
                        XrasActionLog.received_time <= until)
                .order_by(XrasActionLog.received_time, XrasActionLog.xras_action_log_id)
                .all())
    by_request: Dict[int, List[XrasActionLog]] = {}
    for row in refusals:
        by_request.setdefault(row.request_id, []).append(row)
    if not by_request:
        return []
    processed = (session.query(XrasActionLog.request_id, XrasActionLog.received_time,
                               XrasActionLog.projcode_result)
                 .filter(XrasActionLog.status == 'processed',
                         XrasActionLog.request_id.in_(sorted(by_request)),
                         XrasActionLog.received_time >= since,
                         XrasActionLog.received_time <= until)
                 .order_by(XrasActionLog.received_time, XrasActionLog.xras_action_log_id)
                 .all())
    episodes = []
    for request_id, rows in by_request.items():
        first = rows[0].received_time
        applied = next((p for p in processed
                        if p.request_id == request_id and p.received_time > first), None)
        if applied is not None:
            rows = [r for r in rows if r.received_time < applied.received_time]
        latest = rows[-1]
        episodes.append(Episode(
            request_id=request_id, request_number=latest.request_number or '',
            action_type=latest.action_type or '', first_refused=first,
            refusals=len(rows), latest_log_id=latest.xras_action_log_id,
            latest_refused=latest.received_time,
            messages=tuple(_split_errors(latest.error_messages)),
            payload=_payload(latest.raw_payload),
            applied_at=applied.received_time if applied else None,
            projcode=applied.projcode_result if applied else None))
    return sorted(episodes, key=lambda e: (e.first_refused, e.request_id))


def _payload(raw: Optional[str]) -> Mapping[str, Any]:
    try:
        loaded = json.loads(raw or '{}')
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def blocker_digest(messages: Sequence[str]) -> str:
    return hashlib.sha1('\n'.join(messages).encode()).hexdigest()[:16]


def open_key(request_id: int) -> str:
    return f'xras_ticket:{request_id}:open'


def content_key(request_id: int, messages: Sequence[str]) -> str:
    """The blocker list a ticket last carried. A -> B -> A does not comment twice."""
    return f'xras_ticket:{request_id}:{blocker_digest(messages)}'


def applied_key(request_id: int) -> str:
    return f'xras_ticket:{request_id}:applied'


# -- What the ticket says -------------------------------------------------------


def ticket_summary(episode: Episode) -> str:
    return (f'XRAS request {episode.request_number} cannot be posted '
            f'[{ticket_handle(episode.request_id)}]')


def blocker_groups(messages: Sequence[str]) -> Tuple[List[dict], List[str]]:
    """``([{'username', 'lines'}], other_lines)``: one checklist entry per person."""
    people: Dict[str, List[str]] = {}
    others = []
    for m in messages:
        username = person_username(m)
        if username:
            people.setdefault(username, []).append(m)
        else:
            others.append(m)
    return [{'username': u, 'lines': lines} for u, lines in people.items()], others


def _people(payload: Mapping[str, Any]) -> Dict[str, dict]:
    """``username -> {'name', 'email', 'pi'}`` for the people on the request at its
    action date: the handler's roster plus both role-candidate lists, which take a
    looser begin date (legacy defect 3), as ``xras_accounts._roster_from_action`` does."""
    from sam.xras.roster import (ALLOCATION_MANAGER_ROLE, PI_ROLE, normalize_username,
                                 role_candidates, roster_usernames)
    pis = role_candidates(payload, PI_ROLE)
    on_request = (*roster_usernames(payload), *pis,
                  *role_candidates(payload, ALLOCATION_MANAGER_ROLE))
    out: Dict[str, dict] = {u: {'name': '', 'email': '', 'pi': u in pis}
                            for u in on_request if u}
    for role in payload.get('roles') or ():
        entry = out.get(normalize_username(role.get('username')))
        person = role.get('person') or {}
        if entry is not None and not entry['name']:
            entry['name'] = ' '.join(p for p in (person.get('firstName'),
                                                 person.get('lastName')) if p)
            entry['email'] = (person.get('email') or '').strip()
    return out


def ticket_context(session: Session, episode: Episode, *, card_url: str = '') -> dict:
    """The ``xras_request_ticket`` template's context."""
    from sam.core.account_requests import OPEN_STATES
    from sam.queries.account_requests import requests_by_xras_username
    from sam.queries.xras_accounts import is_placeholder, sam_merge_targets

    people = _people(episode.payload)
    pis = [f"{p['name'] or u} ({u})" for u, p in people.items() if p['pi']]
    groups, others = blocker_groups(episode.messages)
    blocked = [g['username'] for g in groups]

    queued = []
    for rows in requests_by_xras_username(session, people).values():
        for row in rows:
            if row.state in OPEN_STATES and not row.is_fulfilled:
                queued.append({'id': row.account_request_id, 'name': row.display_name,
                               'email': row.email, 'purpose': row.purpose,
                               'state': row.state})

    placeholders = [u for u in blocked if is_placeholder(u)]
    emails = {u: people.get(u, {}).get('email', '') for u in placeholders}
    targets = sam_merge_targets(session, emails.values())
    merges = [{'placeholder': u,
               'target': (targets.get(emails[u].lower()) or {}).get('username') or ''}
              for u in placeholders]
    return {
        'request_number': episode.request_number,
        'xras_request_id': episode.request_id,
        'action_type': episode.action_type,
        'title': (episode.payload.get('requestTitle') or '').strip(),
        'pi': ', '.join(pis),
        'first_refused': episode.first_refused.strftime('%Y-%m-%d %H:%M'),
        'refusals': episode.refusals,
        'person_blockers': groups,
        'other_blockers': others,
        'pointers': pointer_lines(episode.messages),
        'queued': sorted(queued, key=lambda r: r['id']),
        'merges': merges,
        'card_url': card_url,
    }


def changed_comment(episode: Episode) -> str:
    lines = [f'The blockers changed. SAM refused the post of '
             f'{episode.latest_refused:%Y-%m-%d %H:%M} with:']
    lines += [f'- {m}' for m in episode.messages]
    pointers = pointer_lines(episode.messages)
    if pointers:
        lines += ['', 'Where each is fixed:'] + [f'- {p}' for p in pointers]
    return '\n'.join(lines)


def applied_comment(episode: Episode) -> str:
    return (f'Request {episode.request_number} posted and applied as {episode.projcode} '
            f'({episode.applied_at:%Y-%m-%d %H:%M}). SAM has nothing left to block; '
            'close this ticket when the work here is done.')
