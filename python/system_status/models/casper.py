#-------------------------------------------------------------------------bh-
# Casper Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Index, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin
from .system import SystemStatusMixin


class CasperStatus(StatusBase, StatusSnapshotMixin, SessionMixin, SystemStatusMixin):
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

    # Common metrics inherited from SystemStatusMixin

    # Compute Nodes - VIZ Partition (Casper-specific)
    viz_nodes_total = Column(Integer, nullable=False)
    viz_nodes_available = Column(Integer, nullable=False)
    viz_nodes_down = Column(Integer, nullable=False, default=0)
    viz_nodes_reserved = Column(Integer, nullable=False, default=0)

    # VIZ Utilization (Casper-specific)
    viz_count_total = Column(Integer, nullable=False)
    viz_count_allocated = Column(Integer, nullable=False)
    viz_count_idle = Column(Integer, nullable=False)
    viz_utilization_percent = Column(Float, nullable=True)

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
