"""File one ticket exactly once: the ledger check, find-before-create, a ``queued``
row, the create, and the ``external_ticket`` link on its own session.

Flask-free, so the webapp's account tickets (``webapp/register/handoff_mail.py``)
and the ``xras_request_tickets`` task share one routine. The caller supplies the
ledger, the message (its ``dedup_key`` is the once-only key) and a renderer, and
decides what an unsuccessful filing means (the webapp mails, the task reports).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sam.integration.tickets.base import (TicketDraft, TicketProvider, TicketRef,
                                          TicketRejected, TicketSourceUnavailable)
from sam.integration.tickets.models import ExternalTicket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Filing:
    """``status``: ``suppressed`` (the ledger holds it), ``found`` (the tracker
    already had it), ``created``, ``unavailable`` (the tracker failed or refused;
    stop asking it) or ``error`` (a local fault before anything was attempted)."""

    status: str
    ref: Optional[TicketRef] = None
    log_id: Optional[int] = None
    error: Optional[Exception] = None

    @property
    def ok(self) -> bool:
        return self.status in ('suppressed', 'found', 'created')


@dataclass(frozen=True)
class LinkTarget:
    """The entity an ``external_ticket`` row ties the ticket to."""

    entity_type: str
    entity_id: int
    requested_by: str


def file_once(provider: TicketProvider, ledger, message, *, handle: str,
              render: Callable[[], object],
              draft: Callable[[object], TicketDraft], target: LinkTarget,
              session_factory: Callable, label: str) -> Filing:
    """File ``message`` through ``provider`` unless its key is already recorded.

    ``render()`` is called only when a create is needed; ``draft(rendered)``
    builds what is filed.
    """
    if ledger.already_sent(message.dedup_key):
        logger.info('%s: ticket already filed under %s', label, message.dedup_key)
        return Filing('suppressed')
    try:
        found = provider.find(handle)
        if found is not None:
            link_ticket(session_factory, provider, found, target, 'learned', label)
            log_id = ledger.record(message, status='sent', transport=provider.name,
                                   detail=f'found {found.key}')
            logger.info('%s: ticket %s already existed', label, found.key)
            return Filing('found', ref=found, log_id=log_id)
        rendered = render()
        log_id = ledger.record(message, status='queued', transport=provider.name,
                               rendered=rendered)
    except TicketSourceUnavailable as exc:
        return Filing('unavailable', error=exc)
    except Exception as exc:
        logger.exception('%s: %s filing could not start', label, provider.name)
        return Filing('error', error=exc)

    ticket = draft(rendered)
    try:
        ref = provider.create(ticket)
    except Exception as exc:        # TicketSourceUnavailable, or a provider bug
        ref = filed_anyway(provider, handle, exc)
        if ref is None:
            ledger.resolve(log_id, status='failed', detail=f'{type(exc).__name__}: {exc}')
            return Filing('unavailable', log_id=log_id, error=exc)
        logger.warning('%s: create failed (%s) but %s carries %s',
                       label, exc, ref.key, handle)
    ledger.resolve(log_id, status='sent', detail=ref.key)
    link_ticket(session_factory, provider, ref, target, 'created', label)
    logger.info('%s: filed %s through %s', label, ref.key, provider.name)
    return Filing('created', ref=ref, log_id=log_id)


def filed_anyway(provider: TicketProvider, handle: str, exc: Exception
                 ) -> Optional[TicketRef]:
    """A create that timed out may have filed: the desk commits, then the 5 s
    budget expires. Filing again would duplicate it, so ask once. A 4xx
    (``TicketRejected``) or a provider bug means nothing was filed."""
    if not isinstance(exc, TicketSourceUnavailable) or isinstance(exc, TicketRejected):
        return None
    try:
        return provider.find(handle)
    except Exception:
        return None


def link_ticket(session_factory: Callable, provider: TicketProvider, ref: TicketRef,
                target: LinkTarget, origin: str, label: str) -> None:
    """Its own short session: a filed ticket must survive any later rollback.
    A failure is logged, never raised."""
    try:
        with session_factory() as session:
            ExternalTicket.create(session, provider=provider.name, ticket_key=ref.key,
                                  entity_type=target.entity_type,
                                  entity_id=target.entity_id, origin=origin,
                                  requested_by=target.requested_by,
                                  status=ref.status, closed=ref.closed,
                                  synced_at=datetime.now())
            session.commit()
    except Exception:
        logger.exception('%s: could not link %s', label, ref.key)
