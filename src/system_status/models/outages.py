#-------------------------------------------------------------------------bh-
# System Outages and Reservations Models
#-------------------------------------------------------------------------eh-

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, Enum as SQLEnum
from ..base import StatusBase, SessionMixin


class SystemOutage(StatusBase, SessionMixin):
    """
    Known outages and degradations.
    Tracks system issues with severity, status, and resolution information.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'system_outages'

    __table_args__ = (
        Index('ix_outage_system_name', 'system_name'),
        Index('ix_outage_start_time', 'start_time'),
        Index('ix_outage_status', 'status'),
    )

    outage_id = Column(Integer, primary_key=True, autoincrement=True)

    # System Information
    system_name = Column(String(64), nullable=False, index=True)
    component = Column(String(128), nullable=True)  # e.g., "login nodes", "queue", "filesystem"

    # Severity
    severity = Column(
        SQLEnum('critical', 'major', 'minor', 'maintenance', name='outage_severity'),
        nullable=False,
        default='minor'
    )

    # Status
    status = Column(
        SQLEnum('investigating', 'identified', 'monitoring', 'resolved', name='outage_status'),
        nullable=False,
        default='investigating'
    )

    # Details
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Timestamps
    start_time = Column(DateTime, nullable=False, default=datetime.now)
    end_time = Column(DateTime, nullable=True)
    estimated_resolution = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.now)

    def __str__(self):
        return f"{self.system_name}: {self.title} ({self.severity}/{self.status})"

    def __repr__(self):
        return f"<SystemOutage(id={self.outage_id}, system='{self.system_name}', severity='{self.severity}', status='{self.status}')>"


class ResourceReservation(StatusBase, SessionMixin):
    """
    Scheduled reservations for maintenance, special allocations, etc.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'resource_reservations'

    __table_args__ = (
        Index('ix_reservation_system_name', 'system_name'),
        Index('ix_reservation_start_time', 'start_time'),
    )

    reservation_id = Column(Integer, primary_key=True, autoincrement=True)

    # System Information
    system_name = Column(String(64), nullable=False, index=True)
    reservation_name = Column(String(128), nullable=False)

    # Details
    description = Column(Text, nullable=True)

    # Timestamps
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)

    # Resource Details
    node_count = Column(Integer, nullable=True)
    partition = Column(String(64), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.now)

    def __str__(self):
        return f"{self.system_name}: {self.reservation_name} ({self.start_time} - {self.end_time})"

    def __repr__(self):
        return f"<ResourceReservation(id={self.reservation_id}, system='{self.system_name}', name='{self.reservation_name}')>"
