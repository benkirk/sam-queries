"""Hourly ticket upkeep for account requests: learn the keys of tickets a mail
filed, then refresh the status of open ones. Reads only.

Fail-open twice, like ``xras_api/comments.py``: no provider or unconfigured is
``skipped`` before any row; the tracker failing mid-loop stops the pass, keeps
the stamps already made, and reports. Nothing here raises past a row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sam.integration.tickets.base import TicketProvider, TicketSourceUnavailable
from sam.integration.tickets.models import ExternalTicket
from sam.integration.tickets.queries import (ACCOUNT_REQUEST, learn_candidates,
                                             refresh_candidates)
from sam.integration.tickets.registry import read_providers
from sam.queries.account_notices import ticket_handle

logger = logging.getLogger(__name__)

REQUESTED_BY = 'task:account_requests_reconcile'
#: A ticket mail older than this is not looked for; the desk never ingested it.
MAX_AGE_DAYS = 60
#: An open ticket is re-read on every hourly run. Under an hour on purpose:
#: stamps are the slot clock, exactly 60 min apart, and `<` would skip every other run.
STALE_AFTER = timedelta(minutes=50)
#: Reads per pass, each half; a pasted roster is easily 40 open tickets.
DEFAULT_LOOKUP_MAX = 50


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
                 providers: Optional[List[TicketProvider]] = None) -> Dict[str, Any]:
    """Learn then refresh through each provider with reads on (``JIRA_ENABLED``),
    whatever ``TICKET_PROVIDER`` says; ``{'skipped': True, 'reason': ...}`` when none."""
    providers = read_providers() if providers is None else [
        p for p in providers if p.configured]
    if not providers:
        return {'skipped': True, 'reason': 'no ticket provider has reads on (JIRA_ENABLED)'}
    runs: Dict[str, Any] = {}
    for provider in providers:
        learned = learn_ticket_keys(session, provider, clock=clock, limit=limit)
        refreshed = (refresh_ticket_status(session, provider, clock=clock, limit=limit)
                     if not learned['error'] else
                     {'checked': 0, 'closed': 0, 'missing': 0, 'error': 'not attempted'})
        runs[provider.name] = {'learn': learned, 'refresh': refreshed}
    return {'skipped': False, 'limit': limit, 'providers': runs}


def describe(result: Dict[str, Any]) -> str:
    """One line for the task message."""
    if result.get('skipped'):
        return f"tickets skipped ({result['reason']})"
    parts = []
    for name, run in result['providers'].items():
        learn, refresh = run['learn'], run['refresh']
        text = (f"{name}: {learn['learned']} learned of {learn['checked']}, "
                f"{refresh['closed']} closed of {refresh['checked']} refreshed")
        error = learn['error'] or (refresh['error'] if refresh['error'] != 'not attempted' else '')
        parts.append(f'{text} ({error})' if error else text)
    return 'tickets ' + '; '.join(parts)
