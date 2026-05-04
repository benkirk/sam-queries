#-------------------------------------------------------------------------bh-
# Queue Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin, QueueRollupMetricsMixin
from .lookups import System, QueueDef


class QueueStatus(StatusBase, StatusSnapshotMixin, QueueRollupMetricsMixin, SessionMixin):
    """
    Per-queue metrics (5-minute intervals).
    Tracks queues on multiple machines.

    Phase 2 (PR-A): the legacy text columns ``queue_name`` / ``system_name``
    have been replaced by FK columns ``queue_id`` / ``system_id`` against
    the ``queues`` and ``systems`` lookup tables. Property accessors below
    preserve the legacy attribute interface — see
    ``system_status.queries.lookups`` for the resolution machinery.

    Rollup metric columns (running_jobs, pending_jobs, held_jobs, cores_*,
    gpus_*, nodes_allocated) come from ``QueueRollupMetricsMixin`` and are
    shared with ``UserProjQueueStatus``.
    """
    __bind_key__ = "system_status"
    __tablename__ = 'queue_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'queue_id', name='uq_queue_status_timestamp_queue_id'),
    )

    queue_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other)
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    # Lookup FKs (Phase 2).
    system_id = Column(Integer, ForeignKey('systems.system_id'),
                       nullable=False, index=True)
    queue_id = Column(Integer, ForeignKey('queues.queue_id'),
                      nullable=False, index=True)

    # Queue-grain only (not in the rollup mixin: not meaningful per-user).
    active_users = Column(Integer, nullable=False, default=0)

    # Relationships
    derecho_status = relationship('DerechoStatus', back_populates='queues',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='queues',
                                foreign_keys=[casper_status_id])
    # Use class objects (not string lookups) — `QueueDef` is named to avoid
    # collision with `sam.resources.Queue` in the shared declarative registry.
    system = relationship(System, foreign_keys=[system_id])
    queue = relationship(QueueDef, foreign_keys=[queue_id])

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
    def queue_name(self):
        pending = self.__dict__.get('_pending_queue_name')
        if pending is not None:
            return pending
        return self.queue.name if self.queue is not None else None

    @queue_name.setter
    def queue_name(self, value):
        self.__dict__['_pending_queue_name'] = value

    def __str__(self):
        return f"{self.queue_name} ({self.system_name}, {self.timestamp})"

    def __repr__(self):
        return (f"<QueueStatus(id={self.queue_status_id}, queue='{self.queue_name}', "
                f"system='{self.system_name}', running={self.running_jobs})>")
