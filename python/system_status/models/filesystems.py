#-------------------------------------------------------------------------bh-
# Filesystem Status Models (Common to all systems)
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, UniqueConstraint
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin


class FilesystemStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """
    Filesystem health monitoring (5-minute intervals).
    Tracks glade, campaign, scratch filesystems across all HPC systems.
    Used by Derecho, Casper, and other systems.
    """
    __tablename__ = 'filesystem_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'filesystem_name', name='uq_fs_timestamp_name'),
    )

    fs_status_id = Column(Integer, primary_key=True, autoincrement=True)
    filesystem_name = Column(String(64), nullable=False, index=True)
    system_name = Column(String(32), nullable=True, index=True, comment='System using this filesystem (derecho, casper, etc.)')

    # Status inherited from AvailabilityMixin: available, degraded

    # Capacity
    capacity_tb = Column(Float, nullable=True)
    used_tb = Column(Float, nullable=True)
    utilization_percent = Column(Float, nullable=True)
