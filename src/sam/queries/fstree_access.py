"""
FairShare Tree query functions for SAM.

Provides get_fstree_data() which reproduces the output of the legacy Java
`GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>` endpoint.

The data is organized as a hierarchical tree:
  fairShareTree → Facility → AllocationType → Project → Resource

with per-node fairshare percentages and, at the Resource level,
allocation balances, charge usage, and active user rosters.

This data is consumed by the PBS batch scheduler to build job
fairshare trees and by LDAP tooling for account provisioning.

Design notes
------------
Two bulk raw-SQL queries are used for the tree skeleton and user rosters.
Charges are aggregated via Project.batch_get_account_charges(), which uses a
VALUES CTE to issue one query per charge model with all account IDs and their
individual allocation date windows inlined — far faster than a LEFT JOIN across
the charge summary tables (which produces a huge intermediate fanout).

Query 1 — tree skeleton + active allocation metadata (fast, ~0.04s)
Query 2 — active users per account (fast, ~0.07s)
Charges  — via batch_get_account_charges() VALUES CTE (fast, ~0.1s)

Python assembles the nested response from the three result sets.

accountStatus semantics
-----------------------
Legacy Java computes status from 30-day and 90-day usage trends
(InfrastructureConfig default thresholds).  That trend logic is not yet
implemented.  We derive a two-state approximation:
  - "Normal"    — adjustedUsage ≤ allocationAmount
  - "Overspent" — adjustedUsage > allocationAmount

Hierarchical charge aggregation (MPPT subtree rollup) is also left as
future work; charges are per-account only for this implementation.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# SQL Queries
# ---------------------------------------------------------------------------

# Query 1: Tree skeleton (facility → panel → alloc_type → project → account
#          → resource) plus active allocation metadata.
#
# One row per (account, resource) pair.  An account may have multiple
# concurrent allocations; the LEFT JOIN delivers all — Python keeps only
# the first one encountered (they should be effectively equivalent for
# the fstree use case, where a single active allocation per account is
# expected in normal operation).
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
# when a resource-specific percentage has been configured (FacilityResourceDTOFacilityFacade).
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
        a.account_id,
        a.cutoff_threshold,
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

# Query 3: Active users per account.
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


def _compute_status(adjusted_usage: float, allocation_amount: Optional[float]) -> str:
    """
    Compute accountStatus from current balance.

    MVP: two-state approximation.  Legacy Java uses 30-day/90-day usage trend
    thresholds (InfrastructureConfig) which are not yet implemented.

    Future work: add "Warning" tier based on trend thresholds.
    """
    if allocation_amount is None or allocation_amount == 0:
        return 'Normal'
    return 'Normal' if adjusted_usage <= allocation_amount else 'Overspent'


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
        ``accountStatus`` is "Normal" or "Overspent" based on current balance.
        30-day / 90-day usage trend thresholds matching legacy Java are future work.

        Charge aggregation is per-account (not hierarchical subtree rollup).
        MPPT-based subtree aggregation is future work.
    """
    from sam.projects.projects import Project

    now = datetime.now()
    params = {'resource': resource_name}

    # ------------------------------------------------------------------
    # Query 1 — Tree skeleton
    # ------------------------------------------------------------------
    skeleton_rows = session.execute(_SQL_FSTREE_SKELETON, params).fetchall()

    # ------------------------------------------------------------------
    # Build alloc_infos for batch charge lookup from skeleton rows.
    # Track which accounts we've seen to handle multiple allocation rows
    # per account from the LEFT JOIN (take the first active allocation).
    # ------------------------------------------------------------------
    seen_alloc_accounts: set = set()
    alloc_infos: List[Dict[str, Any]] = []

    for row in skeleton_rows:
        account_id = row.account_id
        if account_id in seen_alloc_accounts:
            continue
        seen_alloc_accounts.add(account_id)

        if row.allocation_id is not None:
            alloc_infos.append({
                'key':           account_id,
                'account_id':    account_id,
                'resource_type': row.resource_type,
                'start_date':    row.start_date,
                'end_date':      row.end_date or now,
            })

    # ------------------------------------------------------------------
    # Charges — via batch_get_account_charges() (VALUES CTE, fast)
    #
    # Uses one SQL query per charge model with all account IDs and their
    # individual allocation date windows inlined.  Much faster than a
    # LEFT JOIN on the charge summary tables which fans out to millions
    # of intermediate rows before aggregation.
    # ------------------------------------------------------------------
    raw_charges = Project.batch_get_account_charges(
        session,
        alloc_infos,
        include_adjustments=True,
    )

    # charge_map: account_id → adjusted_usage (float)
    charge_map: Dict[int, float] = {}
    for account_id, data in raw_charges.items():
        total = sum(data['charges_by_type'].values()) + data['adjustment']
        charge_map[account_id] = total

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
    # Python assembly
    # ------------------------------------------------------------------
    # Intermediate structure:
    #   facilities_dict[facility_name] = {
    #       'description': ...,
    #       'fairSharePercentage': ...,
    #       'alloc_types': {
    #           alloc_type_key: {
    #               'name': ...,        # e.g. "C_CSL"
    #               'description': ..., # e.g. "CSL"
    #               'fairSharePercentage': ...,
    #               'projects': {
    #                   projcode: {
    #                       'active': ...,
    #                       'resources': {
    #                           resource_name: {resource fields}
    #                       }
    #                   }
    #               }
    #           }
    #       }
    #   }

    facilities_dict: Dict[str, Dict] = {}

    # Track seen (account_id, resource) to handle duplicate skeleton rows
    # (e.g. multiple active allocations on the same account — rare but possible).
    seen_accounts: set = set()

    for row in skeleton_rows:
        fac_name = row.facility_name

        # Ensure facility entry exists
        if fac_name not in facilities_dict:
            facilities_dict[fac_name] = {
                'description':        row.facility_description,
                'fairSharePercentage': float(row.facility_fsp) if row.facility_fsp is not None else 0.0,
                'alloc_types':        {},
            }

        # Build AllocationType key and name
        alloc_type_key  = (row.allocation_type_id, row.allocation_type)
        alloc_type_name = _alloc_type_name(row.facility_code or '', row.allocation_type)

        fac = facilities_dict[fac_name]
        if alloc_type_key not in fac['alloc_types']:
            fac['alloc_types'][alloc_type_key] = {
                'name':               alloc_type_name,
                'description':        row.allocation_type,
                'fairSharePercentage': float(row.type_fsp) if row.type_fsp is not None else 0.0,
                'projects':           {},
            }

        alloc_type = fac['alloc_types'][alloc_type_key]
        projcode   = row.projcode

        if projcode not in alloc_type['projects']:
            alloc_type['projects'][projcode] = {
                'active':    bool(row.project_active),
                'resources': {},
            }

        proj = alloc_type['projects'][projcode]
        resource_name_row = row.resource_name

        # Skip duplicate account rows (multiple active allocations)
        account_id = row.account_id
        acct_resource_key = (account_id, resource_name_row)
        if acct_resource_key in seen_accounts:
            continue
        seen_accounts.add(acct_resource_key)

        # Look up charge data for this account (from batch_get_account_charges)
        adjusted_usage    = charge_map.get(account_id, 0.0)
        allocation_amount = float(row.allocation_amount) if row.allocation_amount is not None else None
        balance           = (allocation_amount - adjusted_usage) if allocation_amount is not None else None
        account_status    = _compute_status(adjusted_usage, allocation_amount)

        users = user_map.get(account_id, [])

        proj['resources'][resource_name_row] = {
            'name':             resource_name_row,
            'accountStatus':    account_status,
            'cutoffThreshold':  row.cutoff_threshold if row.cutoff_threshold is not None else 100,
            'adjustedUsage':    int(round(adjusted_usage)),
            'balance':          int(round(balance)) if balance is not None else None,
            'allocationAmount': int(round(allocation_amount)) if allocation_amount is not None else None,
            'users':            users,
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
                # Sort resources by name for deterministic output
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
