#-------------------------------------------------------------------------bh-
# Filesystem Status Models (Common to all systems)
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, Float, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin
from .lookups import System, Filesystem as _LookupFilesystem


class FilesystemStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """
    Filesystem health monitoring (5-minute intervals).

    Phase 2 (PR-A): legacy text columns ``filesystem_name`` / ``system_name``
    are replaced by FK columns ``filesystem_id`` / ``system_id`` against
    the ``filesystems`` and ``systems`` lookup tables. Property accessors
    preserve the legacy attribute interface.
    """
    __bind_key__ = "system_status"
    __tablename__ = 'filesystem_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'filesystem_id', name='uq_filesystem_status_timestamp_filesystem_id'),
    )

    fs_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other)
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    # Lookup FKs (Phase 2)
    system_id = Column(Integer, ForeignKey('systems.system_id'),
                       nullable=False, index=True)
    filesystem_id = Column(Integer, ForeignKey('filesystems.filesystem_id'),
                           nullable=False, index=True)

    # Status inherited from AvailabilityMixin: available, degraded

    # Capacity
    capacity_tb = Column(Float, nullable=True)
    used_tb = Column(Float, nullable=True)
    utilization_percent = Column(Float, nullable=True)

    # Inode Capacity
    capacity_inodes = Column(Float, nullable=True)
    used_inodes = Column(Float, nullable=True)
    inodes_utilization_percent = Column(Float, nullable=True)

    # Relationships
    derecho_status = relationship('DerechoStatus', back_populates='filesystems',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='filesystems',
                                foreign_keys=[casper_status_id])
    system = relationship(System, foreign_keys=[system_id])
    filesystem = relationship(_LookupFilesystem, foreign_keys=[filesystem_id])

    # ------------------------------------------------------------------
    # Backward-compat property accessors
    # ------------------------------------------------------------------
    @property
    def system_name(self):
        pending = self.__dict__.get('_pending_system_name')
        if pending is not None:
            return pending
        return self.system.name if self.system is not None else None

    @system_name.setter
    def system_name(self, value):
        self.__dict__['_pending_system_name'] = value

    @property
    def filesystem_name(self):
        pending = self.__dict__.get('_pending_filesystem_name')
        if pending is not None:
            return pending
        return self.filesystem.name if self.filesystem is not None else None

    @filesystem_name.setter
    def filesystem_name(self, value):
        self.__dict__['_pending_filesystem_name'] = value

    def __str__(self):
        return f"{self.filesystem_name} ({self.system_name}, {self.timestamp})"

    def __repr__(self):
        return (f"<FilesystemStatus(id={self.fs_status_id}, fs='{self.filesystem_name}', "
                f"system='{self.system_name}', util={self.utilization_percent})>")
