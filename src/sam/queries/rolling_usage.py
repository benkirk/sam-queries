"""
Rolling window usage queries for SAM.

Provides get_project_rolling_usage() — a public, project-centric interface for
computing trailing-N-day charge totals against prorated allocation amounts.

The core SQL helpers (_query_window_charges, _query_window_subtree_charges) are
shared with fstree_access.py, which imports them from here.  The fstree path is
unchanged in behavior; it continues to call these helpers only for the small
number of accounts that have threshold percentages configured.

Formula (NDayUsagePeriod.java):
    duration_days   = max((alloc_end - alloc_start).days - 1, 1)
    prorated_alloc  = window_days × allocated / duration_days
    pct_of_prorated = window_charges / prorated_alloc × 100
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload, selectinload

from sam.enums import ResourceTypeName
from sam.projects.projects import Project
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.resources.resources import Resource, ResourceType


# ---------------------------------------------------------------------------
# Module-private SQL helpers (shared with fstree_access)
# ---------------------------------------------------------------------------

def _query_window_charges(
    session: Session,
    account_ids: List[int],
    window_days: int,
    now: datetime,
    alloc_windows: Dict[int, tuple],
) -> Dict[int, float]:
    """
    Query total charges for a set of accounts over a trailing N-day window,
    clamped to each account's allocation date range.
    """
    if not account_ids:
        return {}

    window_start_global = now - timedelta(days=window_days)
    result: Dict[int, float] = {aid: 0.0 for aid in account_ids}

    rows_sql = ', '.join(
        f'ROW({aid}, :ws{i}, :we{i})'
        for i, aid in enumerate(account_ids)
    )
    params: Dict[str, Any] = {}
    for i, aid in enumerate(account_ids):
        alloc_start, alloc_end = alloc_windows.get(aid, (now, now))
        clamped_start = max(window_start_global, alloc_start)
        clamped_end   = min(now, alloc_end or now)
        params[f'ws{i}'] = clamped_start
        params[f'we{i}'] = clamped_end

    for table, col in [
        ('comp_charge_summary', 'activity_date'),
        ('dav_charge_summary',  'activity_date'),
    ]:
        sql = text(f"""
            WITH w (account_id, ws, we) AS (VALUES {rows_sql})
            SELECT w.account_id, SUM(COALESCE(cs.charges, 0))
            FROM {table} cs
            JOIN w ON cs.account_id = w.account_id
                   AND cs.{col} >= w.ws
                   AND cs.{col} <= w.we
            GROUP BY w.account_id
        """)
        for aid, amount in session.execute(sql, params).all():
            if amount:
                result[aid] += float(amount)

    adj_sql = text(f"""
        WITH w (account_id, ws, we) AS (VALUES {rows_sql})
        SELECT w.account_id, SUM(COALESCE(ca.amount, 0))
        FROM charge_adjustment ca
        JOIN w ON ca.account_id = w.account_id
               AND ca.adjustment_date >= w.ws
               AND ca.adjustment_date <= w.we
        GROUP BY w.account_id
    """)
    for aid, amount in session.execute(adj_sql, params).all():
        if amount:
            result[aid] += float(amount)

    return result


def _query_window_subtree_charges(
    session: Session,
    subtree_accts: Dict[int, Dict],
    window_days: int,
    now: datetime,
    alloc_windows: Dict[int, tuple],
) -> Dict[int, float]:
    """
    Query total charges for a set of non-leaf accounts over a trailing N-day window,
    using MPTT subtree rollup so that descendant project charges are included.

    Parallel to _query_window_charges() but joins through project tree coordinates
    (tree_root/tree_left/tree_right) instead of direct account_id, matching the
    pattern used by batch_get_subtree_charges().

    Args:
        subtree_accts: Dict mapping account_id → alloc_info dict (must contain
                       tree_root, tree_left, tree_right, resource_id keys).
        window_days:   Trailing window length (30 or 90).
        now:           Current datetime.
        alloc_windows: Dict mapping account_id → (alloc_start, alloc_end) for clamping.
    """
    if not subtree_accts:
        return {}

    window_start_global = now - timedelta(days=window_days)
    result: Dict[int, float] = {aid: 0.0 for aid in subtree_accts}

    # Build VALUES rows: (anchor_key=index, tree_root, tree_left, tree_right, resource_id, ws, we)
    # anchor_key is the positional index; mapped back to account_id via idx_to_aid.
    entries = list(subtree_accts.items())  # [(account_id, alloc_info), ...]
    idx_to_aid: Dict[int, int] = {}
    values_parts = []
    params: Dict[str, Any] = {}

    for i, (aid, info) in enumerate(entries):
        idx_to_aid[i] = aid
        alloc_start, alloc_end = alloc_windows.get(aid, (now, now))
        clamped_start = max(window_start_global, alloc_start)
        clamped_end   = min(now, alloc_end or now)
        values_parts.append(f'ROW(:ak{i}, :tr{i}, :tl{i}, :rr{i}, :ri{i}, :ws{i}, :we{i})')
        params[f'ak{i}'] = i
        params[f'tr{i}'] = info['tree_root']
        params[f'tl{i}'] = info['tree_left']
        params[f'rr{i}'] = info['tree_right']
        params[f'ri{i}'] = info['resource_id']
        params[f'ws{i}'] = clamped_start
        params[f'we{i}'] = clamped_end

    values_sql = ', '.join(values_parts)

    for table, col in [
        ('comp_charge_summary', 'activity_date'),
        ('dav_charge_summary',  'activity_date'),
    ]:
        sql = text(f"""
            WITH anchors (anchor_key, tree_root, tree_left, tree_right, resource_id, ws, we) AS (
                VALUES {values_sql}
            )
            SELECT a.anchor_key, SUM(COALESCE(cs.charges, 0))
            FROM {table} cs
            JOIN account acc ON cs.account_id  = acc.account_id
            JOIN project p   ON acc.project_id = p.project_id
            JOIN anchors a   ON p.tree_root      =  a.tree_root
                            AND p.tree_left      >= a.tree_left
                            AND p.tree_right     <= a.tree_right
                            AND acc.resource_id  =  a.resource_id
                            AND cs.{col}         >= a.ws
                            AND cs.{col}         <= a.we
            GROUP BY a.anchor_key
        """)
        for anchor_key, amount in session.execute(sql, params).all():
            if amount:
                result[idx_to_aid[anchor_key]] += float(amount)

    adj_sql = text(f"""
        WITH anchors (anchor_key, tree_root, tree_left, tree_right, resource_id, ws, we) AS (
            VALUES {values_sql}
        )
        SELECT a.anchor_key, SUM(COALESCE(ca.amount, 0))
        FROM charge_adjustment ca
        JOIN account acc ON ca.account_id  = acc.account_id
        JOIN project p   ON acc.project_id = p.project_id
        JOIN anchors a   ON p.tree_root      =  a.tree_root
                        AND p.tree_left      >= a.tree_left
                        AND p.tree_right     <= a.tree_right
                        AND acc.resource_id  =  a.resource_id
                        AND ca.adjustment_date >= a.ws
                        AND ca.adjustment_date <= a.we
        GROUP BY a.anchor_key
    """)
    for anchor_key, amount in session.execute(adj_sql, params).all():
        if amount:
            result[idx_to_aid[anchor_key]] += float(amount)

    return result


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
            windows          – dict keyed by window_days (int), each with:
                charges          – total charges in window (float, AU)
                prorated_alloc   – prorated allocation for this period (float, AU)
                pct_of_prorated  – charges / prorated_alloc × 100 (float, %)
                threshold_pct    – configured threshold % from account (int | None)
                                   30d window → account.first_threshold
                                   90d window → account.second_threshold
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
    # Load project with accounts → allocations + resource → resource_type
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
    # account_id → metadata for result assembly
    account_meta: Dict[int, Dict] = {}
    # account_id → (alloc_start, alloc_end) for window clamping
    alloc_windows: Dict[int, tuple] = {}
    # leaf account_ids (direct charge lookup)
    leaf_ids: List[int] = []
    # non-leaf account_id → MPTT info (subtree rollup)
    subtree_map: Dict[int, Dict] = {}

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
        alloc_windows[aid] = (active_alloc.start_date, active_alloc.end_date)
        account_meta[aid] = {
            'resource_name':    res.resource_name,
            'allocated':        float(active_alloc.amount) if active_alloc.amount is not None else 0.0,
            'start_date':       active_alloc.start_date,
            'end_date':         active_alloc.end_date,
            # Threshold percentages from account — may be None for most accounts.
            # first_threshold → 30d window, second_threshold → 90d window
            # (matching DefaultAccountStatusCalculator.java convention)
            'threshold_30': acct.first_threshold,
            'threshold_90': acct.second_threshold,
        }

        # Leaf vs. non-leaf determines charge rollup strategy.
        # project.is_leaf() uses NestedSetMixin (base.py:303): tree_right == tree_left + 1
        if project.is_leaf():
            leaf_ids.append(aid)
        else:
            subtree_map[aid] = {
                'tree_root':   project.tree_root,
                'tree_left':   project.tree_left,
                'tree_right':  project.tree_right,
                'resource_id': acct.resource_id,
            }

    if not account_meta:
        return {}

    # ------------------------------------------------------------------
    # Run window charge queries and assemble results
    # ------------------------------------------------------------------
    result: Dict[str, Dict] = {}

    for w in windows:
        window_charges: Dict[int, float] = {}
        if leaf_ids:
            window_charges.update(_query_window_charges(session, leaf_ids, w, now, alloc_windows))
        if subtree_map:
            window_charges.update(_query_window_subtree_charges(session, subtree_map, w, now, alloc_windows))

        for aid, charges in window_charges.items():
            meta = account_meta[aid]
            rname = meta['resource_name']

            if rname not in result:
                result[rname] = {
                    'allocated':  meta['allocated'],
                    'start_date': meta['start_date'],
                    'end_date':   meta['end_date'],
                    'windows':    {},
                }

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
                'prorated_alloc':  prorated,
                'pct_of_prorated': pct,
                'threshold_pct':   threshold_pct,   # None when not configured
                'use_limit':       use_limit,        # AU ceiling; None when no threshold
                'pct_of_limit':    pct_of_lim,       # % of limit used; None when no threshold
            }

    return result
