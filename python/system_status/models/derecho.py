#-------------------------------------------------------------------------bh-
# Derecho Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin


class DerechoStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
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

    # NOTE: Login node metrics moved to derecho_login_node_status table

    # Compute Nodes - CPU Partition
    cpu_nodes_total = Column(Integer, nullable=False)
    cpu_nodes_available = Column(Integer, nullable=False)
    cpu_nodes_down = Column(Integer, nullable=False, default=0)
    cpu_nodes_reserved = Column(Integer, nullable=False, default=0)

    # Compute Nodes - GPU Partition
    gpu_nodes_total = Column(Integer, nullable=False)
    gpu_nodes_available = Column(Integer, nullable=False)
    gpu_nodes_down = Column(Integer, nullable=False, default=0)
    gpu_nodes_reserved = Column(Integer, nullable=False, default=0)

    # CPU Utilization
    cpu_cores_total = Column(Integer, nullable=False)
    cpu_cores_allocated = Column(Integer, nullable=False)
    cpu_cores_idle = Column(Integer, nullable=False)
    cpu_utilization_percent = Column(Float, nullable=True)

    # GPU Utilization
    gpu_count_total = Column(Integer, nullable=False)
    gpu_count_allocated = Column(Integer, nullable=False)
    gpu_count_idle = Column(Integer, nullable=False)
    gpu_utilization_percent = Column(Float, nullable=True)

    # Memory Utilization
    memory_total_gb = Column(Float, nullable=False)
    memory_allocated_gb = Column(Float, nullable=False)
    memory_utilization_percent = Column(Float, nullable=True)

    # Jobs
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    held_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

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
