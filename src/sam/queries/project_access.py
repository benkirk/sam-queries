"""
Project Access query functions for SAM.

Provides get_project_group_status() which reproduces the output of the legacy
Java `GET /api/protected/admin/sysacct/groupstatus/{access_branch}` endpoint.

The data is organized by access branch (hpc, hpc-data, hpc-dev) and describes
which projects are accessible on each branch, which resources they have
allocations on, and their current allocation status.

This is the companion to directory_access.py (which handles unix group / account
data).  Both use the same access_branch_resource JOIN pattern and the same
ACCESS_GRACE_PERIOD constant.

Note on `autoRenewing`:
    This field appears in the legacy output but is NOT stored in the SAM
    database — no table has an auto_renewing or auto_renewal column.
    All observed production values are `false`.  We hardcode False here to
    match legacy behaviour rather than omit the field.
"""

from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Reuse constants from the sibling directory_access module
from .directory_access import ACCESS_GRACE_PERIOD


# ---------------------------------------------------------------------------
# SQL Query
# ---------------------------------------------------------------------------

# One row per (access_branch, project, resource) — Python aggregates the rest.
#
# Filters:
#   • r.configurable = TRUE   — same gate used by directory_access (omits
#                               non-provisioned resources)
#   • al.deleted = FALSE      — ignore soft-deleted allocations
#   • end_date within grace period — include recently-expired projects
#
# The subquery MAX(al.end_date) gives the latest allocation end_date for each
# (account, resource) pair; the outer MAX in Python finds the overall project
# expiration across all resources.
_SQL_PROJECT_GROUP_STATUS = text("""
    SELECT ab.name              AS access_branch_name,
           LOWER(p.projcode)   AS group_name,
           p.active            AS project_active,
           at.allocation_type  AS panel,
           r.resource_name     AS resource_name,
           MAX(al.end_date)    AS end_date
      FROM account AS a
      JOIN project AS p
           ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources AS r
           ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource AS abr
           ON r.resource_id = abr.resource_id
      JOIN access_branch AS ab
           ON abr.access_branch_id = ab.access_branch_id
      JOIN allocation AS al
           ON (a.account_id = al.account_id
               AND al.deleted = FALSE
               AND (al.end_date + INTERVAL :dead_cutoff DAY) > NOW())
      LEFT JOIN allocation_type AS at
           ON p.allocation_type_id = at.allocation_type_id
     WHERE (:branch IS NULL OR ab.name = :branch)
     GROUP BY ab.name, p.projcode, p.active, at.allocation_type, r.resource_name
     ORDER BY LOWER(p.projcode), r.resource_name
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEAD_CUTOFF_DAYS = 365  # projects expired beyond this are not returned at all


def get_project_group_status(
    session: Session,
    access_branch: Optional[str] = None,
    grace_period_days: int = ACCESS_GRACE_PERIOD,
    dead_cutoff_days: int = DEAD_CUTOFF_DAYS,
) -> Dict[str, List[Dict]]:
    """
    Build the per-access-branch project group status directory.

    Reproduces the legacy Java ``GET /api/protected/admin/sysacct/groupstatus``
    endpoint output, keyed by access branch name.

    Returns:
        dict keyed by branch name, each value a list of project dicts::

            {
                "hpc": [
                    {
                        "groupName":    "wyom0218",      # lowercase projcode
                        "panel":        "WRAP",          # allocation_type name
                        "autoRenewing": False,           # never set in DB; hardcoded
                        "projectActive": True,           # project.active
                        "status":       "ACTIVE",        # "ACTIVE", "EXPIRED", or "DEAD"
                        "days_expired": None,            # int when EXPIRED/DEAD, else omitted
                        "expiration":   "2028-07-01",    # max end_date (ISO string)
                        "resourceGroupStatuses": [
                            {"resourceName": "Derecho",  "endDate": "2028-07-01"},
                            {"resourceName": "Casper",   "endDate": "2028-07-01"},
                        ],
                    },
                    ...
                ],
                "hpc-data": [...],
                "hpc-dev":  [...],
            }

        Status semantics:
          - ``ACTIVE``  — max end_date >= today
          - ``EXPIRED`` — expired 1–grace_period_days ago (default: 1–90 days)
          - ``DEAD``    — expired more than grace_period_days ago (matches legacy label)

        All statuses include ``days_expired`` when status != ACTIVE.

    Args:
        session: SQLAlchemy session.
        access_branch: Optional branch name filter.  ``None`` returns all branches.
        grace_period_days: Boundary between EXPIRED and DEAD (default: 90).
        dead_cutoff_days: Projects expired beyond this many days are omitted entirely
                          (default: 365).
    """
    params = {
        'branch': access_branch,
        'dead_cutoff': dead_cutoff_days,
    }

    rows = session.execute(_SQL_PROJECT_GROUP_STATUS, params).fetchall()

    today = date.today()

    # Accumulate per (branch, group_name): collect resource statuses + track max end_date
    #   branches[branch_name][group_name] = {
    #       'project_active': bool,
    #       'panel': str | None,
    #       'resources': [(resource_name, end_date), ...],
    #       'max_end_date': date | None,
    #   }
    branches: Dict[str, Dict] = {}

    for row in rows:
        branch_name = row.access_branch_name

        b = branches.setdefault(branch_name, {})
        proj = b.setdefault(row.group_name, {
            'project_active': row.project_active,
            'panel': row.panel,
            'resources': [],
            'max_end_date': None,
        })

        end_date = row.end_date.date() if row.end_date else None

        if end_date is not None:
            proj['resources'].append({
                'resourceName': row.resource_name,
                'endDate': end_date.isoformat(),
            })
            if proj['max_end_date'] is None or end_date > proj['max_end_date']:
                proj['max_end_date'] = end_date

    # Assemble final output
    result: Dict[str, List[Dict]] = {}

    for branch_name, projects in branches.items():
        project_list = []
        for group_name, proj in sorted(projects.items()):
            max_end = proj['max_end_date']

            if max_end is not None and max_end >= today:
                status = 'ACTIVE'
                days_expired = None
            else:
                days_expired = (today - max_end).days if max_end is not None else None
                if days_expired is not None and days_expired > grace_period_days:
                    status = 'DEAD'
                else:
                    status = 'EXPIRED'

            entry: Dict = {
                'groupName':    group_name,
                'panel':        proj['panel'],
                'autoRenewing': False,  # Not stored in SAM DB; always False in legacy
                'projectActive': bool(proj['project_active']),
                'status':        status,
                'expiration':    max_end.isoformat() if max_end else None,
                'resourceGroupStatuses': proj['resources'],
            }
            if days_expired is not None:
                entry['days_expired'] = days_expired

            project_list.append(entry)

        result[branch_name] = project_list

    return result
