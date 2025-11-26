#-------------------------------------------------------------------------bh-
# Casper Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class CasperStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Aggregate system metrics for Casper (5-minute intervals).
    Casper is a heterogeneous system with multiple node types.
    """
    __tablename__ = 'casper_status'

    __table_args__ = (
        Index('ix_casper_status_created_at', 'created_at'),
    )

    status_id = Column(Integer, primary_key=True, autoincrement=True)

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

    # Compute Nodes - VIZ Partition
    viz_nodes_total = Column(Integer, nullable=False)
    viz_nodes_available = Column(Integer, nullable=False)
    viz_nodes_down = Column(Integer, nullable=False, default=0)
    viz_nodes_reserved = Column(Integer, nullable=False, default=0)

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

    # VIZ Utilization
    viz_count_total = Column(Integer, nullable=False)
    viz_count_allocated = Column(Integer, nullable=False)
    viz_count_idle = Column(Integer, nullable=False)
    viz_utilization_percent = Column(Float, nullable=True)

    # Memory Utilization
    memory_total_gb = Column(Float, nullable=False)
    memory_allocated_gb = Column(Float, nullable=False)
    memory_utilization_percent = Column(Float, nullable=True)

    # Jobs (Aggregate)
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)


class CasperNodeTypeStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-node-type breakdown for Casper (5-minute intervals).
    Tracks heterogeneous node types: standard, bigmem, gpu-mi100, gpu-v100, gpu-a100.
    """
    __tablename__ = 'casper_node_type_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'node_type', name='uq_casper_nodetype_timestamp_type'),
    )

    node_type_status_id = Column(Integer, primary_key=True, autoincrement=True)
    node_type = Column(String(64), nullable=False, index=True)

    # Node Counts
    nodes_total = Column(Integer, nullable=False)
    nodes_available = Column(Integer, nullable=False)
    nodes_down = Column(Integer, nullable=False, default=0)
    nodes_allocated = Column(Integer, nullable=False, default=0)

    # Hardware Specs (varies by node type)
    cores_per_node = Column(Integer, nullable=True)
    memory_gb_per_node = Column(Integer, nullable=True)
    gpu_model = Column(String(64), nullable=True)
    gpus_per_node = Column(Integer, nullable=True)

    # Utilization
    utilization_percent = Column(Float, nullable=True)
    memory_utilization_percent = Column(Float, nullable=True)


class CasperQueueStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-queue metrics for Casper (5-minute intervals).
    Tracks casper, gpudev, htc queues.
    """
    __tablename__ = 'casper_queue_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'queue_name', name='uq_casper_queue_timestamp_name'),
    )

    queue_status_id = Column(Integer, primary_key=True, autoincrement=True)
    queue_name = Column(String(64), nullable=False, index=True)

    # Queue Metrics
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

    # Resource Allocations
    cores_allocated = Column(Integer, nullable=False, default=0)
    gpus_allocated = Column(Integer, nullable=False, default=0)
    nodes_allocated = Column(Integer, nullable=False, default=0)
