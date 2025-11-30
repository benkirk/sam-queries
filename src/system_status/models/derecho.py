#-------------------------------------------------------------------------bh-
# Derecho Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, Index
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin
from .system import SystemStatusMixin


class DerechoStatus(StatusBase, StatusSnapshotMixin, SessionMixin, SystemStatusMixin):
    """
    System-level Derecho metrics (5-minute intervals).
    Captures overall system health, compute resources, and utilization.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'derecho_status'

    __table_args__ = (
        Index('ix_derecho_status_created_at', 'created_at'),
    )

    status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Common metrics are inherited from SystemStatusMixin

    # Relationships (children linked via foreign keys, eager loaded)
    login_nodes = relationship('LoginNodeStatus',
                               foreign_keys='LoginNodeStatus.derecho_status_id',
                               back_populates='derecho_status',
                               cascade='all, delete-orphan',
                               lazy='selectin')

    queues = relationship('QueueStatus',
                          foreign_keys='QueueStatus.derecho_status_id',
                          back_populates='derecho_status',
                          cascade='all, delete-orphan',
                          lazy='selectin')

    filesystems = relationship('FilesystemStatus',
                               foreign_keys='FilesystemStatus.derecho_status_id',
                               back_populates='derecho_status',
                               cascade='all, delete-orphan',
                               lazy='selectin')
