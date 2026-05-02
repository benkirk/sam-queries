# Plan: `GET /api/v1/project_access` — Project Group Status Endpoint

## Context

Legacy SAM exposes `GET /api/protected/admin/sysacct/groupstatus/{access_branch}` which the
LDAP provisioning toolchain consumes to know which projects are active per HPC access branch
(hpc, hpc-data, hpc-dev) and which resources they have allocations on.

The new endpoint reproduces this output under `/api/v1/project_access` using the SAM ORM and
raw-SQL query patterns already established in `directory_access.py`.

---

## Design Discussion: Existing Functions vs. Raw SQL

**User preference**: piece together from existing functions.

**Reality check**: the access-branch filter requires JOINs through `access_branch_resource` that
no existing query function exposes. With ~1,000 projects × multiple resources each, ORM lazy-
loading would generate thousands of SQL round-trips. The established pattern in
`directory_access.py` uses raw SQL for exactly this reason.

**Recommendation / compromise**:
- Write ONE new clean raw-SQL function in `sam/queries/project_access.py`
- Reuse the same constants (`ACCESS_GRACE_PERIOD`), JOIN structure, and blueprint pattern as
  `directory_access.py` — we are extending that pattern, not reinventing it
- The function is fully reusable from both the API and any future caller

---

## Legacy Output Schema

```json
[
  {
    "groupName": "wyom0218",
    "panel": "WRAP",
    "autoRenewing": false,
    "projectActive": true,
    "status": "ACTIVE",
    "expiration": "2028-07-01",
    "resourceGroupStatuses": [
      {"resourceName": "Derecho",       "endDate": "2028-07-01"},
      {"resourceName": "Derecho GPU",   "endDate": "2028-07-01"},
      {"resourceName": "Casper",        "endDate": "2028-07-01"},
      {"resourceName": "Campaign_Store","endDate": "2028-07-01"}
    ]
  },
  ...
]
```

**Field mapping investigation results**:
- `groupName` = `LOWER(project.projcode)`
- `panel` = `allocation_type.allocation_type` (same string as panel_name in our data)
- `autoRenewing` = **NOT in database**; `auto_renewing` field does not exist in any table —
  hardcode `False` (all observed production values are `false`)
- `projectActive` = `project.active`
- `status` = `"ACTIVE"` if `MAX(end_date) >= NOW()`, else `"EXPIRED"` (within grace period)
- `days_expired` = days since last allocation expired (`NULL` / omitted when status is ACTIVE); derived from `(NOW() - MAX(end_date)).days`
- `expiration` = `MAX(allocation.end_date)` across all resource allocations for the project
- `resourceGroupStatuses` = per-resource `(resource_name, end_date)` pairs

---

## Implementation Plan

### 1. `src/sam/queries/project_access.py` (NEW)

One public function:

```python
def get_project_group_status(
    session: Session,
    access_branch: Optional[str] = None,
    grace_period_days: int = ACCESS_GRACE_PERIOD,  # import from directory_access
) -> Dict[str, List[Dict]]:
    """
    Returns per-access-branch list of project group status dicts,
    matching legacy groupstatus endpoint output.

    Returns:
        {
            "hpc": [
                {
                    "groupName": "wyom0218",
                    "panel": "WRAP",
                    "autoRenewing": False,
                    "projectActive": True,
                    "status": "ACTIVE",
                    "expiration": "2028-07-01",
                    "resourceGroupStatuses": [
                        {"resourceName": "Derecho", "endDate": "2028-07-01"},
                        ...
                    ]
                }, ...
            ],
            "hpc-data": [...],
            "hpc-dev": [...],
        }
    """
```

**SQL design** — one query returning one row per (branch, project, resource):

```sql
SELECT ab.name            AS access_branch_name,
       LOWER(p.projcode)  AS group_name,
       at.allocation_type AS panel,
       p.active           AS project_active,
       r.resource_name    AS resource_name,
       MAX(al.end_date)   AS end_date
  FROM account AS a
  JOIN project AS p        ON (a.project_id = p.project_id)
  JOIN resources AS r      ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
  JOIN access_branch_resource AS abr ON r.resource_id = abr.resource_id
  JOIN access_branch AS ab ON abr.access_branch_id = ab.access_branch_id
  JOIN allocation AS al    ON (a.account_id = al.account_id
       AND al.deleted = FALSE
       AND (al.end_date + INTERVAL :grace_period DAY) > NOW())
  LEFT JOIN allocation_type AS at ON p.allocation_type_id = at.allocation_type_id
 WHERE (:branch IS NULL OR ab.name = :branch)
 GROUP BY ab.name, p.projcode, p.active, at.allocation_type, r.resource_name
 ORDER BY LOWER(p.projcode), r.resource_name
```

**Python aggregation** — group rows by `(branch, group_name)`:
- Build `resourceGroupStatuses` list per project
- `expiration` = `max(end_date)` across all resources (date string `YYYY-MM-DD`)
- `status` = `"ACTIVE"` if `max(end_date) >= today`, else `"EXPIRED"`
- `days_expired` = `(today - max(end_date)).days` when EXPIRED, else omit from response
- `autoRenewing` = `False` (hardcoded — field not in database)
- Sort final list by `groupName` within each branch

Export from `sam/queries/__init__.py`.

---

### 2. `src/webapp/api/v1/project_access.py` (NEW)

Matches `directory_access.py` blueprint pattern exactly:

```
GET /api/v1/project_access/           → all branches (cached 5 min)
                                        {"hpc": [...], "hpc-data": [...], "hpc-dev": [...]}
GET /api/v1/project_access/<branch>   → single branch, 404 if empty
                                        {"hpc": [...]}
POST /api/v1/project_access/refresh   → cache invalidation
```

- `@login_required` + `@require_permission(Permission.VIEW_PROJECTS)` on read routes
- `@cache.cached(timeout=300)` on read routes
- 404 if branch produces empty result

---

### 3. `src/webapp/run.py` (MODIFY)

Add 2 lines (import + register_blueprint) matching existing directory_access pattern.

---

### 4. Tests

**`tests/unit/test_project_access_queries.py`** (NEW, ~12 tests):
- `get_project_group_status()` returns dict keyed by branch names
- Each branch is a list of project dicts
- Required fields present: groupName, panel, autoRenewing, projectActive, status, expiration, resourceGroupStatuses
- resourceGroupStatuses entries have resourceName + endDate
- groupName is lowercase
- autoRenewing is always False
- Branch filter works; unknown branch returns empty
- expiration = max of all resource end_dates for that project

**`tests/api/test_project_access.py`** (NEW, ~10 tests):
- GET `/api/v1/project_access/` → 200 with all branches
- GET `/api/v1/project_access/hpc` → 200 with single branch list
- GET `/api/v1/project_access/unknown-branch` → 404
- Unauthenticated → 302 or 401
- POST `/api/v1/project_access/refresh` → 200

---

### Known Gap

`autoRenewing` is not in the SAM database at all — hardcoded `False`. This matches all
observed production values. Flag in code comment.

---

## Critical Files

| File | Action |
|------|--------|
| `src/sam/queries/project_access.py` | CREATE — `get_project_group_status()` |
| `src/sam/queries/__init__.py` | MODIFY — add import + export |
| `src/webapp/api/v1/project_access.py` | CREATE — Flask blueprint |
| `src/webapp/run.py` | MODIFY — register blueprint |
| `tests/unit/test_project_access_queries.py` | CREATE — query tests |
| `tests/api/test_project_access.py` | CREATE — API endpoint tests |

**Reference** (pattern source, do not modify):
- `src/sam/queries/directory_access.py` — SQL style, constants (`ACCESS_GRACE_PERIOD`)
- `src/webapp/api/v1/directory_access.py` — blueprint pattern

---

## Verification

```bash
# Run tests
source etc/config_env.sh
pytest tests/unit/test_project_access_queries.py -v
pytest tests/api/test_project_access.py -v
pytest tests/ --no-cov

# Compare with legacy production API
python3 sam_projects_json_populator.py > /tmp/legacy.json
curl -s http://localhost:5050/api/v1/project_access/hpc-dev > /tmp/new.json
# diff / compare counts
```
