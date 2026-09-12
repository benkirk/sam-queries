"""Projection and freshness gate for the allocation/usage read-model
(``account_allocation_state``).

``project_allocation_state`` turns the dashboards' batched builder output into
table rows; the hourly ``refresh_allocation_state`` task is its only writer.
``fresh_state`` is the read side: rows for a scope when they are fresh, with
stale MPTT trees re-projected in memory. Not exported from ``sam.queries``:
consumers import this module directly. Design: ``docs/plans/READ_MODEL.md``.
"""
import contextvars
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.sqlcompat import sam_now
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

#: True while a projection runs. The batched builder consults the gate, so
#: without this a stale tree's re-projection would re-enter the gate and
#: recurse; a projection is always the live computation.
_PROJECTING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    'read_model_projecting', default=False)


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
    token = _PROJECTING.set(True)
    try:
        for chunk in _chunks(projects, CHUNK_SIZE):
            by_project = _build_user_projects_resources_batched(session, chunk, active_at=now)
            for project in chunk:
                for res in by_project.get(project.project_id, []):
                    if res['allocation_id'] is None:
                        continue
                    rows.append(_row(project, res, now, resource_ids))
    finally:
        _PROJECTING.reset(token)
    return rows


# ============================================================================
# The freshness gate (read side)
# ============================================================================

import os
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, union_all

from sam.accounting.adjustments import ChargeAdjustment
from sam.accounting.allocations import AllocationTransaction
from sam.summaries.allocation_state import AccountAllocationState

logger = logging.getLogger(__name__)

_TRUE = ('1', 'true', 'yes', 'on')

#: Called with every ``Lookup`` the gate returns. The webapp registers a
#: forwarder onto the request profile (``webapp.request_timing``); ``sam``
#: never imports ``webapp``, so the hook is registered from the other side.
_LOOKUP_OBSERVER: Optional[Callable[['Lookup'], None]] = None


def set_lookup_observer(fn: Optional[Callable[['Lookup'], None]]) -> None:
    global _LOOKUP_OBSERVER
    _LOOKUP_OBSERVER = fn


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
    #: More stale trees than this and the scope goes live: re-projecting
    #: every candidate project per request costs more than one live call.
    patch_max_trees: int

    @classmethod
    def from_environment(cls) -> 'ReadModelConfig':
        return cls(enabled=_config_bool('READ_MODEL_ENABLED', False),
                   max_age=timedelta(seconds=_config_int('READ_MODEL_MAX_AGE', 7200)),
                   patch_max_trees=_config_int('READ_MODEL_PATCH_MAX_TREES', 250))


@dataclass(frozen=True)
class Lookup:
    """``rows`` (by ``allocation_id``) when the read-model may serve the scope, else None with why.

    ``patched`` counts the MPTT trees re-projected in memory for this answer;
    ``stale_trees`` names them (tree = ``project.tree_root`` or the project id).
    """
    rows: Optional[Dict[int, AccountAllocationState]]
    reason: str
    patched: int = 0
    stale_trees: Tuple[int, ...] = ()

    @property
    def by_account(self) -> Dict[int, AccountAllocationState]:
        return {r.account_id: r for r in (self.rows or {}).values()}


def _noted(lookup: Lookup) -> Lookup:
    logger.debug('read-model %s patched=%d trees=%s', lookup.reason,
                 lookup.patched, lookup.stale_trees[:10])
    if _LOOKUP_OBSERVER is not None:
        _LOOKUP_OBSERVER(lookup)
    return lookup


def db_now(session: Session) -> datetime:
    """The database clock, naive like the stamps the gate compares it against;
    the app clock only agrees with it where the DB server runs Mountain."""
    return session.execute(select(sam_now())).scalar()


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


def _tree_stamps(session: Session, resource_ids, project_ids):
    """One statement: the DB clock plus the newest structural stamp per project.

    Returns ``(now, tree_of, stamp_of)``: ``tree_of`` maps every scope project
    to its MPTT tree (``tree_root`` or itself), ``stamp_of`` maps a tree to its
    newest stamp. The stale unit is the tree because a child's change moves
    the parent's subtree rollup and a pool root's members' ``used``.
    """
    accts = _scope_accounts(session, resource_ids, project_ids)
    projects = select(Account.project_id).where(Account.account_id.in_(accts))
    pid = Project.project_id.label('project_id')
    tree = func.coalesce(Project.tree_root, Project.project_id).label('tree')
    acct_proj = Project.project_id == Account.project_id
    alloc_acct = Account.account_id == Allocation.account_id
    in_scope = Account.account_id.in_(accts)

    def via_account(stamp):
        return (select(pid, tree, stamp.label('stamp'))
                .join_from(Account, Project, acct_proj).where(in_scope))

    def via_allocation(stamp):
        return (select(pid, tree, stamp.label('stamp'))
                .join_from(Allocation, Account, alloc_acct)
                .join(Project, acct_proj).where(in_scope))

    parts = [
        via_account(Account.modified_time),
        via_account(Account.creation_time),
        via_allocation(Allocation.modified_time),
        via_allocation(Allocation.creation_time),
        select(pid, tree, AllocationTransaction.creation_time.label('stamp'))
        .join_from(AllocationTransaction, Allocation,
                   Allocation.allocation_id == AllocationTransaction.allocation_id)
        .join(Account, alloc_acct).join(Project, acct_proj).where(in_scope),
        select(pid, tree, ChargeAdjustment.adjustment_date.label('stamp'))
        .join_from(ChargeAdjustment, Account,
                   Account.account_id == ChargeAdjustment.account_id)
        .join(Project, acct_proj).where(in_scope),
        # Over the scope's projects, so a project with no stamped account
        # still appears in tree_of.
        select(pid, tree, Project.modified_time.label('stamp'))
        .where(Project.project_id.in_(projects)),
    ]
    stamps = union_all(*parts).subquery('stamps')
    stmt = (select(sam_now(), stamps.c.project_id, stamps.c.tree,
                   func.max(stamps.c.stamp))
            .group_by(stamps.c.project_id, stamps.c.tree))
    result = session.execute(stmt).all()

    now = result[0][0] if result else db_now(session)
    tree_of: Dict[int, int] = {}
    stamp_of: Dict[int, datetime] = {}
    for _, project_id, tree_id, stamp in result:
        tree_of[project_id] = tree_id
        if stamp is not None and (tree_id not in stamp_of or stamp > stamp_of[tree_id]):
            stamp_of[tree_id] = stamp
    return now, tree_of, stamp_of


def structural_watermark(session: Session, *, resource_ids=None,
                         project_ids=None) -> Optional[datetime]:
    """The newest structural change in scope, or None when nothing is stamped.

    Columns: allocation create/modify, allocation_transaction create, account
    create/modify, project modify, and charge_adjustment.adjustment_date (the
    table has no creation stamp, so a back-dated adjustment waits for the
    hourly pass). Charge accrual is deliberately not a signal.
    """
    return max(_tree_stamps(session, resource_ids, project_ids)[2].values(), default=None)


def _patch(session: Session, rows, stale: Set[int], tree_of: Dict[int, int],
           resource_ids, project_ids, refreshed_at: datetime):
    """Table rows with every stale tree replaced by a fresh in-memory projection.

    The stale trees' table rows are dropped first, so an allocation the
    projection no longer produces (deleted, expired) leaves the answer. The
    transient rows are never added to the session; the hourly task remains
    the table's only writer.
    """
    wanted = set(project_ids) if project_ids is not None else None
    pids = [p for p, t in tree_of.items()
            if t in stale and (wanted is None or p in wanted)]
    fresh: List[Dict] = []
    if pids:
        projects = session.query(Project).filter(Project.project_id.in_(pids)).all()
        fresh = project_allocation_state(session, now=datetime.now(), projects=projects)

    kept = {r.allocation_id: r for r in rows
            if tree_of.get(r.project_id, r.project_id) not in stale}
    scope = set(resource_ids) if resource_ids is not None else None
    for d in fresh:
        if scope is not None and d['resource_id'] not in scope:
            continue
        values = {k: d.get(k) for k in AccountAllocationState.VALUE_COLUMNS}
        values['refreshed_at'] = refreshed_at
        kept[d['allocation_id']] = AccountAllocationState(
            allocation_id=d['allocation_id'], **values)
    return kept


def fresh_state(session: Session, *, resource_ids=None, project_ids=None,
                as_of: Optional[datetime] = None) -> Lookup:
    """Rows for the scope if the read-model may serve it right now.

    Falls back (``rows is None``) when the flag is off, ``as_of`` is not today,
    the scope has no rows, the oldest row is older than ``READ_MODEL_MAX_AGE``,
    more than ``READ_MODEL_PATCH_MAX_TREES`` trees changed since the refresh,
    or re-projecting a stale tree raised. A tree with a structural change at
    least as new as its rows' refresh is re-projected in memory and served.
    """
    if _PROJECTING.get():
        return Lookup(None, 'projecting')       # inner call: not the request's verdict
    cfg = ReadModelConfig.from_environment()
    if not cfg.enabled:
        return _noted(Lookup(None, 'disabled'))
    if as_of is not None and as_of.date() != date.today():
        return _noted(Lookup(None, 'not-today'))

    q = session.query(AccountAllocationState)
    if resource_ids is not None:
        q = q.filter(AccountAllocationState.resource_id.in_(list(resource_ids)))
    if project_ids is not None:
        q = q.filter(AccountAllocationState.project_id.in_(list(project_ids)))
    rows = q.all()
    if not rows:
        return _noted(Lookup(None, 'no-rows'))

    oldest = min(r.refreshed_at for r in rows)
    now, tree_of, stamp_of = _tree_stamps(session, resource_ids, project_ids)
    if now - oldest > cfg.max_age:
        return _noted(Lookup(None, 'too-old'))

    oldest_by_tree: Dict[int, datetime] = {}
    for r in rows:
        t = tree_of.get(r.project_id, r.project_id)
        oldest_by_tree[t] = min(r.refreshed_at, oldest_by_tree.get(t, r.refreshed_at))
    # A tree without rows compares against the scope's oldest refresh: that
    # admits a project created since the refresh, and excludes the retired
    # projects in a resource scope whose stamps predate every refresh.
    stale = {t for t, stamp in stamp_of.items()
             if stamp >= oldest_by_tree.get(t, oldest)}
    if not stale:
        return _noted(Lookup({r.allocation_id: r for r in rows}, 'ok'))
    trees = tuple(sorted(stale))
    if len(stale) > cfg.patch_max_trees:
        return _noted(Lookup(None, 'too-many-stale', stale_trees=trees))
    try:
        patched = _patch(session, rows, stale, tree_of, resource_ids, project_ids, now)
    except Exception:
        logger.warning('read-model patch failed for trees %s; serving live',
                       trees[:10], exc_info=True)
        return _noted(Lookup(None, 'patch-failed', stale_trees=trees))
    return _noted(Lookup(patched, 'ok-patched', patched=len(stale), stale_trees=trees))


def read_model_rows_for(session: Session, project: Project) -> Dict[int, AccountAllocationState]:
    """Rows by allocation_id for an API route serializing one project's
    accounts through ``AllocationWithUsageSchema``; empty means live."""
    return fresh_state(session, project_ids=[project.project_id]).rows or {}
