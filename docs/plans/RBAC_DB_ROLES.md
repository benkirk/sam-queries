# DB-backed RBAC: `samuel_role_*` tables and Admin -> Configuration -> Roles & access

Status: built through step 8 on branch `rbac-db-roles` (from `origin/staging`
da3f9a2f), PR #640 to staging, 2026-09-27; steps 9-10 wait on the dev and prod
DDL + seed. The commit series is section 9; the
in-PR rollout, with the dispatch deploys to dev and prod, is section 10. Decisions
resolved with Ben are in section 11. Deviations from this plan are recorded in
section 12 as they happen.

## Progress

- [x] 1. This document.
- [x] 2. `Permission` + `ALL_*` moved to `sam/security/permissions.py`, shim in `rbac.py`, suite green unedited.
- [x] 3. `rbac_catalog.py`, `DEFAULT_ROLES` / `DEFAULT_GRANTS`, derived dicts, `RBAC_SOURCE` / `RBAC_DB_TTL`, catalog-backed predicates, `AuthUser.roles`.
- [x] 4. ORM models, DDL, `_BOOTSTRAP_TABLES`, anonymizer, schema pins, factories; regenerated LFS blob (own commit).
- [x] 5. `sam-admin rbac`.
- [x] 6. Roles page: schemas, handlers, routes, templates, nav, tile, route-map snapshot, page tests.
- [x] 7. API keys as a grant subject; token-path enforcement in `db` mode; the `api_*` roles.
- [x] 8. Docs rewrite, CLAUDE.md, `api_auth.py` docstrings.
- [x] 9. Dev flip (`values-dev.yaml` `RBAC_SOURCE: "db"`), after the dev DDL + seed.
- [ ] 10. Prod flip (`values.yaml`), after the prod DDL + seed; merge after prod runs it.

**One PR to staging** carrying the code, the DDL, the prod role seed (users, groups, API
keys) and, as its final commits, `RBAC_SOURCE=db` in both helm values files. Deploys happen
from the branch by workflow dispatch, dev first then prod, before merge (section 10).

## 1. Context

Every authorization decision in the webapp runs through `src/webapp/utils/rbac.py`: the
55-member `Permission` enum, three hard-coded dicts (`GROUP_PERMISSIONS` keyed by POSIX
group → bundle; `USER_PERMISSION_OVERRIDES` per user; `USER_FACILITY_PERMISSIONS` per
user per facility) and the predicates `get_user_permissions` / `has_permission` /
`has_permission_for_facility` / `has_permission_any_facility` / `user_facility_scope` /
`can_impersonate`. ~165 `@require_permission` sites, ~100 template `has_permission(`
sites, `access_control.py`, `project_permissions.py`, `nav.py` and the cache key all go
through those predicates; nothing reads the dicts directly except docstrings and tests.
Any grant change is a code edit and a redeploy. The legacy `role` / `role_user` tables
are per-user only and are used solely by the API-key path (`ROLE_XRAS`); they stay
untouched.

Goal: the same granularity (bundles that extend a base bundle, per-user extras,
facility-scoped grants), for **user and group** subjects, stored in app-owned
`samuel_role_*` tables, editable from an admin card with no redeploy. Re-login to pick
up a change is acceptable; the design does better (≤ 60 s).

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | `Permission` enum (+ `_perms_with_action`, `ALL_*`) moves to `src/sam/security/permissions.py`; `rbac.py` re-exports (shim, zero import-site edits). | `sam/__init__.py` imports every model eagerly and `rbac.py` imports Flask; an ORM model or CLI command needing the enum must not pull Flask into `import sam`. |
| D2 | Three tables: `samuel_role`, `samuel_role_permission`, `samuel_role_grant` (§3). No FKs, utf8mb3, app-clock timestamps, table-prefixed index names — the `external_ticket` precedent. | House convention for app-owned tables; Postgres index namespace. |
| D3 | A role has one optional parent (`extends_role_id`), depth-capped, cycle-refused. Permissions are **explicit rows**; no wildcards. One code rule: `SYSTEM_ADMIN` implies every permission. | Wildcards cannot express today's `ALL_EDIT − {EDIT_RESOURCES, EDIT_FACILITIES}` without deny rows. SYSTEM_ADMIN-implies-all means a new `Permission` never locks admins out; operators tick it into bundles via the editor, and `sam-admin rbac --diff` + a gate test surface drift. (Confirmed §11.) |
| D4 | Grant subjects by **name**: `subject_type` `user`\|`group`, `subject_name` = `users.username` / `adhoc_group.group_name`. Exactly one of `role_id` / `permission` per grant; `facility_name` NULL = unscoped. | Matches how groups resolve today (`get_user_group_access` by username string); survives the obfuscated snapshot (a username-keyed prod grant dangles harmlessly, a user_id-keyed one would attach to a random obfuscated person). |
| D5 | Revoke is a **soft** revoke (`revoked_by`, `revoked_at`); grant rows are history. Roles use `active`. | The current git-blame audit trail (FACILITY_SCOPED_ADMIN.md decision log) is replaced by rows, not by the ephemeral `model_audit.log`. |
| D6 | Source switch is an explicit config knob `RBAC_SOURCE` = `defaults` (code, the current dicts) \| `db`. Code default `defaults` (tests, local, any unseeded deploy); `helm/values.yaml` and `values-dev.yaml` set `db` in the PR's final commits, and that image is dispatched only **after** the DDL + seed have run on that environment. In `db` mode there is **no fallback to code defaults**: DB error → serve the last-good snapshot (the `API_KEYS_DB_TTL` pattern in `api_auth.py`); empty tables → nobody holds anything, tile + startup `logger.error` say so, and the CLI is the way back. | A row-count heuristic is an escalation path (a revoked user regains admin when the table is empty) and a test landmine (one committed route-test row flips every xdist worker). The knob keeps every non-flipped environment behavior-identical and makes the flip a reviewed commit, not a heuristic. |
| D7 | The three dicts **stay** as module globals in `rbac.py`, now *derived* from the role-shaped seed (`DEFAULT_ROLES` / `DEFAULT_GRANTS`) through the same closure code the DB path uses. | 12 test files monkeypatch or read those names (`test_nav.py`, `test_project_permissions.py`, `test_allocations_performance.py`, `test_dashboard_routes.py`, `test_db_browser_routes.py`, `test_admin_events_routes.py`, `jobs/test_jobs_config_scopes.py`, `disk_scans/test_disk_scans_routes_browse.py`, `test_oidc_auth.py`, `test_route_authz_hardening.py`, `conftest.py::_register_admin_testing_bundle`, `test_rbac.py`). Unedited-green proves the closure reproduces today's bundles. |
| D8 | Catalog snapshot per process with `RBAC_DB_TTL` (60 s), invalidated locally on every admin write; other replicas catch up within the TTL. Resolved `(unscoped set, facility map)` cached per `AuthUser` instance in `db` mode. | `get_user_permissions` runs 100+ times per page. Per-request cost stays one group query. |
| D9 | Last-holder invariant, model layer: a write may not leave zero **active, unscoped** grants whose closure holds `MANAGE_ROLES` or `SYSTEM_ADMIN`. Raised as `RbacInvariantError`; the handler only maps it. Break-glass is `sam-admin rbac --grant`, which never depended on the webapp. | Group membership lives in `adhoc_system_account_entry`, so "holders" must be defined over grants. |
| D10 | Page gate: the existing, unused `Permission.MANAGE_ROLES` (confirmed §11). Tile on Admin → Configuration at `VIEW_SYSTEM_CONFIG` (counts + source only); "Details »" to `/admin/roles`. | Mirrors the Notifications tile/page split. |
| D11 | Facility-scoped **group** grants are supported by the schema and resolver from day one (today's dicts cannot express them; the catalog gets `facility_groups`). | Free once grants carry `facility_name`; the WNA-manager case is the obvious next ask. |
| D12 | **API keys are a third subject type** (`subject_type='apikey'`, `subject_name` = `api_credentials.username` or the `API_KEYS_<USER>` config name). The token path of `login_or_token_required(permission)` checks the route's `Permission` against the key's grants, and `api_key_required` gains an optional `permission=` (status ingest → `MANAGE_SYSTEM_STATUS`, charge-summaries ingest → `MANAGE_CHARGE_SUMMARIES`). Enforcement is **implied by `RBAC_SOURCE=db`**; `defaults` mode never checks keys. There is no second switch. The legacy `roles=('ROLE_XRAS',)` check is untouched and stays additive. | Today any valid key passes every token route (`api_auth.py:292-318`); only the XRAS blueprint checks `roles=`. All 27 token-accepting routes already declare their `Permission`, so enforcement is mechanical once keys can hold grants. Both flips land in the same PR, so one switch carries both: the image must not run `db` before `--seed` and `--seed-keys` have run, and `--keys` proves that before the dispatch. |

## 3. Schema (`scripts/sql/create_samuel_roles.sql`, one file, three tables)

```
samuel_role
  samuel_role_id     INT AUTO_INCREMENT PK
  name               VARCHAR(40)  NOT NULL   UNIQUE KEY samuel_role_name
  description        VARCHAR(255) NULL
  extends_role_id    INT NULL                -- single parent, depth ≤ 8, no cycles
  active             TINYINT(1) NOT NULL
  created_by, creation_time, modified_by, modified_time   (VARCHAR(35) / DATETIME, app clock)

samuel_role_permission
  samuel_role_id     INT NOT NULL
  permission         VARCHAR(64) NOT NULL    -- exact Permission.value
  PRIMARY KEY pk_samuel_role_permission (samuel_role_id, permission)

samuel_role_grant
  samuel_role_grant_id INT AUTO_INCREMENT PK
  subject_type       VARCHAR(8)  NOT NULL    -- user | group | apikey
  subject_name       VARCHAR(35) NOT NULL    -- username | adhoc group_name | api key name
  samuel_role_id     INT NULL                -- exactly one of these two
  permission         VARCHAR(64) NULL
  facility_name      VARCHAR(40) NULL        -- NULL = every facility
  note               VARCHAR(255) NULL
  created_by VARCHAR(35) NOT NULL, creation_time DATETIME NOT NULL
  revoked_by VARCHAR(35) NULL,     revoked_at DATETIME NULL
  KEY samuel_role_grant_subject (subject_type, subject_name)
```
Header, `IF NOT EXISTS`, and the verification SELECTs per `create_external_ticket.sql`.

ORM: `src/sam/security/samuel_roles.py` — `SamuelRole(Base, ActiveFlagMixin, SessionMixin)`,
`SamuelRolePermission(Base)`, `SamuelRoleGrant(Base, SessionMixin)` with an `is_active`
hybrid (`revoked_at IS NULL`). §7 write methods: `SamuelRole.create/update/set_permissions`,
`SamuelRoleGrant.create/revoke`; `load_catalog(session) → RoleCatalog`;
`seed_defaults(session, *, by)` (idempotent: only when `samuel_role` is empty);
`assert_manage_roles_survives(session)` (D9) called from `revoke`, `update(active=False)`,
`set_permissions`. Register in `src/sam/__init__.py` beside `Role`.

## 4. Resolution (pure logic, `src/sam/security/rbac_catalog.py`, no ORM/Flask)

- `RoleDef(name, extends, permissions)`, `GrantDef(subject_type, subject_name, role, permission, facility)`.
- `expand_roles(defs)` → `{name: frozenset[Permission]}`: parent closure, SYSTEM_ADMIN ⇒ all, unknown permission strings dropped with a warning, cycle/depth → error.
- `RoleCatalog` (frozen): `groups`, `users`, `apikeys` (unscoped sets), `facility_users`, `facility_groups` (`{subject: {facility: set}}`), `group_subjects` (every group named by any grant), `source`, `loaded_at`. `catalog.apikey_permissions(name)` is the token-path lookup; keys are never facility-scoped in v1 (the token routes are the legacy-compat set and carry no facility notion).
- `build_catalog(role_defs, grant_defs, source)`; `RoleCatalog.as_dicts()` → the three legacy dict shapes; `RoleCatalog.from_dicts(...)` for defaults mode (read live so monkeypatches keep working).
- `DEFAULT_ROLES` / `DEFAULT_GRANTS` (today's bundles, relocated with their trap comments):
  roles `allocation_admin`, `csg` (extends allocation_admin + EDIT_RESOURCES, MANAGE_EVENTS, ADMIN_DATABASE), `ssg`, `system_admin` ({SYSTEM_ADMIN}), `viewer` (ALL_VIEW + ACCESS_ADMIN_DASHBOARD), `facility_manager` (sureshm's 15);
  grants group `nusd`→allocation_admin, `csg`→csg, `ssg`→ssg; users `benkirk`, `kyledavis`→system_admin; `mcjones`→viewer; `sureshm`→facility_manager @ `WNA`.
- **API-key roles** (D12), also in `DEFAULT_ROLES`: `api_legacy` = the union every
  token-accepting legacy-compat route declares today (`VIEW_USERS`, `VIEW_PROJECTS`,
  `VIEW_RESOURCES`, `VIEW_ALL_JOB_DATA`), i.e. exactly what any key can reach now;
  `api_collector` = {`MANAGE_SYSTEM_STATUS`, `MANAGE_CHARGE_SUMMARIES`}; `api_admin` =
  {`SYSTEM_ADMIN`} (the `sam-admin cache --refresh` credential hits
  `login_or_token_required(Permission.SYSTEM_ADMIN)` in `api/v1/admin.py`). Key **grants**
  are not static code: prod key names are data, so `--seed-keys` (§5) grants them at
  seed time from the live `api_credentials` rows + config key names, and the exceptions
  (collector → `api_collector`, the cache-refresh key → `api_admin`) are made in the UI or
  with `--grant` before the enforcement flip. The config key `collector` is code-known and
  is in `DEFAULT_GRANTS` → `api_collector`.
- `WITHHELD` = the permissions no default role grants (today: DELETE_USERS/GROUPS/RESOURCES/FACILITIES, CREATE_USERS, EDIT/CREATE_GROUPS, ADMIN_XRAS, MANAGE_ROLES, MANAGE_SYSTEM_STATUS, MANAGE_CHARGE_SUMMARIES, EXPORT_DATA, VIEW_REPORTS, VIEW_CHARGE_SUMMARIES …); a gate test asserts every `Permission` is granted by a default role or listed here.

`rbac.py` after the change: `GROUP_PERMISSIONS`, `USER_PERMISSION_OVERRIDES`,
`USER_FACILITY_PERMISSIONS = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS).as_dicts()`;
`_active_catalog()` = defaults mode → `RoleCatalog.from_dicts(the three globals)`;
db mode → TTL snapshot loaded with `load_catalog(db.session)`, last-good on error,
`invalidate_catalog()`; `get_user_permissions` / the three facility predicates read the
catalog and also union `facility_groups` over `user.roles`; signatures unchanged.
`AuthUser.roles` (`auth/models.py`) filters POSIX groups by `catalog.group_subjects`
instead of `GROUP_PERMISSIONS` keys. Config: `RBAC_SOURCE`, `RBAC_DB_TTL` in
`src/webapp/config.py` (Testing: `defaults`, TTL 0).

**API-key path (`src/webapp/utils/api_auth.py`, D12).** One helper
`api_key_allowed(ident, permission) -> bool`: `True` when `RBAC_SOURCE != 'db'` or
`permission is None`; otherwise `permission in _active_catalog().apikey_permissions(ident['username'])`
(SYSTEM_ADMIN closure applies). `login_or_token_required`: after `_set_api_identity` and
the existing `roles=` check, deny 403 via `_deny` when not allowed (log
`API auth denied: user path permission`). `api_key_required` becomes
`api_key_required(f=None, *, permission=None)` so the 5 bare `@api_key_required` sites are
byte-unchanged; `status.py` ×4 pass `MANAGE_SYSTEM_STATUS`, `charges.py` ingest passes
`MANAGE_CHARGE_SUMMARIES`. `ApiCredentials.as_api_key_map` and the legacy `roles` list are
untouched. Denials on the token path log the key name, never the route's caller list.

## 5. CLI — `sam-admin rbac` (click command in `src/cli/cmds/admin.py`, class in `src/cli/security/commands.py`)

`--seed` (one-time bootstrap: writes `DEFAULT_ROLES` + `DEFAULT_GRANTS` as rows stamped `created_by='cli:seed'` in one transaction, **only when `samuel_role` is empty**; on a populated DB it reports the count and exits 0 without writing, so it can never overwrite UI edits; `--diff` is the read-only comparison) · `--seed-keys` (grants `api_legacy` to every enabled `api_credentials` row and every config key that holds **no** active grant yet; idempotent per key; prints the keys it touched and the ones it skipped, so a second run is a no-op and an operator-made exception is never overwritten) · `--effective USER` / `--effective apikey:NAME` (resolved permissions per facility with provenance: group/grant/role chain) · `--list` (roles + active grants) · `--diff` (DB catalog vs `DEFAULT_ROLES`/`DEFAULT_GRANTS`) · `--keys` (every enabled `api_credentials` row and config key name with its grant count; the pre-flip checklist) · `--grant SUBJECT --role R|--permission P [--facility F] [--note]` where `SUBJECT` is `user:NAME` / `group:NAME` / `apikey:NAME` · `--revoke ID`. Reads `RBAC_SOURCE`-independent (always the DB). rich output + `--format json` per `src/cli/README.md`.

## 6. UI (wire per `.claude/skills/wire-dashboard-feature`)

- **Tile** in `templates/dashboards/admin/fragments/configuration_card.html` ("Roles & access": source, TTL, active roles, active grants, MANAGE_ROLES holders count, **keys without a grant: N** with a warning row when N > 0 (in `db` mode such a key is denied everywhere); "Details »" only `{% if has_permission(Permission.MANAGE_ROLES) %}`), fed by an `rbac` block in `config_inspect.gather_runtime_state` (rollback-on-missing-table like `_tickets_block`).
- **Page** `GET /admin/roles` (`roles_routes.py`, imported at `admin/blueprint.py:1272`; `@require_permission_any_facility(ACCESS_ADMIN_DASHBOARD)` + `@require_permission(MANAGE_ROLES)`), `templates/dashboards/admin/roles.html` extending `dashboards/base.html`, tabs via `read_tab` like `notifications.html`, nav item after Configuration in `utils/nav.py` gated `MANAGE_ROLES`:
  - **Grants** tab: `#grantsCard` lazy `hx-get` (`load, reloadGrantsCard from:body`). Table (subject badge user/group/API key, name, role or permission, facility or "all", note, created by/when, `text-end text-nowrap` actions with `delete_row_button` → revoke) + "show revoked" checkbox read with `read_flag`. Add form (`modal_form.htmx_form` in a card-opened modal shell): `subject_type` radio, user via `fk_search_field` (reuse `admin_dashboard.htmx_search_users`), group via `select_field` over active `AdhocGroup`s, API key via `select_field` over enabled `api_credentials` usernames + `config['API_KEYS']` names (the hash never leaves the server), role `select_field` **or** permission `select_field` (grouped by domain), facility `select_field` (blank = all; disabled for API keys), `note` `text_field`, `form_errors_panel`.
  - **Roles** tab: list (name, extends, direct/effective counts, active, actions) + editor fragment: `text_field` name, `textarea_field` description, `select_field` extends, permission matrix as `checkbox_field`s grouped by domain (inherited ones shown checked+disabled with "via <parent>"), `active` checkbox (explicit boolean injection).
  - **Check** tab: username `fk_search_field` → fragment listing effective permissions per facility with provenance (the `--effective` output rendered).
- **Handlers**: `_AddGrantHandler(FlattenedFieldErrors, HtmxFormHandler)` (schema `AddGrantForm`; `clean()` resolves user/group/facility/role existence via `User.get_by_username` / `AdhocGroup.get_by_name` / `Facility`, rejects both-or-neither role/permission and duplicates; `perform()` → `SamuelRoleGrant.create`), `_SaveRoleHandler` (schema `SaveRoleForm` with `permissions = f.List(...)`; `perform()` → create/update + `set_permissions`; maps `RbacInvariantError`/cycle to `FormError`), hand-written `DELETE /htmx/roles/grants/<id>` (revoke inside `management_transaction`, `HX-Trigger: reloadGrantsCard`), all with `invalidate_catalog()` in `after_commit`. Schemas in `src/sam/schemas/forms/security.py`, exported from `forms/__init__.py`.
- Traps to honor: openers on the card, in-modal controls only `hx-target`; no inline scripts/handlers (CSP); tokens-only CSS; `url_for('static', …)`; `ROUTE_MAP_REGEN=1` snapshot; browser smoke at 3 layouts × 2 themes after `redis-cli FLUSHDB`.

## 7. Tests

- `tests/unit/webapp/test_rbac.py`: existing assertions unchanged (they now pin the derived dicts) + `TestCatalogClosure` (extends chain, cycle/depth refusal, SYSTEM_ADMIN ⇒ all, `facility_groups` union in the three facility predicates, `group_subjects` includes facility-only groups).
- New `tests/unit/security/test_rbac_catalog.py` (pure): `build_catalog(DEFAULT_*)`.as_dicts() equals the module dicts; every `Permission` granted by a default or in `WITHHELD`; `manage_roles_is_held(defaults)`.
- New `tests/unit/security/test_samuel_roles.py` (savepoint; factories `make_samuel_role`, `make_samuel_grant` in `tests/factories/security.py`): create/update/set_permissions/revoke + app-clock stamps; exactly-one-of; unknown permission/subject_type refused; `seed_defaults` idempotent; **round-trip** seed → `load_catalog` → `as_dicts()` == the dicts; last-holder refusal (revoke and dropping MANAGE_ROLES from the only holding role); cycle refused via `update(extends=)`.
- New `tests/unit/webapp/test_rbac_db_mode.py`: `RBAC_SOURCE='db'` + monkeypatched `load_catalog`: TTL reuse, `invalidate_catalog`, last-good on exception, per-instance cache, `AuthUser.roles` filtered by `group_subjects`.
- `tests/api/test_api_credentials_auth.py` (extend) + new `tests/api/test_api_key_enforcement.py`: `defaults` mode → every key passes as today (byte-identical bodies on the legacy-compat blueprints); `db` mode → a key with the route's permission passes, a key with a different permission gets 403 through the route's own `deny` shape (`_xras_deny` unaffected), a key with `SYSTEM_ADMIN` passes everything, config-sourced keys resolve by name, `api_key_required(permission=)` gates the status/charge-summaries ingest and the bare form is unchanged; `roles=('ROLE_XRAS',)` still enforced independently.
- New `tests/unit/webapp/test_admin_roles_page.py` (mirror `test_admin_notifications_page.py`): VIEW_SYSTEM_CONFIG-only client → 403 on page/fragments/POST/DELETE; tile link hidden; `auth_client` renders page + cards; POST validation errors re-render inline (unknown user, both role and permission, unknown facility); no committed writes (route tests never seed).
- `tests/unit/cli/test_rbac_command.py`: `--seed` on empty, `--effective` provenance, `--diff` empty after seed.
- `tests/integration/test_schema_validation.py`: three pins (columns, PK, index names, no FKs, no CURRENT_TIMESTAMP); `_BOOTSTRAP_TABLES` gains three tuples; `anonymize_sam_db.py` purge tuple gains the three tables; `make -C containers/sam-sql-dev regen-lfs-blob` (reset 3307 first); Postgres run on 5434 (`make pytest-pg`).
- Gates: `test_route_map_parity`, `test_modal_shell_contract`, `test_collapse_trigger_rows`, `test_action_cells_nowrap`, `test_static_assets`, `test_template_csp_lint`, `test_css_tokens`, `test_docs.py` (cited paths in AUTHENTICATION.md / TESTING.md / README.md rewritten).

## 8. Docs

`docs/plans/RBAC_DB_ROLES.md` (this plan, house shape: Status/branch, Progress checklist, decisions, as-built); rewrite `docs/AUTHENTICATION.md` "Testing RBAC locally" (§241-267) and `src/webapp/README.md` RBAC sections (also fixes the stale `hsg`); `docs/TESTING.md:141`; CLAUDE.md: one paragraph under *Security / Integration*, `sam-admin rbac` line in the CLI block, the `RBAC_SOURCE` fail-safe, and the §8 note ("the Basic-Auth path bypasses the Permission check") rewritten as conditional; `api_auth.py` docstrings likewise; `helm/values.yaml` and `values-dev.yaml` `RBAC_SOURCE` set to `db` in commits 9–10.

## 9. Commit series (one PR → staging; the branch is deployed by dispatch before merge)

1. docs: `docs/plans/RBAC_DB_ROLES.md`.
2. Move `Permission` + `ALL_*` to `sam/security/permissions.py`; shim in `rbac.py` (suite green unedited).
3. `rbac_catalog.py` + `DEFAULT_ROLES/GRANTS`; `rbac.py` dicts become derived; `RBAC_SOURCE`/`RBAC_DB_TTL` config; catalog-backed predicates; `AuthUser.roles` (suite green unedited except new tests).
4. ORM models + DDL + `_BOOTSTRAP_TABLES` + anonymizer + schema pins + factories; regenerated LFS blob in its own commit.
5. `sam-admin rbac` (`--seed`, `--seed-keys`, `--keys`, `--effective`, `--list`, `--diff`, `--grant`, `--revoke`).
6. Form schemas, handlers, routes, templates, nav, tile, route-map snapshot, page tests.
7. API keys: `apikey` subject in the picker and CLI, `api_key_allowed`, the two decorator changes, the three `api_*` roles, enforcement tests (bare decorator sites byte-unchanged).
8. Docs rewrite, CLAUDE.md (§8 note rewritten as conditional), `api_auth.py` docstrings.
9. **Flip dev**: `helm/values-dev.yaml` `RBAC_SOURCE: "db"` (after §10 steps 2–4 on dev).
10. **Flip prod**: `helm/values.yaml` same value (after §10 steps 5–7 on prod). The PR is merged only after step 10's image has run on prod.

## 10. Rollout, inside the PR (Ben owns deploy mechanics; each external step is a prompt, not an action)

1. Push commits 1–8. CI green on MySQL and Postgres. Dispatch the branch to **dev** (`gh workflow run "Publish Images and CIRRUS Deploy" --ref rbac-db-roles`): dev runs the new code with `RBAC_SOURCE=defaults`, behavior identical. `cirrus_watch --env dev` quiet.
2. Dev tables: `make refresh-dev` builds `samuel_role_*` from the ORM on `sam_dev`. Seed: `sam-admin rbac --seed` then `--seed-keys` against `sam_dev` (`SAM_DEV_PG_*` env); `--effective benkirk`, `--effective sureshm`, `--keys` shows every key granted; grant the collector and cache-refresh keys their `api_*` roles via `--grant`.
3. Commit 9 (dev flip, `d6f21c63`); dispatch dev again. **Done 2026-09-27**: seeded through `kubectl exec` (9 roles, `--diff` clean, `collector` also granted `api_admin` because `sam-admin cache --refresh` uses that key); on `sha-d6f21c6` with `RBAC_SOURCE=db` the collector key gets 200 on `/api/v1/queue/` and on the cache refresh, a bogus key 401, tick clean. Verify: login as benkirk (OIDC) reaches Admin → Configuration, tile reads source = database, keys without a grant 0; Roles page add/revoke round trip; `sam-admin cache --refresh --env dev` works through the API key; collectors keep posting (status page updates; `cirrus_watch --env dev` shows no `/api/` 401/403). Soak at least one collector cycle and one XRAS sweep.
4. Browser smoke of the page at 3 layouts × 2 themes on dev; fix-ups as further commits, re-dispatch.
5. Prod DDL (**prompt**): `mysql -u "$PROD_SAM_DB_USERNAME" -h sam-sql.ucar.edu -p sam < scripts/sql/create_samuel_roles.sql`; the script's verification SELECTs match. **Done 2026-09-27** (hpc-writer; 0/0/0 rows, 9/2/11 columns, 0 FKs), ahead of the dev steps so `everything-coherent` carries the empty tables into every copy.
6. Prod seed (**prompt**): `sam-admin rbac --seed`, `--seed-keys`, then `--keys` and `--grant` for the collector and cache-refresh keys, `--effective benkirk`, `--diff` empty. Prod is still running the pre-flip image, so nothing changes yet.
7. Commit 10 (prod flip); dispatch prod (**prompt**). Verify as in step 3 on prod with `cirrus_watch`; watch the first collector post and the next XRAS action. Rollback is the previous image (the tables and rows are inert under `RBAC_SOURCE=defaults`).
8. Merge to staging; staging → main promotion as usual. `sam-admin cache --refresh` after each deploy (cached fragments embed permission-gated links).

Local webdev: `.env` `RBAC_SOURCE=db` + `sam-admin rbac --seed` to exercise the UI against 3306.

## 11. Resolved with Ben (2026-09-27)

- Page gate is `MANAGE_ROLES` (D10 final). Seeded into `system_admin` only.
- Explicit permission rows + SYSTEM_ADMIN implies all (D3 final). No wildcards. The
  "new VIEW_* auto-joins operator bundles" behavior is retired; `sam-admin rbac --diff`
  and the `WITHHELD` gate test replace it.
- One facility field for any subject: facility-scoped **group** grants are exposed in
  the v1 add-grant form (D11 final).
- API keys: full support in this PR; enforcement is implied by `RBAC_SOURCE=db`, no
  second switch (D12 final, 2026-09-27: "would we remove the switch support at the end?"
  → never build it). `RBAC_SOURCE` itself is a mode the test suite, local dev and
  rollback rely on, not a migration switch, and stays. Memory `reference_api_token_bypasses_permission` becomes conditional once
  merged; update it then.
- Delivery: one PR with DDL, prod roles and both switches flipped in the final commits;
  deploys by workflow dispatch from the branch, dev then prod, before merge (§9, §10).

## 12. Deviations

- The report builders (`build_listing`, `build_effective`, `build_keys`, `build_diff`,
  `permission_groups`) live in `sam/security/rbac_reports.py`, not under `cli/`, so the
  Roles page's Check tab and the CLI share them.
- No depth cap on `extends`: cycle refusal is enough for a finite graph, and the cap
  could never fire in dictionary order.
- `WITHHELD` does not carry EDIT_GROUPS, VIEW_REPORTS or VIEW_CHARGE_SUMMARIES: today's
  `ALL_VIEW` / `ALL_EDIT` bundles grant them, and the seed reproduces today.
  EDIT_FACILITIES is withheld (only system_admin held it).
- The last-holder check runs on the writes that can lose a holder (revoke,
  deactivate, set_permissions, re-parent), not on `create`, so an empty table can be
  seeded. A refused revoke restores its stamp.
- The charge-summary ingest is session-only (`login_required`), so `api_collector`'s
  MANAGE_CHARGE_SUMMARIES is inert; only the four status-ingest routes carry
  `api_key_required(permission=MANAGE_SYSTEM_STATUS)`.
- The Postgres table bootstrap in `tests/conftest.py` now runs under the same file
  lock as MySQL: two xdist workers creating one table raced on its sequence.
- The Roles page uses inline forms and a server-side subject-type cascade, no modals
  and no script, so it adds nothing to `HTMX_FRAGMENT_SHELL_DEPS`.
