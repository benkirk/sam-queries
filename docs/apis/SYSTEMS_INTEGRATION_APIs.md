# Systems Integration APIs

Two read-only API endpoints that serve LDAP provisioning tools and other HPC
systems integration workflows.  Both reproduce output from the legacy SAM Java
system (`sam.ucar.edu/api/protected/admin/sysacct/`) and share a common design:
raw-SQL queries over the `access_branch_resource` join path, 5-minute response
caching, and the same authentication/authorization model as all other v1 APIs.

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

Defined in `src/sam/queries/directory_access.py` matching `Constants.java`:

| Constant                  | Value      | Meaning                                      |
|---------------------------|------------|----------------------------------------------|
| `ACCESS_GRACE_PERIOD`     | 90 days    | Allocations expired within this window are included |
| `GLOBAL_LDAP_GROUP`       | `"ncar"`   | Name of the global group                     |
| `GLOBAL_LDAP_GROUP_UNIX_GID` | 1000    | GID for the `ncar` group                     |
| `DEFAULT_GID`             | 1000       | Fallback GID when `primary_gid` is NULL      |
| `DEFAULT_SHELL`           | `/bin/tcsh`| Fallback login shell                         |
| `DEFAULT_HOME_BASE`       | `/home`    | Fallback home directory base path            |

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

## Common Design Notes

### Shared Infrastructure

Both APIs use the same `access_branch_resource` JOIN pattern to filter by
branch:

```sql
JOIN resources AS r          ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
JOIN access_branch_resource AS abr  ON r.resource_id = abr.resource_id
JOIN access_branch AS ab     ON abr.access_branch_id = ab.access_branch_id
WHERE (:branch IS NULL OR ab.name = :branch)
```

Passing `branch = NULL` returns all branches in a single query; passing a
branch name filters to one.

### Performance

Both APIs use raw SQL with bulk fetches to avoid N+1 ORM loads across thousands
of groups and accounts.  Python-side aggregation assembles the nested response
structure.  Response times are typically under 2 seconds for a full all-branches
request; cached responses are near-instant.

### Comparing with the Legacy System

The new APIs were validated against the live legacy system
(`sam.ucar.edu/api/protected/admin/sysacct/`):

| API              | Legacy count | New count | Gap explanation                          |
|------------------|-------------|-----------|------------------------------------------|
| directory_access groups   | ~1,600 / branch | ~1,600 / branch | 1 project missing (DB mirror lag) |
| directory_access accounts | ~4,300 / branch | ~4,300 / branch | 1 user missing (same DB mirror lag) |
| project_access hpc        | 1,419       | ~1,463    | New API has wider `DEAD` window (180d vs legacy ~124d effective window); `EXPIRING`/`EXPIRED` states are new |
| project_access hpc-dev    | 8           | 8         | Exact match                              |

The one consistent gap (1 project, 1 user) is a known local database mirror
sync lag — not a code defect.

### Cache Refresh Workflow

```bash
# Invalidate both caches after a bulk SAM update
curl -X POST -b session.cookie https://sam.ucar.edu/api/v1/directory_access/refresh
curl -X POST -b session.cookie https://sam.ucar.edu/api/v1/project_access/refresh
```
