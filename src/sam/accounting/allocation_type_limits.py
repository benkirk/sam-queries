"""``allocation_type_resource_limit``: a soft default and hard bounds per allocation type per resource.

Amounts are in the resource's own unit. A pair with no active row has no limit, and the
form says so. Applies to New, Supplement and Renewal requests, never to the operator's
request editor. Rows are keyed by id, but callers look up by **name**, mapping every row
of a duplicated name ("Small" is two allocation types). No foreign keys (two dialects).
DDL and seed: ``scripts/sql/create_allocation_type_resource_limit.sql`` and
``seed_allocation_type_resource_limit.sql``, each with a ``_pg`` twin. Design:
``docs/plans/XRAS_SUBMISSION.md`` § 4.5.
"""

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from sam.base import ActiveFlagMixin, Base, SessionMixin


class AllocationTypeResourceLimit(Base, SessionMixin, ActiveFlagMixin):
    """The request bounds for one (allocation type, resource) pair."""

    @staticmethod
    def check_bounds(min_amount, default_amount, max_amount) -> None:
        """``0 <= min <= default <= max`` over the bounds that are set; ``ValueError`` otherwise."""
        present = [(n, v) for n, v in (('minimum', min_amount), ('default', default_amount),
                                       ('maximum', max_amount)) if v is not None]
        for name, value in present:
            if value < 0:
                raise ValueError(f'The {name} must not be negative.')
        for (low_name, low), (high_name, high) in zip(present, present[1:]):
            if low > high:
                raise ValueError(f'The {low_name} ({low:,g}) exceeds the {high_name} ({high:,g}).')

    __tablename__ = 'allocation_type_resource_limit'
    __table_args__ = (
        UniqueConstraint('allocation_type_id', 'resource_id',
                         name='allocation_type_resource_limit_pair'),
    )

    allocation_type_resource_limit_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_type_id = Column(Integer, nullable=False)
    resource_id = Column(Integer, nullable=False)
    min_amount = Column(Float)
    default_amount = Column(Float)
    max_amount = Column(Float)
    created_by = Column(String(35), nullable=False)
    # App clock, naive-Mountain, no server_default.
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime)

    # No foreign keys, so view-only joins for display.
    allocation_type = relationship(
        'AllocationType', viewonly=True,
        primaryjoin='foreign(AllocationTypeResourceLimit.allocation_type_id)'
                    ' == AllocationType.allocation_type_id')
    resource = relationship(
        'Resource', viewonly=True,
        primaryjoin='foreign(AllocationTypeResourceLimit.resource_id) == Resource.resource_id')

    @classmethod
    def create(cls, session, *, allocation_type_id: int, resource_id: int, created_by: str,
               min_amount: Optional[float] = None, default_amount: Optional[float] = None,
               max_amount: Optional[float] = None) -> 'AllocationTypeResourceLimit':
        """Raises ``ValueError`` when the bounds are out of order or the pair already has a row."""
        cls.check_bounds(min_amount, default_amount, max_amount)
        if session.query(cls.allocation_type_resource_limit_id).filter_by(
                allocation_type_id=allocation_type_id, resource_id=resource_id).first():
            raise ValueError('this allocation type and resource already have a limit; edit it')
        now = datetime.now()
        row = cls(allocation_type_id=allocation_type_id, resource_id=resource_id,
                  min_amount=min_amount, default_amount=default_amount,
                  max_amount=max_amount, created_by=str(created_by)[:35], active=True,
                  creation_time=now, modified_time=now)
        session.add(row)
        session.flush()
        return row

    _UNSET = object()

    def update(self, *, min_amount=_UNSET, default_amount=_UNSET, max_amount=_UNSET,
               active=None) -> 'AllocationTypeResourceLimit':
        """Change the bounds (``None`` clears one; omitted leaves it); ``ValueError`` when out of order."""
        bounds = {'min_amount': min_amount, 'default_amount': default_amount,
                  'max_amount': max_amount}
        merged = {k: getattr(self, k) if v is self._UNSET else v for k, v in bounds.items()}
        self.check_bounds(merged['min_amount'], merged['default_amount'], merged['max_amount'])
        for name, value in merged.items():
            setattr(self, name, value)
        if active is not None:
            self.active = bool(active)
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def refusal(self, amount: float) -> Optional[str]:
        """Why *amount* is out of bounds, or ``None`` when it is within them."""
        if self.max_amount is not None and amount > self.max_amount:
            return f'more than the {self.max_amount:,.0f} limit'
        if self.min_amount is not None and amount < self.min_amount:
            return f'less than the {self.min_amount:,.0f} minimum'
        return None

    def __str__(self):
        return (f'limit type {self.allocation_type_id} on resource {self.resource_id} '
                f'({self.min_amount}/{self.default_amount}/{self.max_amount})')

    def __repr__(self):
        return (f'<AllocationTypeResourceLimit(type={self.allocation_type_id}, '
                f'resource={self.resource_id}, {self.min_amount}/{self.default_amount}/'
                f'{self.max_amount}, active={self.active})>')


def _type_ids(session, allocation_type_name: str):
    from sam.accounting.allocations import AllocationType
    return [i for (i,) in session.query(AllocationType.allocation_type_id)
            .filter(AllocationType.allocation_type == allocation_type_name)]


def lookup_resource_limit(session, *, allocation_type_name: str,
                          resource_name: str) -> Optional[AllocationTypeResourceLimit]:
    """The active limit for a pair named by type and resource, or ``None`` (no limit)."""
    from sam.resources.resources import Resource
    type_ids = _type_ids(session, allocation_type_name)
    if not type_ids:
        return None
    return (session.query(AllocationTypeResourceLimit)
            .join(Resource, Resource.resource_id == AllocationTypeResourceLimit.resource_id)
            .filter(Resource.resource_name == resource_name)
            .filter(AllocationTypeResourceLimit.allocation_type_id.in_(type_ids))
            .filter(AllocationTypeResourceLimit.is_active)
            .order_by(AllocationTypeResourceLimit.allocation_type_id)
            .first())


def limits_for_allocation_type(session, allocation_type_name: str
                               ) -> Dict[str, AllocationTypeResourceLimit]:
    """``{resource_name: limit}`` for every active limit of the named type (first id wins on a duplicate)."""
    from sam.resources.resources import Resource
    type_ids = _type_ids(session, allocation_type_name)
    if not type_ids:
        return {}
    rows = (session.query(Resource.resource_name, AllocationTypeResourceLimit)
            .join(AllocationTypeResourceLimit,
                  Resource.resource_id == AllocationTypeResourceLimit.resource_id)
            .filter(AllocationTypeResourceLimit.allocation_type_id.in_(type_ids))
            .filter(AllocationTypeResourceLimit.is_active)
            .order_by(AllocationTypeResourceLimit.allocation_type_id))
    found: Dict[str, AllocationTypeResourceLimit] = {}
    for name, limit in rows:
        found.setdefault(name, limit)
    return found
