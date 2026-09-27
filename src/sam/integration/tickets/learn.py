"""Hourly ticket upkeep for account requests: learn the keys of tickets a mail
filed, then refresh the status of open ones. Reads only.

Fail-open twice, like ``xras_api/comments.py``: no provider or unconfigured is
``skipped`` before any row; the tracker failing mid-loop stops the pass, keeps
the stamps already made, and reports. Nothing here raises past a row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sam.integration.tickets.base import (TicketNotConfigured, TicketProvider,
                                          TicketSourceUnavailable)
from sam.integration.tickets.models import ExternalTicket
from sam.integration.tickets.queries import (ACCOUNT_REQUEST, learn_candidates,
                                             refresh_candidates)
from sam.integration.tickets.registry import provider_from_environment
from sam.queries.account_notices import ticket_handle

logger = logging.getLogger(__name__)

REQUESTED_BY = 'task:account_requests_reconcile'
#: A ticket mail older than this is not looked for; the desk never ingested it.
MAX_AGE_DAYS = 60
#: An open ticket is re-read at most this often.
STALE_AFTER = timedelta(hours=6)
DEFAULT_LOOKUP_MAX = 25


def learn_ticket_keys(session: Session, provider: TicketProvider, *,
                      clock: datetime, limit: int) -> Dict[str, Any]:
    """Find the ticket each recently mailed request produced; insert a ``learned`` row per hit.
    A miss writes nothing, so a mail not yet ingested is retried next hour."""
    counts: Dict[str, Any] = {'checked': 0, 'learned': 0, 'missed': 0, 'error': ''}
    for request_id in learn_candidates(session, now=clock, max_age_days=MAX_AGE_DAYS,
                                       limit=limit):
        counts['checked'] += 1
        try:
            ref = provider.find(ticket_handle(request_id))
        except TicketSourceUnavailable as exc:
            counts['error'] = str(exc)
            logger.warning('tickets: learn stopped at request %s: %s', request_id, exc)
            break
        if ref is None:
            counts['missed'] += 1
            continue
        try:
            with session.begin_nested():
                ExternalTicket.create(session, provider=provider.name, ticket_key=ref.key,
                                      entity_type=ACCOUNT_REQUEST, entity_id=request_id,
                                      origin='learned', requested_by=REQUESTED_BY,
                                      status=ref.status, closed=ref.closed,
                                      synced_at=clock, when=clock)
        except IntegrityError:
            logger.warning('tickets: %s already linked; request %s not linked',
                           ref.key, request_id)
            continue
        counts['learned'] += 1
    return counts


def refresh_ticket_status(session: Session, provider: TicketProvider, *,
                          clock: datetime, limit: int) -> Dict[str, Any]:
    """Re-read open tickets not read in ``STALE_AFTER``; stamp ``closed_at`` the first
    time the tracker reports them done."""
    counts: Dict[str, Any] = {'checked': 0, 'closed': 0, 'missing': 0, 'error': ''}
    for row in refresh_candidates(session, provider=provider.name, now=clock,
                                  stale_after=STALE_AFTER, limit=limit):
        counts['checked'] += 1
        try:
            ref = provider.get(row.ticket_key)
        except TicketSourceUnavailable as exc:
            counts['error'] = str(exc)
            logger.warning('tickets: refresh stopped at %s: %s', row.ticket_key, exc)
            break
        if ref is None:
            # Deleted or moved; stamp the read so it is not asked for every hour.
            counts['missing'] += 1
            row.mark_synced(status=None, closed=None, when=clock)
            continue
        was_open = row.closed_at is None
        row.mark_synced(status=ref.status, closed=ref.closed, when=clock)
        if was_open and row.closed_at is not None:
            counts['closed'] += 1
    return counts


def sync_tickets(session: Session, *, clock: datetime, limit: int = DEFAULT_LOOKUP_MAX,
                 provider: Optional[TicketProvider] = None) -> Dict[str, Any]:
    """Learn then refresh; ``{'skipped': True, 'reason': ...}`` when Jira is not in play."""
    if provider is None:
        try:
            provider = provider_from_environment()
        except TicketNotConfigured as exc:
            return {'skipped': True, 'reason': str(exc)}
    if provider is None:
        return {'skipped': True, 'reason': 'TICKET_PROVIDER selects mail'}
    if not provider.configured:
        return {'skipped': True, 'reason': f'{provider.name}: reads are not configured'}
    learned = learn_ticket_keys(session, provider, clock=clock, limit=limit)
    refreshed = (refresh_ticket_status(session, provider, clock=clock, limit=limit)
                 if not learned['error'] else
                 {'checked': 0, 'closed': 0, 'missing': 0, 'error': 'not attempted'})
    return {'skipped': False, 'provider': provider.name, 'limit': limit,
            'learn': learned, 'refresh': refreshed}


def describe(result: Dict[str, Any]) -> str:
    """One line for the task message."""
    if result.get('skipped'):
        return f"tickets skipped ({result['reason']})"
    learn, refresh = result['learn'], result['refresh']
    text = (f"tickets: {learn['learned']} learned of {learn['checked']}, "
            f"{refresh['closed']} closed of {refresh['checked']} refreshed")
    error = learn['error'] or (refresh['error'] if refresh['error'] != 'not attempted' else '')
    return f'{text}; {error}' if error else text
