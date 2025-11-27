#-------------------------------------------------------------------------bh-
# Derecho Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin


class DerechoStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    System-level Derecho metrics (5-minute intervals).
    Captures overall system health, compute resources, and utilization.
    """
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


class DerechoQueueStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-queue metrics for Derecho (5-minute intervals).
    Tracks jobs, users, and resource allocations by queue.
    """
    __tablename__ = 'derecho_queue_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'queue_name', name='uq_derecho_queue_timestamp_name'),
    )

    queue_status_id = Column(Integer, primary_key=True, autoincrement=True)
    queue_name = Column(String(64), nullable=False, index=True)

    # Queue Metrics
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    held_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

    # Resource Allocations
    cores_allocated = Column(Integer, nullable=False, default=0)
    gpus_allocated = Column(Integer, nullable=False, default=0)
    nodes_allocated = Column(Integer, nullable=False, default=0)


# NOTE: Filesystem tracking moved to common FilesystemStatus model
# See system_status/models/filesystems.py
