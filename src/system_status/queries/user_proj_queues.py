"""
Query helpers for ``user_proj_queue_status`` (per-user / per-project /
per-queue rollups).

Phase B reads only. Aggregations are queue-scoped — every helper takes
``system`` + ``queue_name`` so results plug directly into the existing
queue drill-down view (``status_dashboard.queue_history``).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from system_status.models import (
    ProjectCodeDef,
    QueueDef,
    System,
    UserDef,
    UserProjQueueStatus,
)


_VALID_METRICS = {
    'running_jobs': 'Running jobs',
    'pending_jobs': 'Pending jobs',
    'held_jobs':    'Held jobs',
}
_VALID_GROUP_BY = ('user', 'project')


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


def get_latest_user_proj_queue_snapshot(
    session: Session,
    *,
    system: str,
    queue_name: str,
) -> List[Dict[str, Any]]:
    """One row per ``(user, project_code)`` for the most recent tick of
    this queue.

    Returns a list of dicts shaped for direct template rendering:

        [{'username': 'benkirk', 'project_code': 'SCSG0001',
          'running_jobs': 3, 'pending_jobs': 0, 'held_jobs': 0,
          'cores_allocated': 256, 'cores_pending': 0, 'cores_held': 0,
          'gpus_allocated': 0, 'gpus_pending': 0, 'gpus_held': 0,
          'nodes_allocated': 4, 'timestamp': datetime(...)}, ...]

    Empty list if the queue is unknown or has never reported a snapshot.
    The ``_unknown_`` project bucket (jobs missing ``Account_Name``) is
    included as just another row.
    """
    queue_id = _resolve_queue_id(session, system, queue_name)
    if queue_id is None:
        return []

    latest_ts = session.execute(
        select(UserProjQueueStatus.timestamp)
        .where(UserProjQueueStatus.queue_id == queue_id)
        .order_by(UserProjQueueStatus.timestamp.desc())
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
        )
        .join(UserDef, UserProjQueueStatus.user_id == UserDef.user_id)
        .join(ProjectCodeDef,
              UserProjQueueStatus.project_code_id == ProjectCodeDef.project_code_id)
        .where(UserProjQueueStatus.queue_id == queue_id,
               UserProjQueueStatus.timestamp == latest_ts)
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
            'timestamp': r.timestamp,
        }
        for r in rows
    ]


def get_user_proj_queue_timeseries(
    session: Session,
    *,
    system: str,
    queue_name: str,
    start_date: datetime,
    end_date: datetime,
    metric: str,
    group_by: str,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Top-N + Others time series for one queue, ranked by peak in window.

    Returns a dict shaped for stacked-area rendering::

        {
          'dates':  [datetime, ...],                # sorted ascending (5-min ticks)
          'series': [
            {'label': 'Others', 'values': [int, ...]},   # iff > top_n series
            {'label': 'alice',  'values': [int, ...]},   # smallest named (peak)
            ...,
            {'label': 'zach',   'values': [int, ...]},   # largest named (peak)
          ],
          'metric_label':   'Running jobs' | 'Pending jobs' | 'Held jobs',
          'group_by_label': 'User' | 'Project code',
        }

    Series ordering matches ``get_disk_usage_timeseries_by_user``: Others
    first (so it stacks at the bottom under a neutral colour), then named
    series smallest-to-largest by peak value over the window so the
    largest sits on top of the stack.

    Ranking by **MAX over the window** (not latest tick) so a brief spike
    still earns a slot — important for queue load where short pending/held
    bursts are exactly what an operator wants to see.

    Empty input returns ``{'dates': [], 'series': [], 'metric_label': ...,
    'group_by_label': ...}``.
    """
    if metric not in _VALID_METRICS:
        raise ValueError(
            f'metric must be one of {sorted(_VALID_METRICS)}, got {metric!r}'
        )
    if group_by not in _VALID_GROUP_BY:
        raise ValueError(
            f'group_by must be one of {_VALID_GROUP_BY}, got {group_by!r}'
        )

    metric_label = _VALID_METRICS[metric]
    group_by_label = 'User' if group_by == 'user' else 'Project code'

    queue_id = _resolve_queue_id(session, system, queue_name)
    if queue_id is None:
        return {'dates': [], 'series': [],
                'metric_label': metric_label, 'group_by_label': group_by_label}

    metric_col = getattr(UserProjQueueStatus, metric)
    if group_by == 'user':
        label_col = UserDef.username.label('label')
        join_target, join_cond = UserDef, UserProjQueueStatus.user_id == UserDef.user_id
    else:
        label_col = ProjectCodeDef.project_code.label('label')
        join_target, join_cond = (
            ProjectCodeDef,
            UserProjQueueStatus.project_code_id == ProjectCodeDef.project_code_id,
        )

    # Sum the metric within each (timestamp, label) bucket — multiple
    # (user, project) rows may collapse to a single series row when
    # grouping by just one of those keys.
    from sqlalchemy import func
    rows = session.execute(
        select(
            UserProjQueueStatus.timestamp,
            label_col,
            func.sum(metric_col).label('value'),
        )
        .join(join_target, join_cond)
        .where(
            UserProjQueueStatus.queue_id == queue_id,
            UserProjQueueStatus.timestamp >= start_date,
            UserProjQueueStatus.timestamp <= end_date,
        )
        .group_by(UserProjQueueStatus.timestamp, label_col)
    ).all()

    if not rows:
        return {'dates': [], 'series': [],
                'metric_label': metric_label, 'group_by_label': group_by_label}

    per_label: Dict[str, Dict[datetime, int]] = {}
    timestamps: set = set()
    for ts, label, value in rows:
        timestamps.add(ts)
        per_label.setdefault(label, {})[ts] = int(value or 0)

    dates = sorted(timestamps)

    # Rank by peak value over the window (catches brief spikes).
    ranked = sorted(
        per_label.items(),
        key=lambda kv: max(kv[1].values()) if kv[1] else 0,
        reverse=True,
    )
    top = ranked[:top_n]
    rest = ranked[top_n:]

    series: List[Dict[str, Any]] = []
    if rest:
        others_values = [0] * len(dates)
        for _label, by_ts in rest:
            for i, d in enumerate(dates):
                others_values[i] += by_ts.get(d, 0)
        series.append({'label': 'Others', 'values': others_values})
    for label, by_ts in reversed(top):
        series.append({
            'label':  label,
            'values': [by_ts.get(d, 0) for d in dates],
        })

    return {
        'dates': dates,
        'series': series,
        'metric_label': metric_label,
        'group_by_label': group_by_label,
    }
