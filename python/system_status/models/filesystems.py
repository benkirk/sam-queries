#-------------------------------------------------------------------------bh-
# Filesystem Status Models (Common to all systems)
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin


class FilesystemStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """
    Filesystem health monitoring (5-minute intervals).
    Tracks glade, campaign, scratch filesystems across all HPC systems.
    Used by Derecho, Casper, and other systems.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'filesystem_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'filesystem_name', name='uq_fs_timestamp_name'),
    )

    fs_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other)
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    filesystem_name = Column(String(32), nullable=False, index=True)
    system_name = Column(String(32), nullable=False, index=True, comment='System using this filesystem (derecho, casper, etc.)')

    # Status inherited from AvailabilityMixin: available, degraded

    # Capacity
    capacity_tb = Column(Float, nullable=True)
    used_tb = Column(Float, nullable=True)
    utilization_percent = Column(Float, nullable=True)

    # Relationships (back_populates to parent status records)
    derecho_status = relationship('DerechoStatus', back_populates='filesystems',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='filesystems',
                                foreign_keys=[casper_status_id])
