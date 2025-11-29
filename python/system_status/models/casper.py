#-------------------------------------------------------------------------bh-
# Casper Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class CasperStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Aggregate system metrics for Casper (5-minute intervals).
    Casper is a heterogeneous system with multiple node types.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
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
    held_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

    # Relationships (children linked via foreign keys, eager loaded)
    login_nodes = relationship('LoginNodeStatus',
                               foreign_keys='LoginNodeStatus.casper_status_id',
                               back_populates='casper_status',
                               cascade='all, delete-orphan',
                               lazy='selectin')

    node_types = relationship('CasperNodeTypeStatus',
                              back_populates='casper_status',
                              cascade='all, delete-orphan',
                              lazy='selectin')

    queues = relationship('QueueStatus',
                          foreign_keys='QueueStatus.casper_status_id',
                          back_populates='casper_status',
                          cascade='all, delete-orphan',
                          lazy='selectin')

    filesystems = relationship('FilesystemStatus',
                               foreign_keys='FilesystemStatus.casper_status_id',
                               back_populates='casper_status',
                               cascade='all, delete-orphan',
                               lazy='selectin')


class CasperNodeTypeStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-node-type breakdown for Casper (5-minute intervals).
    Tracks heterogeneous node types: standard, bigmem, gpu-mi100, gpu-v100, gpu-a100.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'casper_node_type_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'node_type', name='uq_casper_nodetype_timestamp_type'),
    )

    node_type_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign key to parent Casper status record (not nullable - Casper-specific)
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=False, index=True,
                              comment='FK to parent Casper status snapshot')

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

    # Relationship (back_populates to parent Casper status)
    casper_status = relationship('CasperStatus', back_populates='node_types')
