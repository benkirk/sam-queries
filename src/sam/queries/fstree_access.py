"""
FairShare Tree query functions for SAM.

Provides get_fstree_data() which reproduces the output of the legacy Java
`GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>` endpoint.

The data is organized as a hierarchical tree:
  fairShareTree → Facility → AllocationType → Project → Resource

with per-node fairshare percentages and, at the Resource level,
allocation balances, charge usage (including MPTT subtree rollup),
accountStatus, and active user rosters.

This data is consumed by the PBS batch scheduler to build job
fairshare trees and by LDAP tooling for account provisioning.

Design notes
------------
Query 1 (skeleton): fast JOIN-based query returning only projects with a current
  active allocation on an HPC/DAV resource.  Covers the Normal/Overspent/Threshold
  status cases.

Query 2 (lifecycle): targeted query for projects in the AllocationType tree that
  have no current active allocation — produces "Expired" (account exists, past
  allocation) and "No Account" (no account on this resource type) rows.  These are
  the minority; they carry allocationAmount=0, adjustedUsage=0, no users.

Query 3 (users): bulk active-user roster per account.

Charges via Project.batch_get_subtree_charges() (VALUES CTE, MPTT rollup).

accountStatus semantics (matching DefaultAccountStatusCalculator.java):
  1. "No Account"           — project in tree but no account on this resource
  2. "Expired"              — account exists, no current active allocation (has prior)
  3. "Overspent"            — adjustedUsage > allocationAmount
  4. "Exceed Two Thresholds"— both N-day windows exceeded
  5. "Exceed One Threshold" — one N-day window exceeded
  6. "Normal"               — default

Parent → child status propagation (pre-order): if a parent's accountStatus is
non-Normal, that status cascades to all children on the same resource.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# SQL Queries
# ---------------------------------------------------------------------------

# Query 1: Fast skeleton — projects with a CURRENT active allocation.
# Covers the vast majority (~95%+) of fstree rows.
# Returns one row per (project, resource, active-allocation) tuple.
#
# Key columns added vs original:
#   • p.tree_root/tree_left/tree_right — MPTT for subtree charge rollup
#   • p.parent_id                      — parent → child status propagation
#   • a.first_threshold/second_threshold — N-day threshold percentages
_SQL_FSTREE_SKELETON = text("""
    SELECT
        f.facility_id,
        f.facility_name,
        f.code                                                AS facility_code,
        f.description                                         AS facility_description,
        COALESCE(fr.fair_share_percentage,
                 f.fair_share_percentage)                     AS facility_fsp,
        at.allocation_type_id,
        at.allocation_type,
        at.fair_share_percentage                              AS type_fsp,
        p.project_id,
        p.projcode,
        p.active                                              AS project_active,
        p.parent_id,
        p.tree_root,
        p.tree_left,
        p.tree_right,
        a.account_id,
        a.cutoff_threshold,
        a.first_threshold,
        a.second_threshold,
        r.resource_id,
        r.resource_name,
        rt.resource_type,
        al.allocation_id,
        al.amount                                             AS allocation_amount,
        al.start_date,
        al.end_date
    FROM facility f
    JOIN panel             pa  ON (pa.facility_id        = f.facility_id
                                    AND pa.active         IS TRUE)
    JOIN allocation_type   at  ON (at.panel_id            = pa.panel_id
                                    AND at.active          IS TRUE)
    JOIN project           p   ON (p.allocation_type_id   = at.allocation_type_id
                                    AND p.active           IS TRUE)
    JOIN account           a   ON (a.project_id           = p.project_id
                                    AND a.deleted          IS FALSE)
    JOIN resources         r   ON (r.resource_id          = a.resource_id
                                    AND r.configurable     IS TRUE)
    JOIN resource_type     rt  ON (rt.resource_type_id    = r.resource_type_id
                                    AND rt.resource_type  IN ('HPC', 'DAV'))
    LEFT JOIN facility_resource fr
                               ON (fr.facility_id         = f.facility_id
                                    AND fr.resource_id    = r.resource_id)
    JOIN allocation        al  ON (al.account_id          = a.account_id
                                    AND al.deleted         IS FALSE
                                    AND al.start_date     <= NOW()
                                    AND (al.end_date IS NULL OR al.end_date >= NOW()))
    WHERE f.active IS TRUE
      AND (:resource IS NULL OR r.resource_name = :resource)
    ORDER BY f.facility_name, at.allocation_type, p.projcode, r.resource_name
""")

# Query 2: Lifecycle rows — "Expired" and "No Account".
# Fetches the minority of projects whose status is NOT driven by a current
# active allocation.  Runs after the skeleton so we know which (project, resource)
# pairs are already covered.
#
# Returns one row per (facility, alloc_type, project, resource) for:
#   a. Projects with an account on this resource but NO current active allocation
#      — "Expired" if they have any prior ended allocation, else omitted.
#   b. Projects in the AllocationType with NO account at all on HPC/DAV resources
#      — "No Account".
#
# "Waiting" (future allocation only) is explicitly excluded — legacy does not
# surface it in the fstree output.
_SQL_FSTREE_LIFECYCLE = text("""
    -- Part A: Expired — account exists, no current allocation, has prior allocation
    SELECT
        f.facility_name,
        f.code                                                AS facility_code,
        COALESCE(fr.fair_share_percentage,
                 f.fair_share_percentage)                     AS facility_fsp,
        at.allocation_type_id,
        at.allocation_type,
        at.fair_share_percentage                              AS type_fsp,
        p.project_id,
        p.projcode,
        p.active                                              AS project_active,
        p.parent_id,
        a.account_id,
        r.resource_name,
        'Expired'                                             AS lifecycle_status
    FROM facility f
    JOIN panel             pa  ON (pa.facility_id  = f.facility_id AND pa.active IS TRUE)
    JOIN allocation_type   at  ON (at.panel_id     = pa.panel_id  AND at.active IS TRUE)
    JOIN project           p   ON (p.allocation_type_id = at.allocation_type_id
                                    AND p.active    IS TRUE)
    JOIN account           a   ON (a.project_id    = p.project_id AND a.deleted IS FALSE)
    JOIN resources         r   ON (r.resource_id   = a.resource_id AND r.configurable IS TRUE)
    JOIN resource_type     rt  ON (rt.resource_type_id = r.resource_type_id
                                    AND rt.resource_type IN ('HPC', 'DAV'))
    LEFT JOIN facility_resource fr
                               ON (fr.facility_id  = f.facility_id AND fr.resource_id = r.resource_id)
    WHERE f.active IS TRUE
      AND (:resource IS NULL OR r.resource_name = :resource)
      -- No current active allocation
      AND NOT EXISTS (
          SELECT 1 FROM allocation al
          WHERE al.account_id = a.account_id AND al.deleted IS FALSE
            AND al.start_date <= NOW()
            AND (al.end_date IS NULL OR al.end_date >= NOW())
      )
      -- But has at least one prior ended allocation
      AND EXISTS (
          SELECT 1 FROM allocation al2
          WHERE al2.account_id = a.account_id AND al2.deleted IS FALSE
            AND al2.end_date < NOW()
          LIMIT 1
      )

    UNION ALL

    -- Part B: No Account — project in alloc type but no account on this resource type
    SELECT
        f.facility_name,
        f.code                                                AS facility_code,
        f.fair_share_percentage                               AS facility_fsp,
        at.allocation_type_id,
        at.allocation_type,
        at.fair_share_percentage                              AS type_fsp,
        p.project_id,
        p.projcode,
        p.active                                              AS project_active,
        p.parent_id,
        NULL                                                  AS account_id,
        :resource                                             AS resource_name,
        'No Account'                                          AS lifecycle_status
    FROM facility f
    JOIN panel             pa  ON (pa.facility_id  = f.facility_id AND pa.active IS TRUE)
    JOIN allocation_type   at  ON (at.panel_id     = pa.panel_id  AND at.active IS TRUE)
    JOIN project           p   ON (p.allocation_type_id = at.allocation_type_id
                                    AND p.active    IS TRUE)
    WHERE f.active IS TRUE
      AND :resource IS NOT NULL
      -- No account on the requested resource at all
      AND NOT EXISTS (
          SELECT 1 FROM account a2
          JOIN resources r2 ON r2.resource_id = a2.resource_id
            AND r2.configurable IS TRUE AND r2.resource_name = :resource
          WHERE a2.project_id = p.project_id AND a2.deleted IS FALSE
      )

    ORDER BY facility_name, allocation_type, projcode
""")

# Query 3a: Active users per account (for skeleton/Normal/Overspent rows).
# Filters to currently-active account_user records.
_SQL_FSTREE_USERS = text("""
    SELECT
        au.account_id,
        u.username,
        u.unix_uid
    FROM account_user  au
    JOIN users         u   ON (u.user_id              = au.user_id)
    JOIN account       a   ON (a.account_id           = au.account_id
                                AND a.deleted          IS FALSE)
    JOIN project       p   ON (p.project_id           = a.project_id
                                AND p.active           IS TRUE)
    JOIN resources     r   ON (r.resource_id          = a.resource_id
                                AND r.configurable     IS TRUE)
    JOIN resource_type rt  ON (rt.resource_type_id    = r.resource_type_id
                                AND rt.resource_type  IN ('HPC', 'DAV'))
    WHERE (au.end_date   IS NULL OR au.end_date   >= NOW())
      AND (au.start_date IS NULL OR au.start_date <= NOW())
      AND (:resource IS NULL OR r.resource_name = :resource)
    ORDER BY au.account_id, u.username
""")

# Query 3b: Users for Expired accounts — no date filter on account_user,
# matching legacy Java getUsersAssignedToProjectOnResource() which returned
# all users ever on the account regardless of end_date.
_SQL_FSTREE_EXPIRED_USERS = text("""
    SELECT
        au.account_id,
        u.username,
        u.unix_uid
    FROM account_user  au
    JOIN users         u   ON (u.user_id   = au.user_id)
    WHERE au.account_id IN :account_ids
    ORDER BY au.account_id, u.username
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alloc_type_name(facility_code: str, allocation_type: str) -> str:
    """
    Build the AllocationType name matching legacy Java FairShareTreeServiceJacksonImpl:

        name = facilityCode + "_" + allocationTypeDTO.getType().replaceAll("\\W", "")

    Example: code="C", type="CSL"              → "C_CSL"
             code="N", type="Director Reserve" → "N_DirectorReserve"
    """
    cleaned = re.sub(r'\W', '', allocation_type)
    return f'{facility_code}_{cleaned}'


def _compute_status(
    adjusted_usage: float,
    allocation_amount: Optional[float],
    first_threshold: Optional[int],
    second_threshold: Optional[int],
    alloc_start: Optional[datetime],
    alloc_end: Optional[datetime],
    window_charges_30: float,
    window_charges_90: float,
) -> str:
    """
    Compute accountStatus for rows with an active allocation.

    Priority (matching DefaultAccountStatusCalculator.java):
      1. "Overspent"             — total adjustedUsage > allocationAmount
      2. "Exceed Two Thresholds" — both N-day windows exceed per-account thresholds
      3. "Exceed One Threshold"  — exactly one N-day window exceeds its threshold
      4. "Normal"                — default

    N-day threshold formula (NDayUsagePeriod.java):
      threshold_alloc = P × allocationAmount / (duration_days − 1)
      use_limit       = threshold_alloc × (threshold_pct / 100)
      exceeded        = window_charges > use_limit

    Args:
        adjusted_usage:    Total charges + adjustments (subtree rollup).
        allocation_amount: Current active allocation amount.
        first_threshold:   30-day threshold % from account.first_threshold (None → skip).
        second_threshold:  90-day threshold % from account.second_threshold (None → skip).
        alloc_start:       Allocation start_date.
        alloc_end:         Allocation end_date (None for open-ended).
        window_charges_30: Charges in last 30 days (0.0 when no threshold).
        window_charges_90: Charges in last 90 days (0.0 when no threshold).
    """
    if allocation_amount is None:
        return 'Normal'

    # Priority 1: Overspent (includes alloc=0 with any usage, matching legacy behaviour)
    if adjusted_usage > allocation_amount:
        return 'Overspent'

    # Priority 2 & 3: N-day threshold checks (only when thresholds are set)
    n_exceeded = 0
    if (first_threshold is not None or second_threshold is not None) and alloc_start is not None:
        now = datetime.now()
        alloc_end_dt = alloc_end or now
        duration_days = max((alloc_end_dt - alloc_start).days - 1, 1)

        for period_days, threshold_pct, window_charges in (
            (30,  first_threshold,  window_charges_30),
            (90,  second_threshold, window_charges_90),
        ):
            if threshold_pct is None:
                continue
            threshold_alloc = period_days * allocation_amount / duration_days
            use_limit = threshold_alloc * (threshold_pct / 100.0)
            if window_charges > use_limit:
                n_exceeded += 1

    if n_exceeded == 1:
        return 'Exceed One Threshold'
    if n_exceeded >= 2:
        return 'Exceed Two Thresholds'

    return 'Normal'


def _compute_threshold_data(
    allocation_amount: float,
    first_threshold: Optional[int],
    second_threshold: Optional[int],
    alloc_start: datetime,
    alloc_end: Optional[datetime],
    window_charges_30: float,
    window_charges_90: float,
    now: datetime,
) -> Optional[Dict]:
    """
    Build the per-period threshold breakdown for inclusion in the resource dict.

    Called only for the small number of accounts (~12) with first_threshold or
    second_threshold configured.  Shares the same NDayUsagePeriod.java formula
    as _compute_status() but returns the full intermediate values.

    Returns:
        Dict with 'period30' and/or 'period90' sub-dicts, or None if no thresholds
        are configured / allocation_amount is None.

    Each period dict contains:
        days           — window length (30 or 90)
        thresholdPct   — configured threshold percentage (account.first/second_threshold)
        windowCharges  — actual charges in the clamped window (int AU)
        useLimitCharges— the maximum allowed charges for the period (int AU)
        pctUsed        — windowCharges / useLimitCharges × 100, 1 decimal
    """
    if allocation_amount is None:
        return None
    if first_threshold is None and second_threshold is None:
        return None

    alloc_end_dt = alloc_end or now
    duration_days = max((alloc_end_dt - alloc_start).days - 1, 1)

    result: Dict = {}
    for period_days, threshold_pct, window_charges, key in (
        (30, first_threshold,  window_charges_30, 'period30'),
        (90, second_threshold, window_charges_90, 'period90'),
    ):
        if threshold_pct is None:
            continue
        use_limit = period_days * allocation_amount / duration_days * (threshold_pct / 100.0)
        pct_used = round(window_charges / use_limit * 100.0, 1) if use_limit > 0 else 0.0
        result[key] = {
            'days':            period_days,
            'thresholdPct':    threshold_pct,
            'windowCharges':   int(round(window_charges)),
            'useLimitCharges': int(round(use_limit)),
            'pctUsed':         pct_used,
        }
    return result or None


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

    Only called for the small number of accounts with threshold percentages set.
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


# ---------------------------------------------------------------------------
# Internal helper: build/update the facilities_dict from a set of skeleton rows
# ---------------------------------------------------------------------------

def _ensure_project(
    facilities_dict: Dict,
    fac_name: str,
    facility_fsp: Optional[float],
    facility_description: str,
    facility_code: str,
    alloc_type_id: int,
    alloc_type: str,
    type_fsp: Optional[float],
    projcode: str,
    project_active: bool,
) -> Dict:
    """Ensure facility/alloctype/project entries exist; return the project dict."""
    if fac_name not in facilities_dict:
        facilities_dict[fac_name] = {
            'description':         facility_description,
            'fairSharePercentage': float(facility_fsp) if facility_fsp is not None else 0.0,
            'alloc_types':         {},
        }
    fac = facilities_dict[fac_name]

    at_key = (alloc_type_id, alloc_type)
    if at_key not in fac['alloc_types']:
        fac['alloc_types'][at_key] = {
            'name':                _alloc_type_name(facility_code or '', alloc_type),
            'description':         alloc_type,
            'fairSharePercentage': float(type_fsp) if type_fsp is not None else 0.0,
            'projects':            {},
        }
    at = fac['alloc_types'][at_key]

    if projcode not in at['projects']:
        at['projects'][projcode] = {
            'active':    bool(project_active),
            'resources': {},
        }
    return at['projects'][projcode]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_fstree_data(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict:
    """
    Build the PBS fairshare tree dict.

    Reproduces the legacy Java
    ``GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>`` endpoint output.

    Args:
        session: SQLAlchemy session.
        resource_name: Optional resource name filter (e.g. ``"Derecho"``).
                       ``None`` returns all HPC+DAV resources.

    Returns:
        Nested dict with ``name`` and ``facilities`` keys.

    Notes:
        ``adjustedUsage`` is computed via MPTT subtree rollup.
        ``accountStatus`` follows the legacy Java priority chain.
        Parent non-Normal status propagates to children (pre-order walk).
        Projects with no current active allocation appear as "Expired" or
        "No Account" with zero usage/allocation/users (requires resource filter).
    """
    from sam.projects.projects import Project

    now = datetime.now()
    params = {'resource': resource_name}

    # ------------------------------------------------------------------
    # Query 1 — Fast skeleton (current-allocation rows)
    # ------------------------------------------------------------------
    skeleton_rows = session.execute(_SQL_FSTREE_SKELETON, params).fetchall()

    # Track which (project_id, resource_id) pairs the skeleton already covers
    # so the lifecycle query can skip them.
    skeleton_covered: Set[tuple] = set()

    # Build maps needed for charge aggregation and propagation
    pid_to_projcode: Dict[int, str] = {}
    projcode_parent: Dict[str, Optional[str]] = {}

    seen_proj_res: Set[tuple] = set()
    alloc_infos: List[Dict[str, Any]] = []
    threshold_accounts: Dict[int, tuple] = {}
    alloc_windows: Dict[int, tuple] = {}

    for row in skeleton_rows:
        pid_to_projcode[row.project_id] = row.projcode

        proj_res_key = (row.project_id, row.resource_id)
        skeleton_covered.add(proj_res_key)

        if proj_res_key not in seen_proj_res:
            seen_proj_res.add(proj_res_key)
            if row.allocation_id is not None:
                alloc_infos.append({
                    'key':           row.account_id,   # lookup key in charge_map (account_id)
                    'account_id':    row.account_id,
                    'resource_id':   row.resource_id,
                    'resource_type': row.resource_type,
                    'tree_root':     row.tree_root,
                    'tree_left':     row.tree_left,
                    'tree_right':    row.tree_right,
                    'start_date':    row.start_date,
                    'end_date':      row.end_date or now,
                })
                alloc_windows[row.account_id] = (row.start_date, row.end_date or now)
                if row.first_threshold is not None or row.second_threshold is not None:
                    threshold_accounts[row.account_id] = (
                        row.first_threshold, row.second_threshold,
                        row.start_date, row.end_date,
                    )

    for row in skeleton_rows:
        if row.projcode not in projcode_parent:
            projcode_parent[row.projcode] = pid_to_projcode.get(row.parent_id) if row.parent_id else None

    # ------------------------------------------------------------------
    # Query 2 — Lifecycle rows (Expired / No Account)
    # Only meaningful when a specific resource is requested.
    # ------------------------------------------------------------------
    lifecycle_rows = []
    if resource_name is not None and skeleton_rows:
        # Only run lifecycle query when the resource exists (skeleton returned data).
        # Prevents projecting "No Account" rows onto completely unknown/non-HPC resources.
        lifecycle_rows = session.execute(_SQL_FSTREE_LIFECYCLE, params).fetchall()
        for row in lifecycle_rows:
            pid_to_projcode[row.project_id] = row.projcode
            if row.projcode not in projcode_parent:
                projcode_parent[row.projcode] = pid_to_projcode.get(row.parent_id) if row.parent_id else None

    # ------------------------------------------------------------------
    # Charges — hybrid approach matching allocations.py:
    #
    # Non-leaf projects (~28) → batch_get_subtree_charges(): MPTT rollup
    #   that includes descendant charges.  With only ~28 non-leaf entries,
    #   the (resource_type, start_date, end_date) grouping yields ~28 date
    #   groups → ~56 SQL queries (fast).
    #
    # Leaf projects (~1,455) → batch_get_account_charges(): VALUES CTE that
    #   embeds all date ranges in ~5 queries (~0.9s).  Correct for leaves
    #   since their subtree == self.
    #
    subtree_infos: List[Dict[str, Any]] = []
    account_infos: List[Dict[str, Any]] = []
    for info in alloc_infos:
        tl, tr = info['tree_left'], info['tree_right']
        is_leaf = (not info['tree_root'] or not tl or not tr or tr == tl + 1)
        if is_leaf:
            account_infos.append(info)
        else:
            subtree_infos.append(info)

    raw_charges: Dict[Any, Dict] = {}
    if subtree_infos:
        raw_charges.update(
            Project.batch_get_subtree_charges(session, subtree_infos, include_adjustments=True)
        )
    if account_infos:
        raw_charges.update(
            Project.batch_get_account_charges(session, account_infos, include_adjustments=True)
        )

    # charge_map: account_id → adjusted_usage (float)
    charge_map: Dict[int, float] = {}
    for account_id, data in raw_charges.items():
        charge_map[account_id] = sum(data['charges_by_type'].values()) + data['adjustment']

    # ------------------------------------------------------------------
    # N-day window charges (only for threshold accounts — typically ~12)
    # ------------------------------------------------------------------
    threshold_acct_ids = list(threshold_accounts.keys())
    window_30 = _query_window_charges(session, threshold_acct_ids, 30, now, alloc_windows) \
        if threshold_acct_ids else {}
    window_90 = _query_window_charges(session, threshold_acct_ids, 90, now, alloc_windows) \
        if threshold_acct_ids else {}

    # ------------------------------------------------------------------
    # Query 3 — Users per account
    # Active users (skeleton rows) + all-time users (Expired lifecycle rows)
    # ------------------------------------------------------------------
    user_rows = session.execute(_SQL_FSTREE_USERS, params).fetchall()
    user_map: Dict[int, List[Dict]] = {}
    for row in user_rows:
        user_map.setdefault(row.account_id, []).append({
            'username': row.username,
            'uid':      row.unix_uid,
        })

    # Fetch users for Expired accounts (no date filter — matches legacy behaviour)
    expired_acct_ids = [
        row.account_id for row in lifecycle_rows
        if row.lifecycle_status == 'Expired' and row.account_id is not None
    ]
    if expired_acct_ids:
        expired_user_rows = session.execute(
            _SQL_FSTREE_EXPIRED_USERS,
            {'account_ids': tuple(expired_acct_ids)},
        ).fetchall()
        for row in expired_user_rows:
            if row.account_id not in user_map:  # don't overwrite active-user entries
                user_map.setdefault(row.account_id, []).append({
                    'username': row.username,
                    'uid':      row.unix_uid,
                })

    # ------------------------------------------------------------------
    # Python assembly — skeleton rows (normal / overspent / threshold)
    # ------------------------------------------------------------------
    facilities_dict: Dict[str, Dict] = {}
    seen_accounts: Set[tuple] = set()

    for row in skeleton_rows:
        account_id = row.account_id
        acct_res_key = (account_id, row.resource_name)
        if acct_res_key in seen_accounts:
            continue
        seen_accounts.add(acct_res_key)

        proj = _ensure_project(
            facilities_dict,
            row.facility_name, row.facility_fsp, row.facility_description, row.facility_code,
            row.allocation_type_id, row.allocation_type, row.type_fsp,
            row.projcode, row.project_active,
        )

        adjusted_usage    = charge_map.get(account_id, 0.0)
        allocation_amount = float(row.allocation_amount) if row.allocation_amount is not None else None
        balance           = (allocation_amount - adjusted_usage) if allocation_amount is not None else None

        w30 = window_30.get(account_id, 0.0)
        w90 = window_90.get(account_id, 0.0)
        th  = threshold_accounts.get(account_id)

        account_status = _compute_status(
            adjusted_usage, allocation_amount,
            th[0] if th else None, th[1] if th else None,
            row.start_date, row.end_date,
            w30, w90,
        )

        # Threshold breakdown (only for the ~12 accounts with configured thresholds)
        thresholds = None
        if th and allocation_amount is not None and row.start_date is not None:
            thresholds = _compute_threshold_data(
                allocation_amount,
                th[0], th[1],
                row.start_date, row.end_date,
                w30, w90,
                now,
            )

        proj['resources'][row.resource_name] = {
            'name':             row.resource_name,
            'accountStatus':    account_status,
            'cutoffThreshold':  row.cutoff_threshold if row.cutoff_threshold is not None else 100,
            'adjustedUsage':    int(round(adjusted_usage)),
            'balance':          int(round(balance)) if balance is not None else None,
            'allocationAmount': int(round(allocation_amount)) if allocation_amount is not None else None,
            'users':            user_map.get(account_id, []),
            'thresholds':       thresholds,
        }

    # ------------------------------------------------------------------
    # Assembly — lifecycle rows (Expired / No Account)
    # ------------------------------------------------------------------
    for row in lifecycle_rows:
        # Use existing facility description if already in dict (from skeleton rows),
        # else leave blank — lifecycle rows don't select f.description.
        existing_fac = facilities_dict.get(row.facility_name)
        fac_desc = existing_fac['description'] if existing_fac else ''

        proj = _ensure_project(
            facilities_dict,
            row.facility_name, row.facility_fsp, fac_desc, row.facility_code,
            row.allocation_type_id, row.allocation_type, row.type_fsp,
            row.projcode, row.project_active,
        )

        res_name = row.resource_name or resource_name or ''
        if res_name not in proj['resources']:
            users = user_map.get(row.account_id, []) if row.account_id else []
            proj['resources'][res_name] = {
                'name':             res_name,
                'accountStatus':    row.lifecycle_status,
                'cutoffThreshold':  100,
                'adjustedUsage':    0,
                'balance':          0,
                'allocationAmount': 0,
                'users':            users,
                'thresholds':       None,
            }

    # ------------------------------------------------------------------
    # Parent → child status propagation (pre-order per resource)
    # ------------------------------------------------------------------
    _NON_NORMAL = {'Overspent', 'Exceed Two Thresholds', 'Exceed One Threshold'}

    for fac_data in facilities_dict.values():
        for at_data in fac_data['alloc_types'].values():
            projects = at_data['projects']
            parent_statuses: Dict[str, Dict[str, str]] = {}

            for projcode in sorted(projects.keys()):
                proj_data   = projects[projcode]
                parent_pc   = projcode_parent.get(projcode)
                parent_res  = parent_statuses.get(parent_pc, {}) if parent_pc else {}

                for res_name, res_data in proj_data['resources'].items():
                    parent_s = parent_res.get(res_name)
                    if parent_s and parent_s in _NON_NORMAL:
                        res_data['accountStatus'] = parent_s

                parent_statuses[projcode] = {
                    res_name: res_data['accountStatus']
                    for res_name, res_data in proj_data['resources'].items()
                }

    # ------------------------------------------------------------------
    # Serialize
    # ------------------------------------------------------------------
    facilities_list: List[Dict] = []

    for fac_name, fac_data in facilities_dict.items():
        alloc_types_list: List[Dict] = []

        for _at_key, at_data in fac_data['alloc_types'].items():
            projects_list: List[Dict] = []

            for projcode, proj_data in sorted(at_data['projects'].items()):
                resources_list = list(proj_data['resources'].values())
                resources_list.sort(key=lambda r: r['name'])

                projects_list.append({
                    'projectCode': projcode,
                    'active':      proj_data['active'],
                    'resources':   resources_list,
                })

            alloc_types_list.append({
                'name':                at_data['name'],
                'description':         at_data['description'],
                'fairSharePercentage': at_data['fairSharePercentage'],
                'projects':            projects_list,
            })

        facilities_list.append({
            'name':                fac_name,
            'description':         fac_data['description'],
            'fairSharePercentage': fac_data['fairSharePercentage'],
            'allocationTypes':     alloc_types_list,
        })

    return {
        'name':       'fairShareTree',
        'facilities': facilities_list,
    }


# ---------------------------------------------------------------------------
# Remap helpers
# ---------------------------------------------------------------------------

def _remap_fstree_by_project(fstree_data: Dict) -> Dict:
    """
    Remap the fstree dict (from get_fstree_data) into a project-keyed structure.

    Input:  fairShareTree → Facility → AllocationType → Project → Resources
    Output: projectFairShareData → projects[projcode] → Resources

    Each project entry includes its facility and allocation-type context so
    callers don't need to traverse the full tree.  The resources list is
    identical to the fstree output (same fields, same sort order).
    """
    projects: Dict[str, Dict] = {}

    for fac in fstree_data['facilities']:
        for at in fac['allocationTypes']:
            for proj in at['projects']:
                projcode = proj['projectCode']
                projects[projcode] = {
                    'active':                    proj['active'],
                    'facility':                  fac['name'],
                    'allocationType':            at['name'],
                    'allocationTypeDescription': at['description'],
                    'resources':                 sorted(proj['resources'], key=lambda r: r['name']),
                }

    return {
        'name':     'projectFairShareData',
        'projects': projects,
    }


def _remap_fstree_by_user(fstree_data: Dict) -> Dict:
    """
    Remap the fstree dict (from get_fstree_data) into a user-keyed structure.

    Input:  fairShareTree → Facility → AllocationType → Project → Resources → Users
    Output: userFairShareData → users[username] → projects[projcode] → Resources

    A user appears under a project/resource only when they are listed in that
    resource's ``users`` roster.  The per-resource dict omits the ``users``
    key (redundant when already keyed by user).
    """
    users: Dict[str, Dict] = {}

    for fac in fstree_data['facilities']:
        for at in fac['allocationTypes']:
            for proj in at['projects']:
                projcode  = proj['projectCode']
                proj_meta = {
                    'active':                    proj['active'],
                    'facility':                  fac['name'],
                    'allocationType':            at['name'],
                    'allocationTypeDescription': at['description'],
                }

                for resource in proj['resources']:
                    res_entry = {k: v for k, v in resource.items() if k != 'users'}

                    for user in resource.get('users', []):
                        username = user['username']

                        if username not in users:
                            users[username] = {'uid': user['uid'], 'projects': {}}
                        user_entry = users[username]

                        if projcode not in user_entry['projects']:
                            user_entry['projects'][projcode] = {**proj_meta, 'resources': []}
                        user_entry['projects'][projcode]['resources'].append(res_entry)

    # Sort resources within each project by name
    for user_entry in users.values():
        for proj_entry in user_entry['projects'].values():
            proj_entry['resources'].sort(key=lambda r: r['name'])

    return {
        'name':  'userFairShareData',
        'users': users,
    }


# ---------------------------------------------------------------------------
# Public convenience wrappers
# ---------------------------------------------------------------------------

def get_project_fsdata(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict:
    """
    Return fstree data reorganized by project.

    Calls get_fstree_data() internally then remaps the result so callers can
    look up any project directly by projcode without traversing the full
    Facility → AllocationType → Project hierarchy.

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource filter (e.g. ``"Derecho"``).

    Returns:
        Dict with ``name`` (``"projectFairShareData"``) and ``projects`` keys.
        ``projects`` is a dict keyed by projcode; each value contains
        ``active``, ``facility``, ``allocationType``,
        ``allocationTypeDescription``, and ``resources`` (same fields as
        the fstree resource dict, sorted by resource name).

    Example::

        data = get_project_fsdata(session, 'Derecho')
        proj = data['projects']['SCSG0001']
        for res in proj['resources']:
            print(res['name'], res['accountStatus'], res['adjustedUsage'])
    """
    return _remap_fstree_by_project(get_fstree_data(session, resource_name))


def get_user_fsdata(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict:
    """
    Return fstree data reorganized by user.

    Calls get_fstree_data() internally then remaps the result so callers can
    look up all projects and resources accessible to a given user.

    A user appears under a project/resource only when they are listed in
    that resource's active-user roster (or, for Expired accounts, the
    historical roster).

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource filter (e.g. ``"Derecho"``).

    Returns:
        Dict with ``name`` (``"userFairShareData"``) and ``users`` keys.
        ``users`` is a dict keyed by username; each value contains ``uid``
        and ``projects``.  ``projects`` is a dict keyed by projcode; each
        project entry contains ``active``, ``facility``, ``allocationType``,
        ``allocationTypeDescription``, and ``resources`` (same fields as the
        fstree resource dict minus ``users``, sorted by resource name).

    Example::

        data = get_user_fsdata(session, 'Derecho')
        user = data['users']['benkirk']
        for projcode, proj in user['projects'].items():
            for res in proj['resources']:
                print(projcode, res['name'], res['accountStatus'])
    """
    return _remap_fstree_by_user(get_fstree_data(session, resource_name))
