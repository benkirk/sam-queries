#-------------------------------------------------------------------------bh-
# Queue Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, String, Float, Boolean, Index, UniqueConstraint
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class QueueStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    Per-queue metrics (5-minute intervals).
    Tracks queues on multiple machines.
    """
    __tablename__ = 'queue_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'queue_name', 'system_name', name='uq_system_queue_timestamp_name'),
    )

    queue_status_id = Column(Integer, primary_key=True, autoincrement=True)
    queue_name = Column(String(64), nullable=False, index=True)
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
