"""
Query helpers for ``user_proj_queue_status`` (per-user / per-project /
per-queue rollups).

Phase B reads only. Aggregations are queue-scoped — every helper takes
``system`` + ``queue_name`` so results plug directly into the existing
queue drill-down view (``status_dashboard.queue_history``).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import func, select
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


# Parent status table by system, used to fetch the canonical tick
# timeline for span explosion. Mirrors the dict in
# ``user_proj_usage.py`` — duplicated here to avoid a cross-module
# dependency loop.
_PARENT_STATUS_BY_SYSTEM = {
    'derecho': DerechoStatus,
    'casper': CasperStatus,
}


_VALID_GROUP_BY = ('user', 'project')

_VALID_STATES = ('running', 'pending', 'held')
_VALID_METRICS = ('jobs', 'cores', 'gpus', 'nodes')

_VALID_RANK_BY = ('peak', 'current')

# (state, metric) → DB column on UserProjQueueStatus / QueueRollupMetricsMixin.
# `nodes` is only defined for state='running' (the schema has no
# nodes_pending / nodes_held columns); other (state, metric) combos
# raise ValueError when looked up. Callers should clamp before calling.
_STATE_METRIC_TO_COLUMN = {
    ('running', 'jobs'):  'running_jobs',
    ('pending', 'jobs'):  'pending_jobs',
    ('held',    'jobs'):  'held_jobs',
    ('running', 'cores'): 'cores_allocated',
    ('pending', 'cores'): 'cores_pending',
    ('held',    'cores'): 'cores_held',
    ('running', 'gpus'):  'gpus_allocated',
    ('pending', 'gpus'):  'gpus_pending',
    ('held',    'gpus'):  'gpus_held',
    ('running', 'nodes'): 'nodes_allocated',
}

# Display labels — used by chart Y-axis and card titles.
_METRIC_NOUN = {
    'jobs':  'jobs',
    'cores': 'cores',
    'gpus':  'GPUs',
    'nodes': 'nodes',
}
_STATE_ADJECTIVE = {
    'running': 'running',
    'pending': 'pending',
    'held':    'held',
}


def _label_for(state: str, metric: str) -> str:
    """Human-readable Y-axis / chart-title fragment, e.g. 'Pending cores'."""
    return f'{_STATE_ADJECTIVE[state].capitalize()} {_METRIC_NOUN[metric]}'


def _resolve_queue_id(session: Session, system: str, queue_name: str) -> Optional[int]:
    """Return the queue_id for ``(system, queue_name)``, or ``None`` if absent.

    ``QueueDef`` is keyed by ``(system_id, name)`` — looking up by name alone
    would be ambiguous (queues with the same name exist on both systems).
    """
    return session.execute(
        select(QueueDef.queue_id)
        .join(System, QueueDef.system_id == System.system_id)
        .where(System.name == system, QueueDef.name == queue_name)
    ).scalar_one_or_none()


def _resolve_system_id(session: Session, system: str) -> Optional[int]:
    """Return the system_id for ``system``, or ``None`` if absent."""
    return session.execute(
        select(System.system_id).where(System.name == system)
    ).scalar_one_or_none()


def get_latest_user_proj_queue_snapshot(
    session: Session,
    *,
    system: str,
    queue_name: str,
) -> List[Dict[str, Any]]:
    """One row per ``(user, project_code)`` for the most recent tick of
    this queue.

    Under span semantics, "the most recent tick" means rows whose
    ``last_seen`` equals the maximum ``last_seen`` for the queue — i.e.
    spans that were still active at the most recent observation.

    Returns a list of dicts shaped for direct template rendering:

        [{'username': 'benkirk', 'project_code': 'SCSG0001',
          'running_jobs': 3, 'pending_jobs': 0, 'held_jobs': 0,
          'cores_allocated': 256, 'cores_pending': 0, 'cores_held': 0,
          'gpus_allocated': 0, 'gpus_pending': 0, 'gpus_held': 0,
          'nodes_allocated': 4,
          'timestamp':  datetime(...),  # last_seen (template-facing alias)
          'first_seen': datetime(...),  # span start
          'last_seen':  datetime(...)}, ...]

    Empty list if the queue is unknown or has never reported a snapshot.
    The ``_unknown_`` project bucket (jobs missing ``Account_Name``) is
    included as just another row.
    """
    queue_id = _resolve_queue_id(session, system, queue_name)
    if queue_id is None:
        return []

    latest_ts = session.execute(
        select(UserProjQueueStatus.last_seen)
        .where(UserProjQueueStatus.queue_id == queue_id)
        .order_by(UserProjQueueStatus.last_seen.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_ts is None:
        return []

    rows = session.execute(
        select(
            UserDef.username,
            ProjectCodeDef.project_code,
            UserProjQueueStatus.running_jobs,
            UserProjQueueStatus.pending_jobs,
            UserProjQueueStatus.held_jobs,
            UserProjQueueStatus.cores_allocated,
            UserProjQueueStatus.cores_pending,
            UserProjQueueStatus.cores_held,
            UserProjQueueStatus.gpus_allocated,
            UserProjQueueStatus.gpus_pending,
            UserProjQueueStatus.gpus_held,
            UserProjQueueStatus.nodes_allocated,
            UserProjQueueStatus.timestamp,
            UserProjQueueStatus.last_seen,
        )
        .join(UserDef, UserProjQueueStatus.user_id == UserDef.user_id)
        .join(ProjectCodeDef,
              UserProjQueueStatus.project_code_id == ProjectCodeDef.project_code_id)
        .where(UserProjQueueStatus.queue_id == queue_id,
               UserProjQueueStatus.last_seen == latest_ts)
        .order_by(UserProjQueueStatus.running_jobs.desc(),
                  UserDef.username)
    ).all()

    return [
        {
            'username': r.username,
            'project_code': r.project_code,
            'running_jobs': r.running_jobs,
            'pending_jobs': r.pending_jobs,
            'held_jobs': r.held_jobs,
            'cores_allocated': r.cores_allocated,
            'cores_pending': r.cores_pending,
            'cores_held': r.cores_held,
            'gpus_allocated': r.gpus_allocated,
            'gpus_pending': r.gpus_pending,
            'gpus_held': r.gpus_held,
            'nodes_allocated': r.nodes_allocated,
            # Templates and existing callers refer to `'timestamp'`. Under
            # span semantics that key now carries the *last_seen* value
            # (the most recent observation), so the rendered "as of" time
            # is current. `'first_seen'` exposes the span start for
            # callers that want span duration.
            'timestamp': r.last_seen,
            'first_seen': r.timestamp,
            'last_seen': r.last_seen,
        }
        for r in rows
    ]


def get_user_proj_timeseries(
    session: Session,
    *,
    system: str,
    queue_name: Optional[str] = None,
    start_date: datetime,
    end_date: datetime,
    state: str,
    metric: str,
    group_by: str,
    top_n: int = 15,
    rank_by: str = 'peak',
) -> Dict[str, Any]:
    """Top-N + Others time series for one queue, or one system.

    Scope is determined by ``queue_name``:

    - ``queue_name='main'`` → filter on a single ``(system, queue)``,
      one ``UserProjQueueStatus`` row per (timestamp, user, project)
      already aggregated to that queue.
    - ``queue_name=None`` → sum across **all** queues for ``system``,
      so per-tick values reflect the user/project's load on the
      whole system.

    `(state, metric)` selects the underlying column via
    ``_STATE_METRIC_TO_COLUMN``:

        state ∈ {'running', 'pending', 'held'}
        metric ∈ {'jobs', 'cores', 'gpus', 'nodes'}

    `('pending', 'nodes')` and `('held', 'nodes')` are invalid — the
    schema has no `nodes_pending` / `nodes_held` columns. Callers
    (route, UI) clamp these to a valid combo before reaching here; this
    helper raises ``ValueError`` as a defensive belt-and-braces check.

    Returns a dict shaped for stacked-area rendering::

        {
          'dates':  [datetime, ...],                # sorted ascending (5-min ticks)
          'series': [
            {'label': 'Others', 'values': [int, ...]},   # iff > top_n series
            {'label': 'alice',  'values': [int, ...]},   # smallest named (peak)
            ...,
            {'label': 'zach',   'values': [int, ...]},   # largest named (peak)
          ],
          'state':          'running' | 'pending' | 'held',
          'metric':         'jobs' | 'cores' | 'gpus' | 'nodes',
          'metric_label':   'Pending cores' / 'Running GPUs' / ...
          'group_by':       'user' | 'project',
          'group_by_label': 'User' | 'Project code',
          'has_gpus':       bool,   # any GPU activity in scope+window
        }

    Series ordering matches ``get_disk_usage_timeseries_by_user``: Others
    first (so it stacks at the bottom under a neutral colour), then named
    series smallest-to-largest by the rank value so the largest sits on
    top of the stack.

    ``rank_by`` controls top-N selection:

    - ``'peak'`` (default): rank by **MAX over the window** so a brief
      spike still earns a slot — useful for catching short pending/held
      bursts an operator might otherwise miss.
    - ``'current'``: rank by the value at the **latest timestamp** in the
      window — answers "who is loading the system right now", at the cost
      of dropping a label whose run already ended within the window.
    """
    if state not in _VALID_STATES:
        raise ValueError(
            f'state must be one of {_VALID_STATES}, got {state!r}'
        )
    if metric not in _VALID_METRICS:
        raise ValueError(
            f'metric must be one of {_VALID_METRICS}, got {metric!r}'
        )
    if (state, metric) not in _STATE_METRIC_TO_COLUMN:
        raise ValueError(
            f'(state={state!r}, metric={metric!r}) is not a valid combination '
            f'(no schema column); valid combos: {sorted(_STATE_METRIC_TO_COLUMN)}'
        )
    if group_by not in _VALID_GROUP_BY:
        raise ValueError(
            f'group_by must be one of {_VALID_GROUP_BY}, got {group_by!r}'
        )
    if rank_by not in _VALID_RANK_BY:
        raise ValueError(
            f'rank_by must be one of {_VALID_RANK_BY}, got {rank_by!r}'
        )

    column_name = _STATE_METRIC_TO_COLUMN[(state, metric)]
    metric_label = _label_for(state, metric)
    group_by_label = 'User' if group_by == 'user' else 'Project code'

    empty = {
        'dates': [], 'series': [],
        'state': state, 'metric': metric, 'metric_label': metric_label,
        'group_by': group_by, 'group_by_label': group_by_label,
        'has_gpus': False,
    }

    # Resolve scope: single queue if queue_name given, else system-wide.
    if queue_name is not None:
        scope_id = _resolve_queue_id(session, system, queue_name)
        scope_filter = UserProjQueueStatus.queue_id == scope_id
    else:
        scope_id = _resolve_system_id(session, system)
        scope_filter = UserProjQueueStatus.system_id == scope_id
    if scope_id is None:
        return empty

    # Probe whether the scope has ANY GPU activity in the window. The UI
    # uses this to suppress the GPUs metric button on CPU-only queues
    # (or any system+window with no GPU rollup activity) — purely
    # data-driven, no queue/system whitelist. Span overlap predicate:
    # last_seen >= start AND timestamp <= end.
    gpu_max = session.execute(
        select(
            func.coalesce(func.max(UserProjQueueStatus.gpus_allocated), 0),
            func.coalesce(func.max(UserProjQueueStatus.gpus_pending), 0),
            func.coalesce(func.max(UserProjQueueStatus.gpus_held), 0),
        )
        .where(
            scope_filter,
            UserProjQueueStatus.last_seen >= start_date,
            UserProjQueueStatus.timestamp <= end_date,
        )
    ).one()
    has_gpus = any(v > 0 for v in gpu_max)

    metric_col = getattr(UserProjQueueStatus, column_name)
    if group_by == 'user':
        label_col = UserDef.username.label('label')
        join_target, join_cond = UserDef, UserProjQueueStatus.user_id == UserDef.user_id
    else:
        label_col = ProjectCodeDef.project_code.label('label')
        join_target, join_cond = (
            ProjectCodeDef,
            UserProjQueueStatus.project_code_id == ProjectCodeDef.project_code_id,
        )

    # Build the canonical tick timeline from the parent status table for
    # this system. Spans are exploded onto these ticks: each span
    # contributes its constant value to every tick in [first_seen,
    # last_seen] ∩ [start_date, end_date].
    parent = _PARENT_STATUS_BY_SYSTEM.get(system)
    if parent is None:
        return empty
    tick_rows = session.execute(
        select(parent.timestamp)
        .where(parent.timestamp >= start_date,
               parent.timestamp <= end_date)
        .order_by(parent.timestamp)
    ).all()
    tick_list = [r[0] for r in tick_rows]
    if not tick_list:
        return empty
    n_ticks = len(tick_list)
    ticks = np.array(tick_list, dtype='datetime64[us]')
    start_dt64 = np.datetime64(start_date, 'us')
    end_dt64 = np.datetime64(end_date, 'us')

    # Fetch span rows overlapping the window and join the chosen label.
    span_rows = session.execute(
        select(
            UserProjQueueStatus.timestamp,
            UserProjQueueStatus.last_seen,
            label_col,
            metric_col.label('value'),
        )
        .join(join_target, join_cond)
        .where(
            scope_filter,
            UserProjQueueStatus.last_seen >= start_date,
            UserProjQueueStatus.timestamp <= end_date,
        )
    ).all()

    if not span_rows:
        return empty

    per_label: Dict[str, np.ndarray] = {}
    for first_seen, last_seen, label, value in span_rows:
        v = int(value or 0)
        if v == 0:
            continue
        i_first = int(np.searchsorted(
            ticks, np.datetime64(max(first_seen, start_date), 'us')))
        i_last = int(np.searchsorted(
            ticks, np.datetime64(min(last_seen, end_date), 'us'),
            side='right')) - 1
        if i_first > i_last or i_first >= n_ticks or i_last < 0:
            continue
        arr = per_label.get(label)
        if arr is None:
            arr = np.zeros(n_ticks, dtype=np.int64)
            per_label[label] = arr
        # Collapsed scopes (e.g. group_by='user' across multiple
        # queues/projects) sum naturally via in-place addition.
        arr[i_first:i_last + 1] += v

    if not per_label:
        return empty

    dates = list(tick_list)

    # Two ranking modes — peak catches brief spikes, current answers
    # "who's hot right now".
    if rank_by == 'current':
        rank_key = lambda kv: int(kv[1][-1])
    else:  # 'peak'
        rank_key = lambda kv: int(kv[1].max())
    ranked = sorted(per_label.items(), key=rank_key, reverse=True)
    top = ranked[:top_n]
    rest = ranked[top_n:]

    series: List[Dict[str, Any]] = []
    if rest:
        others_arr = np.zeros(n_ticks, dtype=np.int64)
        for _label, arr in rest:
            others_arr += arr
        series.append({'label': 'Others', 'values': others_arr.tolist()})
    for label, arr in reversed(top):
        series.append({
            'label':  label,
            'values': arr.tolist(),
        })

    return {
        'dates': dates,
        'series': series,
        'state': state,
        'metric': metric,
        'metric_label': metric_label,
        'group_by': group_by,
        'group_by_label': group_by_label,
        'has_gpus': has_gpus,
    }
