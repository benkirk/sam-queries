"""
Query helpers for ``user_proj_queue_status`` (per-user / per-project /
per-queue rollups).

Phase B reads only. Aggregations are queue-scoped — every helper takes
``system`` + ``queue_name`` so results plug directly into the existing
queue drill-down view (``status_dashboard.queue_history``).
"""

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
