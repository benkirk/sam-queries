#-------------------------------------------------------------------------bh-
#-------------------------------------------------------------------------eh-

from datetime import datetime
from typing import List, Optional, Dict, Set
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, Boolean, Numeric,
    ForeignKey, ForeignKeyConstraint, PrimaryKeyConstraint,
    Text, BigInteger, TIMESTAMP, text, and_, or_, Index, exists, select
)
from sqlalchemy.orm import relationship, declarative_base, declared_attr, Session
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func


#-------------------------------------------------------------------------bm-
Base = declarative_base()

# ============================================================================
# Mixins - Common patterns extracted
# ============================================================================
class TimestampMixin:
    """Provides creation and modification timestamps."""

    @declared_attr
    def creation_time(cls):
        return Column(DateTime, nullable=False, default=datetime.now, server_default=text('CURRENT_TIMESTAMP'))

    @declared_attr
    def modified_time(cls):
        return Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))


class SoftDeleteMixin:
    """Provides soft delete capability."""

    @declared_attr
    def deleted(cls):
        return Column(Boolean, nullable=False, default=False)

    @declared_attr
    def deletion_time(cls):
        return Column(TIMESTAMP)

    @property
    def is_deleted(self) -> bool:
        """Check if this record is soft-deleted."""
        return bool(self.deleted)


class ActiveFlagMixin:
    """Provides active status flag."""

    @declared_attr
    def active(cls):
        return Column(Boolean, nullable=False, default=True)

    @property
    def is_active(self) -> bool:
        """Check if this record is active."""
        return bool(self.active)


class DateRangeMixin:
    """Provides start_date and end_date for temporal relationships."""

    @declared_attr
    def start_date(cls):
        return Column(DateTime, nullable=False)

    @declared_attr
    def end_date(cls):
        return Column(DateTime)

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if this record is active at a given date."""
        if check_date is None:
            check_date = datetime.now()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

    @hybrid_property
    def is_currently_active(self) -> bool:
        """Check if this record is currently active (Python side)."""
        return self.is_active_at()

    @is_currently_active.expression
    def is_currently_active(cls):
        """Check if this record is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )

class SessionMixin:
    @property
    def session(self) -> Session:
        s = Session.object_session(self)
        #if not s:
        #    return []
        return s
#-------------------------------------------------------------------------em-
