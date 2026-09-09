"""The allocation/usage read-model row (``account_allocation_state``).

One row per allocation the dashboards would show, rebuilt hourly by the
``refresh_allocation_state`` task from the same batched computation the
live paths run. Readers consult it through ``sam.queries.allocation_state``
and fall back to the live computation when the freshness gate says no.
Design and rollout: ``docs/plans/READ_MODEL.md``.
"""
from datetime import datetime as _datetime
from typing import Dict, Iterable, Optional

from sqlalchemy import JSON

from ..base import *


class AccountAllocationState(Base, SessionMixin):
    """A projected allocation with its rolled-up usage as of ``refreshed_at``."""
    __tablename__ = 'account_allocation_state'

    __table_args__ = (
        Index('account_allocation_state_account', 'account_id'),
        Index('account_allocation_state_resource', 'resource_id', 'is_current'),
        Index('account_allocation_state_projcode', 'projcode'),
        Index('account_allocation_state_facility', 'facility_name', 'allocation_type'),
    )

    # No FKs on purpose: a rebuilt-hourly projection whose dangling rows are
    # harmless; the gate self-heals. Readers key on the ids, not relationships.
    allocation_id = Column(Integer, primary_key=True, autoincrement=False)
    account_id = Column(Integer, nullable=False)
    project_id = Column(Integer, nullable=False)
    projcode = Column(String(30), nullable=False)
    resource_id = Column(Integer, nullable=False)
    resource_name = Column(String(40), nullable=False)
    resource_type = Column(String(35), nullable=False)
    facility_name = Column(String(30))
    allocation_type = Column(String(20))

    parent_allocation_id = Column(Integer)
    is_inheriting = Column(Boolean, nullable=False, server_default=text('0'))
    root_projcode = Column(String(30))

    allocated = Column(Float, nullable=False)
    self_used = Column(Float, nullable=False)
    used = Column(Float, nullable=False)
    remaining = Column(Float, nullable=False)
    percent_used = Column(Float, nullable=False)
    self_percent_used = Column(Float)
    charges_by_type = Column(JSON, nullable=False)
    adjustments = Column(Float, nullable=False, server_default=text('0'))
    activity_date = Column(Date)
    rolling_windows = Column(JSON)

    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    is_current = Column(Boolean, nullable=False, server_default=text('1'))
    refreshed_at = Column(DateTime, nullable=False)

    #: Every writable column, in DDL order. ``bulk_replace`` assigns exactly these.
    VALUE_COLUMNS = (
        'account_id', 'project_id', 'projcode', 'resource_id', 'resource_name',
        'resource_type', 'facility_name', 'allocation_type',
        'parent_allocation_id', 'is_inheriting', 'root_projcode',
        'allocated', 'self_used', 'used', 'remaining', 'percent_used',
        'self_percent_used', 'charges_by_type', 'adjustments', 'activity_date',
        'rolling_windows', 'start_date', 'end_date', 'is_current', 'refreshed_at',
    )

    @classmethod
    def bulk_replace(cls, session, rows: Iterable[Dict], *,
                     refreshed_at: _datetime) -> Dict[str, int]:
        """Make the table equal to ``rows`` (dicts keyed by ``allocation_id``).

        Upserts by primary key and deletes every row absent from ``rows``.
        Flushes, never commits: the scheduler runner owns the transaction.
        """
        incoming = {int(r['allocation_id']): r for r in rows}
        existing = {obj.allocation_id: obj for obj in session.query(cls).all()}

        inserted = updated = 0
        for allocation_id, row in incoming.items():
            values = {k: row.get(k) for k in cls.VALUE_COLUMNS}
            values['refreshed_at'] = refreshed_at
            obj = existing.get(allocation_id)
            if obj is None:
                session.add(cls(allocation_id=allocation_id, **values))
                inserted += 1
            else:
                for k, v in values.items():
                    setattr(obj, k, v)
                updated += 1

        stale = [obj for aid, obj in existing.items() if aid not in incoming]
        for obj in stale:
            session.delete(obj)
        session.flush()
        return {'inserted': inserted, 'updated': updated, 'deleted': len(stale)}

    @classmethod
    def oldest_refresh(cls, session) -> Optional[_datetime]:
        """The oldest ``refreshed_at`` in the table, or None when empty."""
        return session.query(func.min(cls.refreshed_at)).scalar()

    def __repr__(self):
        return (f"<AccountAllocationState(allocation_id={self.allocation_id}, "
                f"projcode={self.projcode!r}, resource={self.resource_name!r})>")

    def __str__(self):
        return (f"{self.projcode}/{self.resource_name} "
                f"({self.used:,.1f} of {self.allocated:,.1f} used)")
