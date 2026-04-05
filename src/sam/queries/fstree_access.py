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
Two bulk raw-SQL queries are used for the tree skeleton and user rosters.
Charges are aggregated via Project.batch_get_subtree_charges(), which uses a
VALUES CTE to issue one query per charge model with all MPTT anchor coordinates
inlined — this correctly rolls up charges from all descendant sub-projects, and
is far faster than a LEFT JOIN across the charge summary tables.

Query 1 — tree skeleton + active allocation metadata (fast, ~0.04–0.14s)
Query 2 — active users per account (fast, ~0.07–0.23s)
Charges  — via batch_get_subtree_charges() VALUES CTE (~0.7s for Derecho)

Python assembles the nested response from the three result sets.

accountStatus semantics
-----------------------
Matches legacy Java DefaultAccountStatusCalculator priority order:

  1. "Overspent"             — adjustedUsage > allocationAmount
  2. "Exceed Two Thresholds" — both N-day usage windows exceeded per-account thresholds
  3. "Exceed One Threshold"  — exactly one N-day window exceeded
  4. "Normal"                — default

N-day threshold logic (from NDayUsagePeriod.java):
  threshold_alloc = P × allocationAmount / (duration_days − 1)
  use_limit       = threshold_alloc × (threshold_pct / 100)
  exceeded        = (window_charges > use_limit)

  where P ∈ {30, 90} days and threshold_pct comes from account.first_threshold /
  account.second_threshold (both NULL for ~99.7% of accounts — no threshold check
  is performed when NULL).

Parent → child status propagation (pre-order, matching the Java pre-order tree walk):
  If a parent project's accountStatus on a resource is non-Normal, that status
  propagates down to all child projects on the same resource.

Lifecycle statuses (Expired, Waiting, Disabled, etc.) are not surfaced here
because the skeleton query already filters to active projects with current
active allocations.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# SQL Queries
# ---------------------------------------------------------------------------

# Query 1: Tree skeleton (facility → panel → alloc_type → project → account
#          → resource) plus active allocation metadata and per-account threshold fields.
#
# One row per (project, resource) pair.  An account may have multiple concurrent
# active allocations; the LEFT JOIN delivers all — Python keeps only the first
# one encountered (single active allocation per account is the normal case).
#
# Key additions vs. original:
#   • p.tree_root / tree_left / tree_right — MPTT coordinates for subtree charges
#   • p.parent_id                          — for parent → child status propagation
#   • a.first_threshold / second_threshold — per-account N-day threshold percentages
#
# Filters:
#   • pa.active / at.active / f.active = TRUE   — active taxonomy nodes only
#   • p.active  = TRUE                           — active projects only
#   • a.deleted = FALSE                          — non-deleted accounts
#   • r.configurable = TRUE                      — provisionable resources
#   • rt.resource_type IN ('HPC','DAV')          — fairshare-relevant resources
#   • al.deleted = FALSE + date window           — current active allocations
#
# facility_resource.fair_share_percentage overrides facility.fair_share_percentage
# when a resource-specific percentage is set (FacilityResourceDTOFacilityFacade).
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
    LEFT JOIN allocation   al  ON (al.account_id          = a.account_id
                                    AND al.deleted         IS FALSE
                                    AND al.start_date     <= NOW()
                                    AND (al.end_date IS NULL OR al.end_date >= NOW()))
    WHERE f.active IS TRUE
      AND (:resource IS NULL OR r.resource_name = :resource)
    ORDER BY f.facility_name, at.allocation_type, p.projcode, r.resource_name
""")

# Query 2: Active users per account.
#
# Filters account_user rows to those currently within their active date window.
# NULL start_date / end_date mean "always active" on that boundary.
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
    Compute accountStatus matching legacy Java DefaultAccountStatusCalculator priority order.

    Priority (highest wins):
      1. "Overspent"             — total adjustedUsage > allocationAmount
      2. "Exceed Two Thresholds" — both N-day windows exceed their threshold
      3. "Exceed One Threshold"  — exactly one N-day window exceeds its threshold
      4. "Normal"                — default

    N-day threshold formula (NDayUsagePeriod.java):
      threshold_alloc = P × allocationAmount / (duration_days − 1)
      use_limit       = threshold_alloc × (threshold_pct / 100)
      exceeded        = window_charges > use_limit

    Args:
        adjusted_usage:    Total charges + adjustments over full allocation window.
        allocation_amount: Current active allocation amount (None → "Normal").
        first_threshold:   30-day threshold percentage from account.first_threshold.
                           None → skip 30-day check.
        second_threshold:  90-day threshold percentage from account.second_threshold.
                           None → skip 90-day check.
        alloc_start:       Allocation start_date (needed for duration calculation).
        alloc_end:         Allocation end_date (may be None for open-ended allocations).
        window_charges_30: Charges in the last 30 days (0.0 when first_threshold is None).
        window_charges_90: Charges in the last 90 days (0.0 when second_threshold is None).
    """
    if allocation_amount is None or allocation_amount == 0:
        return 'Normal'

    # Priority 1: Overspent
    if adjusted_usage > allocation_amount:
        return 'Overspent'

    # Priority 2 & 3: N-day threshold checks (skipped when thresholds are NULL)
    n_exceeded = 0

    if (first_threshold is not None or second_threshold is not None) and alloc_start is not None:
        now = datetime.now()
        alloc_end_dt = alloc_end or now
        # duration_days − 1 matches the Java "TODO: durationInDays is one day short" comment
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


def _query_window_charges(
    session: Session,
    account_ids: List[int],
    window_days: int,
    now: datetime,
    alloc_windows: Dict[int, tuple],   # account_id → (start_date, end_date)
) -> Dict[int, float]:
    """
    Query total charges (comp + dav + adjustments) for a set of accounts over a
    trailing N-day window, clamped to the allocation date range.

    Only called for the small number of accounts that have threshold percentages set.

    Args:
        account_ids:   List of account_ids to query.
        window_days:   Number of days for the trailing window (30 or 90).
        now:           Current datetime reference.
        alloc_windows: Per-account (start_date, end_date) allocation boundaries.

    Returns:
        Dict mapping account_id → total window charges.
    """
    if not account_ids:
        return {}

    window_start_global = now - timedelta(days=window_days)
    result: Dict[int, float] = {aid: 0.0 for aid in account_ids}

    # Build a VALUES CTE with per-account window start dates clamped to alloc start.
    # account_id, window_start, window_end
    rows_sql = ', '.join(
        f'ROW({aid}, :ws{i}, :we{i})'
        for i, aid in enumerate(account_ids)
    )
    params: Dict[str, Any] = {'now': now}
    for i, aid in enumerate(account_ids):
        alloc_start, alloc_end = alloc_windows.get(aid, (now, now))
        clamped_start = max(window_start_global, alloc_start)
        clamped_end   = min(now, alloc_end or now)
        params[f'ws{i}'] = clamped_start
        params[f'we{i}'] = clamped_end

    for table, col in [
        ('comp_charge_summary',  'activity_date'),
        ('dav_charge_summary',   'activity_date'),
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

    # charge_adjustment uses adjustment_date (datetime), same clamped window
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
        Nested dict::

            {
                "name": "fairShareTree",
                "facilities": [
                    {
                        "name": "CSL",
                        "description": "Climate Simulation Laboratory",
                        "fairSharePercentage": 31.0,
                        "allocationTypes": [
                            {
                                "name": "C_CSL",
                                "description": "CSL",
                                "fairSharePercentage": 0.0,
                                "projects": [
                                    {
                                        "projectCode": "P93300041",
                                        "active": True,
                                        "resources": [
                                            {
                                                "name": "Derecho",
                                                "accountStatus": "Normal",
                                                "cutoffThreshold": 100,
                                                "adjustedUsage": 48883597,
                                                "balance": 2616402,
                                                "allocationAmount": 51500000,
                                                "users": [
                                                    {"username": "travisa", "uid": 29642},
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }

    Notes:
        Charges are aggregated hierarchically — a parent project's ``adjustedUsage``
        includes charges from all descendant sub-projects (MPTT subtree rollup via
        ``Project.batch_get_subtree_charges()``).

        ``accountStatus`` follows the legacy Java priority chain: Overspent →
        Exceed Two Thresholds → Exceed One Threshold → Normal.  N-day threshold
        checks are only performed for the small number of accounts that have
        ``first_threshold`` or ``second_threshold`` set in the database.

        Parent OVERSPENT / threshold-exceeded status propagates down to child
        projects on the same resource, matching the Java pre-order tree walk.
    """
    from sam.projects.projects import Project

    now = datetime.now()
    params = {'resource': resource_name}

    # ------------------------------------------------------------------
    # Query 1 — Tree skeleton
    # ------------------------------------------------------------------
    skeleton_rows = session.execute(_SQL_FSTREE_SKELETON, params).fetchall()

    # ------------------------------------------------------------------
    # Build lookup maps from skeleton rows
    # ------------------------------------------------------------------

    # project_id → projcode  (for parent lookup)
    pid_to_projcode: Dict[int, str] = {}
    # projcode → parent_projcode  (None for root projects)
    projcode_parent: Dict[str, Optional[str]] = {}

    # alloc_infos for batch_get_subtree_charges (keyed by allocation_id)
    # Track (project_id, resource_id) to avoid duplicating the same subtree anchor.
    seen_proj_res: Set[tuple] = set()
    alloc_infos: List[Dict[str, Any]] = []

    # threshold_accounts: account_id → (first_threshold, second_threshold, alloc_start, alloc_end)
    # Only populated for accounts with at least one threshold set.
    threshold_accounts: Dict[int, tuple] = {}
    # alloc_windows for _query_window_charges: account_id → (start_date, end_date)
    alloc_windows: Dict[int, tuple] = {}

    for row in skeleton_rows:
        pid_to_projcode[row.project_id] = row.projcode

        proj_res_key = (row.project_id, row.resource_id)
        if proj_res_key not in seen_proj_res:
            seen_proj_res.add(proj_res_key)

            if row.allocation_id is not None:
                alloc_infos.append({
                    'key':           row.allocation_id,
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
                        row.first_threshold,
                        row.second_threshold,
                        row.start_date,
                        row.end_date,
                    )

    # Resolve parent projcodes (second pass — all project_ids now known)
    for row in skeleton_rows:
        if row.projcode not in projcode_parent:
            parent_pc = pid_to_projcode.get(row.parent_id) if row.parent_id else None
            projcode_parent[row.projcode] = parent_pc

    # ------------------------------------------------------------------
    # Charges — via batch_get_subtree_charges() (VALUES CTE)
    #
    # Aggregates charges for each project + all its MPTT descendants,
    # matching legacy Java ProjectAccountTreeQuery behavior.
    # Keyed by allocation_id.
    # ------------------------------------------------------------------
    raw_charges = Project.batch_get_subtree_charges(
        session,
        alloc_infos,
        include_adjustments=True,
    )

    # charge_map: allocation_id → adjusted_usage (float)
    charge_map: Dict[int, float] = {}
    for alloc_id, data in raw_charges.items():
        total = sum(data['charges_by_type'].values()) + data['adjustment']
        charge_map[alloc_id] = total

    # ------------------------------------------------------------------
    # N-day window charges (only for accounts with thresholds — typically ~12)
    # ------------------------------------------------------------------
    threshold_acct_ids = list(threshold_accounts.keys())
    window_30: Dict[int, float] = _query_window_charges(
        session, threshold_acct_ids, 30, now, alloc_windows,
    ) if threshold_acct_ids else {}
    window_90: Dict[int, float] = _query_window_charges(
        session, threshold_acct_ids, 90, now, alloc_windows,
    ) if threshold_acct_ids else {}

    # ------------------------------------------------------------------
    # Query 2 — Active users per account
    # ------------------------------------------------------------------
    user_rows = session.execute(_SQL_FSTREE_USERS, params).fetchall()

    # user_map: account_id → [{username, uid}, ...]
    user_map: Dict[int, List[Dict]] = {}
    for row in user_rows:
        user_map.setdefault(row.account_id, []).append({
            'username': row.username,
            'uid':      row.unix_uid,
        })

    # ------------------------------------------------------------------
    # Python assembly — build intermediate facilities_dict
    # ------------------------------------------------------------------
    # facilities_dict[facility_name] = {
    #   'description': str,
    #   'fairSharePercentage': float,
    #   'alloc_types': {
    #     (alloc_type_id, alloc_type): {
    #       'name': str, 'description': str, 'fairSharePercentage': float,
    #       'projects': {
    #         projcode: {
    #           'active': bool,
    #           'resources': {resource_name: {resource fields + '_alloc_id': int}},
    #         }
    #       }
    #     }
    #   }
    # }

    facilities_dict: Dict[str, Dict] = {}
    # Track (account_id, resource_name) to skip duplicate skeleton rows.
    seen_accounts: Set[tuple] = set()
    # allocation_id → account_id (needed for threshold lookup keyed by account_id)
    alloc_to_account: Dict[int, int] = {}

    for row in skeleton_rows:
        fac_name = row.facility_name

        if fac_name not in facilities_dict:
            facilities_dict[fac_name] = {
                'description':         row.facility_description,
                'fairSharePercentage': float(row.facility_fsp) if row.facility_fsp is not None else 0.0,
                'alloc_types':         {},
            }

        alloc_type_key  = (row.allocation_type_id, row.allocation_type)
        alloc_type_name = _alloc_type_name(row.facility_code or '', row.allocation_type)

        fac = facilities_dict[fac_name]
        if alloc_type_key not in fac['alloc_types']:
            fac['alloc_types'][alloc_type_key] = {
                'name':                alloc_type_name,
                'description':         row.allocation_type,
                'fairSharePercentage': float(row.type_fsp) if row.type_fsp is not None else 0.0,
                'projects':            {},
            }

        alloc_type = fac['alloc_types'][alloc_type_key]
        projcode   = row.projcode

        if projcode not in alloc_type['projects']:
            alloc_type['projects'][projcode] = {
                'active':    bool(row.project_active),
                'resources': {},
            }

        proj              = alloc_type['projects'][projcode]
        resource_name_row = row.resource_name
        account_id        = row.account_id

        acct_res_key = (account_id, resource_name_row)
        if acct_res_key in seen_accounts:
            continue
        seen_accounts.add(acct_res_key)

        if row.allocation_id is not None:
            alloc_to_account[row.allocation_id] = account_id

        # Subtree charges keyed by allocation_id
        adjusted_usage    = charge_map.get(row.allocation_id, 0.0) if row.allocation_id else 0.0
        allocation_amount = float(row.allocation_amount) if row.allocation_amount is not None else None
        balance           = (allocation_amount - adjusted_usage) if allocation_amount is not None else None

        # N-day window charges for this account (0.0 for accounts without thresholds)
        w30 = window_30.get(account_id, 0.0)
        w90 = window_90.get(account_id, 0.0)
        th  = threshold_accounts.get(account_id)
        first_th   = th[0] if th else None
        second_th  = th[1] if th else None

        # accountStatus — computed; may be overridden by parent propagation later
        account_status = _compute_status(
            adjusted_usage, allocation_amount,
            first_th, second_th,
            row.start_date, row.end_date,
            w30, w90,
        )

        proj['resources'][resource_name_row] = {
            'name':             resource_name_row,
            'accountStatus':    account_status,
            'cutoffThreshold':  row.cutoff_threshold if row.cutoff_threshold is not None else 100,
            'adjustedUsage':    int(round(adjusted_usage)),
            'balance':          int(round(balance)) if balance is not None else None,
            'allocationAmount': int(round(allocation_amount)) if allocation_amount is not None else None,
            'users':            user_map.get(account_id, []),
        }

    # ------------------------------------------------------------------
    # Parent → child status propagation (pre-order, per resource)
    # Matching DefaultAccountStatusCalculator.defineStatusFromParent():
    #   if parent.statusFromCharging != NORMAL → child inherits parent status
    # ------------------------------------------------------------------
    _NON_NORMAL = {'Overspent', 'Exceed Two Thresholds', 'Exceed One Threshold'}

    for _fac_name, fac_data in facilities_dict.items():
        for _at_key, at_data in fac_data['alloc_types'].items():
            projects = at_data['projects']
            # Per-resource tracking of non-Normal statuses seen on parent projects.
            # projcode → {resource_name → status}
            parent_statuses: Dict[str, Dict[str, str]] = {}

            # Walk in sorted projcode order — parent projcodes lexicographically
            # precede their children (MPTT tree codes are structured that way in SAM).
            # This is a best-effort ordering; the pre-order guarantee holds because
            # parent projects appear before children in the sorted projcode namespace.
            for projcode in sorted(projects.keys()):
                proj_data = projects[projcode]
                parent_pc = projcode_parent.get(projcode)
                parent_res_statuses = parent_statuses.get(parent_pc, {}) if parent_pc else {}

                for res_name, res_data in proj_data['resources'].items():
                    parent_status = parent_res_statuses.get(res_name)
                    if parent_status and parent_status in _NON_NORMAL:
                        # Propagate parent's non-Normal status down
                        res_data['accountStatus'] = parent_status

                # Record this project's statuses for its children
                parent_statuses[projcode] = {
                    res_name: res_data['accountStatus']
                    for res_name, res_data in proj_data['resources'].items()
                }

    # ------------------------------------------------------------------
    # Serialize to legacy output format
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
