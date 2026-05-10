"""
Time-integrated consumption from ``user_proj_queue_status`` spans.

The sibling ``user_proj_queues`` module exposes instantaneous /
time-series views of the per-user / per-project / per-queue rollup
table. This module integrates those spans over a window into
core-hours / GPU-hours / node-hours by ``(user, project, queue)``.

Two complications drive the design:

1. **Tick interval is not a constant 5 minutes** — collectors stall,
   reschedule, get reconfigured. ``dt`` is derived per-tick from the
   actual delta between successive observed timestamps in the parent
   ``DerechoStatus`` / ``CasperStatus`` table (the canonical record of
   "the collector ran at this moment", regardless of whether any user
   had jobs).
2. **Each row is a span, not a snapshot** — a single ``(user, project,
   queue)`` row covers every tick from ``timestamp`` (first_seen) to
   ``last_seen`` inclusive at constant counters (the ingest coalescer
   collapses identical ticks into one span). When the tuple disappears
   from the queue, its row is left alone — ``last_seen`` records the
   final tick of the run.

Integration is **left-step** (rectangle rule): a span at ticks
``[t_i .. t_j]`` with ``cores_allocated = X`` contributes
``X * Σ_{k=i}^{j} (t_{k+1} - t_k)`` core-seconds — equivalently
``X * (cum_dt[j+1] - cum_dt[i])`` using a cumulative-sum prefix array,
which collapses a per-row integral to O(1). The last tick in the
window has ``dt = 0`` so closed spans never over-count.

Spans make this query dramatically smaller than the per-tick era: each
span is one row regardless of how many ticks it covers. Year-scale
queries that previously scanned ~36M snapshot rows now scan however
many spans the workload churn produced. The chunking infrastructure is
preserved for spans that straddle chunk boundaries, with a
``seen_ids`` set deduplicating spans seen in multiple chunks.
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

    # Cumulative-sum prefix so any span [i..j] integrates as
    #   sum(dt_sec[i:j+1]) == cum_dt[j+1] - cum_dt[i]
    # — O(1) per span instead of O(j - i + 1).
    cum_dt = np.concatenate([[0.0], np.cumsum(dt_sec)])

    start_dt64 = np.datetime64(start_date, 'us')
    end_dt64 = np.datetime64(end_date, 'us')

    # ------------------------------------------------------------------
    # 2. Stream user_proj rows in chunks; per-chunk numpy aggregation
    #    merged into a master accumulator dict keyed by composite int.
    # ------------------------------------------------------------------
    # totals[composite_key] = [core_sec, gpu_sec, node_sec,
    #                          first_us, last_us, tick_count]
    totals: Dict[int, List[float]] = {}
    # Spans whose [first_seen, last_seen] cross a chunk boundary may
    # appear in two consecutive chunks; this set dedups them.
    seen_ids: set = set()

    base = (
        select(
            UserProjQueueStatus.user_proj_queue_status_id,
            UserProjQueueStatus.timestamp,
            UserProjQueueStatus.last_seen,
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
        # Span-overlap predicate: a span [first, last] overlaps chunk
        # [t_lo, t_hi] iff last >= t_lo AND first <= t_hi.
        rows = session.execute(
            base.where(UserProjQueueStatus.last_seen >= t_lo,
                       UserProjQueueStatus.timestamp <= t_hi)
        ).all()
        if not rows:
            continue

        # Drop spans we already integrated in a previous chunk.
        rows = [r for r in rows if r[0] not in seen_ids]
        if not rows:
            continue
        seen_ids.update(r[0] for r in rows)

        # Columnar conversion. Tuples come back from SQLAlchemy in a
        # known order, matching the SELECT above.
        ts_arr = np.array([r[1] for r in rows], dtype='datetime64[us]')
        last_arr = np.array([r[2] for r in rows], dtype='datetime64[us]')
        uid_arr = np.fromiter((r[3] for r in rows), dtype=np.int64,
                              count=len(rows))
        pid_arr = np.fromiter((r[4] for r in rows), dtype=np.int64,
                              count=len(rows))
        qid_arr = np.fromiter((r[5] for r in rows), dtype=np.int64,
                              count=len(rows))
        cores_arr = np.fromiter((r[6] for r in rows), dtype=np.float64,
                                count=len(rows))
        gpus_arr = np.fromiter((r[7] for r in rows), dtype=np.float64,
                               count=len(rows))
        nodes_arr = np.fromiter((r[8] for r in rows), dtype=np.float64,
                                count=len(rows))

        _assert_id_fits(uid_arr, pid_arr, qid_arr)

        # Span integration via prefix-sum lookup. Clamp endpoints to the
        # query window so spans that overhang either edge contribute
        # only the in-window portion. searchsorted on a sorted tick
        # array places each clamped endpoint at the matching tick index
        # (or the next-greater for an off-tick value, which is benign
        # since collector ticks and span endpoints share the same set
        # by construction).
        first_clamped = np.maximum(ts_arr, start_dt64)
        last_clamped = np.minimum(last_arr, end_dt64)
        i_first = np.searchsorted(ticks, first_clamped)
        i_last = np.searchsorted(ticks, last_clamped, side='right') - 1

        valid = (i_first <= i_last) & (i_first >= 0) & (i_last < n_ticks)
        if not valid.all():
            uid_arr = uid_arr[valid]
            pid_arr = pid_arr[valid]
            qid_arr = qid_arr[valid]
            cores_arr = cores_arr[valid]
            gpus_arr = gpus_arr[valid]
            nodes_arr = nodes_arr[valid]
            ts_arr = ts_arr[valid]
            last_arr = last_arr[valid]
            i_first = i_first[valid]
            i_last = i_last[valid]
            if i_first.size == 0:
                continue

        dt_total = cum_dt[i_last + 1] - cum_dt[i_first]
        core_sec = cores_arr * dt_total
        gpu_sec = gpus_arr * dt_total
        node_sec = nodes_arr * dt_total
        tick_per_row = (i_last - i_first + 1).astype(np.int64)

        # Composite key for groupby. int64 is large enough by
        # construction (3 × 21 = 63 bits used).
        keys = (uid_arr << (2 * _BITS)) | (pid_arr << _BITS) | qid_arr
        unique_keys, inverse = np.unique(keys, return_inverse=True)
        n_groups = unique_keys.size

        core_sums = np.bincount(inverse, weights=core_sec, minlength=n_groups)
        gpu_sums = np.bincount(inverse, weights=gpu_sec, minlength=n_groups)
        node_sums = np.bincount(inverse, weights=node_sec, minlength=n_groups)
        tick_counts = np.bincount(inverse, weights=tick_per_row.astype(np.float64),
                                  minlength=n_groups)

        # Per-group min(first_seen) and max(last_seen) via reduce-at-indices.
        first_int = ts_arr.astype('int64')
        last_int = last_arr.astype('int64')
        first_us = np.full(n_groups, np.iinfo(np.int64).max, dtype=np.int64)
        last_us = np.full(n_groups, np.iinfo(np.int64).min, dtype=np.int64)
        np.minimum.at(first_us, inverse, first_int)
        np.maximum.at(last_us, inverse, last_int)

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
