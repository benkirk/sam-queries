# Resume — Test Suite Replacement Migration

**Purpose of this file:** bootstrap a fresh Claude Code session so it can
pick up the test-suite refactor mid-flight. Everything you need to continue
is in this document. An older long-form plan exists at
`/Users/benkirk/.claude/plans/composed-doodling-stearns.md` (mirrored in
`docs/plans/REFACTOR_TESTING.md`) but is superseded by this file for
everything Phase 3 and beyond.

---

## TL;DR

You are migrating a 1400-test pytest suite from `tests/` to `new_tests/` on
branch **`tests_refactor`**. Phases 0, 1, 2a–2e, and **3** are done and
committed. **Phase 4 (webapp / Flask / API / Admin / OIDC) is next.**

Quick state check:

```bash
git log --oneline -6        # HEAD is 2941eb7 "test refactor: Phase 3 — factory module + 8 write-path ports"
git status                  # Should be clean except for untracked (follow_up.txt, legacy_sam, old_plan.md)
ls new_tests/factories/     # _seq.py, core.py, resources.py, projects.py, operational.py
```

Run both suites to confirm the baseline:

```bash
# Prereq: mysql-test container must be running (see "Environment commands" below)
docker compose --profile test up -d mysql-test

# new_tests — everything ported so far
SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
  pytest -c new_tests/pytest.ini new_tests/

# Legacy — only Phase 4 (webapp) + Phase 5 (perf) unit files left, plus tests/api/ + tests/integration/
source etc/config_env.sh && pytest -n auto --no-cov
```

**Expected at checkpoint (HEAD=2941eb7):**
- `new_tests/`: **953 passed, 22 skipped** in ~42s (xdist -n auto)
- Legacy `tests/`: **446 passed, 30 skipped, 2 xpassed** in ~71s

**Combined port progress: ~68%** (953 ported / 1399 total).

All that's left is the webapp/Flask tier and the perf tier. No read-only or
write-path unit stragglers remain.

---

## What's committed (branch `tests_refactor`)

```
2941eb7 test refactor: Phase 3 — factory module + 8 write-path ports   ← HEAD
0b0d449 checkpointing resume
9a8a176 test refactor: Phase 2e — finish structural read-path ports
522e020 checkpointing resume
4d8e405 test refactor: Phase 2d — bulk structural read-path ports
e7799ba commit plan
6b2b0d6 commit plan  (actually Phase 2c content)
d870f45 Phase 2b — test_query_functions + representative fixtures
0bbc737 commit plan
53d8dde Phase 0-2 checkpoint
```

Several commits labeled "commit plan" or "checkpointing resume" are
auto-commits from user slash commands that grabbed files before a
manual commit fired. Treat them as content-bearing.

---

## Port tally to date

| Phase | Files | Tests moved | Notes |
|---|---|---|---|
| 0–1 | infra | — | TestingConfig fix, mysql-test container, run-webui-dbg.sh |
| 2a | schema_validation + views | ~42 | SAVEPOINT discovery |
| 2b | test_query_functions | 41 | representative-fixtures strategy |
| 2c | rolling_usage + fstree + project_access | ~70 | module-scoped cache pattern |
| 2d | bulk structural read-path | ~400 | largest batch |
| 2e | finish structural read-path | ~80 | accounting/charging/orm/email/CLI mocks |
| **3** | **wallclock_exemptions, transaction_context, management_functions, crud_operations, allocation_tree, audit_logging, manage_summaries, renew_extend + factory module** | **147 + 22 smoke** | **full write-path suite + 13 factories** |
| 4 | webapp/API/Admin/OIDC | **0 / ~459** | **next — this plan** |
| 5 | allocations_performance | **0 / 111** | deferred to last |

---

## Directory map (current)

```
new_tests/
├── README.md
├── pytest.ini                                 # -c flag required
├── conftest.py                                # Safety guard + session + representative fixtures + sys.path
├── factories/                                 # Layer 2 — plain builder functions
│   ├── __init__.py
│   ├── _seq.py                                # Worker-namespaced sequence counters
│   ├── core.py                                # make_user, make_organization
│   ├── resources.py                           # make_resource_type, make_resource, make_machine, make_queue
│   ├── projects.py                            # make_facility, make_aoi_group, make_aoi, make_project, make_account, make_allocation
│   └── operational.py                         # make_wallclock_exemption
├── unit/
│   ├── test_accounting_models.py              # P2e
│   ├── test_active_interface.py               # P2e
│   ├── test_allocation_tree.py                # P3 (21 tests, factory tree)
│   ├── test_audit_logging.py                  # P3 (5 tests, logger infra + exclude api_credentials)
│   ├── test_basic_read.py                     # P2e
│   ├── test_charging_models.py                # P2e
│   ├── test_cli_jupyterhub.py                 # mocked JupyterHub CLI
│   ├── test_crud_operations.py                # P3 (19 tests, CRUD smoke)
│   ├── test_directory_access_queries.py       # P2e
│   ├── test_email_notifications.py            # P2e
│   ├── test_factories.py                      # P3 (22 factory smoke tests)
│   ├── test_fmt.py                            # zero-DB
│   ├── test_fstree_queries.py                 # P2c
│   ├── test_manage_facilities.py              # P2d (.update() contract)
│   ├── test_manage_organizations.py           # P2d (.update() contract)
│   ├── test_manage_resources.py               # P2d (.update() contract)
│   ├── test_manage_summaries.py               # P3 (53 tests, charge summary upserts)
│   ├── test_management_functions.py           # P3 (14 tests, add/remove member)
│   ├── test_notification_enhancements.py      # zero-DB mocks
│   ├── test_orm_descriptors.py                # zero-DB
│   ├── test_project_access_queries.py         # P2e
│   ├── test_project_models.py                 # P2e
│   ├── test_project_permissions.py            # zero-DB mocks
│   ├── test_query_functions.py                # P2b
│   ├── test_renew_extend.py                   # P3 (23 tests, standalone/inheriting/divergent)
│   ├── test_rolling_usage.py                  # P2c
│   ├── test_sam_search_cli.py                 # CLI with mocked session
│   ├── test_security_models.py                # P2e
│   ├── test_smoke.py                          # infra smoke
│   ├── test_transaction_context.py            # P3 (4 tests, management_transaction)
│   └── test_wallclock_exemptions.py           # P3 (8 tests, first port)
└── integration/
    ├── test_schema_validation.py              # ORM ↔ MySQL alignment
    └── test_views.py                          # XRAS / CompActivityCharge read-only

tests/                                         # legacy — only Phase 4 + Phase 5 files left
├── conftest.py                                # legacy Flask app/session/auth_client fixtures
├── unit/
│   ├── test_admin_defaults.py                 # Phase 4 (18 tests)
│   ├── test_oidc_auth.py                      # Phase 4 (52 tests)
│   └── test_allocations_performance.py        # Phase 5 (111 tests)
├── api/                                       # Phase 4 (~216 tests across 13 files)
│   ├── conftest.py                            # api_key_client fixture
│   ├── test_allocation_schemas.py
│   ├── test_api_auth.py
│   ├── test_charge_endpoints.py
│   ├── test_directory_access.py
│   ├── test_fstree_access.py
│   ├── test_health_endpoints.py
│   ├── test_member_management.py
│   ├── test_project_access.py
│   ├── test_project_endpoints.py
│   ├── test_schemas.py
│   ├── test_status_endpoints.py
│   ├── test_status_schemas.py
│   ├── test_user_endpoints.py
│   └── test_user_search.py
└── integration/                               # Phase 4 (~100 tests across 4 files)
    ├── conftest.py                            # empty shell
    ├── test_legacy_api_parity.py              # 27 tests — external legacy API, mostly skipped
    ├── test_sam_search_cli.py                 # 67 subprocess CLI tests
    ├── test_status_dashboard.py               # 3 tests — auth_client + status_session
    └── test_status_flow.py                    # 3 tests — pure status DB ORM
```

---

## Key architectural decisions already made (Phases 0–3)

### Two-tier test data strategy (unchanged, still the law)

| Tier | Where | When to use |
|---|---|---|
| **Layer 1 — Representative fixtures** | `new_tests/conftest.py` (`active_project`, `multi_project_user`, `hpc_resource`, `any_*`) | Read-path tests. Fetch ANY row of the required shape from the snapshot. |
| **Layer 2 — Factories** | `new_tests/factories/` | Write-path tests. Build fresh synthetic rows inside the test's SAVEPOINT. |

**Never blend them inside a single helper.** But tests are welcome (and
encouraged) to *compose* both layers — e.g. `active_project` for the heavy
Project+Account graph and `make_user` for a fresh user that's
unambiguously not on the project. Phase 3 relies on this pattern.

### Session isolation — raw SAVEPOINT mode

`new_tests/conftest.py::session` uses `join_transaction_mode="create_savepoint"`.
Every test-level `session.commit()` / `session.rollback()` becomes a
SAVEPOINT op inside an outer transaction that rolls back at teardown.
This is what makes `.update()` contract tests, `management_transaction`
tests, and all write-path ports portable without requiring true commits.

### DB isolation

- **Host port 3307** → `samuel-mysql-test` container (dedicated test DB)
- **Host port 3306** → `samuel-mysql` container (dev stack, shared)
- **Allowlist guard** in `new_tests/conftest.py::pytest_configure` aborts
  with exit code 2 unless `SAM_TEST_DB_URL` points at
  `(127.0.0.1|localhost, 3307)` or `(mysql-test, 3306)`.

### Factory design (Phase 3)

Plain builder functions, not `factory-boy`. `session` is the first
positional arg. Each builder auto-builds the minimum FK graph it needs,
calls `session.flush()` (never `commit()`), returns the flushed instance.

**Three xdist gotchas handled:**
1. `_seq.next_seq()` bakes `PYTEST_XDIST_WORKER` into every generated
   identifier — concurrent workers never collide on UNIQUE columns even
   while holding SAVEPOINT-scoped write locks on the shared mysql-test DB.
2. `make_organization` uses a high worker-namespaced ID range
   (`10_000_000 + worker*100_000 + counter`) because
   `organization.organization_id` has `autoincrement=False`.
3. `make_project` sets `parent_id=` on the constructor, flushes, then
   calls `Project._ns_place_in_tree(session, parent=...)` — NestedSetMixin's
   tree-coordinate populator requires an explicit call after flush.

### Existing factories (13 builders)

```
core:        make_user, make_organization
resources:   make_resource_type, make_resource, make_machine, make_queue
projects:    make_facility, make_aoi_group, make_aoi, make_project, make_account, make_allocation
operational: make_wallclock_exemption
```

### Legacy suite flake (unchanged)

The legacy suite has a pre-existing ~30% flake rate under xdist because
its `session` fixture doesn't use SAVEPOINT isolation. **Not fixing this**
— the migration itself is the fix. If the legacy suite flakes during a
regression check, re-run up to 2× before investigating.

---

## Why this migration (unchanged, abbreviated)

The legacy suite had five fundamental problems:

1. **It ran under `DevelopmentConfig`, not `TestingConfig`** (dev API keys,
   dev role mapping, 3600s cache TTL silently active in tests). **Fixed in P0.**
2. **Tests ran against whatever `SAM_DB_SERVER` pointed at in `.env`** — no
   prod fence. **Fixed in P1** with a dedicated `mysql-test` compose
   service + allowlist guard.
3. **Latent xdist flakes** in write-path tests from missing SAVEPOINT
   isolation. **Fixed in new_tests/** via `join_transaction_mode="create_savepoint"`.
4. **Snapshot-dependent magic names** (`benkirk`, `SCSG0001`, `Derecho`,
   `NMMM0003`) broke on every obfuscated-snapshot refresh.
   **Fixed in P2b** (representative fixtures) and **P3** (factories).
5. **Cache-dependent speed** — fstree/dashboard results were cached under
   the wrong config. **Fixed in P2c** with explicit module-scoped fixture caching.

---

# PHASE 4 — Webapp / API / Admin / OIDC

This is the hardest phase because it introduces a **Flask `app` fixture**
and the question of how `db.session` (Flask-SQLAlchemy's scoped session)
relates to our raw SAVEPOINT-isolated `session` fixture.

## Phase 4 scope

**~459 tests across 18 files** split across 4 surfaces:

### Surface A — webapp unit (2 files, 70 tests)

| File | Tests | Needs | Difficulty |
|---|---|---|---|
| `tests/unit/test_admin_defaults.py` | 18 | `app`, `session` — Flask-Admin ModelView internals | Moderate |
| `tests/unit/test_oidc_auth.py` | 52 | `app`, `client`, `auth_client`, mocked Authlib oauth | Moderate–Hard |

### Surface B — API endpoints (13 files, ~216 tests)

Split into three groups by difficulty:

**Trivial (pure schema / auth decorator / health — no DB writes):**
- `test_allocation_schemas.py` (13) — Marshmallow serialization
- `test_api_auth.py` (10) — HTTP Basic auth decorator
- `test_health_endpoints.py` (22) — /health, /alive, /ready, /api/v1/admin/db_pool
- `test_schemas.py` (18) — User/Project/Institution schemas
- `test_status_schemas.py` (10) — Derecho/Casper status schemas

**Moderate (auth + snapshot reads, no writes):**
- `test_directory_access.py` (23)
- `test_fstree_access.py` (13)
- `test_project_access.py` (14)
- `test_project_endpoints.py` (24)
- `test_user_endpoints.py` (11)
- `test_user_search.py` (15)

**Moderate–Hard (auth + DB writes):**
- `test_charge_endpoints.py` (25) — writes ComputeCharge/DiskCharge/ArchiveCharge
- `test_member_management.py` (17) — writes AccountUser rows via API
- `test_status_endpoints.py` (11) — writes DerechoStatus/CasperStatus

### Surface C — integration (4 files, ~100 tests)

| File | Tests | Verdict |
|---|---|---|
| `test_legacy_api_parity.py` | 27 | **DROP or mark opt-in.** Requires live external `sam.ucar.edu` legacy API + `PROD_SAM_DB_*` env vars, mostly skipped in practice. |
| `test_sam_search_cli.py` | 67 | **Port separately.** These are subprocess-based CLI tests that happen to live under `tests/integration/`. They already have a mocked-session equivalent at `new_tests/unit/test_sam_search_cli.py`. Decide: port the 67 subprocess tests too (run sam-search as a subprocess against mysql-test), or retire them as redundant with the unit tests. |
| `test_status_dashboard.py` | 3 | Port — needs `auth_client` + `status_session`. |
| `test_status_flow.py` | 3 | **Trivial port** — pure status DB ORM writes, no auth. |

### Surface D — Phase 5 perf (1 file, 111 tests) — defer

`tests/unit/test_allocations_performance.py` (937 lines, 111 tests).
Tests allocations dashboard route perf, Flask-Caching NullCache,
matplotlib lru_cache, TTLCache module. Module-level autouse fixture
resets `sam.queries.usage_cache._cache` globals. **Defer to after Phase 4.**

---

## Phase 4 architectural challenge

`src/webapp/` uses **`flask_sqlalchemy.SQLAlchemy`** (`db = SQLAlchemy()`
in `src/webapp/extensions.py`), not raw SQLAlchemy. The connection URL
flows through a brittle chain:

1. **Module import time:** `sam/session/__init__.py` reads `SAM_DB_*` env
   vars and assembles `sam.session.connection_string` as a module global.
2. **`create_app()`:** `src/webapp/run.py` sets
   `app.config['SQLALCHEMY_DATABASE_URI'] = sam.session.connection_string`
   — just copies the global verbatim. Same for `system_status`.
3. **`db.init_app(app)`:** Flask-SQLAlchemy lazily builds the engine on
   first access to `db.engine` from the app's config.

**Consequences for the port:**

- You **cannot** swap the engine after `create_app()` — the connection
  string has already been baked in.
- To point the Flask app at `mysql-test`, you must set `SAM_DB_*` env
  vars **before** importing `sam.session`. The legacy conftest does this
  in `pytest_configure` (runs before any module import).
- The legacy `tests/conftest.py` does NOT bridge its raw `session` fixture
  with `db.session`. They're two independent sessions against (potentially)
  the same DB, and the legacy suite just works around it by:
  - Using `session` for ORM queries that don't go through Flask views
  - Using `auth_client` + `app.test_client()` for route tests that pass
    data through real HTTP
  - Skipping tests that need to observe factory-built data through a
    Flask view response

### The SAVEPOINT bridging question

**Can we make Flask's `db.session` and our raw `session` share an outer
transaction so factory rows are visible through views?**

Short answer: **yes but it's surgery**. Approach:
1. Open a raw `connection` from a test engine bound to mysql-test.
2. `transaction = connection.begin()` — outer transaction.
3. Bind the raw `session` to `connection` with
   `join_transaction_mode="create_savepoint"` (same as today).
4. Override `app.config['SQLALCHEMY_DATABASE_URI']` and force
   `db.engine` to use the same `connection` (using Flask-SQLAlchemy's
   `engine_options={"creator": lambda: connection}` or by replacing
   the bind-to-engine mapping).
5. Also wrap `db.session`'s session factory with
   `join_transaction_mode="create_savepoint"`.
6. At teardown, `transaction.rollback()` + `connection.close()`.

**This is non-trivial.** Flask-SQLAlchemy's session handling isn't
designed to accept a pre-existing connection, and overriding the engine
post-init is documented as unsupported. There's working precedent in the
pytest-flask-sqlalchemy plugin, but it adds a dependency and the plugin's
patterns assume the ORM is pure Flask-SQLAlchemy (ours is mixed).

### Recommendation: **Path A — snapshot data + auth_client, factories where cheap**

Don't invest in full SAVEPOINT bridging up front. Instead:

- Build a Phase 4 `app` fixture that:
  - Sets `SAM_DB_SERVER=127.0.0.1`, `SAM_DB_PORT=3307` + all the other
    `SAM_DB_*` vars in `pytest_configure` **before** any module imports
    touch `sam.session`.
  - Calls `create_app()` with `FLASK_CONFIG=testing` — picks up
    `TestingConfig` (which already mirrors `DEV_ROLE_MAPPING` so
    `benkirk → admin` works).
  - Yields the app for the session.
- Build an `auth_client` fixture that mirrors the legacy one: set
  `_user_id` via `client.session_transaction()` for benkirk.
- For tests that need factory data visible to a route: use **`session.commit()`**
  in the raw `session` fixture (which, under SAVEPOINT mode, is a
  SAVEPOINT release — the data IS visible to a subsequent query on the
  same connection but still gets rolled back at teardown). **But** this
  only works if both sessions share the same connection, which they
  don't by default.
- Pragmatic compromise: for **read-path** route tests, use snapshot data
  via representative fixtures (same as Phase 2). For **write-path** route
  tests (charge_endpoints, member_management, status_endpoints), make
  the POST through the test client and then query `db.session` (same app)
  to verify — that works because the POST handler commits via
  `management_transaction`. Teardown via a dedicated `db_rollback` fixture
  that walks the new rows and deletes them, OR accept that write-path
  route tests leak into the mysql-test database and rely on the container
  being dockerfile-rebuildable.

**Main tradeoff:** Path A ships faster and matches what the legacy suite
already does (the mysql-test container is cheap to rebuild). Path B (full
SAVEPOINT bridging) is architecturally cleaner but likely costs 1–2 days
of infrastructure work up front with no test-port progress during that
window.

**Third option worth considering:** For write-path API tests, use
**Option A with an after-test DB-reset hook** — drop and re-snapshot the
mysql-test database between test runs (not between tests). That gives
clean initial state without requiring per-test isolation. Acceptable
because write-path API tests are the minority and their writes are
append-only (charges, status rows) rather than mutating shared rows.

### Open questions to confirm before porting

1. **Does `auth_client`'s benkirk lookup work when benkirk exists in the
   mysql-test snapshot?** Should be yes (benkirk is preserved unmodified
   in the obfuscated snapshot per `~/.claude/projects/.../project_test_db_fixtures.md`).
2. **Does `TestingConfig.DEV_ROLE_MAPPING` apply automatically when
   `FLASK_CONFIG=testing`?** Verified in `src/webapp/config.py:140`.
   Yes — `benkirk → admin` is mirrored.
3. **Does `create_app()` initialize OIDC in test mode?** No — it only
   registers OAuth when `AUTH_PROVIDER='oidc'`, and TestingConfig leaves
   `AUTH_PROVIDER='stub'` (the default).
4. **Can `pytest_configure` set `SAM_DB_*` vars after our existing
   allowlist guard runs?** Yes — the guard runs in `pytest_configure`,
   and we can add the env-var setting to the same hook. Order matters:
   set env vars FIRST, then run the existing allowlist check, then
   import anything that needs the vars.

---

## Phase 4 execution plan

### 4a — Infrastructure: `app`/`client`/`auth_client` fixtures (no ports)

**Goal:** Produce a minimal Phase 4 skeleton that passes a smoke test.

1. Extend `new_tests/conftest.py::pytest_configure` to set `SAM_DB_*` env
   vars (username=root, password=root, server=127.0.0.1, port=3307, name=sam)
   BEFORE the allowlist check, and BEFORE any webapp imports.
2. Add a new `new_tests/webapp_conftest.py` or extend `new_tests/conftest.py`
   with session-scoped `app` + function-scoped `client` + `auth_client`
   fixtures. Copy the patterns from `tests/conftest.py` lines 252–327
   verbatim — they already work.
3. Add `new_tests/unit/test_webapp_smoke.py` with 2 tests:
   - `test_health_endpoint_returns_200(client)` — unauthenticated GET /health
   - `test_auth_client_is_logged_in(auth_client)` — GET /api/v1/users/me and
     verify `response.status_code == 200` and `response.json['username'] == 'benkirk'`
4. Run it. If both pass, infrastructure is good and ports can begin.

**Acceptance:** Both smoke tests pass in a parallel xdist run. Existing
953 new_tests still pass.

### 4b — Schema / health / auth trivial ports (5 files, 73 tests)

Port in this order (simplest to most complex):
- `test_status_schemas.py` (10) → zero DB, zero auth
- `test_api_auth.py` (10) → HTTP Basic decorator, no DB
- `test_schemas.py` (18) → pure Marshmallow, use factories for test data
- `test_allocation_schemas.py` (13) → Marshmallow + factories
- `test_health_endpoints.py` (22) → `client` + `auth_client` + `non_admin_client`

The `non_admin_client` fixture needs a fresh non-admin user. Since
`DEV_ROLE_MAPPING` defaults non-benkirk users to `['user']`, create via
`make_user(session)` and then inject their user_id into the client session
— same pattern as `auth_client` but with a factory-built user.

**Acceptance:** All 5 files ported, legacy deleted, smoke stable.

### 4c — API read-only ports (6 files, ~100 tests)

- `test_user_endpoints.py` (11)
- `test_user_search.py` (15)
- `test_project_access.py` (14)
- `test_fstree_access.py` (13)
- `test_directory_access.py` (23)
- `test_project_endpoints.py` (24)

All of these are GET endpoints returning snapshot data. Use representative
fixtures (`active_project`, `hpc_resource`) for the DB side; use `auth_client`
for the HTTP side. No SAVEPOINT bridging needed — the views read via
`db.session` which sees the committed snapshot data directly.

**Gotcha:** Tests that assert on specific projcodes (`SCSG0001`, `CESM0002`)
need to be rewritten to use `active_project.projcode` or
`subtree_project.projcode` instead.

### 4d — API write-path ports (3 files, 53 tests)

- `test_charge_endpoints.py` (25) — POSTs ComputeCharge/DiskCharge/ArchiveCharge
- `test_member_management.py` (17) — POST/PATCH/DELETE project members
- `test_status_endpoints.py` (11) — POSTs DerechoStatus/CasperStatus

**Strategy:** Build fresh project+user graphs via factories, `session.commit()`
(SAVEPOINT release — data visible to same connection), issue the test-client
POST, then query via `db.session` or the same `session` to verify. **BUT**
— this only works if `db.session` and the raw `session` share a connection,
which they don't by default.

**Decision point during 4d:** If the naive approach (fresh factories +
test-client request) fails because `db.session` can't see factory data,
escalate to one of:
1. Real `session.commit()` that actually persists, with manual cleanup
   in a teardown fixture (row IDs recorded during the test, deleted after).
2. Invest the 1–2 days in full SAVEPOINT bridging.
3. Drop these tests and rely on the Phase 3 unit tests for the underlying
   `manage.*` functions (which already exercise the exact same code paths
   under factory isolation).

**Recommendation:** Option 3 if the bridging turns out to be hard.
`test_charge_endpoints` overlaps heavily with `test_manage_summaries` (Phase 3,
53 tests, already ported). `test_member_management` overlaps with
`test_management_functions` (Phase 3, 14 tests, already ported). Only the
HTTP-layer concerns (route wiring, marshmallow input validation, error
formatting) are unique to these files — and those are well-covered by
the `TestInputSchemas` classes already ported in Phase 3.

### 4e — Webapp unit ports (2 files, 70 tests)

- `test_admin_defaults.py` (18) → Flask-Admin ModelView internals. Uses
  `app` fixture. Pure config/introspection tests — no DB writes, no auth.
  Easy port once 4a lands.
- `test_oidc_auth.py` (52) → mix of pure unit tests (with mocked Authlib)
  and route tests (stub-mode login/logout). The provider tests should
  port cleanly. The route tests need `app` + `client` and careful mocking
  of `authlib.integrations.flask_client.OAuth`.

### 4f — Integration ports (4 files, ~100 tests)

- `test_status_flow.py` (3) → trivial, no auth, pure status DB ORM. Port first.
- `test_status_dashboard.py` (3) → needs `auth_client` + `status_session`.
  Add a `status_session` fixture to new_tests/conftest.py that mirrors the
  legacy one (separate system_status DB connection, own SAVEPOINT).
- `test_sam_search_cli.py` (67) → **DECIDE**: port as subprocess tests, or
  retire as redundant with the existing mocked-session version at
  `new_tests/unit/test_sam_search_cli.py`. Recommend retire with a brief
  note in the commit message.
- `test_legacy_api_parity.py` (27) → **DROP** or move behind an env-var
  gate. It requires `sam.ucar.edu` + PROD credentials and is skipped in
  practice. Not worth porting.

### 4g — Phase 4 cleanup + final counts

After 4a–4f:
- new_tests/: ~1390 passing (953 + ~440 new)
- legacy tests/: 111 tests remaining (only `test_allocations_performance.py`)
- Factory additions: probably 1–2 small ones (e.g. `make_charge_adjustment`
  if `test_charge_endpoints` ports require them)

---

# PHASE 5 — Performance tier

**Scope:** `tests/unit/test_allocations_performance.py` only (937 lines, 111 tests).

**Key things to know:**
- Module-level `_reset_usage_cache_globals` autouse fixture resets
  `sam.queries.usage_cache._cache = None` before every test to prevent
  cross-test bleed. Must be ported carefully.
- Tests hit `webapp.dashboards.charts` (matplotlib lru_cache).
- Tests hit `sam.queries.usage_cache` (TTLCache singleton).
- Cache tests have their own `.cache_info()` / `.cache_clear()` assertions.
- Config defaults already set: `CACHE_DEFAULT_TIMEOUT=300`,
  `CACHE_TYPE='NullCache'` in TestingConfig (`ALLOCATION_USAGE_CACHE_TTL=0`,
  `ALLOCATION_USAGE_CACHE_SIZE=0`).

**Port strategy:** Mostly route tests + cache-state tests. Needs the same
Phase 4 infrastructure (`app`, `client`, `auth_client`). Factory-build
Derecho/Casper resources, SCSG0001 project, allocations. Cache tests stay
close to the legacy originals but swap in factory data.

**Estimate:** ~1 day once Phase 4 is done.

---

# POST-MIGRATION CLEANUP

After Phase 5 (everything ported):

1. **Delete legacy `tests/` directory** in its entirety (conftest.py,
   fixtures/, unit/, api/, integration/). Keep `tests/conftest.py` around
   during the phase for reference; delete last.
2. **Rename `new_tests/` → `tests/`** (single commit).
3. **Update `pytest.ini`** at project root (remove `-c` flag workaround,
   drop `testpaths = tests/` override since that's now the default).
4. **Remove `new_tests/README.md`** or roll its content into the root README.
5. **Remove `Resume.md`** (this file — its purpose is complete).
6. **Update `docs/plans/REFACTOR_TESTING.md`** to a post-mortem or delete it.
7. **Update `CLAUDE.md`** to reflect the new fixture names (session,
   factories, etc.) and remove any references to legacy `test_user` /
   `test_project` fixtures.
8. **Consider removing** `/Users/benkirk/.claude/plans/composed-doodling-stearns.md`
   and `/Users/benkirk/.claude/plans/mellow-sleeping-axolotl.md` if not
   needed for archival.
9. **Optional:** run `git log --grep='commit plan'` to identify auto-commits
   from the user's slash command and consider rewriting commit messages
   (interactive rebase) to be more descriptive. Low value, skip unless
   asked.

---

# REFERENCE

## Critical environment / invocation commands

```bash
# Activate conda env + load .env
source etc/config_env.sh

# Start the test MySQL container (~90s first time for dump restore; ~5s thereafter)
docker compose --profile test up -d mysql-test

# Wait for MySQL to accept TCP — the healthcheck lies during init
until mysqladmin ping -h 127.0.0.1 -P 3307 -u root -proot --silent 2>/dev/null; do sleep 2; done

# Run new_tests/
SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
  pytest -c new_tests/pytest.ini new_tests/

# Run legacy
pytest -n auto --no-cov

# Debug webapp against the test DB (port 5051, safe from dev stack on 5050)
PORT=5051 utils/run-webui-dbg.sh
```

## Representative fixtures defined in new_tests/conftest.py

**Shape-constrained (session-cached ID + function-scoped `session.get()`):**
- `active_project` — any Project with active allocations
- `subtree_project` — any Project with ≥3 active child projects
- `multi_project_user` — any active user on ≥2 active projects
- `hpc_resource` — any currently-active HPC Resource

**"Any row" (function-scoped `.first()` + skip fallback):**
- `any_facility`, `any_panel`, `any_panel_session`, `any_allocation_type`
- `any_organization`, `any_institution`, `any_aoi`, `any_aoi_group`
- `any_contract`, `any_contract_source`, `any_nsf_program`
- `any_resource`, `any_resource_type`, `any_machine`, `any_queue`

## Gotchas you'll hit

### `benkirk` is deliberately preserved in the snapshot

The obfuscated DB rewrites most usernames to `user_<hex>` but **`benkirk`
is preserved unmodified** as a named test account. See
`~/.claude/projects/-Users-benkirk-codes-project-samuel-main/memory/project_test_db_fixtures.md`.
Use `benkirk` directly when a test needs a specific guaranteed-known
username (e.g., `auth_client` login in Phase 4); use `multi_project_user`
fixture when any active multi-project user will do.

**Why this matters:** `src/cli/user/commands.py:37` does
`pattern.replace('%','').replace('_','')` before passing to
`User.search_users`. A substring-match against an obfuscated `user_<hex>`
username becomes `userhex` and matches nothing. Pattern-search CLI tests
have to target `benkirk`.

### Flask app config resolution

- `FLASK_CONFIG` (NOT `FLASK_ENV`) selects the config class via
  `src/webapp/config.py::get_webapp_config()`. Legacy tests had a bug
  where they set `FLASK_ENV='testing'` which did nothing — fixed in P0.
- `TestingConfig.DEV_ROLE_MAPPING` mirrors `DevelopmentConfig.DEV_ROLE_MAPPING`
  so `benkirk → admin` works in tests without needing `role_user` DB rows.
- `TestingConfig.API_KEYS` has one bcrypt-hashed key (rounds=4 for speed):
  `{'collector': <hash for 'test-api-key'>}`.

### `sam.session.connection_string` is a module global set at import time

To point the Flask app at `mysql-test`, you MUST set `SAM_DB_SERVER`
(=127.0.0.1), `SAM_DB_PORT` (=3307), `SAM_DB_USERNAME` (=root),
`SAM_DB_PASSWORD` (=root), `SAM_DB_NAME` (=sam) BEFORE the first import
of anything under `sam.*`. `pytest_configure` runs early enough; fixture
setup does not.

### Legacy conftest uses `session_commit` for Phase 3-style write tests

The legacy suite has both `session` (rollback) and `session_commit`
(actually commits) fixtures. New_tests/ has only `session` — write-path
tests use SAVEPOINT + `management_transaction` semantics. When porting
Phase 4 files that reference `session_commit`, rewrite them to use the
SAVEPOINT pattern or the test-client-as-integration approach (HTTP POST
with real commit via the view's own `management_transaction`).

### fstree query cost and TestingConfig

`TestingConfig` disables the LRU cache (`ALLOCATION_USAGE_CACHE_TTL=0`).
Every call to expensive query functions (`get_fstree_data`,
`get_user_dashboard_data`, `get_project_dashboard_data`,
`get_resource_detail_data`) pays the full cost. **When porting tests that
call these, add a module-scoped fixture cache** (see
`new_tests/unit/test_fstree_queries.py` for the pattern).

### `.update()` contract tests are NOT write-path tests

`test_manage_facilities.py`, `test_manage_organizations.py`,
`test_manage_resources.py` all follow: fetch real row → call `.update()`
→ `session.rollback()`. Under SAVEPOINT isolation this is completely safe.

### Legacy post-rollback re-assertions are stale

Legacy code like `assert f.description == original` AFTER
`session.rollback()` is a smell. Under SAVEPOINT isolation the Python
object is still attached, so the re-assertion is either a tautology or
wrong depending on session state. **Drop these during port.**

### Flask-SQLAlchemy engine override is unsupported

`flask_sqlalchemy.SQLAlchemy()` does NOT support swapping the engine
after `db.init_app(app)`. If you need the Flask app to see a custom
connection, the connection URL must be in place BEFORE `create_app()`
is called. No post-init surgery.

## Working principles from this user

- **Two-tier test data strategy** — never blend snapshot data with
  synthetic factory data in one helper. Composition across layers in
  a single test is fine and encouraged.
- **Test ordering flakes are not my problem to fix in legacy** — the
  migration IS the fix. Re-run legacy tests up to 2× during regression
  checks.
- **User commits plan-file changes via a slash command** — auto-commits
  labeled "commit plan" land before manual commits. Don't panic if a
  commit fails with "nothing added to commit" after staging — check if
  the user's slash command grabbed it first.
- **User runs pytest by hand** (memory: `feedback_testing.md`). OK to
  run tests from Bash when actively porting/iterating; don't run them
  unnecessarily.
- **Keep port commits reasonably sized** — 3–5 files per commit is the
  rhythm. One commit per "batch" with a detailed message summarizing
  what moved and why.
- **Use `docker compose --profile test up -d mysql-test`** — the test
  container is behind a profile to avoid starting with the dev stack.
- **Only commit when explicitly asked** (project convention). The user
  asks for "checkpoint" commits at natural breakpoints.

---

## Starting prompt for the next session

Paste this into a fresh Claude Code session to bootstrap:

> Continuing a test-suite refactor. Please read
> `/Users/benkirk/codes/project_samuel/devel/Resume.md` first — that has
> the full state of the migration, what's committed, and the Phase 4
> execution plan. Then confirm the current state by running:
>
> ```bash
> git log --oneline -6
> docker compose ps
> SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
>   pytest -c new_tests/pytest.ini new_tests/ 2>&1 | tail -5
> ```
>
> If `samuel-mysql-test` isn't running, start it with
> `docker compose --profile test up -d mysql-test` and wait for TCP.
>
> Then kick off **Phase 4a**: build the Flask `app` / `client` /
> `auth_client` fixture infrastructure in `new_tests/conftest.py` (setting
> `SAM_DB_*` env vars in `pytest_configure` before imports), add a smoke
> test file `new_tests/unit/test_webapp_smoke.py` with 2 tests (health
> endpoint unauthenticated, `/api/v1/users/me` via auth_client), and
> verify both pass under xdist. Do NOT port any real Phase 4 files yet —
> the goal of 4a is just to prove the Flask fixtures work.
>
> Once 4a is green, proceed to 4b (5 trivial port files).
