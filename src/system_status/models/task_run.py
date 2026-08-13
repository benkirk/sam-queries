"""``TaskRun`` — the scheduled-task ledger.

One row per (task, occurrence). The UNIQUE constraint on
``(task_name, occurrence_key)`` is simultaneously the dedup key and the
mutual-exclusion lock: whoever wins the INSERT owns the slot.

**Why this table lives in `system_status` and not SAM MySQL.** Since 2026-08-10
`hpc-writer` can create tables in `sam` directly, and `notification_log` set
the precedent of a framework table living there — so this was a real choice,
made for three reasons in order of weight:

1. **The test tier.** `system_status` tables are created by
   ``db.create_all(bind_key='system_status')`` against a per-worker SQLite
   tempfile, so a new table exists in CI the moment the model does. A new SAM
   table only reaches CI after ``make bootstrap`` regenerates the LFS test-DB
   blob and someone recommits it — a path that has silently half-failed
   before. The ledger is the most test-heavy component in this design
   (competing claims, stale reclaim, prune), so putting it where tests are
   free is decisive.
2. **Free schema coverage.** ``tests/integration/test_alembic_migrations.py``
   already asserts ``upgrade head == StatusBase.metadata`` and a
   ``head -> base -> head`` round trip; ``0006`` inherits both with no new
   test. SAM has no in-repo migration path at all.
3. **Failure isolation.** With the ledger in Postgres, a SAM MySQL outage
   cannot take down `system_status` retention.

The honest cost is two ledgers in two databases — this and
``notification_log``. Tolerable, because they want no foreign key between them
anyway.

See ``docs/plans/SCHEDULED_TASKS.md`` § 4.
"""

from sqlalchemy import Column, DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint

from ..base import SessionMixin, StatusBase

#: Terminal and in-flight states. ``running`` is the only non-terminal one.
TASK_STATES = ('running', 'succeeded', 'partial', 'failed', 'skipped')

#: How the run was triggered.
TASK_TRIGGERS = ('schedule', 'catchup', 'manual')


class TaskRun(StatusBase, SessionMixin):
    """One attempt at one occurrence of one task.

    **The INSERT is the claim.** There is deliberately no ``claimed`` state
    distinct from ``running``: the row is written microseconds before the task
    body starts, in the same process, so a separate ``claimed`` row would be
    observable only during a window nobody can query — and would add a
    transition that can itself fail. A process that dies between the claim and
    its first heartbeat leaves a ``running`` row with a stale ``heartbeat_at``,
    which is exactly what the reclaim rule handles.

    ``detail`` is ``Text`` holding JSON rather than a JSON column type: MySQL
    is still the default ``STATUS_DB_DRIVER``, production is Postgres and tests
    are SQLite. Portability over queryability, consistent with the rest of
    `system_status`.
    """

    __bind_key__ = "system_status"
    __tablename__ = 'task_run'

    __table_args__ = (
        # The dedup key AND the mutual-exclusion lock. Everything else in the
        # design rests on this one constraint.
        UniqueConstraint('task_name', 'occurrence_key',
                         name='uq_task_run_task_name_occurrence_key'),
        # "last run of X", and the history listing.
        Index('ix_task_run_task_name_claimed_at', 'task_name', 'claimed_at'),
        # The stale sweep.
        Index('ix_task_run_state', 'state'),
    )

    task_run_id = Column(Integer, primary_key=True, autoincrement=True)

    #: Registry key, e.g. ``cleanup_status_snapshots``. **Stable forever** —
    #: renaming a task orphans its history.
    task_name = Column(String(64), nullable=False)

    #: ``20260810T081500Z``, or ``M20260809T143002Z`` for a forced run. The
    #: leading ``M`` cannot collide with a scheduled key, which is what keeps a
    #: manual run from satisfying a scheduled slot.
    occurrence_key = Column(String(24), nullable=False)

    #: One of :data:`TASK_STATES`.
    state = Column(String(16), nullable=False)
    #: One of :data:`TASK_TRIGGERS`.
    trigger = Column(String(16), nullable=False)

    #: Bumped only by a stale reclaim, never by a normal run.
    attempt = Column(SmallInteger, nullable=False, default=1)

    #: Naive UTC, like every other timestamp on this bind.
    claimed_at = Column(DateTime, nullable=False)
    #: Naive UTC. The lease — see ``scheduling.ledger``.
    heartbeat_at = Column(DateTime, nullable=False)
    #: NULL while ``running``.
    finished_at = Column(DateTime)

    duration_ms = Column(Integer)

    #: The pod name, so a row ties back to ``kubectl logs``.
    runner_id = Column(String(64))

    #: JSON: the task's ``TaskResult.detail``, or a truncated traceback.
    detail = Column(Text)

    def __str__(self):
        return f'{self.task_name}@{self.occurrence_key} ({self.state})'

    def __repr__(self):
        return (f'<TaskRun(id={self.task_run_id}, task={self.task_name!r}, '
                f'occurrence={self.occurrence_key!r}, state={self.state!r}, '
                f'attempt={self.attempt})>')
