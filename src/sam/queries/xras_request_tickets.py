"""Reads for the one NUSD ticket a refused XRAS New or Renewal earns
(docs/plans/XRAS_REQUEST_TICKETS.md): the 422's pointer and state lines, and the
ticket state the webapp shows without any outbound call.

SQLAlchemy only; the task that files lives in ``scheduling/tasks/xras_request_tickets.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from sqlalchemy.orm import Session

from sam.integration.tickets.models import ExternalTicket
from sam.notify.models import NotificationLog
from sam.queries.xras_requests import message_kind

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


@dataclass(frozen=True)
class TicketState:
    """``pending`` (no ticket yet), ``open`` (``key`` set) or ``failed`` (the
    task's last attempt to open one failed)."""

    status: str
    key: str = ''


def request_ticket_state(session: Session, request_id: int) -> TicketState:
    ticket = (session.query(ExternalTicket)
              .filter(ExternalTicket.entity_type == ENTITY_TYPE,
                      ExternalTicket.entity_id == request_id)
              .order_by(ExternalTicket.creation_time.desc(),
                        ExternalTicket.external_ticket_id.desc())
              .first())
    if ticket is not None:
        return TicketState('open', ticket.ticket_key)
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
    return (f'{_REFUSED}. If it is still refused in about {grace_minutes + 5} minutes, '
            'a ticket opens with NUSD.')


def reply_lines(messages: Sequence[str], *, state: Optional[TicketState] = None,
                grace_minutes: int = GRACE_MINUTES) -> List[str]:
    """The 422's list: the state line (when tickets are armed), the blockers
    verbatim, then one pointer per blocker kind."""
    head = [state_line(state, grace_minutes=grace_minutes)] if state is not None else []
    return head + list(messages) + pointer_lines(messages)
