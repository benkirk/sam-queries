"""Ingest-time coalescer for ``user_proj_queue_status`` spans.

Each row in the table is a *span* of unchanging counts for a single
``(user, project, queue)`` tuple — see the ``UserProjQueueStatus``
docstring. On each ingest tick the collector POSTs the active set of
tuples; this helper folds those incoming rows against the most recent
tick's spans and decides per-row whether to:

* **Extend** an existing span (UPDATE its ``last_seen``) — when the
  tuple's counts match an active span exactly.
* **Insert** a new span (with ``timestamp = last_seen = T_new``) — when
  the tuple is new at this tick or its counts differ from the active
  span.

The coalescer mutates ``parent_status.user_project_queues`` in place
*before* the parent is added to the session, so detached duplicates
never enter SQLAlchemy's unit-of-work — no ``cascade='all,
delete-orphan'`` surgery required.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from system_status.models import UserProjQueueStatus

from .lookups import resolve_user_proj_queue_pending


# A `MAX_SPAN_GAP`-or-greater jump between the previous tick's `last_seen`
# and the incoming `T_new` is treated as a collector outage: spans don't
# extend across it, even if counts happen to match. Three missed 5-minute
# ticks is the operational definition.
MAX_SPAN_GAP = timedelta(minutes=20)


# Order of the QueueRollupMetricsMixin counters compared for span equality.
# The 10-tuple built per child must use this order.
_METRIC_FIELDS: Tuple[str, ...] = (
    'running_jobs',
    'pending_jobs',
    'held_jobs',
    'cores_allocated',
    'gpus_allocated',
    'nodes_allocated',
    'cores_pending',
    'gpus_pending',
    'cores_held',
    'gpus_held',
)


def _metric_tuple(obj) -> Tuple[int, ...]:
    return tuple(int(getattr(obj, f) or 0) for f in _METRIC_FIELDS)


def coalesce_user_proj_queue_spans(
    session: Session,
    parent_status,
    T_new: datetime,
) -> Dict[str, int]:
    """Coalesce ``parent_status.user_project_queues`` against the previous
    tick's active span set.

    Mutates the children list in place: extended spans are loaded by id
    and have ``last_seen`` bumped; new spans get ``last_seen = T_new``
    set and stay attached to the parent for INSERT.

    Returns ``{'inserted': N, 'extended': M}`` for logging.

    The parent status object is expected to be **transient** at call time
    (Marshmallow has built it but the route hasn't called
    ``session.add(parent_status)`` yet). That keeps the children-list
    mutations as pure Python list operations, untouched by SQLAlchemy
    cascade machinery.
    """
    children = list(parent_status.user_project_queues)
    if not children:
        return {'inserted': 0, 'extended': 0}

    # 1. Resolve `_pending_*` strings on every child. Share caches across
    #    children so a batch of 340 rows hits each lookup table at most
    #    once. The helper assigns `obj.system / obj.queue / obj.user /
    #    obj.project`; we may need to flush so newly-created lookup rows
    #    have ids before we query against them.
    sys_cache: dict = {}
    queue_cache: dict = {}
    user_cache: dict = {}
    proj_cache: dict = {}
    for child in children:
        resolve_user_proj_queue_pending(
            session, child,
            sys_cache=sys_cache, queue_cache=queue_cache,
            user_cache=user_cache, proj_cache=proj_cache,
        )

    # Materialize ids for any newly-created lookup rows so we can read
    # `child.user.user_id / queue.queue_id / project.project_code_id /
    # system.system_id` below.
    session.flush()

    # Read FKs through the resolved relationships (the children are
    # transient, so their column-level FKs aren't auto-synced yet).
    def _child_fks(c):
        return (
            c.user.user_id if c.user is not None else None,
            c.project.project_code_id if c.project is not None else None,
            c.queue.queue_id if c.queue is not None else None,
            c.system.system_id if c.system is not None else None,
        )

    # 2. Determine system_id (every child on one parent shares it).
    _, _, _, system_id = _child_fks(children[0])
    if system_id is None:
        # Defensive: nothing to coalesce against without a system scope.
        for child in children:
            child.last_seen = T_new
        return {'inserted': len(children), 'extended': 0}

    # 3. Most recent `last_seen` for this system. None ⇒ first ingest.
    prev_ts = session.execute(
        select(func.max(UserProjQueueStatus.last_seen))
        .where(UserProjQueueStatus.system_id == system_id)
    ).scalar_one_or_none()

    # 4. Span-gap guard: skip coalescing across collector outages.
    if prev_ts is None or (T_new - prev_ts) > MAX_SPAN_GAP:
        for child in children:
            child.last_seen = T_new
        return {'inserted': len(children), 'extended': 0}

    # 5. Active set: spans whose `last_seen == prev_ts` for this system.
    active_rows = session.execute(
        select(
            UserProjQueueStatus.user_proj_queue_status_id,
            UserProjQueueStatus.user_id,
            UserProjQueueStatus.project_code_id,
            UserProjQueueStatus.queue_id,
            UserProjQueueStatus.running_jobs,
            UserProjQueueStatus.pending_jobs,
            UserProjQueueStatus.held_jobs,
            UserProjQueueStatus.cores_allocated,
            UserProjQueueStatus.gpus_allocated,
            UserProjQueueStatus.nodes_allocated,
            UserProjQueueStatus.cores_pending,
            UserProjQueueStatus.gpus_pending,
            UserProjQueueStatus.cores_held,
            UserProjQueueStatus.gpus_held,
        )
        .where(
            UserProjQueueStatus.system_id == system_id,
            UserProjQueueStatus.last_seen == prev_ts,
        )
    ).all()

    # Index by (user_id, project_code_id, queue_id) → (row_id, metric_tuple)
    active: Dict[Tuple[int, int, int], Tuple[int, Tuple[int, ...]]] = {
        (r[1], r[2], r[3]): (r[0], tuple(int(v or 0) for v in r[4:]))
        for r in active_rows
    }

    inserted = 0
    extended = 0
    for child in children:
        uid, pid, qid, _ = _child_fks(child)
        key = (uid, pid, qid)
        match = active.get(key)
        if match is not None and match[1] == _metric_tuple(child):
            row_id, _ = match
            existing = session.get(UserProjQueueStatus, row_id)
            if existing is not None:
                existing.last_seen = T_new
                # Drop the duplicate before the parent enters the session.
                # This is a plain list mutation while parent_status is still
                # transient — no cascade machinery fires.
                parent_status.user_project_queues.remove(child)
                extended += 1
                continue
        # New span: stamp last_seen and leave attached for INSERT.
        child.last_seen = T_new
        inserted += 1

    return {'inserted': inserted, 'extended': extended}
