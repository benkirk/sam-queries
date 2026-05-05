"""
Time-integrated consumption from ``user_proj_queue_status`` snapshots.

The sibling ``user_proj_queues`` module exposes instantaneous /
time-series views of the per-user / per-project / per-queue rollup
table. This module integrates those snapshots over a window into
core-hours / GPU-hours / node-hours by ``(user, project, queue)``.

Two complications drive the design:

1. **Tick interval is not a constant 5 minutes** — collectors stall,
   reschedule, get reconfigured. ``dt`` is derived per-tick from the
   actual delta between successive observed timestamps in the parent
   ``DerechoStatus`` / ``CasperStatus`` table (the canonical record of
   "the collector ran at this moment", regardless of whether any user
   had jobs).
2. **Sparse rows** — a ``(user, project, queue)`` row exists only when
   that tuple has ≥1 job at that tick. Absence at a tick is treated as
   ``0 cores / 0 gpus / 0 nodes`` for that tuple at that tick (NOT
   carry-forward — that would attribute load forever after a user's
   jobs end).

Integration is **left-step** (rectangle rule): a row at tick ``t_i``
with ``cores_allocated = X`` contributes ``X * (t_{i+1} - t_i)``
core-seconds. The last tick in the window has no successor and
contributes 0 (boundary effect is negligible for any window > a few
ticks). Trapezoidal would average with a "0" at the next tick when a
tuple disappears, under-counting by half — left-step is the honest
discretization for jobs that start/stop in discrete jumps.

For year-scale windows (~36M rows) the snapshot rows are streamed in
monthly chunks; per-chunk aggregation uses numpy ``bincount`` on a
composite ``(user_id, project_code_id, queue_id)`` key, then merged
into a master accumulator dict. See ``get_user_proj_usage`` for the
full algorithm.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from system_status.models import (
    CasperStatus,
    DerechoStatus,
    ProjectCodeDef,
    QueueDef,
    System,
    UserDef,
    UserProjQueueStatus,
)

from .user_proj_queues import _resolve_queue_id, _resolve_system_id


_PARENT_STATUS_BY_SYSTEM = {
    'derecho': DerechoStatus,
    'casper': CasperStatus,
}

# Composite key packing for (user_id, project_code_id, queue_id) into one
# int64. 21 bits each = 2,097,151 max — well above current cardinalities
# (status_users < 1k, project_codes < 500, queues < 100) with headroom
# for years of growth. The ``_assert_id_fits`` guard in
# ``get_user_proj_usage`` catches the day this assumption breaks.
_BITS = 21
_MASK = (1 << _BITS) - 1
_MAX_ID = _MASK


def _assert_id_fits(uid_arr: np.ndarray, pid_arr: np.ndarray,
                    qid_arr: np.ndarray) -> None:
    """Defensive: refuse to silently truncate IDs into the composite key."""
    for name, arr in (('user_id', uid_arr),
                      ('project_code_id', pid_arr),
                      ('queue_id', qid_arr)):
        if arr.size and int(arr.max()) > _MAX_ID:
            raise RuntimeError(
                f'{name} exceeds composite-key budget ({_MAX_ID}); '
                'increase _BITS in user_proj_usage.py'
            )


def _iter_chunks(start: datetime, end: datetime,
                 chunk_days: int) -> List[tuple]:
    """Yield ``(t_lo, t_hi)`` chunks covering ``[start, end]`` inclusive,
    each spanning at most ``chunk_days`` days."""
    out = []
    cursor = start
    span = timedelta(days=chunk_days)
    while cursor <= end:
        nxt = min(cursor + span, end)
        out.append((cursor, nxt))
        if nxt >= end:
            break
        # +1 microsecond so the next chunk doesn't double-count rows at
        # the boundary (timestamps are unique per row, and the upper
        # bound of the previous chunk was inclusive).
        cursor = nxt + timedelta(microseconds=1)
    return out


def get_user_proj_usage(
    session: Session,
    *,
    system: str,
    start_date: datetime,
    end_date: datetime,
    queue_name: Optional[str] = None,
    username: Optional[str] = None,
    project_code: Optional[str] = None,
    exclude_unknown_project: bool = False,
    chunk_days: int = 30,
) -> List[Dict[str, Any]]:
    """Integrate ``UserProjQueueStatus`` snapshots into core-hours /
    GPU-hours / node-hours by ``(user, project, queue)`` over
    ``[start_date, end_date]``.

    Returns one dict per tuple with non-zero usage, sorted descending by
    ``core_hours``::

        {
            'username':     'benkirk',
            'project_code': 'SCSG0001',
            'queue_name':   'main',
            'system':       'derecho',
            'core_hours':   1234.56,
            'gpu_hours':    0.0,
            'node_hours':   38.58,
            'first_seen':   datetime(...),   # earliest tick the tuple appeared
            'last_seen':    datetime(...),   # latest tick the tuple appeared
            'tick_count':   42,              # how many ticks the tuple was present
        }

    **Integration model.** Left-step rectangle rule over the global tick
    timeline derived from the parent system status table. A snapshot
    at tick ``t_i`` with value ``x_i`` contributes ``x_i * (t_{i+1} -
    t_i)`` to the integral. Absence of a tuple at a tick is treated as
    0. The last tick in the window has no successor and contributes 0.

    **Filters** (all optional, all combine with AND):

    - ``queue_name`` — restrict to a single queue on ``system``.
    - ``username`` / ``project_code`` — restrict to a single
      user / project. Returns ``[]`` if the lookup row doesn't exist.
    - ``exclude_unknown_project`` — drop the ``'_unknown_'`` bucket
      (jobs whose ``Account_Name`` was missing/empty).

    **Performance envelope** for windows on prod-shaped data
    (~98 k rows/day, ~340 distinct tuples/tick):

    ============  =========  ===========================
    Window        Rows       Wall time (target)
    ============  =========  ===========================
    1 hour        ~4 k       <50 ms
    1 day         ~100 k     <500 ms
    1 week        ~700 k     ~3 s
    1 month       ~3 M       ~10–15 s
    1 year        ~36 M      ~2–4 min  (offline only)
    ============  =========  ===========================

    Windows >60 days should NOT be hit from a synchronous request
    handler. If year-scale UI becomes a need, the right answer is a
    nightly-rolled summary table, not optimizing this query.
    """
    parent = _PARENT_STATUS_BY_SYSTEM.get(system)
    if parent is None:
        raise ValueError(
            f'system must be one of {sorted(_PARENT_STATUS_BY_SYSTEM)}, '
            f'got {system!r}'
        )
    if start_date > end_date:
        return []

    system_id = _resolve_system_id(session, system)
    if system_id is None:
        return []

    # Optional scope filter — single queue.
    queue_id = None
    if queue_name is not None:
        queue_id = _resolve_queue_id(session, system, queue_name)
        if queue_id is None:
            return []

    # Optional user / project filters — resolve up front so we avoid a
    # JOIN in the hot fetch loop.
    user_id_filter = None
    if username is not None:
        user_id_filter = session.execute(
            select(UserDef.user_id).where(UserDef.username == username)
        ).scalar_one_or_none()
        if user_id_filter is None:
            return []

    project_code_id_filter = None
    if project_code is not None:
        project_code_id_filter = session.execute(
            select(ProjectCodeDef.project_code_id)
            .where(ProjectCodeDef.project_code == project_code)
        ).scalar_one_or_none()
        if project_code_id_filter is None:
            return []

    unknown_pid = None
    if exclude_unknown_project:
        unknown_pid = session.execute(
            select(ProjectCodeDef.project_code_id)
            .where(ProjectCodeDef.project_code == '_unknown_')
        ).scalar_one_or_none()
        # If no _unknown_ row exists, the filter is a no-op.

    # ------------------------------------------------------------------
    # 1. Global tick timeline from the parent status table.
    # ------------------------------------------------------------------
    tick_rows = session.execute(
        select(parent.timestamp)
        .where(parent.timestamp >= start_date,
               parent.timestamp <= end_date)
        .order_by(parent.timestamp)
    ).all()
    tick_list = [r[0] for r in tick_rows]
    n_ticks = len(tick_list)
    if n_ticks < 2:
        # Single tick or no ticks — nothing to integrate over.
        return []

    ticks = np.array(tick_list, dtype='datetime64[us]')
    # Per-tick interval in **seconds** (float64). Computing in seconds —
    # not microseconds — keeps year-scale sums comfortably under the
    # float64 mantissa precision cliff (~9e15 ≈ 2.5e9 core-hours).
    # dt[N-1] = 0 (last tick has no successor); pad to keep array
    # length aligned with `ticks`.
    dt_sec = np.zeros(n_ticks, dtype=np.float64)
    dt_sec[:-1] = np.diff(ticks).astype('int64').astype(np.float64) / 1e6

    # ------------------------------------------------------------------
    # 2. Stream user_proj rows in chunks; per-chunk numpy aggregation
    #    merged into a master accumulator dict keyed by composite int.
    # ------------------------------------------------------------------
    # totals[composite_key] = [core_sec, gpu_sec, node_sec,
    #                          first_us, last_us, tick_count]
    totals: Dict[int, List[float]] = {}

    base = (
        select(
            UserProjQueueStatus.timestamp,
            UserProjQueueStatus.user_id,
            UserProjQueueStatus.project_code_id,
            UserProjQueueStatus.queue_id,
            UserProjQueueStatus.cores_allocated,
            UserProjQueueStatus.gpus_allocated,
            UserProjQueueStatus.nodes_allocated,
        )
        .where(UserProjQueueStatus.system_id == system_id)
    )
    if queue_id is not None:
        base = base.where(UserProjQueueStatus.queue_id == queue_id)
    if user_id_filter is not None:
        base = base.where(UserProjQueueStatus.user_id == user_id_filter)
    if project_code_id_filter is not None:
        base = base.where(
            UserProjQueueStatus.project_code_id == project_code_id_filter
        )
    if unknown_pid is not None:
        base = base.where(UserProjQueueStatus.project_code_id != unknown_pid)

    for t_lo, t_hi in _iter_chunks(start_date, end_date, chunk_days):
        rows = session.execute(
            base.where(UserProjQueueStatus.timestamp >= t_lo,
                       UserProjQueueStatus.timestamp <= t_hi)
        ).all()
        if not rows:
            continue

        # Columnar conversion. Tuples come back from SQLAlchemy in a
        # known order, matching the SELECT above.
        ts_arr = np.array([r[0] for r in rows], dtype='datetime64[us]')
        uid_arr = np.fromiter((r[1] for r in rows), dtype=np.int64,
                              count=len(rows))
        pid_arr = np.fromiter((r[2] for r in rows), dtype=np.int64,
                              count=len(rows))
        qid_arr = np.fromiter((r[3] for r in rows), dtype=np.int64,
                              count=len(rows))
        cores_arr = np.fromiter((r[4] for r in rows), dtype=np.float64,
                                count=len(rows))
        gpus_arr = np.fromiter((r[5] for r in rows), dtype=np.float64,
                               count=len(rows))
        nodes_arr = np.fromiter((r[6] for r in rows), dtype=np.float64,
                                count=len(rows))

        _assert_id_fits(uid_arr, pid_arr, qid_arr)

        # Vectorized dt lookup. `searchsorted` on a sorted tick array
        # gives the index where each row's timestamp would be inserted;
        # for an exact match the index points AT that tick. Defensive
        # mask drops anything that doesn't land on a known tick (should
        # be empty in practice — parent and child ticks share the same
        # timestamp by construction).
        idx = np.searchsorted(ticks, ts_arr)
        valid = (idx < n_ticks) & (ticks[np.minimum(idx, n_ticks - 1)] == ts_arr)
        if not valid.all():
            uid_arr = uid_arr[valid]
            pid_arr = pid_arr[valid]
            qid_arr = qid_arr[valid]
            cores_arr = cores_arr[valid]
            gpus_arr = gpus_arr[valid]
            nodes_arr = nodes_arr[valid]
            ts_arr = ts_arr[valid]
            idx = idx[valid]
            if idx.size == 0:
                continue

        dt_per_row = dt_sec[idx]   # seconds; 0 for last-tick rows
        core_sec = cores_arr * dt_per_row
        gpu_sec = gpus_arr * dt_per_row
        node_sec = nodes_arr * dt_per_row

        # Composite key for groupby. int64 is large enough by
        # construction (3 × 21 = 63 bits used).
        keys = (uid_arr << (2 * _BITS)) | (pid_arr << _BITS) | qid_arr
        unique_keys, inverse = np.unique(keys, return_inverse=True)
        n_groups = unique_keys.size

        core_sums = np.bincount(inverse, weights=core_sec, minlength=n_groups)
        gpu_sums = np.bincount(inverse, weights=gpu_sec, minlength=n_groups)
        node_sums = np.bincount(inverse, weights=node_sec, minlength=n_groups)
        tick_counts = np.bincount(inverse, minlength=n_groups)

        # Per-group min/max timestamp via reduce-at-indices. Convert to
        # int64 microseconds for arithmetic, convert back at merge.
        ts_int = ts_arr.astype('int64')
        first_us = np.full(n_groups, np.iinfo(np.int64).max, dtype=np.int64)
        last_us = np.full(n_groups, np.iinfo(np.int64).min, dtype=np.int64)
        np.minimum.at(first_us, inverse, ts_int)
        np.maximum.at(last_us, inverse, ts_int)

        # Merge into accumulator dict. Iterating over n_groups (not
        # n_rows) keeps Python overhead bounded.
        for g in range(n_groups):
            k = int(unique_keys[g])
            entry = totals.get(k)
            if entry is None:
                totals[k] = [
                    float(core_sums[g]),
                    float(gpu_sums[g]),
                    float(node_sums[g]),
                    int(first_us[g]),
                    int(last_us[g]),
                    int(tick_counts[g]),
                ]
            else:
                entry[0] += float(core_sums[g])
                entry[1] += float(gpu_sums[g])
                entry[2] += float(node_sums[g])
                if first_us[g] < entry[3]:
                    entry[3] = int(first_us[g])
                if last_us[g] > entry[4]:
                    entry[4] = int(last_us[g])
                entry[5] += int(tick_counts[g])

    if not totals:
        return []

    # ------------------------------------------------------------------
    # 3. Resolve labels in three batched lookups (no N+1).
    # ------------------------------------------------------------------
    uid_set: set = set()
    pid_set: set = set()
    qid_set: set = set()
    for k in totals:
        uid_set.add((k >> (2 * _BITS)) & _MASK)
        pid_set.add((k >> _BITS) & _MASK)
        qid_set.add(k & _MASK)

    uid_to_name = dict(session.execute(
        select(UserDef.user_id, UserDef.username)
        .where(UserDef.user_id.in_(uid_set))
    ).all())
    pid_to_code = dict(session.execute(
        select(ProjectCodeDef.project_code_id, ProjectCodeDef.project_code)
        .where(ProjectCodeDef.project_code_id.in_(pid_set))
    ).all())
    qid_to_name = dict(session.execute(
        select(QueueDef.queue_id, QueueDef.name)
        .where(QueueDef.queue_id.in_(qid_set))
    ).all())

    # ------------------------------------------------------------------
    # 4. Convert to hours, drop zero-usage entries, sort.
    # ------------------------------------------------------------------
    SEC_PER_HOUR = 3600.0
    out: List[Dict[str, Any]] = []
    for k, (core_s, gpu_s, node_s, first_i, last_i, count) in totals.items():
        core_h = core_s / SEC_PER_HOUR
        gpu_h = gpu_s / SEC_PER_HOUR
        node_h = node_s / SEC_PER_HOUR
        if core_h == 0.0 and gpu_h == 0.0 and node_h == 0.0:
            continue
        uid = (k >> (2 * _BITS)) & _MASK
        pid = (k >> _BITS) & _MASK
        qid = k & _MASK
        out.append({
            'username':     uid_to_name.get(uid),
            'project_code': pid_to_code.get(pid),
            'queue_name':   qid_to_name.get(qid),
            'system':       system,
            'core_hours':   core_h,
            'gpu_hours':    gpu_h,
            'node_hours':   node_h,
            'first_seen':   np.datetime64(first_i, 'us').astype(datetime),
            'last_seen':    np.datetime64(last_i, 'us').astype(datetime),
            'tick_count':   count,
        })

    out.sort(key=lambda r: r['core_hours'], reverse=True)
    return out
