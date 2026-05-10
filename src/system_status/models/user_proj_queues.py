#-------------------------------------------------------------------------bh-
# Per-user / per-project queue rollup snapshots
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, DateTime, Integer, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin, QueueRollupMetricsMixin
from .lookups import System, QueueDef, UserDef, ProjectCodeDef


class UserProjQueueStatus(StatusBase, StatusSnapshotMixin,
                          QueueRollupMetricsMixin, SessionMixin):
    """
    Per-user / per-project / per-queue rollup spans (formerly per-tick snapshots).

    Each row is a **span of unchanging counts** for a single
    ``(user, project_code, queue)`` tuple. The ``timestamp`` column is the
    span's *first_seen* — the earliest tick the row was observed — and
    ``last_seen`` is the most recent tick at which the same counts were
    observed. Equality at ingest (all ten ``QueueRollupMetricsMixin``
    counters identical) extends an existing span by bumping ``last_seen``;
    any change in counters or the appearance of a new tuple inserts a
    fresh row with ``timestamp == last_seen == T_new``. When the tuple
    leaves the queue, its row is left alone — ``last_seen`` is never bumped
    again, so the row becomes a self-contained record of one steady-state
    run.

    The parent FK (``derecho_status_id`` / ``casper_status_id``) points at
    the parent status row at *first_seen* and is never rewritten when the
    span is extended. CASCADE delete is preserved: pruning old parent
    snapshots also prunes spans that started in that window.

    Same counter shape as ``QueueStatus`` (shared via
    ``QueueRollupMetricsMixin``) but keyed by ``(user, project_code, queue)``
    instead of just ``queue``. The span representation collapses long
    runs of identical ticks into a single row.

    Username and project_code denormalize through ``UserDef`` and
    ``ProjectCodeDef`` lookups for compact integer keys. The ``before_flush``
    listener in ``system_status.queries.lookups`` resolves the staged
    ``_pending_username`` / ``_pending_project_code`` strings into FKs at
    flush time for INSERTs; the ingest coalescer in
    ``system_status.queries.user_proj_queue_ingest`` resolves them
    synchronously before deciding INSERT-vs-UPDATE.
    """
    __bind_key__ = "system_status"
    __tablename__ = 'user_proj_queue_status'

    # Span uniqueness: (timestamp, user, project_code, queue) — i.e.
    # ``(first_seen, ...)``. Each new span has a distinct first_seen, so
    # the constraint is unchanged from the per-tick era. ``system_id`` is
    # intentionally absent — ``queue_id`` references a single ``QueueDef``
    # row which is itself ``(system_id, name)``-keyed.
    __table_args__ = (
        UniqueConstraint('timestamp', 'user_id', 'project_code_id', 'queue_id',
                         name='uq_user_proj_queue_status_snapshot'),
    )

    user_proj_queue_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other,
    # mirrors QueueStatus parent linkage so cascade-delete works).
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    # Lookup FKs.
    user_id = Column(Integer, ForeignKey('status_users.user_id'),
                     nullable=False, index=True)
    project_code_id = Column(Integer, ForeignKey('project_codes.project_code_id'),
                             nullable=False, index=True)
    system_id = Column(Integer, ForeignKey('systems.system_id'),
                       nullable=False, index=True)
    queue_id = Column(Integer, ForeignKey('queues.queue_id'),
                      nullable=False, index=True)

    # Span endpoint: most recent tick at which the same (user, project,
    # queue, counts) was observed. Equals ``timestamp`` (first_seen) on a
    # brand-new span; bumped forward when ingest coalesces an identical
    # tick.
    last_seen = Column(DateTime, nullable=False, index=True,
                       comment='Most recent tick at which counts matched timestamp (first_seen)')

    # Rollup metric columns inherited from QueueRollupMetricsMixin:
    # running_jobs, pending_jobs, held_jobs, cores_allocated, gpus_allocated,
    # nodes_allocated, cores_pending, gpus_pending, cores_held, gpus_held.

    # Relationships. Naming: relationships use short attribute names (``user``,
    # ``project``, ``queue``, ``system``) so the textual accessor properties
    # (``username``, ``project_code``, ``queue_name``, ``system_name``) can
    # carry the JSON-contract names without colliding.
    derecho_status = relationship('DerechoStatus', back_populates='user_project_queues',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='user_project_queues',
                                 foreign_keys=[casper_status_id])
    user = relationship(UserDef, foreign_keys=[user_id])
    project = relationship(ProjectCodeDef, foreign_keys=[project_code_id])
    system = relationship(System, foreign_keys=[system_id])
    queue = relationship(QueueDef, foreign_keys=[queue_id])

    # ------------------------------------------------------------------
    # Backward-compat / collector-friendly property accessors.
    # Mirror the pattern in queues.py: setters stage strings as
    # _pending_* attributes; the before_flush listener resolves them.
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

    @property
    def username(self):
        pending = self.__dict__.get('_pending_username')
        if pending is not None:
            return pending
        return self.user.username if self.user is not None else None

    @username.setter
    def username(self, value):
        self.__dict__['_pending_username'] = value

    @property
    def project_code(self):
        pending = self.__dict__.get('_pending_project_code')
        if pending is not None:
            return pending
        return self.project.project_code if self.project is not None else None

    @project_code.setter
    def project_code(self, value):
        self.__dict__['_pending_project_code'] = value

    def __str__(self):
        return (f"{self.username}/{self.project_code} on {self.queue_name} "
                f"({self.system_name}, {self.timestamp}..{self.last_seen})")

    def __repr__(self):
        return (f"<UserProjQueueStatus(id={self.user_proj_queue_status_id}, "
                f"user='{self.username}', project='{self.project_code}', "
                f"queue='{self.queue_name}', system='{self.system_name}', "
                f"first_seen={self.timestamp}, last_seen={self.last_seen}, "
                f"running={self.running_jobs})>")
