"""
Rolling window usage queries for SAM.

Provides get_project_rolling_usage() — a public, project-centric interface for
computing trailing-N-day charge totals against prorated allocation amounts.

trailing_window_charges() sums a trailing window per anchor through the shared
batch_charges builder; fstree_access.py imports it for its threshold accounts.

Formula (NDayUsagePeriod.java):
    duration_days   = max((alloc_end - alloc_start).days - 1, 1)
    prorated_alloc  = window_days × allocated / duration_days
    pct_of_prorated = window_charges / prorated_alloc × 100
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload, selectinload

from sam.accounting.calculator import batch_charges
from sam.enums import ResourceTypeName
from sam.projects.projects import Project
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.resources.resources import Resource, ResourceType


def trailing_window_charges(session: Session, infos: List[Dict], window_days: int, now: datetime,
                            *, subtree: bool) -> Dict[Any, float]:
    """``{info['key']: charges + adjustments}`` over the trailing window, clamped to each allocation.

    Infos are `batch_charges` infos whose ``start_date`` / ``end_date`` are the allocation's dates.
    """
    # Midnight, so the first day counts whole: a mid-day bound against the DATE column counted
    # it on MySQL for some query shapes and never on Postgres (2026-10-03).
    window_start = (now - timedelta(days=window_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    clamped = [{**info, 'start_date': max(window_start, info['start_date']),
                'end_date': min(now, info['end_date'] or now)} for info in infos]
    sums = batch_charges(session, clamped, subtree=subtree)
    return {key: sum(c['charges_by_type'].values()) + c['adjustment'] for key, c in sums.items()}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_project_rolling_usage(
    session: Session,
    projcode: str,
    windows: Optional[List[int]] = None,
    resource_name: Optional[str] = None,
) -> Dict[str, Dict]:
    """
    Rolling window charge data for a project's active HPC/DAV allocations.

    Handles both leaf projects (direct account charges) and parent projects
    with children (MPTT subtree rollup — same algorithm as the fairshare tree).
    Adds charge adjustments within each clamped window.

    Args:
        session:       SQLAlchemy session.
        projcode:      Project code (e.g. ``'NMMM0003'``).
        windows:       List of trailing-day windows to compute.  Default ``[30, 90]``.
        resource_name: Optional filter to a single resource (e.g. ``'Derecho'``).

    Returns:
        Dict keyed by resource name.  Each entry contains::

            allocated        – allocation amount (float, AU)
            start_date       – allocation start (datetime)
            end_date         – allocation end (datetime | None)
            is_inheriting    – True if this project's allocation is a child node
                               in a shared-allocation tree (charges roll up
                               across peers)
            root_projcode    – projcode of the master allocation's project when
                               inheriting; None otherwise
            windows          – dict keyed by window_days (int), each with:
                charges          – total charges in window (float, AU).  When
                                   ``is_inheriting`` is True this is the
                                   *pool* burn (root allocation's project
                                   subtree); otherwise this project's own.
                self_charges     – this project's own contribution to the
                                   pool when inheriting; None otherwise.
                prorated_alloc   – prorated allocation for this period (float, AU)
                pct_of_prorated  – charges / prorated_alloc × 100 (float, %)
                                   — pool burn vs prorated when inheriting,
                                   so this is the runway-meaningful number.
                threshold_pct    – configured threshold % from account (int | None)
                                   30d window -> account.first_threshold
                                   90d window -> account.second_threshold
                use_limit        – AU ceiling at threshold (int | None)
                pct_of_limit     – charges / use_limit × 100 (float | None)

        Returns ``{}`` if the project does not exist or has no eligible allocations.

    Example::

        usage = get_project_rolling_usage(session, 'NMMM0003')
        for res, data in usage.items():
            w30 = data['windows'][30]
            print(f"{res}: 30d {w30['pct_of_prorated']:.1f}% of prorated allocation")
    """
    if windows is None:
        windows = [30, 90]

    now = datetime.now()

    # ------------------------------------------------------------------
    # Load project with accounts -> allocations + resource -> resource_type
    # ------------------------------------------------------------------
    project = (
        session.query(Project)
        .options(
            selectinload(Project.accounts)
            .joinedload(Account.resource)
            .joinedload(Resource.resource_type),
            selectinload(Project.accounts)
            .selectinload(Account.allocations),
        )
        .filter(Project.projcode == projcode)
        .first()
    )
    if not project:
        return {}

    # ------------------------------------------------------------------
    # Collect eligible accounts (non-deleted, HPC or DAV, active alloc)
    # ------------------------------------------------------------------
    # account_id -> metadata for result assembly
    account_meta: Dict[int, Dict] = {}
    # trailing_window_charges infos keyed by account_id: leaf and subtree anchors yield *self*
    # charges; pool anchors are an inheriting account's *root* project subtree.
    leaf_infos: List[Dict] = []
    subtree_infos: List[Dict] = []
    pool_infos: List[Dict] = []

    for acct in project.accounts:
        if acct.deleted:
            continue
        res = acct.resource
        if res is None:
            continue
        if res.resource_type is None or not ResourceTypeName.is_compute(res.resource_type.resource_type):
            continue
        if resource_name and res.resource_name != resource_name:
            continue

        # Find the active allocation
        active_alloc: Optional[Allocation] = None
        for alloc in acct.allocations:
            if alloc.is_active:
                active_alloc = alloc
                break
        if active_alloc is None:
            continue

        aid = acct.account_id
        anchor = {'key': aid, 'account_id': aid, 'resource_id': acct.resource_id,
                  'activity_type': res.activity_type,
                  'start_date': active_alloc.start_date, 'end_date': active_alloc.end_date}

        # Pool detection — when the active allocation is inheriting, walk to
        # the root allocation and prepare a parallel subtree query against
        # the *root project's* tree, so this window's `charges` reflects
        # pool burn (the rate that actually depletes the shared allocation).
        # Mirrors Project.get_detailed_allocation_usage (projects.py:660-673).
        is_inheriting = False
        root_projcode = None
        if active_alloc.is_inheriting:
            root_alloc = active_alloc.root
            root_account = root_alloc.account if root_alloc is not None else None
            root_project = root_account.project if root_account is not None else None
            if (root_project is not None
                    and root_project.tree_root is not None
                    and root_project.tree_left is not None
                    and root_project.tree_right is not None):
                is_inheriting = True
                root_projcode = root_project.projcode
                pool_infos.append({**anchor, 'tree_root': root_project.tree_root,
                                   'tree_left': root_project.tree_left,
                                   'tree_right': root_project.tree_right})

        account_meta[aid] = {
            'resource_name':    res.resource_name,
            'allocated':        float(active_alloc.amount) if active_alloc.amount is not None else 0.0,
            'start_date':       active_alloc.start_date,
            'end_date':         active_alloc.end_date,
            'is_inheriting':    is_inheriting,
            'root_projcode':    root_projcode,
            # Threshold percentages from account — may be None for most accounts.
            # first_threshold -> 30d window, second_threshold -> 90d window
            # (matching DefaultAccountStatusCalculator.java convention)
            'threshold_30': acct.first_threshold,
            'threshold_90': acct.second_threshold,
        }

        # Leaf vs. non-leaf determines self-charge rollup strategy.
        # project.is_leaf() uses NestedSetMixin (base.py:303): tree_right == tree_left + 1
        if project.is_leaf():
            leaf_infos.append(anchor)
        else:
            subtree_infos.append({**anchor, 'tree_root': project.tree_root,
                                  'tree_left': project.tree_left, 'tree_right': project.tree_right})

    if not account_meta:
        return {}

    # ------------------------------------------------------------------
    # Run window charge queries and assemble results
    # ------------------------------------------------------------------
    result: Dict[str, Dict] = {}

    for w in windows:
        # Self-charges: this project's own contribution (existing logic).
        self_charges_by_aid = trailing_window_charges(session, leaf_infos, w, now, subtree=False)
        self_charges_by_aid.update(trailing_window_charges(session, subtree_infos, w, now, subtree=True))
        # Pool charges: root allocation's project subtree, only for inheriting accounts.
        pool_charges_by_aid = trailing_window_charges(session, pool_infos, w, now, subtree=True)

        for aid, self_charges in self_charges_by_aid.items():
            meta = account_meta[aid]
            rname = meta['resource_name']
            inheriting = meta['is_inheriting']

            if rname not in result:
                result[rname] = {
                    'allocated':     meta['allocated'],
                    'start_date':    meta['start_date'],
                    'end_date':      meta['end_date'],
                    'is_inheriting': inheriting,
                    'root_projcode': meta['root_projcode'],
                    'windows':       {},
                }

            # `charges` is pool burn for inheriting allocations, this project's
            # own otherwise.  `self_charges_value` is the project-only number,
            # surfaced separately for "(N yours)" annotation in the UI.
            if inheriting:
                charges = pool_charges_by_aid.get(aid, 0.0)
                self_charges_value: Optional[float] = self_charges
            else:
                charges = self_charges
                self_charges_value = None

            alloc_start = meta['start_date']
            alloc_end   = meta['end_date']
            allocated   = meta['allocated']

            if alloc_start is not None and allocated > 0:
                alloc_end_dt  = alloc_end or now
                duration_days = max((alloc_end_dt - alloc_start).days - 1, 1)
                prorated      = w * allocated / duration_days
                pct           = round(charges / prorated * 100.0, 1) if prorated > 0 else 0.0
            else:
                prorated = 0.0
                pct      = 0.0

            # Threshold limit for this window (only defined for w=30 and w=90)
            threshold_key = {30: 'threshold_30', 90: 'threshold_90'}.get(w)
            threshold_pct = meta.get(threshold_key) if threshold_key else None
            if threshold_pct is not None and prorated > 0:
                use_limit  = round(prorated * threshold_pct / 100.0)
                pct_of_lim = round(charges / (prorated * threshold_pct / 100.0) * 100.0, 1)
            else:
                use_limit  = None
                pct_of_lim = None

            result[rname]['windows'][w] = {
                'charges':         charges,
                'self_charges':    self_charges_value,  # None when not inheriting
                'prorated_alloc':  prorated,
                'pct_of_prorated': pct,
                'threshold_pct':   threshold_pct,   # None when not configured
                'use_limit':       use_limit,        # AU ceiling; None when no threshold
                'pct_of_limit':    pct_of_lim,       # % of limit used; None when no threshold
            }

    return result
