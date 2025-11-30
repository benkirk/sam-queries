#-------------------------------------------------------------------------bh-
#-------------------------------------------------------------------------eh-

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, Boolean, Numeric,
    ForeignKey, Index, Text, BigInteger, TIMESTAMP, text, and_, or_
)
from sqlalchemy.orm import relationship, declarative_base, declared_attr, Session
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func
import os


#-------------------------------------------------------------------------bm-
def _get_status_base_class():
    """
    Get appropriate base class for system_status models.

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

StatusBase = _get_status_base_class()

# ============================================================================
# Mixins - Common patterns for system status tracking
# ============================================================================

class StatusTimestampMixin:
    """Provides timestamp field for status snapshots."""

    @declared_attr
    def timestamp(cls):
        return Column(DateTime, nullable=False, index=True)

    @declared_attr
    def created_at(cls):
        return Column(DateTime, nullable=False, default=datetime.now,
                     server_default=text('CURRENT_TIMESTAMP'))


class StatusSnapshotMixin(StatusTimestampMixin):
    """
    Mixin for time-series status tables.
    Provides timestamp indexing and snapshot identification.
    """
    pass


class AvailabilityMixin:
    """Provides availability status tracking."""

    @declared_attr
    def available(cls):
        return Column(Boolean, nullable=False, default=True)

    @declared_attr
    def degraded(cls):
        return Column(Boolean, nullable=False, default=False)

    @property
    def is_available(self) -> bool:
        """Check if resource is fully available."""
        return bool(self.available and not self.degraded)

    @property
    def status_name(self) -> str:
        """Get human-readable status name."""
        if not self.available:
            return "offline"
        elif self.degraded:
            return "degraded"
        else:
            return "online"


class SessionMixin:
    """Provides access to SQLAlchemy session."""

    @property
    def session(self) -> Session:
        s = Session.object_session(self)
        return s
#-------------------------------------------------------------------------em-
