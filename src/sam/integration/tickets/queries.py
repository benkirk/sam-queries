"""Reads over ``external_ticket`` and the ledger: the card's lookup and the
hourly learn/refresh selections. SQLAlchemy only."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Iterable, List

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from sam.integration.tickets.models import ExternalTicket
from sam.notify.models import NotificationLog

ACCOUNT_REQUEST = 'account_request'
TICKET_KIND = 'account_ticket'


def tickets_for(session: Session, entity_type: str,
                ids: Iterable[int]) -> Dict[int, List[ExternalTicket]]:
    """``{entity_id: [ticket, ...]}``, oldest first; one query."""
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    rows = (session.query(ExternalTicket)
            .filter(ExternalTicket.entity_type == entity_type,
                    ExternalTicket.entity_id.in_(wanted))
            .order_by(ExternalTicket.creation_time, ExternalTicket.external_ticket_id)
            .all())
    out: Dict[int, List[ExternalTicket]] = defaultdict(list)
    for row in rows:
        out[row.entity_id].append(row)
    return dict(out)


def learn_candidates(session: Session, *, now: datetime, max_age_days: int,
                     limit: int) -> List[int]:
    """Account-request ids whose ticket mail was ``sent`` within the window and
    that have no ``external_ticket`` row yet. Newest first, so a mail the desk
    never ingested sinks instead of starving new ones."""
    linked = exists().where(ExternalTicket.entity_type == ACCOUNT_REQUEST,
                            ExternalTicket.entity_id == NotificationLog.entity_id)
    newest = func.max(NotificationLog.creation_time)
    stmt = (select(NotificationLog.entity_id)
            .where(NotificationLog.kind == TICKET_KIND,
                   NotificationLog.status == 'sent',
                   NotificationLog.entity_type == ACCOUNT_REQUEST,
                   NotificationLog.entity_id.is_not(None),
                   NotificationLog.creation_time >= now - timedelta(days=max_age_days),
                   ~linked)
            .group_by(NotificationLog.entity_id)
            .order_by(newest.desc(), NotificationLog.entity_id.desc())
            .limit(limit))
    return [row[0] for row in session.execute(stmt)]


def refresh_candidates(session: Session, *, provider: str, now: datetime,
                       stale_after: timedelta, limit: int) -> List[ExternalTicket]:
    """Open tickets not read within ``stale_after``, least recently read first."""
    cutoff = now - stale_after
    return (session.query(ExternalTicket)
            .filter(ExternalTicket.provider == provider,
                    ExternalTicket.is_active,
                    or_(ExternalTicket.synced_at.is_(None),
                        ExternalTicket.synced_at < cutoff))
            .order_by(ExternalTicket.synced_at.is_not(None),
                      ExternalTicket.synced_at, ExternalTicket.external_ticket_id)
            .limit(limit).all())
