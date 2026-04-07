# Plan: Directory Access API (`/api/v1/directory_access`)

## Context

Legacy SAM exposes `GET /api/protected/admin/sysacct/directoryaccess` (Java/Hibernate) which
LDAP provisioning tools (e.g. `sam_ldap_json_populator.py`) consume to build passwd/group files for
HPC systems. We need to reproduce this endpoint under `/api/v1/directory_access` in the Flask webapp,
backed by reusable Python functions that can also serve other internal use cases.

The endpoint is large (**confirmed from live API**: hpc=1590 groups/4326 accounts,
hpc-data=1587/4326, hpc-dev=198/116) and latency-sensitive; a simple
eager ORM approach will be slow — the implementation should use focused SQL queries.

---

## Legacy Output Format (must match)

Jackson serializes `DirectoryAccess` → `AccessBranchDirectory` list:

```json
{
  "accessBranchDirectories": [
    {
      "accessBranchName": "hpc",
      "unixGroups": [
        { "accessBranchName":"hpc", "groupName":"scsg0001", "gid":68283, "usernames":["benkirk","user2"] }
      ],
      "unixAccounts": [
        {
          "accessBranchName":"hpc", "username":"benkirk",
          "uid":12345, "gid":1000,
          "homeDirectory":"/glade/u/home/benkirk", "loginShell":"/bin/bash",
          "name":"Benjamin Kirk", "upid":12345,
          "gecos":"Benjamin Kirk,UCAR/USS,"
        }
      ]
    },
    { "accessBranchName": "hpc-data", ... },
    { "accessBranchName": "hpc-dev",  ... }
  ]
}
```

**Notes on fields:**
- `phoneNumber`, `organizationAcronym`, `institutionName` are `@JsonIgnoreProperties` in legacy → **omit** from new API
- `gecos` is a computed field: `"{name},{org_info},{phone}"` — can be `""` for external users
- `gid` defaults to `1000` if `users.primary_gid` is NULL
- There are currently 3 access branches: `hpc`, `hpc-data`, `hpc-dev`

---

## Group Sources (three layers — matches legacy pipeline)

### 1. Implicit Project Groups
- Each `Project` (where `project.active=1` and `resource.configurable=1`) with a valid allocation
  within the **90-day grace period** (`al.end_date + 90 days > NOW()`) linked to the access branch
  via `account → resource → access_branch_resource → access_branch` becomes a group.
- `group_name` = `projcode.lower()`
- `gid` = `project.unix_gid`
- Members = active `AccountUser` entries (`au.start_date ≤ NOW() AND (au.end_date IS NULL OR au.end_date > NOW())`)
  joined to `User` where `user.active=1`

### 2. Explicit AdhocGroups
- `AdhocGroup` (where `active=1`) appears in access branches whose names match any of its **tags**
  (`adhoc_group_tag.tag` case-insensitive match to `access_branch.name`)
- `group_name` = `adhoc_group.group_name`
- `gid` = `adhoc_group.unix_gid`
- Members = `AdhocSystemAccountEntry` entries for the group, filtered by `access_branch_name`

### 3. Global "ncar" Group (`gid=1000`)
- After all groups are built, collect every unique username across all access branches
- Add each as a member of the `ncar` group in that branch (replicates `GlobalGroupInjectingSystemDirectoryReader`)

---

## User Data Sources

Per-user per-access-branch:
- `username`, `unix_uid`, `upid` — from `users` table
- `gid` — `users.primary_gid` (default `1000` if NULL)
- `name` — `CONCAT(IFNULL(nickname, first_name), ' ', last_name)`
- `homeDirectory` — `user_resource_home.home_directory` for the "key resource" of the branch,
  fallback to `resource.default_home_dir_base + '/' + username`, fallback `/home/{username}`
- `loginShell` — `user_resource_shell → resource_shell.path` for the key resource, fallback
  `resource.default_resource_shell_id → resource_shell.path`, fallback `/bin/tcsh`

**"Key resource" kludge** (from legacy SQL): The "hpc-data" branch uses `GLADE*` resource attributes
for home dir / shell. This is implemented as: for a given `access_branch.name`, find the matching
`resource` by `resource_name = access_branch_name` (case-insensitive), with `hpc-data` falling back
to any `resource_name LIKE 'GLADE%'`.

---

## Implementation Plan

### Step 1 — Backend query module: `src/sam/queries/directory_access.py`

**`group_populator(session, access_branch=None, active_only=True, grace_period_days=90)`**

Returns `(groups, user_groups)` where:
- `groups`: `dict[str, dict]` — `{group_name: {"gid": int, "members": set[str], "access_branches": set[str]}}`
- `user_groups`: `dict[str, list[str]]` — `{username: [group_name, ...]}`

Implementation (use two focused raw-SQL or ORM queries per source):

```python
GRACE_PERIOD = 90  # days

# --- Implicit project groups ---
# Query 1: groups (no-member entries for groups that should exist)
# Same as legacy sysAcctGroups
# Query 2: members
# Same as legacy sysAcctMembers

# --- Explicit adhoc groups ---
# Query AdhocGroup + AdhocGroupTag + AccessBranch (tag→branch name match)
# Query AdhocSystemAccountEntry for members

# --- Global "ncar" group ---
# After above, inject all usernames into "ncar" group per branch
```

**`user_populator(session, access_branch=None, active_only=True, grace_period_days=90)`**

Returns `dict[str, dict]` — `{username: {uid, gid, homeDirectory, loginShell, name, upid, gecos}}`

Use raw SQL (mirrors legacy `unixAccountForAccessBranchNameAndUsername` query) to avoid N+1 for
home dirs and shells. The query joins through account → allocation (grace period) → access_branch,
then LEFT JOINs user_resource_home and user_resource_shell using the "key resource" kludge for
hpc-data.

Returned gecos: `"{name},,"`  (phone omitted since it's `@JsonIgnoreProperties` in legacy output;
org acronym/institution also omitted — gecos simplifies to `"{name},,"`).

**Note:** `user_populator` users are determined by access-branch membership (same join as
sysAcctMembers) not the group_populator output, to avoid a two-pass approach.

### Step 2 — API endpoint: `src/webapp/api/v1/directory_access.py`

```python
bp = Blueprint('api_directory_access', __name__)
register_error_handlers(bp)

@bp.route('/', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)  # admin-only, same level as sysacct
def get_directory_access():
    branch = request.args.get('access_branch')   # optional filter
    groups, _ = group_populator(db.session, access_branch=branch)
    users     = user_populator(db.session, access_branch=branch)
    return jsonify(build_response(groups, users))

@bp.route('/<access_branch_name>', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)
def get_directory_access_branch(access_branch_name):
    groups, _ = group_populator(db.session, access_branch=access_branch_name)
    users     = user_populator(db.session, access_branch=access_branch_name)
    if not groups and not users:
        abort(404)
    return jsonify(build_response(groups, users, single_branch=access_branch_name))
```

`build_response()` assembles the `accessBranchDirectories` list matching legacy JSON shape.

### Step 3 — Blueprint registration: `src/webapp/run.py`

Add import and `app.register_blueprint(api_directory_access_bp, url_prefix='/api/v1/directory_access')`
after existing API blueprint registrations (lines 186–191).

### Step 4 — Tests: `tests/api/test_directory_access.py`

```python
class TestDirectoryAccessEndpoint:
    def test_full_response_structure(self, auth_client): ...
    def test_access_branch_filter(self, auth_client): ...
    def test_invalid_branch_returns_404(self, auth_client): ...
    def test_unauthenticated_returns_401(self, client): ...
    def test_group_has_required_fields(self, auth_client): ...
    def test_user_has_required_fields(self, auth_client): ...
    def test_ncar_global_group_present(self, auth_client): ...
```

Unit tests in `tests/unit/test_directory_access_queries.py` for `group_populator` and
`user_populator` directly (verify counts, ncar group injection, access_branch filtering).

---

## Critical Files

| File | Action |
|------|--------|
| `src/sam/queries/directory_access.py` | **Create** — `group_populator()`, `user_populator()` |
| `src/sam/queries/__init__.py` | **Edit** — export new functions |
| `src/webapp/api/v1/directory_access.py` | **Create** — Flask Blueprint |
| `src/webapp/run.py` | **Edit** — register blueprint (lines ~191) |
| `tests/api/test_directory_access.py` | **Create** — API tests |
| `tests/unit/test_directory_access_queries.py` | **Create** — unit tests for query functions |

### ORM models to import (no changes needed)
- `src/sam/core/groups.py` — `AdhocGroup`, `AdhocGroupTag`, `AdhocSystemAccountEntry`
- `src/sam/security/access.py` — `AccessBranch`, `AccessBranchResource`
- `src/sam/projects/projects.py` — `Project`
- `src/sam/accounting/accounts.py` — `Account`, `AccountUser`
- `src/sam/accounting/allocations.py` — `Allocation`
- `src/sam/resources/resources.py` — `Resource`, `ResourceShell`
- `src/sam/core/users.py` — `User`, `UserResourceHome`, `UserResourceShell`

---

## Verification

### End-to-end (after implementation)
```bash
# 1. Start the webapp
docker compose up

# 2. Hit the new endpoint (will need auth token or DEV_AUTO_LOGIN)
curl -s http://localhost:5050/api/v1/directory_access/ | python -m json.tool | head -50

# 3. Compare with legacy
python sam_ldap_json_populator.py  > /tmp/legacy.json
curl -s http://localhost:5050/api/v1/directory_access/ > /tmp/new.json
python -c "
import json
old = json.load(open('/tmp/legacy.json'))
new = json.load(open('/tmp/new.json'))
# compare accessBranchDirectories counts and spot-check entries
"
```

### Tests
```bash
source etc/config_env.sh
pytest tests/api/test_directory_access.py tests/unit/test_directory_access_queries.py -v
pytest tests/ --no-cov  # ensure no regressions
```

---

## Resolved Design Decisions

1. **gecos format**: Full legacy format `"{name},{org},{phone}"` — confirmed from live API:
   - `org`: `"UCAR/{org.acronym}"` (via `UserOrganization` → `Organization.acronym`,
     `src/sam/core/organizations.py:149`) for UCAR staff; `"{institution.name}"` (via
     `UserInstitution` → `Institution.name`, `src/sam/core/organizations.py:391`) for external
     users; `""` if neither. Use `MAX()` aggregate as users may have multiple orgs/institutions.
   - `phone`: UCAR Office first, then External Office (`Phone.phone_type.phone_type` comparison);
     `""` if none. `Phone` model at `src/sam/core/users.py:591`.
   - Examples: `"Aaron Hill,UNIVERSITY OF OKLAHOMA,206-890-8299"` / `"Abdullah Alshaffi,UCAR/HAOVISITORS,"`

2. **hpc-data resource kludge**: Handled in Python — if `access_branch.name == 'hpc-data'`,
   look up the key resource by matching `resource_name LIKE 'GLADE%'` for home dir / shell defaults.

3. **Caching**: Use `@cache.cached(timeout=300, query_string=True)` from the existing Flask-Cache
   extension (`webapp.extensions.cache`) — matches the pattern in `dashboards/allocations/blueprint.py`.
   Add a `POST /api/v1/directory_access/refresh` endpoint (requires `Permission.VIEW_USERS`) that
   calls `cache.delete_memoized(...)` to invalidate, matching the `purge_cache()` pattern.
