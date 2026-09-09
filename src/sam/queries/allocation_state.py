"""Projection for the allocation/usage read-model (``account_allocation_state``).

``project_allocation_state`` turns the dashboards' batched builder output into
table rows; the hourly ``refresh_allocation_state`` task is its only writer.
Readers arrive in a later commit (the freshness gate). Not exported from
``sam.queries``: consumers import this module directly.
Design: ``docs/plans/READ_MODEL.md``.
"""
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.queries.dashboard import (
    DashboardResource,
    _build_user_projects_resources_batched,
)

#: An allocation that ended within this many days still gets a row — the
#: dashboards' own selection rule (`_select_query_alloc`), mirrored so the
#: candidate set and the builder's per-account choice agree.
RECENT_DAYS = 90

#: Projects per builder call. Bounds the VALUES-CTE anchor list and the
#: per-chunk identity map; the projection is per project, so chunking
#: changes nothing but memory.
CHUNK_SIZE = 400


def candidate_projects(session: Session, now: datetime) -> List[Project]:
    """Projects with any non-deleted account whose allocation could be shown."""
    cutoff = now - timedelta(days=RECENT_DAYS)
    return (
        session.query(Project)
        .join(Account, Account.project_id == Project.project_id)
        .join(Allocation, Allocation.account_id == Account.account_id)
        .filter(
            Account.is_active,
            Allocation.deleted == False,  # noqa: E712 -- soft-delete flag, not an active check
            or_(Allocation.end_date.is_(None), Allocation.end_date >= cutoff),
        )
        .distinct()
        .order_by(Project.projcode)
        .all()
    )


def _chunks(items: List, size: int) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _is_current(res: DashboardResource, now: datetime) -> bool:
    start, end = res['start_date'], res['end_date']
    if start is None or start > now:
        return False
    return end is None or end >= now


def _row(project: Project, res: DashboardResource, now: datetime,
         resource_ids: Dict[str, int]) -> Dict:
    alloc_type = project.allocation_type
    panel = alloc_type.panel if alloc_type is not None else None
    facility = panel.facility if panel is not None else None
    rolling = None
    if res['rolling_30'] is not None or res['rolling_90'] is not None:
        rolling = {'30': res['rolling_30'], '90': res['rolling_90']}
    self_used = res['self_used'] if res['self_used'] is not None else res['used']
    return {
        'allocation_id': res['allocation_id'],
        'account_id': res['account_id'],
        'project_id': project.project_id,
        'projcode': project.projcode,
        'resource_id': resource_ids[res['resource_name']],
        'resource_name': res['resource_name'],
        'resource_type': res['resource_type'],
        'facility_name': facility.facility_name if facility is not None else None,
        'allocation_type': alloc_type.allocation_type if alloc_type is not None else None,
        'parent_allocation_id': res['parent_allocation_id'],
        'is_inheriting': bool(res['is_inheriting']),
        'root_projcode': res['root_projcode'],
        'allocated': float(res['allocated']),
        'self_used': float(self_used),
        'used': float(res['used']),
        'remaining': float(res['remaining']),
        'percent_used': float(res['percent_used']),
        'self_percent_used': res['self_percent_used'],
        'charges_by_type': {k: float(v) for k, v in res['charges_by_type'].items()},
        'adjustments': float(res['adjustments'] or 0.0),
        'activity_date': res['activity_date'],
        'rolling_windows': rolling,
        'start_date': res['start_date'],
        'end_date': res['end_date'],
        'is_current': _is_current(res, now),
    }


def project_allocation_state(session: Session, *, now: datetime,
                             projects: Optional[List[Project]] = None) -> List[Dict]:
    """Row dicts for every allocation the dashboards would show, as of ``now``.

    ``projects`` narrows the projection (tests, parity checks); the default is
    every candidate project.
    """
    if projects is None:
        projects = candidate_projects(session, now)
    resource_ids = dict(session.query(Resource.resource_name, Resource.resource_id).all())
    rows: List[Dict] = []
    for chunk in _chunks(projects, CHUNK_SIZE):
        by_project = _build_user_projects_resources_batched(session, chunk, active_at=now)
        for project in chunk:
            for res in by_project.get(project.project_id, []):
                if res['allocation_id'] is None:
                    continue
                rows.append(_row(project, res, now, resource_ids))
    return rows
