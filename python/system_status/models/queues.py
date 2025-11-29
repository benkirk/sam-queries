#-------------------------------------------------------------------------bh-
# Queue Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class QueueStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-queue metrics (5-minute intervals).
    Tracks queues on multiple machines.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'queue_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'queue_name', 'system_name', name='uq_system_queue_timestamp_name'),
    )

    queue_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other)
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    queue_name = Column(String(32), nullable=False, index=True)
    system_name = Column(String(32), nullable=False, index=True, comment='System to which ths queue belongs (derecho, casper, etc.)')

    # Queue Metrics
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    held_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

    # Resource Allocations
    cores_allocated = Column(Integer, nullable=False, default=0)
    gpus_allocated = Column(Integer, nullable=False, default=0)
    nodes_allocated = Column(Integer, nullable=False, default=0)

    # Resources Pending
    cores_pending = Column(Integer, nullable=False, default=0)
    gpus_pending = Column(Integer, nullable=False, default=0)

    # Resources Held
    cores_held = Column(Integer, nullable=False, default=0)
    gpus_held = Column(Integer, nullable=False, default=0)

    # Relationships (back_populates to parent status records)
    derecho_status = relationship('DerechoStatus', back_populates='queues',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='queues',
                                foreign_keys=[casper_status_id])
