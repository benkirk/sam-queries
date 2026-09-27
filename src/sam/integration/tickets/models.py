"""``ExternalTicket``: a help-desk ticket SAM filed (``created``) or found
after a mail filed it (``learned``), keyed to one SAM entity.

SQLAlchemy and ``sam.base`` only: ``sam/__init__.py`` imports this eagerly.
DDL: ``scripts/sql/create_external_ticket.sql``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property

from sam.base import Base, SessionMixin

ORIGINS = ('created', 'learned')


class ExternalTicket(Base, SessionMixin):
    """One ticket in an external tracker. No FKs, no stored URL."""

    __tablename__ = 'external_ticket'
    __table_args__ = (
        UniqueConstraint('provider', 'ticket_key', name='external_ticket_provider_key'),
        Index('external_ticket_entity', 'entity_type', 'entity_id'),
    )

    external_ticket_id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(32), nullable=False)
    ticket_key = Column(String(32), nullable=False)
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(Integer, nullable=False)
    origin = Column(String(16), nullable=False)
    requested_by = Column(String(35), nullable=False)
    status = Column(String(32))
    closed_at = Column(DateTime)
    synced_at = Column(DateTime)
    # App clock, naive-Mountain, no server_default.
    creation_time = Column(DateTime, nullable=False)

    @hybrid_property
    def is_active(self) -> bool:
        """Open as SAM last saw it. A reopen after close is not tracked."""
        return self.closed_at is None

    @is_active.expression
    def is_active(cls):
        return cls.closed_at.is_(None)

    @classmethod
    def create(cls, session, *, provider: str, ticket_key: str, entity_type: str,
               entity_id: int, origin: str, requested_by: str,
               status: Optional[str] = None, closed: Optional[bool] = None,
               synced_at: Optional[datetime] = None,
               when: Optional[datetime] = None) -> 'ExternalTicket':
        """Raises ``ValueError`` on an unknown origin or an empty identifier."""
        if origin not in ORIGINS:
            raise ValueError(f'origin must be one of {", ".join(ORIGINS)}')
        for label, value in (('provider', provider), ('ticket_key', ticket_key),
                             ('entity_type', entity_type), ('requested_by', requested_by)):
            if not (value or '').strip():
                raise ValueError(f'{label} is required')
        now = when or datetime.now()
        row = cls(provider=provider, ticket_key=ticket_key.strip(),
                  entity_type=entity_type, entity_id=entity_id, origin=origin,
                  requested_by=requested_by[:35], status=(status or None) and status[:32],
                  closed_at=now if closed else None, synced_at=synced_at,
                  creation_time=now)
        session.add(row)
        session.flush()
        return row

    def mark_synced(self, *, status: Optional[str], closed: Optional[bool],
                    when: Optional[datetime] = None) -> 'ExternalTicket':
        """Record a successful read. ``closed_at`` is stamped once, the first time."""
        now = when or datetime.now()
        if status:
            self.status = status[:32]
        if closed and self.closed_at is None:
            self.closed_at = now
        self.synced_at = now
        self.session.flush()
        return self

    def __repr__(self) -> str:
        return (f'<ExternalTicket {self.provider} {self.ticket_key} '
                f'{self.entity_type}:{self.entity_id} {self.origin}>')
