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


# ============================================================================
# The freshness gate (read side)
# ============================================================================

import os
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from sam.accounting.adjustments import ChargeAdjustment
from sam.accounting.allocations import AllocationTransaction
from sam.summaries.allocation_state import AccountAllocationState

_TRUE = ('1', 'true', 'yes', 'on')


def _raw(key: str, default):
    """Flask app config inside an app context, else the environment."""
    try:
        from flask import current_app
        return current_app.config.get(key, os.environ.get(key, default))
    except (RuntimeError, ImportError):
        return os.environ.get(key, default)


def _config_bool(key: str, default: bool = False) -> bool:
    value = _raw(key, default)
    return value if isinstance(value, bool) else str(value).strip().lower() in _TRUE


def _config_int(key: str, default: int) -> int:
    try:
        return int(_raw(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ReadModelConfig:
    enabled: bool
    max_age: timedelta

    @classmethod
    def from_environment(cls) -> 'ReadModelConfig':
        return cls(enabled=_config_bool('READ_MODEL_ENABLED', False),
                   max_age=timedelta(seconds=_config_int('READ_MODEL_MAX_AGE', 7200)))


@dataclass(frozen=True)
class Lookup:
    """``rows`` (by ``allocation_id``) when the read-model may serve the scope, else None with why."""
    rows: Optional[Dict[int, AccountAllocationState]]
    reason: str

    @property
    def by_account(self) -> Dict[int, AccountAllocationState]:
        return {r.account_id: r for r in (self.rows or {}).values()}


def db_now(session: Session) -> datetime:
    """The database clock. The ON UPDATE stamps the gate compares against use
    it; the app clock only agrees with it where the DB server runs Mountain."""
    return session.execute(select(func.now())).scalar()


def _scope_accounts(session: Session, resource_ids, project_ids):
    """Subquery of account ids in scope. A project scope spans its whole MPTT
    tree: subtree rollups and shared-pool roots both live there."""
    q = select(Account.account_id)
    if resource_ids is not None:
        q = q.where(Account.resource_id.in_(list(resource_ids)))
    if project_ids is not None:
        ids = list(project_ids)
        roots = select(Project.tree_root).where(Project.project_id.in_(ids),
                                                Project.tree_root.isnot(None))
        tree = select(Project.project_id).where(
            or_(Project.tree_root.in_(roots), Project.project_id.in_(ids)))
        q = q.where(Account.project_id.in_(tree))
    return q


def structural_watermark(session: Session, *, resource_ids=None,
                         project_ids=None) -> Optional[datetime]:
    """The newest structural change in scope, or None when nothing is stamped.

    Columns: allocation create/modify, allocation_transaction create, account
    create/modify, project modify, and charge_adjustment.adjustment_date (the
    table has no creation stamp, so a back-dated adjustment waits for the
    hourly pass). Charge accrual is deliberately not a signal.
    """
    accts = _scope_accounts(session, resource_ids, project_ids)
    stamps = []
    stamps += session.execute(select(func.max(Account.modified_time),
                                     func.max(Account.creation_time))
                              .where(Account.account_id.in_(accts))).one()
    stamps += session.execute(select(func.max(Allocation.modified_time),
                                     func.max(Allocation.creation_time))
                              .where(Allocation.account_id.in_(accts))).one()
    stamps += session.execute(select(func.max(AllocationTransaction.creation_time))
                              .join(Allocation, Allocation.allocation_id ==
                                    AllocationTransaction.allocation_id)
                              .where(Allocation.account_id.in_(accts))).one()
    stamps += session.execute(select(func.max(ChargeAdjustment.adjustment_date))
                              .where(ChargeAdjustment.account_id.in_(accts))).one()
    projects = select(Account.project_id).where(Account.account_id.in_(accts))
    stamps += session.execute(select(func.max(Project.modified_time))
                              .where(Project.project_id.in_(projects))).one()
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def fresh_state(session: Session, *, resource_ids=None, project_ids=None,
                as_of: Optional[datetime] = None) -> Lookup:
    """Rows for the scope if the read-model may serve it right now.

    Falls back (``rows is None``) when the flag is off, ``as_of`` is not today,
    the scope has no rows, the oldest row is older than ``READ_MODEL_MAX_AGE``,
    or a structural change in scope is at least as new as the refresh.
    """
    cfg = ReadModelConfig.from_environment()
    if not cfg.enabled:
        return Lookup(None, 'disabled')
    if as_of is not None and as_of.date() != date.today():
        return Lookup(None, 'not-today')

    q = session.query(AccountAllocationState)
    if resource_ids is not None:
        q = q.filter(AccountAllocationState.resource_id.in_(list(resource_ids)))
    if project_ids is not None:
        q = q.filter(AccountAllocationState.project_id.in_(list(project_ids)))
    rows = q.all()
    if not rows:
        return Lookup(None, 'no-rows')

    oldest = min(r.refreshed_at for r in rows)
    if db_now(session) - oldest > cfg.max_age:
        return Lookup(None, 'too-old')
    stamp = structural_watermark(session, resource_ids=resource_ids,
                                 project_ids=project_ids)
    if stamp is not None and stamp >= oldest:
        return Lookup(None, 'structural-change')
    return Lookup({r.allocation_id: r for r in rows}, 'ok')


def read_model_rows_for(session: Session, project: Project) -> Dict[int, AccountAllocationState]:
    """Rows by allocation_id for an API route serializing one project's
    accounts through ``AllocationWithUsageSchema``; empty means live.

    Leaf projects only: the schema sums the account, the row the subtree,
    and the two agree only when the subtree is the account.
    """
    if not project.is_leaf():
        return {}
    return fresh_state(session, project_ids=[project.project_id]).rows or {}
