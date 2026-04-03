#-------------------------------------------------------------------------bh-
#-------------------------------------------------------------------------eh-

from datetime import datetime, date as _date, time as _time
from typing import List, Optional, Dict, Set
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, Boolean, Numeric,
    ForeignKey, ForeignKeyConstraint, PrimaryKeyConstraint,
    Text, BigInteger, TIMESTAMP, text, and_, or_, Index, exists, select
)
from sqlalchemy.orm import relationship, declarative_base, declared_attr, Session, validates
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func
import os


#-------------------------------------------------------------------------bm-
def _get_base_class():
    """
    Get appropriate base class for SAM models.

    Returns:
        - Flask-SQLAlchemy's db.Model if FLASK_ACTIVE=1 (Flask context)
        - SQLAlchemy's declarative_base() otherwise (CLI/standalone)
    """
    if os.environ.get('FLASK_ACTIVE') == '1':
        try:
            from webapp.extensions import db
            return db.Model
        except ImportError:
            # Fallback if webapp not available
            from sqlalchemy.orm import declarative_base
            return declarative_base()
    else:
        # CLI/standalone context
        from sqlalchemy.orm import declarative_base
        return declarative_base()

Base = _get_base_class()

# ============================================================================
# Custom Column Types
# ============================================================================
def normalize_end_date(value):
    """Normalize an end-of-period date value to end-of-day (23:59:59).

    Enforces the SAM convention that end dates are stored at 23:59:59, not
    midnight. Called by @validates decorators on ORM models whose end-date
    columns use the end-of-day convention.

    Converts:
      - pure ``datetime.date`` object  →  datetime at 23:59:59
      - ``datetime`` at exactly midnight  →  same date at 23:59:59
      - ``datetime`` with non-zero time  →  unchanged (trust the caller)
      - None  →  None (open-ended / no expiry)
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.replace(hour=23, minute=59, second=59)
        return value
    if isinstance(value, _date):
        # pure date.date (not datetime subclass)
        return datetime.combine(value, _time(23, 59, 59))
    return value


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

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if this record is active (not deleted) at a given date (date-insensitive)."""
        return not bool(self.deleted)

    @hybrid_property
    def is_active(self) -> bool:
        """Check if this record is active (not deleted) (Python side)."""
        return not bool(self.deleted)

    @is_active.expression
    def is_active(cls):
        """Check if this record is active (not deleted) (SQL side)."""
        return cls.deleted == False


class ActiveFlagMixin:
    """Provides active status flag."""

    @declared_attr
    def active(cls):
        return Column(Boolean, nullable=False, default=True)

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if this record is active at a given date (date-insensitive; ignores check_date)."""
        return bool(self.active)

    @hybrid_property
    def is_active(self) -> bool:
        """Check if this record is active (Python side)."""
        return bool(self.active)

    @is_active.expression
    def is_active(cls):
        """Check if this record is active (SQL side)."""
        return cls.active == True


class DateRangeMixin:
    """Provides start_date and end_date for temporal relationships."""

    @declared_attr
    def start_date(cls):
        return Column(DateTime, nullable=False)

    @declared_attr
    def end_date(cls):
        return Column(DateTime)

    @validates('end_date')
    def _validate_end_date(self, key, value):
        return normalize_end_date(value)

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

    @hybrid_property
    def is_active(self) -> bool:
        """Check if this record is currently active (Python side). Alias for is_currently_active."""
        return self.is_currently_active

    @is_active.expression
    def is_active(cls):
        """Check if this record is currently active (SQL side). Alias for is_currently_active."""
        return cls.is_currently_active

class SessionMixin:
    @property
    def session(self) -> Session:
        s = Session.object_session(self)
        #if not s:
        #    return []
        return s


class NestedSetMixin:
    """
    Mixin for models implementing the nested set model for tree hierarchies.

    Subclasses must set:
        _ns_pk_col     - primary key attribute name (e.g., 'project_id')
        _ns_parent_col - parent FK attribute name (e.g., 'parent_id')

    Subclasses may set:
        _ns_root_col   - tree_root FK attribute name, or None (default)
        _ns_path_attr  - attribute used in get_path() display (default: 'name')
    """
    _ns_pk_col = 'id'
    _ns_parent_col = 'parent_id'
    _ns_root_col = None       # None → no tree_root scoping
    _ns_path_attr = 'name'

    def _ns_apply_root_scope(self, query):
        """Optionally scope query to tree_root."""
        if self._ns_root_col:
            cls = type(self)
            root_val = getattr(self, self._ns_root_col, None)
            root_col = getattr(cls, self._ns_root_col)
            if root_val:
                query = query.filter(root_col == root_val)
        return query

    def get_ancestors(self, include_self: bool = False) -> list:
        """Get ancestor nodes ordered root → immediate parent."""
        cls = type(self)
        if not self.tree_left or not self.tree_right:
            return []
        query = self.session.query(cls).filter(
            cls.tree_left < self.tree_left,
            cls.tree_right > self.tree_right
        )
        query = self._ns_apply_root_scope(query)
        ancestors = query.order_by(cls.tree_left).all()
        if include_self:
            ancestors.append(self)
        return ancestors

    def get_descendants(self, include_self: bool = False,
                        max_depth: Optional[int] = None) -> list:
        """Get all descendant nodes (depth-first order)."""
        cls = type(self)
        if not self.tree_left or not self.tree_right:
            return []
        query = self.session.query(cls).filter(
            cls.tree_left > self.tree_left,
            cls.tree_right < self.tree_right
        )
        query = self._ns_apply_root_scope(query)
        descendants = query.order_by(cls.tree_left).all()
        if max_depth is not None:
            my_depth = self.get_depth()
            descendants = [d for d in descendants
                           if d.get_depth() - my_depth <= max_depth]
        return ([self] + descendants) if include_self else descendants

    def get_children(self) -> list:
        """Get immediate children via parent FK."""
        cls = type(self)
        my_pk = getattr(self, self._ns_pk_col)
        parent_col = getattr(cls, self._ns_parent_col)
        return self.session.query(cls).filter(parent_col == my_pk).all()

    def get_siblings(self, include_self: bool = False) -> list:
        """Get nodes sharing the same parent."""
        cls = type(self)
        my_parent = getattr(self, self._ns_parent_col)
        if my_parent is None:
            return []
        parent_col = getattr(cls, self._ns_parent_col)
        query = self.session.query(cls).filter(parent_col == my_parent)
        if not include_self:
            pk_col = getattr(cls, self._ns_pk_col)
            my_pk = getattr(self, self._ns_pk_col)
            query = query.filter(pk_col != my_pk)
        return query.all()

    def get_root(self):
        """Get the root node of this tree."""
        ancestors = self.get_ancestors()
        return ancestors[0] if ancestors else self

    def get_depth(self) -> int:
        """Depth in tree (0 for root nodes)."""
        if not self.tree_left or not self.tree_right:
            return 0
        return len(self.get_ancestors())

    def get_level(self) -> int:
        """Alias for get_depth()."""
        return self.get_depth()

    def is_root(self) -> bool:
        """True if node has no parent."""
        return getattr(self, self._ns_parent_col) is None

    def is_leaf(self) -> bool:
        """True if node has no children (right == left + 1)."""
        if not self.tree_left or not self.tree_right:
            return True
        return self.tree_right == self.tree_left + 1

    def is_ancestor_of(self, other) -> bool:
        """True if self contains other in its subtree."""
        if not all([self.tree_left, self.tree_right,
                    other.tree_left, other.tree_right]):
            return False
        return (self.tree_left < other.tree_left and
                self.tree_right > other.tree_right)

    def is_descendant_of(self, other) -> bool:
        """True if other contains self in its subtree."""
        return other.is_ancestor_of(self)

    def get_subtree_size(self) -> int:
        """Number of descendants (not including self)."""
        if not self.tree_left or not self.tree_right:
            return 0
        return (self.tree_right - self.tree_left - 1) // 2

    def get_path(self, separator: str = ' > ') -> str:
        """Full path from root to self using _ns_path_attr."""
        nodes = self.get_ancestors(include_self=True)
        return separator.join(getattr(n, self._ns_path_attr) for n in nodes)

    @hybrid_property
    def has_children(self) -> bool:
        """True if node has children (Python side)."""
        return not self.is_leaf()

    @has_children.expression
    def has_children(cls):
        """True if node has children (SQL side)."""
        return cls.tree_right > cls.tree_left + 1
#-------------------------------------------------------------------------em-
