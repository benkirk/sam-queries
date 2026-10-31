"""``allocation_type_guide``: what a request page says about each allocation type.

One row per XRAS allocation type (``allocationTypeInfo.allocationTypeId``). The prose
(summary, funding eligibility, review cadence, notes) is SAM's own, modeled on ARC's
opportunities table; the limits shown beside it come from ``allocation_type_resource_limit``
through ``allocation_type``, a SAM type **name** resolved at read time. ``audience``
``'ncar'`` keeps a type off the public page. No foreign keys (two dialects). DDL and seed:
``scripts/sql/create_allocation_type_guide.sql`` and ``seed_allocation_type_guide.sql``,
each with a ``_pg`` twin. Design: ``docs/plans/XRAS_SUBMISSION_SPRINT2.md``.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from sam.base import ActiveFlagMixin, Base, SessionMixin

AUDIENCES = ('university', 'ncar')


class AllocationTypeGuide(Base, SessionMixin, ActiveFlagMixin):
    """The request page's card for one XRAS allocation type."""

    __tablename__ = 'allocation_type_guide'
    __table_args__ = (
        UniqueConstraint('xras_allocation_type_id', name='allocation_type_guide_xras_type'),
    )

    allocation_type_guide_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_allocation_type_id = Column(Integer, nullable=False)
    allocation_type = Column(String(20))
    title = Column(String(64), nullable=False)
    summary = Column(Text)
    funding = Column(String(128))
    cadence = Column(String(64))
    notes = Column(Text)
    audience = Column(String(16))
    display_order = Column(Integer, nullable=False, default=0)
    created_by = Column(String(35), nullable=False)
    # App clock, naive-Mountain, no server_default.
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime)

    _TEXT = ('allocation_type', 'title', 'summary', 'funding', 'cadence', 'notes', 'audience')

    @staticmethod
    def _check(title, audience) -> None:
        if title is not None and not title.strip():
            raise ValueError('A title is required.')
        if audience is not None and audience not in AUDIENCES:
            raise ValueError(f'The audience must be one of {", ".join(AUDIENCES)}, or empty.')

    @staticmethod
    def _clean(value):
        return value.strip() or None if isinstance(value, str) else value

    @classmethod
    def create(cls, session, *, xras_allocation_type_id: int, title: str, created_by: str,
               allocation_type: Optional[str] = None, summary: Optional[str] = None,
               funding: Optional[str] = None, cadence: Optional[str] = None,
               notes: Optional[str] = None, audience: Optional[str] = None,
               display_order: int = 0) -> 'AllocationTypeGuide':
        """Raises ``ValueError`` on a blank title, an unknown audience or a type already guided."""
        audience = cls._clean(audience)
        cls._check(title, audience)
        if session.query(cls.allocation_type_guide_id).filter_by(
                xras_allocation_type_id=xras_allocation_type_id).first():
            raise ValueError('this XRAS allocation type already has a guide row; edit it')
        now = datetime.now()
        row = cls(xras_allocation_type_id=xras_allocation_type_id, title=title.strip(),
                  allocation_type=cls._clean(allocation_type), summary=cls._clean(summary),
                  funding=cls._clean(funding), cadence=cls._clean(cadence),
                  notes=cls._clean(notes), audience=audience, display_order=display_order,
                  created_by=str(created_by)[:35], active=True,
                  creation_time=now, modified_time=now)
        session.add(row)
        session.flush()
        return row

    def update(self, *, display_order=None, active=None, **text) -> 'AllocationTypeGuide':
        """Change the given text fields (blank clears one; omitted leaves it), the order or the flag."""
        unknown = set(text) - set(self._TEXT)
        if unknown:
            raise TypeError(f'unknown fields: {", ".join(sorted(unknown))}')
        text = {k: self._clean(v) for k, v in text.items()}
        self._check(text.get('title', self.title) or '', text.get('audience', self.audience))
        for name, value in text.items():
            setattr(self, name, value)
        if display_order is not None:
            self.display_order = int(display_order)
        if active is not None:
            self.active = bool(active)
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def __str__(self):
        return f'{self.title} (XRAS type {self.xras_allocation_type_id})'

    def __repr__(self):
        return (f'<AllocationTypeGuide(xras_type={self.xras_allocation_type_id}, '
                f'title={self.title!r}, audience={self.audience}, active={self.active})>')


def public_guides(session) -> List[AllocationTypeGuide]:
    """The active rows a visitor may see (every audience but ``ncar``), in display order."""
    return (session.query(AllocationTypeGuide)
            .filter(AllocationTypeGuide.is_active)
            .filter((AllocationTypeGuide.audience.is_(None))
                    | (AllocationTypeGuide.audience != 'ncar'))
            .order_by(AllocationTypeGuide.display_order, AllocationTypeGuide.title)
            .all())
