#-------------------------------------------------------------------------bh-
# JupyterHub Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, Float, Boolean, Index
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class JupyterHubStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    JupyterHub metrics (5-minute intervals).
    Placeholder implementation for Phase 1, full implementation in Phase 2+.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'jupyterhub_status'

    __table_args__ = (
        Index('ix_jupyterhub_status_created_at', 'created_at'),
    )

    status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Basic Metrics
    available = Column(Boolean, nullable=False, default=True)
    active_users = Column(Integer, nullable=False, default=0)
    active_sessions = Column(Integer, nullable=False, default=0)

    # Utilization
    cpu_utilization_percent = Column(Float, nullable=True)
    memory_utilization_percent = Column(Float, nullable=True)
