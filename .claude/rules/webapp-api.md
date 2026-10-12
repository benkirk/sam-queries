---
paths:
  - "src/webapp/api/**/*"
  - "src/webapp/auth/**/*"
  - "src/sam/schemas/**/*"
  - "src/sam/security/**/*"
  - "src/sam/queries/*_access.py"
  - "tests/api/**/*"
---

# API, serialization and RBAC

Loaded with the API blueprints, schemas and RBAC. Route protection (§8) is in the root `CLAUDE.md`.

## Security / Integration models

- **Role**, **ApiCredentials**, **RoleApiCredentials** — legacy; only the API-key role names (`ROLE_XRAS`, `ROLE_API_ADMIN`, `ROLE_API_HEUV`) are read
- **SamuelRole** / **SamuelRolePermission** / **SamuelRoleGrant** (`sam/security/samuel_roles.py`,
  DDL `scripts/sql/create_samuel_roles.sql`): the RBAC catalog — roles extend one parent,
  SYSTEM_ADMIN implies everything, a grant names a `user` / `group` / `apikey` (optionally one
  facility), a revoke is a stamp. **`RBAC_SOURCE`** `defaults` (code, tests) | `db` (prod: TTL
  snapshot, NO fallback to code, API keys held to each token route's Permission). Admin → Roles &
  access (`MANAGE_ROLES`), `sam-admin rbac`; record `docs/plans/implemented/RBAC_DB_ROLES.md`.
- **XrasUserView**, **XrasAllocationView**, etc.: read-only database views

## Serialization Schemas (`sam/schemas/`)

Marshmallow schemas for API output. Three tiers per entity — pick by cost:

1. **Full** (`UserSchema`, `ProjectSchema`): all fields + nested relationships —
   single-object detail views only.
2. **List** (`UserListSchema`, `ProjectListSchema`): core fields, no expensive
   nesting — collection endpoints / pagination.
3. **Summary** (`UserSummarySchema`, `ProjectSummarySchema`): minimal identifiers —
   for nesting inside other schemas (e.g. `project.lead`).

Datetimes serialize to ISO automatically (DB stores naive datetimes). Use
`fields.Method` to expose `@property` values.

**`AllocationWithUsageSchema`** ⭐ is the key one — computes `used` / `remaining` /
`percent_used` / `charges_by_type` / `adjustments`. It needs context:
`{'account': ..., 'session': ..., 'include_adjustments': ...}` (+ `'state'`, a
read-model row, when the route has one).

Form-validation schemas are a separate concern — see §9 in `webapp-ui.md`; they live in
`sam/schemas/forms/` (one module per domain, exported from its `__init__.py`).

## API Endpoints (webapp `api/v1/`)

Registered blueprints: `projects`, `users`, `charges`, `allocations`, `status`,
`health`, `admin`, plus the **legacy-compat** set below, `api/ldapsync/` at `/api/protected/admin` (`sam-ldap-syncd`'s contract; `docs/apis/SYSTEMS_INTEGRATION_APIs.md` § 8) and `api/heuv/` at `/api/protected/heuv/v1` (`docs/apis/HEUV_API.md`).

Key endpoints (all JSON):
- `GET /api/v1/users/`, `/users/<username>`, `/users/<username>/projects`. Every GET here takes a
  key or a session (a key: the Permission by grant, never membership); writes, `/users/me`: session
- `GET /api/v1/projects/`, `/projects/<projcode>`, `/projects/<projcode>/members`
- `GET /api/v1/projects/<projcode>/allocations` → `AllocationWithUsageSchema(many=True)` ⭐
- `GET /api/v1/projects/<projcode>/charges` (+ `/summary`) — detailed / aggregated charge rollups ⭐
- `GET /api/v1/projects/expiring`, `/projects/recently_expired`; `GET /api/v1/status/*`
- `GET /api/v1/allocations/<allocation_id>` (+ `PUT` for updates)
- Project-member mutations: `POST/DELETE /api/v1/projects/<projcode>/members[...]`,
  `PUT .../admin`
- Charge-summary ingest: `POST /api/v1/charge-summaries/{comp,disk,archive}`

**Legacy-compat blueprints — DO NOT REFACTOR**: `directory_access.py`,
`project_access.py`, `fstree_access.py`, `queue.py`, `wallclock_exemption.py`
intentionally match legacy Java API response shapes for systems-integration
consumers. Additive changes only (e.g. `invalidate_queue_cache()` in queue.py);
response bytes must not change.

**Output shaping — the hand-built dict is a frozen exception, not the norm.**
Those five blueprints assemble camelCase JSON by hand in `sam/queries/` for
byte-shape reasons. Do NOT copy that into new work. A **new** endpoint — even a
legacy-shaped one — declares its output via a Marshmallow schema (`data_key` for
camelCase; `disk_quota.py` + `schemas/disk_quota.py` is the reference). Fetch is
ORM by default (`queue_access.py`); raw `text()` SQL only when a measured perf
constraint forces it, then with the split-and-assemble discipline
(`directory_access.py`) plus a `make perf` benchmark. Deep nested trees
(wallclock/fstree) that map poorly onto declarative fields stay the documented
hand-rolled exception.
