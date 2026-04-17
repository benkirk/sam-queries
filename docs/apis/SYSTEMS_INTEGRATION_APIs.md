# Systems Integration APIs

Three read-only API endpoints that serve LDAP provisioning tools, PBS batch
schedulers, and other HPC systems integration workflows.  All reproduce output
from the legacy SAM Java system (`sam.ucar.edu/api/protected/admin/`) and share
a common design: bulk raw-SQL queries, 5-minute response caching, and the same
authentication/authorization model as all other v1 APIs.

---

## Background: Access Branches

HPC resources are grouped into logical *access branches* that map to LDAP
directory trees provisioned on each system:

| Branch     | Purpose                          | Typical resources                   |
|------------|----------------------------------|-------------------------------------|
| `hpc`      | Batch compute accounts           | Derecho, Derecho GPU, Casper, ...   |
| `hpc-data` | Data-access / interactive logins | GLADE, Campaign Store, ...          |
| `hpc-dev`  | Developer / special accounts     | Small set of internal projects      |

The mapping is stored in `access_branch_resource` (join table between
`access_branch` and `resources`).  Only resources with `configurable = TRUE`
are provisioned.

---

## Authentication and Caching

Both endpoints require an authenticated session:

- **Auth**: `@login_required` + role-based permission check (see below)
- **Cache**: Responses are cached for **5 minutes** (`SimpleCache` in
  development, configurable for production).
- **Cache invalidation**: `POST .../refresh` clears the cache immediately.

---

## 1. Directory Access API

**Base URL**: `/api/v1/directory_access/`  
**Legacy equivalent**: `GET /api/protected/admin/sysacct/directoryaccess`  
**Permission required**: `VIEW_USERS`  
**Source**: `src/webapp/api/v1/directory_access.py`, `src/sam/queries/directory_access.py`

Provides the unix group and account data that LDAP provisioning systems use to
create and maintain accounts on HPC resources.  Returns three layers of group
membership and full unix account details (uid, gid, home directory, shell, gecos)
for every active user on each access branch.

### Endpoints

#### `GET /api/v1/directory_access/`

Returns data for **all** access branches.

**Response schema**:

```json
{
  "accessBranchDirectories": [
    {
      "accessBranchName": "hpc",
      "unixGroups": [
        {
          "accessBranchName": "hpc",
          "groupName":        "wyom0218",
          "gid":              68283,
          "usernames":        ["benkirk", "jsmith", "...]
        },
        {
          "accessBranchName": "hpc",
          "groupName":        "ncar",
          "gid":              1000,
          "usernames":        ["benkirk", "jsmith", "..."]
        }
      ],
      "unixAccounts": [
        {
          "accessBranchName": "hpc",
          "username":         "benkirk",
          "uid":              12345,
          "gid":              1000,
          "homeDirectory":    "/glade/u/home/benkirk",
          "loginShell":       "/bin/bash",
          "name":             "Benjamin Kirk",
          "upid":             9876,
          "gecos":            "Benjamin Kirk,UCAR/USS,"
        }
      ]
    },
    {
      "accessBranchName": "hpc-data",
      "unixGroups": [...],
      "unixAccounts": [...]
    },
    {
      "accessBranchName": "hpc-dev",
      "unixGroups": [...],
      "unixAccounts": [...]
    }
  ]
}
```

#### `GET /api/v1/directory_access/<branch>`

Returns data for a **single** access branch.  Response shape is identical —
`accessBranchDirectories` will contain exactly one entry.

Returns **404** if the branch name is not recognized or has no data.

**Example**: `GET /api/v1/directory_access/hpc`

#### `POST /api/v1/directory_access/refresh`

Invalidates the response cache.  The next GET request will recompute from the
database.

**Response**: `{"status": "ok"}`

---

### Group Pipeline

Unix groups are assembled in three layers (matching the legacy
`HibernateSysAcctQuery` pipeline):

1. **Implicit project groups** — every active project with a current allocation
   (within the 90-day grace period) linked to the branch via
   `account → resource → access_branch_resource`.  Group name = lowercase
   `projcode`; GID = `project.unix_gid`.

2. **Explicit adhoc groups** — `AdhocGroup` records whose tags match an access
   branch name (`adhoc_group_tag.tag = access_branch.name`).  Members come
   from `adhoc_system_account_entry`.

3. **Global `ncar` group** (GID 1000) — every user already present on the
   branch is injected as a member.  This group always exists on every branch.

The result dict returned by `group_populator()` also contains a symmetric
`user_groups` index for O(1) per-user group lookups:

```python
branch_data['user_groups']['benkirk']
# → [{"group_name": "wyom0218", "gid": 68283},
#    {"group_name": "ncar",     "gid": 1000}, ...]
```

---

### Unix Account Fields

| Field           | Source                                                          |
|-----------------|-----------------------------------------------------------------|
| `username`      | `users.username`                                                |
| `uid`           | `users.unix_uid`                                               |
| `gid`           | `users.primary_gid` (default: 1000)                            |
| `homeDirectory` | `user_resource_home.home_directory` → resource default → `/home/<username>` |
| `loginShell`    | `user_resource_shell` → resource default → `/bin/tcsh`          |
| `name`          | `nickname` (or `first_name`) + `last_name`                     |
| `upid`          | `users.upid`                                                    |
| `gecos`         | `"{name},{org},{phone}"` — see below                            |

**gecos format**: `"{name},{org},{phone}"`
- `org`: `"UCAR/{acronym}"` for internal staff (from `user_organization`),
  institution name for external users (from `user_institution`), empty if neither.
- `phone`: UCAR Office phone preferred over External Office phone; empty if none.

**hpc-data kludge**: home directory and login shell for the `hpc-data` branch
are resolved using the `GLADE*` resource attributes rather than the `hpc-data`
access branch resource.  This reproduces legacy behaviour.

---

### Constants

Defined in `src/sam/queries/directory_access.py` (with the group-name
defaults re-exported from `src/sam/core/groups.py`) matching
`Constants.java`:

| Constant                     | Value      | Meaning                                      |
|------------------------------|------------|----------------------------------------------|
| `ACCESS_GRACE_PERIOD`        | 90 days    | Allocations expired within this window are included |
| `DEFAULT_COMMON_GROUP`       | `"ncar"`   | Name of the system-wide default unix group (from `sam.core.groups`) |
| `DEFAULT_COMMON_GROUP_GID`   | 1000       | GID for the `ncar` group; also the fallback when `primary_gid` is NULL |
| `DEFAULT_SHELL`              | `/bin/tcsh`| Fallback login shell                         |
| `DEFAULT_HOME_BASE`          | `/home`    | Fallback home directory base path            |

---

### Scale (production approximate)

| Branch   | Unix Groups | Unix Accounts |
|----------|-------------|---------------|
| hpc      | ~1,600      | ~4,300        |
| hpc-data | ~1,600      | ~4,300        |
| hpc-dev  | ~10         | ~30           |

---

## 2. Project Access API

**Base URL**: `/api/v1/project_access/`  
**Legacy equivalent**: `GET /api/protected/admin/sysacct/groupstatus/{access_branch}`  
**Permission required**: `VIEW_PROJECTS`  
**Source**: `src/webapp/api/v1/project_access.py`, `src/sam/queries/project_access.py`

Provides per-access-branch project group status: which projects are active on
each branch, which resources they hold allocations on, and the current
allocation lifecycle status.  Companion to the Directory Access API.

### Endpoints

#### `GET /api/v1/project_access/`

Returns status for **all** access branches.

**Response schema**:

```json
{
  "hpc": [
    {
      "groupName":    "wyom0218",
      "panel":        "WRAP",
      "autoRenewing": false,
      "projectActive": true,
      "status":       "ACTIVE",
      "days_remaining": 452,
      "expiration":   "2028-07-01",
      "resourceGroupStatuses": [
        {"resourceName": "Derecho",        "endDate": "2028-07-01"},
        {"resourceName": "Derecho GPU",    "endDate": "2028-07-01"},
        {"resourceName": "Casper",         "endDate": "2028-07-01"},
        {"resourceName": "Campaign_Store", "endDate": "2028-07-01"}
      ]
    },
    {
      "groupName":    "uclb0049",
      "panel":        "UNIV USS",
      "autoRenewing": false,
      "projectActive": true,
      "status":       "DEAD",
      "days_expired": 124,
      "expiration":   "2025-12-01",
      "resourceGroupStatuses": [
        {"resourceName": "Derecho", "endDate": "2025-12-01"}
      ]
    }
  ],
  "hpc-data": [...],
  "hpc-dev":  [...]
}
```

Projects are sorted alphabetically by `groupName` within each branch.

#### `GET /api/v1/project_access/<branch>`

Returns status for a **single** access branch.  Response is a dict with one
key (the branch name) to keep the shape consistent with the all-branches route:

```json
{"hpc": [...]}
```

Returns **404** if the branch name is not recognized or has no data.

**Example**: `GET /api/v1/project_access/hpc-dev`

#### `POST /api/v1/project_access/refresh`

Invalidates the response cache.

**Response**: `{"status": "ok"}`

---

### Response Fields

| Field                    | Type    | Description                                              |
|--------------------------|---------|----------------------------------------------------------|
| `groupName`              | string  | Lowercase project code (`projcode`)                      |
| `panel`                  | string  | Allocation type name (e.g. `"WRAP"`, `"UNIV USS"`)       |
| `autoRenewing`           | bool    | Always `false` — not stored in SAM database              |
| `projectActive`          | bool    | `project.active` flag                                    |
| `status`                 | string  | See status values below                                  |
| `days_remaining`         | int     | Days until expiration — present when `ACTIVE` or `EXPIRING` |
| `days_expired`           | int     | Days since expiration — present when `EXPIRED` or `DEAD` |
| `expiration`             | string  | ISO date (`YYYY-MM-DD`): max `end_date` across all resources |
| `resourceGroupStatuses`  | array   | Per-resource `{resourceName, endDate}` pairs             |

---

### Status Values

Status is computed from the maximum allocation `end_date` across all resources
for the project.  Thresholds match legacy Java `DefaultGroupStatusQuery` and
`InfrastructureConfig` defaults:

| Status      | Condition                                    | Extra field      |
|-------------|----------------------------------------------|------------------|
| `ACTIVE`    | Expires more than **30 days** in the future  | `days_remaining` |
| `EXPIRING`  | Expires within the next **30 days**          | `days_remaining` |
| `EXPIRED`   | Expired **1–90 days** ago                    | `days_expired`   |
| `DEAD`      | Expired **more than 90 days** ago            | `days_expired`   |

Projects whose last allocation expired more than **180 days** ago are excluded
from results entirely (controlled by `dead_cutoff_days`).

> **Note**: The legacy Java endpoint emitted only `ACTIVE` and `DEAD`.  `EXPIRING`
> and `EXPIRED` are new states added here for finer-grained consumer logic (e.g.
> expiration notifications, grace-period workflows).

---

### Constants

Defined in `src/sam/queries/project_access.py`, all matching
`InfrastructureConfig.java` defaults:

| Constant              | Value    | Java property                              | Meaning                                    |
|-----------------------|----------|--------------------------------------------|-------------------------------------------|
| `ACCESS_GRACE_PERIOD` | 90 days  | `accessBranch.group.active.gracePeriod`    | `EXPIRED` vs `DEAD` boundary              |
| `DEAD_CUTOFF_DAYS`    | 180 days | `accessBranch.group.query.gracePeriod`     | Projects older than this are omitted      |
| `WARNING_PERIOD_DAYS` | 30 days  | `accessBranch.group.warningPeriod`         | `ACTIVE` vs `EXPIRING` boundary           |

---

### Scale (production approximate, hpc branch)

| Status   | Count  |
|----------|--------|
| ACTIVE   | ~1,200 |
| EXPIRING | ~85    |
| EXPIRED  | ~73    |
| DEAD     | ~88    |
| **Total**| **~1,450** |

---

## 3. FairShare Tree API

**Base URL**: `/api/v1/fstree_access/`  
**Legacy equivalent**: `GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>`  
**Permission required**: `VIEW_PROJECTS`  
**Source**: `src/webapp/api/v1/fstree_access.py`, `src/sam/queries/fstree_access.py`

Provides the PBS batch scheduler fairshare tree: a hierarchical grouping of active
HPC/DAV projects by `Facility → AllocationType → Project → Resource`, with per-node
fairshare percentages and, at the resource level, allocation amounts, adjusted charge
usage, balance, and active user rosters.

### Endpoints

#### `GET /api/v1/fstree_access/`

Returns the fairshare tree for **all** HPC+DAV resources.

**Response schema**:

```json
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
                    {"username": "bonan",   "uid": 4681}
                  ]
                }
              ]
            }
          ]
        }
      ]
    },
    {"name": "NCAR", "...": "..."},
    {"name": "UNIV", "...": "..."}
  ]
}
```

Projects are sorted alphabetically by `projectCode` within each `AllocationType`.

#### `GET /api/v1/fstree_access/<resource_name>`

Returns the tree filtered to a **single resource**.  Response shape is identical —
only resource entries matching the requested resource appear under each project.

Resource names with spaces must be URL-encoded by the caller:

```
GET /api/v1/fstree_access/Derecho
GET /api/v1/fstree_access/Derecho%20GPU
GET /api/v1/fstree_access/Casper
```

Returns **404** if the resource name is not recognized or has no active project data.

#### `POST /api/v1/fstree_access/refresh`

Invalidates the response cache.  The next GET will recompute from the database.

**Response**: `{"status": "ok"}`

---

### Tree Hierarchy

The tree mirrors the `Facility → Panel → AllocationType → Project` taxonomy stored
in the SAM database:

| Level | DB source | Notes |
|---|---|---|
| `facilities[].name` | `facility.facility_name` | Active facilities only |
| `facilities[].fairSharePercentage` | `facility_resource.fair_share_percentage` (if set) else `facility.fair_share_percentage` | Resource-specific override takes precedence |
| `allocationTypes[].name` | `{facility.code}_{allocation_type.replaceAll("\\W","")}` | e.g. CSL facility + "CSL" type → `"C_CSL"`, NCAR + "Director Reserve" → `"N_DirectorReserve"` |
| `allocationTypes[].fairSharePercentage` | `allocation_type.fair_share_percentage` | |
| `projects[].projectCode` | `project.projcode` | Original case (not lowercased) |

---

### Resource-Level Fields

| Field | Type | Source |
|---|---|---|
| `name` | string | `resources.resource_name` |
| `accountStatus` | string | Computed — see below |
| `cutoffThreshold` | int | `account.cutoff_threshold` (default: 100) |
| `adjustedUsage` | int | `SUM(comp+dav charges) + SUM(charge_adjustments)` over active allocation window |
| `balance` | int | `allocationAmount - adjustedUsage` |
| `allocationAmount` | int | `allocation.amount` for the current active allocation |
| `users[].username` | string | `users.username` |
| `users[].uid` | int | `users.unix_uid` |

**adjustedUsage** includes `comp_charge_summary` + `dav_charge_summary` +
`charge_adjustment` records within `[allocation.start_date, allocation.end_date]`.
This matches the legacy Java `ProjectAccountDetailDTO.getDebit()` definition.

**balance** = `allocationAmount - adjustedUsage`.

**adjustedUsage** is computed via MPTT subtree rollup — a parent project's usage
includes charges from all descendant sub-projects.  This matches the legacy Java
`ProjectAccountTreeQuery` behavior.

**accountStatus** values (priority order, matching `DefaultAccountStatusCalculator.java`):

| Priority | Value | Condition |
|---|---|---|
| 1 | `"Overspent"` | `adjustedUsage > allocationAmount` |
| 2 | `"Exceed Two Thresholds"` | both N-day usage windows exceeded per-account thresholds |
| 3 | `"Exceed One Threshold"` | exactly one N-day window exceeded |
| 4 | `"Normal"` | default |

**N-day threshold logic** (from `NDayUsagePeriod.java`):
```
threshold_alloc = P × allocationAmount / (allocation_duration_days − 1)
use_limit       = threshold_alloc × (account.first_threshold or second_threshold) / 100
exceeded        = window_charges_in_P_days > use_limit
```
where P ∈ {30, 90} days.  Threshold percentages come from `account.first_threshold`
(30-day) and `account.second_threshold` (90-day).  Both are `NULL` for ~99.7% of
accounts, so the threshold check is skipped for almost all accounts.

**Parent → child propagation**: If a parent project's `accountStatus` is non-Normal,
that status cascades to all child projects on the same resource (pre-order walk,
matching `DefaultAccountStatusCalculator.defineStatusFromParent()`).

---

### Resource Scope

Resources included match `FairshareTreeResourceSelector.java`'s filter of
`ActivityType IN (HPC, DAV, COMP)` — mapped to `resource_type IN ('HPC', 'DAV')`
in the Python schema, combined with `configurable = TRUE`:

| Resource | Type |
|---|---|
| Derecho, Derecho GPU | HPC |
| Casper GPU | HPC |
| Gust, Gust GPU | HPC |
| Cheyenne, Laramie, DNext | HPC |
| Casper, Anemone | DAV |

---

### Constants

No grace-period cutoffs apply (unlike `project_access`).  All active projects
(those with a current non-deleted allocation) appear in the tree.

---

### Scale (production approximate)

| Resource | Facilities | Projects |
|---|---|---|
| Derecho | 6 | ~1,260 |
| Casper | 6 | ~1,430 |
| All HPC+DAV | 6 | ~1,470 (combined unique) |

---

### Performance

| Request | Uncached | Cached |
|---|---|---|
| Single resource (e.g. `Derecho`) | ~0.7s | near-instant |
| All HPC+DAV resources | ~2.8s | near-instant |

Charge aggregation uses `Project.batch_get_account_charges()` (VALUES CTE primary
path) — one query per charge model with all account IDs and date windows inlined.
This avoids the LEFT JOIN fanout on charge summary tables that would otherwise
produce hundreds of millions of intermediate rows.

---

## Common Design Notes

### Shared Infrastructure

`directory_access` and `project_access` use the same `access_branch_resource`
JOIN pattern to filter by branch:

```sql
JOIN resources AS r          ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
JOIN access_branch_resource AS abr  ON r.resource_id = abr.resource_id
JOIN access_branch AS ab     ON abr.access_branch_id = ab.access_branch_id
WHERE (:branch IS NULL OR ab.name = :branch)
```

`fstree_access` uses a different join path through the facility taxonomy:

```sql
JOIN facility f
JOIN panel pa            ON (pa.facility_id = f.facility_id AND pa.active IS TRUE)
JOIN allocation_type at  ON (at.panel_id    = pa.panel_id   AND at.active IS TRUE)
JOIN project p           ON (p.allocation_type_id = at.allocation_type_id AND p.active IS TRUE)
JOIN account a           ON (a.project_id = p.project_id AND a.deleted IS FALSE)
JOIN resources r         ON (r.resource_id = a.resource_id AND r.configurable IS TRUE)
JOIN resource_type rt    ON (rt.resource_type_id = r.resource_type_id
                              AND rt.resource_type IN ('HPC', 'DAV'))
WHERE (:resource IS NULL OR r.resource_name = :resource)
```

### Performance

All three APIs use raw SQL with bulk fetches to avoid N+1 ORM loads.
Python-side aggregation assembles the nested response structure.
Cached responses are near-instant; see per-API scale tables for uncached times.

### Comparing with the Legacy System

The new APIs were validated against the live legacy system
(`sam.ucar.edu/api/protected/admin/`):

| API              | Legacy count | New count | Gap explanation                          |
|------------------|-------------|-----------|------------------------------------------|
| directory_access groups   | ~1,600 / branch | ~1,600 / branch | 1 project missing (DB mirror lag) |
| directory_access accounts | ~4,300 / branch | ~4,300 / branch | 1 user missing (same DB mirror lag) |
| project_access hpc        | 1,419       | ~1,463    | New API has wider `DEAD` window (180d vs legacy ~124d effective window); `EXPIRING`/`EXPIRED` states are new |
| project_access hpc-dev    | 8           | 8         | Exact match                              |
| fstree_access Derecho     | ~1,260      | ~1,257    | ~3 projects missing (DB mirror lag; same root cause as above) |

The one consistent gap (1 project, 1 user across LDAP APIs; ~3 projects in fstree)
is a known local database mirror sync lag — not a code defect.

### Cache Refresh Workflow

```bash
# Invalidate all caches after a bulk SAM update
curl -X POST -b session.cookie https://sam.ucar.edu/api/v1/directory_access/refresh
curl -X POST -b session.cookie https://sam.ucar.edu/api/v1/project_access/refresh
curl -X POST -b session.cookie https://sam.ucar.edu/api/v1/fstree_access/refresh
```
