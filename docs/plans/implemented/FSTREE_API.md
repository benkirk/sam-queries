# Plan: `GET /api/v1/fstree_access` — PBS FairShare Tree Endpoint

## Context

Legacy SAM exposes `GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>` which the PBS
batch scheduler and LDAP tooling consume to build the fairshare tree for job scheduling.  The
tree groups active projects under `Facility → AllocationType → Project → Resource`, with
per-node fairshare percentages, allocation amounts, charge usage, and user rosters.

The new endpoint reproduces this output under `/api/v1/fstree_access` following the raw-SQL
bulk-query pattern established in `directory_access.py` and `project_access.py`.

---

## Legacy Output Schema

```json
{
  "name": "fairShareTree",
  "facilities": [
    {
      "name": "CSL",
      "description": "Climate Simulation Laboratory",
      "fairSharePercentage": 1.0,
      "allocationTypes": [
        {
          "name": "C_CSL",
          "description": "CSL",
          "fairSharePercentage": 0.0,
          "projects": [
            {
              "projectCode": "P93300041",
              "active": true,
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
                    ...
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

---

## Field Mapping (legacy Java → Python/DB)

| Response field | Source |
|---|---|
| `facilities[].name` | `facility.facility_name` |
| `facilities[].description` | `facility.description` |
| `facilities[].fairSharePercentage` | `facility_resource.fair_share_percentage` if not NULL (resource query) else `facility.fair_share_percentage` |
| `allocationTypes[].name` | `{facility.code}_{allocation_type.replaceAll("\\W","")}` (e.g. "C_CSL") |
| `allocationTypes[].description` | `allocation_type.allocation_type` |
| `allocationTypes[].fairSharePercentage` | `allocation_type.fair_share_percentage` |
| `projects[].projectCode` | `project.projcode` (original case — NOT lowercased) |
| `projects[].active` | `project.active` |
| `resources[].name` | `resources.resource_name` |
| `resources[].accountStatus` | Computed: `"Normal"` if adjustedUsage ≤ allocationAmount else `"Overspent"` |
| `resources[].cutoffThreshold` | `account.cutoff_threshold` (default 100) |
| `resources[].adjustedUsage` | `SUM(charges) + SUM(adjustments)` over active allocation window |
| `resources[].balance` | `allocationAmount - adjustedUsage` |
| `resources[].allocationAmount` | Active `allocation.amount` (most recent active for the account) |
| `resources[].users[].username` | `users.username` |
| `resources[].users[].uid` | `users.unix_uid` |

**AllocationType name construction** (from `FairShareTreeServiceJacksonImpl.java` line 173):
```
name = facilityCode + "_" + allocationType.replaceAll("\\W", "")
```
e.g. CSL facility (code "C") + "CSL" type → "C_CSL";
NCAR facility (code "N") + "Director Reserve" → "N_DirectorReserve"

---

## Resource Scope

Matching `FairshareTreeResourceSelector.java` which filters `ActivityType IN (HPC, DAV, COMP)`:
- Include resources where `resource_type.resource_type IN ('HPC', 'DAV')`
- `configurable = TRUE` filter (same gate as directory_access / project_access)
- Current HPC+DAV resources: Derecho, Derecho GPU, Casper, Casper GPU, Gust, Gust GPU,
  Anemone, Laramie, DNext, HPC_Futures_Lab, Cheyenne (if active)

The new API accepts an optional `resource` URL parameter:
- No resource → return all HPC+DAV resources
- `?resource=Derecho` or `/<resource_name>` → filter to one resource

---

## Key Design Decisions

### 1. Performance: Three Bulk SQL Queries

With ~1,200–1,400 active projects × ~5 resources each ≈ 6,000–7,000 project-resource pairs,
calling `project.get_detailed_allocation_usage()` per-project would make ~1,400 ORM calls —
too slow. Instead, use three bulk raw-SQL queries (same pattern as `project_access.py`):

**Query 1 — Tree skeleton + active allocations** (one row per project-resource-account):
Joins `facility → panel → allocation_type → project → account → resource → allocation`.

**Query 2 — Charge totals per account**:
Sums `comp_charge_summary` + `dav_charge_summary` + `charge_adjustment` within each
account's active allocation date window. Returns `{account_id: (total_charges, adjustments)}`.

**Query 3 — Active users per account**:
Joins `account_user → users`, filtered to currently active date range.
Returns `{account_id: [{username, uid}, ...]}`.

Python assembly then merges these three dicts and builds the nested tree structure.

### 2. accountStatus: Simple Two-State (Future Work for 30/90-day Thresholds)

Legacy Java derives `accountStatus` from `ProjectAccountDetailDTO.getStatus()`, which uses
the 30-day and 90-day usage thresholds from `InfrastructureConfig` to produce states like
`"Normal"`, `"Warning"`, `"Overspent"`. These thresholds track 30-day usage trends.

**MVP**: Compute from current balance only:
```python
accountStatus = "Normal" if adjustedUsage <= allocationAmount else "Overspent"
```
Flag in code comment as future work; document in API docs.

### 3. OVERSPENT Tree Propagation (Future Work)

The prompt notes: *"when a rolled up project is OVERSPENT I think that should apply to the whole project tree."*

Legacy Java uses `ProjectAccountTreeQuery.getProjectAccountTree()` which aggregates charges
hierarchically via the MPPT nested-set tree. For MVP, we compute **per-account charges only**
(non-hierarchical). The `Project` model has `get_subtree_charges()` for future implementation.

Note: Most fstree projects are likely leaf nodes; the discrepancy would only affect multi-level
hierarchies with parent allocations.

### 4. Charges Include Adjustments

`adjustedUsage = SUM(comp_charges) + SUM(dav_charges) + SUM(charge_adjustments)` over the
allocation start_date to end_date window. This matches `project.get_detailed_allocation_usage()`
with `include_adjustments=True`.

---

## Implementation Plan

### 1. `src/sam/queries/fstree_access.py` (NEW, ~200 lines)

Public function:
```python
def get_fstree_data(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict:
    """
    Returns the fairshare tree dict for PBS scheduler consumption.

    Args:
        session: SQLAlchemy session
        resource_name: Optional resource filter (e.g. "Derecho").
                       None → return all HPC+DAV resources.
    Returns:
        {"name": "fairShareTree", "facilities": [...]}
    """
```

**Three SQL queries:**

```sql
-- Query 1: Tree skeleton (facility → alloc_type → project → account → resource → allocation)
SELECT
    f.facility_id,  f.facility_name,  f.code AS facility_code,
    f.description   AS facility_description,
    COALESCE(fr.fair_share_percentage, f.fair_share_percentage) AS facility_fsp,
    at.allocation_type_id, at.allocation_type, at.fair_share_percentage AS type_fsp,
    p.project_id,   p.projcode,        p.active AS project_active,
    a.account_id,   a.cutoff_threshold,
    r.resource_id,  r.resource_name,
    al.allocation_id,  CAST(al.amount AS SIGNED) AS allocation_amount,
    al.start_date,  al.end_date
FROM facility f
JOIN panel       pa  ON (pa.facility_id = f.facility_id AND pa.active IS TRUE)
JOIN allocation_type at ON (at.panel_id  = pa.panel_id  AND at.active IS TRUE)
JOIN project     p   ON (p.allocation_type_id = at.allocation_type_id AND p.active IS TRUE)
JOIN account     a   ON (a.project_id   = p.project_id   AND a.deleted  IS FALSE)
JOIN resources   r   ON (r.resource_id  = a.resource_id  AND r.configurable IS TRUE)
JOIN resource_type rt ON (rt.resource_type_id = r.resource_type_id
                           AND rt.resource_type IN ('HPC', 'DAV'))
LEFT JOIN facility_resource fr
       ON (fr.facility_id = f.facility_id AND fr.resource_id = r.resource_id)
LEFT JOIN allocation al
       ON (al.account_id = a.account_id AND al.deleted IS FALSE
           AND al.start_date <= NOW()
           AND (al.end_date IS NULL OR al.end_date >= NOW()))
WHERE f.active IS TRUE
  AND (:resource IS NULL OR r.resource_name = :resource)
ORDER BY f.facility_name, at.allocation_type, p.projcode, r.resource_name
```

```sql
-- Query 2: Charge totals per account over allocation window
SELECT
    a.account_id,
    COALESCE(SUM(ccs.charges), 0) AS comp_charges,
    COALESCE(SUM(dcs.charges), 0) AS dav_charges,
    COALESCE(SUM(adj.amount),  0) AS adjustments
FROM account a
JOIN project      p   ON (p.project_id = a.project_id AND p.active IS TRUE)
JOIN resources    r   ON (r.resource_id = a.resource_id AND r.configurable IS TRUE)
JOIN resource_type rt ON (rt.resource_type_id = r.resource_type_id
                           AND rt.resource_type IN ('HPC', 'DAV'))
JOIN allocation   al  ON (al.account_id = a.account_id AND al.deleted IS FALSE
                           AND al.start_date <= NOW()
                           AND (al.end_date IS NULL OR al.end_date >= NOW()))
LEFT JOIN comp_charge_summary    ccs ON (ccs.account_id = a.account_id
     AND ccs.activity_date >= al.start_date
     AND ccs.activity_date <= COALESCE(al.end_date, NOW()))
LEFT JOIN dav_charge_summary     dcs ON (dcs.account_id = a.account_id
     AND dcs.activity_date >= al.start_date
     AND dcs.activity_date <= COALESCE(al.end_date, NOW()))
LEFT JOIN charge_adjustment      adj ON (adj.account_id = a.account_id
     AND adj.adjustment_date >= al.start_date
     AND adj.adjustment_date <= COALESCE(al.end_date, NOW()))
WHERE a.deleted IS FALSE
  AND (:resource IS NULL OR r.resource_name = :resource)
GROUP BY a.account_id
```

```sql
-- Query 3: Active users per account
SELECT au.account_id, u.username, u.unix_uid
FROM account_user au
JOIN users        u   ON au.user_id    = u.user_id
JOIN account      a   ON au.account_id = a.account_id AND a.deleted IS FALSE
JOIN project      p   ON a.project_id  = p.project_id AND p.active  IS TRUE
JOIN resources    r   ON a.resource_id = r.resource_id AND r.configurable IS TRUE
JOIN resource_type rt ON r.resource_type_id = rt.resource_type_id
                          AND rt.resource_type IN ('HPC', 'DAV')
WHERE (au.end_date   IS NULL OR au.end_date   >= NOW())
  AND (au.start_date IS NULL OR au.start_date <= NOW())
  AND (:resource IS NULL OR r.resource_name = :resource)
ORDER BY au.account_id, u.username
```

**Python assembly:**
1. Build `charge_map: {account_id: (adjusted_usage, allocation_amount, balance, status)}`
2. Build `user_map: {account_id: [{"username": ..., "uid": ...}, ...]}`
3. Walk Query 1 rows → nested dict `facility_name → alloc_type → projcode → resource_name → {resource fields}`
4. Assemble final `{"name": "fairShareTree", "facilities": [...]}` respecting legacy key order

**accountStatus computation** (per account):
```python
adjusted_usage = comp_charges + dav_charges + adjustments
balance = (allocation_amount or 0) - adjusted_usage
account_status = "Normal" if adjusted_usage <= (allocation_amount or 0) else "Overspent"
```

Export `get_fstree_data` from `sam/queries/__init__.py`.

---

### 2. `src/webapp/api/v1/fstree_access.py` (NEW, ~80 lines)

Matching `project_access.py` blueprint pattern:

```
GET  /api/v1/fstree_access/                   → all HPC+DAV resources (cached 5 min)
GET  /api/v1/fstree_access/<resource_name>    → single resource (404 if no data)
POST /api/v1/fstree_access/refresh            → cache invalidation
```

- `@login_required` + `@require_permission(Permission.VIEW_PROJECTS)` on read routes
- `@cache.cached(timeout=300, query_string=True)` on read routes
- 404 if resource filter produces empty tree (no facilities)

`<resource_name>` in the URL will be URL-encoded for resources with spaces
(e.g., `Derecho%20GPU`); Flask handles decoding automatically.

---

### 3. `src/sam/queries/__init__.py` (MODIFY)
Add `get_fstree_data` import + export.

---

### 4. `src/webapp/run.py` (MODIFY)
Add import of `api_fstree_access_bp` and register at `/api/v1/fstree_access`.

---

### 5. Tests

**`tests/unit/test_fstree_queries.py`** (NEW, ~15 tests):
- `get_fstree_data()` returns dict with `"name"` and `"facilities"` keys
- Each facility has `name`, `description`, `fairSharePercentage`, `allocationTypes`
- Each allocationType has `name`, `description`, `fairSharePercentage`, `projects`
- AllocationType name follows `{facilityCode}_{type_no_special_chars}` convention
- Each project has `projectCode`, `active`, `resources`
- Each resource has `name`, `accountStatus`, `cutoffThreshold`, `adjustedUsage`, `balance`, `allocationAmount`, `users`
- `accountStatus` always "Normal" or "Overspent"
- `users` entries have `username` and `uid`
- Resource filter (e.g. `resource_name="Derecho"`) produces only Derecho in resources
- Unknown resource returns empty tree (no facilities or empty facilities)
- balance = allocationAmount - adjustedUsage (when allocation exists)

**`tests/api/test_fstree_access.py`** (NEW, ~10 tests):
- `GET /api/v1/fstree_access/` → 200 with valid schema
- `GET /api/v1/fstree_access/Derecho` → 200 with Derecho data
- `GET /api/v1/fstree_access/UnknownResource` → 404
- Unauthenticated → 302 or 401
- `POST /api/v1/fstree_access/refresh` → 200

---

## Critical Files

| File | Action |
|------|--------|
| `src/sam/queries/fstree_access.py` | CREATE — `get_fstree_data()` |
| `src/sam/queries/__init__.py` | MODIFY — add import + export |
| `src/webapp/api/v1/fstree_access.py` | CREATE — Flask blueprint |
| `src/webapp/run.py` | MODIFY — register blueprint |
| `tests/unit/test_fstree_queries.py` | CREATE — query tests |
| `tests/api/test_fstree_access.py` | CREATE — API endpoint tests |

**Reference** (pattern source, do not modify):
- `src/sam/queries/project_access.py` — bulk raw-SQL + Python assembly pattern
- `src/webapp/api/v1/project_access.py` — blueprint pattern
- `src/sam/queries/directory_access.py` — constants, ACCESS_GRACE_PERIOD
- `legacy_sam/.../FairShareTreeServiceJacksonImpl.java` — AllocationType name, resource scope
- `legacy_sam/.../FairshareTreeResourceSelector.java` — resource type filter (`HPC`, `DAV`)
- `legacy_sam/.../FacilityResourceDTOFacilityFacade.java` — fairshare percentage precedence

---

## Known Gaps / Future Work

| Item | Current MVP | Future |
|---|---|---|
| `accountStatus` | "Normal" / "Overspent" (balance check) | 30-day + 90-day usage trend thresholds matching legacy Java |
| OVERSPENT propagation | Per-account only | Propagate parent OVERSPENT to entire project subtree |
| Charge hierarchy | Per-account charges only | `get_subtree_charges()` for hierarchical aggregation |

---

## Verification

```bash
# Run tests
source etc/config_env.sh
pytest tests/unit/test_fstree_queries.py -v
pytest tests/api/test_fstree_access.py -v
pytest tests/ --no-cov

# Compare with legacy API output (from sam_tree_json_populator.py)
# Inspect structure, facility names, alloc type names, project counts
python3 sam_tree_json_populator.py > /tmp/legacy_fstree.json
curl -s -b session.cookie http://localhost:5050/api/v1/fstree_access/Derecho > /tmp/new_fstree.json

# Count projects in legacy vs new (expect small diff from DB mirror lag + MVP gaps)
python3 -c "
import json
with open('/tmp/legacy_fstree.json') as f: legacy = json.load(f)
# Count all projects
total = sum(len(at['projects']) for fac in legacy['facilities'] for at in fac['allocationTypes'])
print(f'Legacy project entries: {total}')
"
```
