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
branch **`tests_refactor`**. Phases 0, 1, 2a–2e, **3**, and **4a (infra)**
are done and committed. **Phase 4b (first real webapp ports) is next.**

Quick state check:

```bash
git log --oneline -6        # HEAD is the "Phase 4a infra" commit (create_app config_overrides kwarg + app/client/auth_client fixtures + 2 smoke tests)
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

**Expected at checkpoint (HEAD = Phase 4a commit):**
- `new_tests/`: **955 passed, 22 skipped** in ~35s (xdist -n auto)
  (953 Phase-3 ports + 2 new webapp smoke tests)
- Legacy `tests/`: **446 passed, 30 skipped, 2 xpassed** in ~54s
  (zero change — Phase 4a is purely additive, `create_app()` no-args path is untouched)

**Combined port progress: ~68%** (955 ported / 1399 total).

All that's left is the webapp/Flask tier and the perf tier. No read-only or
write-path unit stragglers remain.

---

## What's committed (branch `tests_refactor`)

```
<Phase 4a>  webapp refactor: create_app(config_overrides=) + app/client/auth_client fixtures  ← HEAD
cef2eef     docs: rewrite Resume.md for Phase 4
2941eb7     test refactor: Phase 3 — factory module + 8 write-path ports
0b0d449     checkpointing resume
9a8a176     test refactor: Phase 2e — finish structural read-path ports
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

**Phase 4a (infra) is done.** `src/webapp/run.py::create_app()` was
extended with a `config_overrides=` keyword-only kwarg that lets the test
suite inject test-DB config *after* defaults land but *before*
`db.init_app(app)` runs. This collapsed what looked like the hardest
architectural problem (bridging raw `session` with Flask-SQLAlchemy's
`db.session`) into a ~10-line refactor. The old "Path A vs Path B"
analysis in this document has been **superseded** — full SAVEPOINT
bridging is now trivially available if we want it.

The Phase 4a commit also added three new fixtures to
`new_tests/conftest.py` (`app`, `client`, `auth_client`) and a
`new_tests/unit/test_webapp_smoke.py` acceptance-gate file (2 tests).

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

## Phase 4 architecture (solved in 4a)

`src/webapp/` uses **`flask_sqlalchemy.SQLAlchemy`** (`db = SQLAlchemy()`
in `src/webapp/extensions.py`). The connection URL flows through the
module globals in `sam.session` / `system_status.session`, which are
populated at import time from `SAM_DB_*` / `STATUS_DB_*` env vars.

The old worry was: to point Flask-SQLAlchemy at the mysql-test container,
you'd have to set env vars before any import could touch `sam.session`,
then call `init_sam_db_defaults()` to rebuild the globals, then call
`create_app()`. And full SAVEPOINT bridging (so factory rows are visible
through Flask views via `db.session`) would require swapping the engine
post-init, which Flask-SQLAlchemy doesn't support.

**None of that is true anymore.** As of Phase 4a, `create_app()` accepts
an optional `config_overrides=` kwarg:

```python
# src/webapp/run.py
def create_app(*, config_overrides: dict | None = None):
    ...
    app.config['SQLALCHEMY_DATABASE_URI'] = sam.session.connection_string
    app.config['SQLALCHEMY_BINDS'] = {
        'system_status': system_status.session.connection_string,
    }
    ...
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

    # Apply caller-supplied overrides AFTER defaults, BEFORE extensions bind.
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
```

The test `app` fixture uses this to point the URL at the mysql-test
container directly — no env-var dance, no module global mutation, no
`init_*_db_defaults()` re-call:

```python
# new_tests/conftest.py
@pytest.fixture(scope="session")
def app(test_db_url):
    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
    from webapp.run import create_app
    return create_app(config_overrides={
        "SQLALCHEMY_DATABASE_URI": test_db_url,
    })
```

### SAVEPOINT bridging is now a drop-in

If and when a Phase 4 write-path port genuinely needs factory rows to
be visible through a Flask view without committing for real, the pattern
is ~10 lines:

```python
# Hypothetical future app_with_shared_connection fixture
from sqlalchemy.pool import StaticPool

@pytest.fixture
def app_with_shared_connection(test_db_url, engine):
    connection = engine.connect()
    transaction = connection.begin()
    try:
        from webapp.run import create_app
        app = create_app(config_overrides={
            "SQLALCHEMY_DATABASE_URI": test_db_url,
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "creator": lambda: connection,
                "poolclass": StaticPool,
            },
        })
        yield app
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
```

`creator=lambda: connection` tells Flask-SQLAlchemy's engine to hand
out the same connection every time something asks, and `StaticPool`
disables pooling so no new connections are created. Combined with the
raw `session` fixture binding to the same connection, factory rows
become visible through `db.session` inside the outer SAVEPOINT.

**We haven't needed this yet.** Phase 4b (schemas, health, API auth)
is all read-path or pure mock, so the basic `app` fixture suffices.
Revisit when Phase 4d (write-path API) forces the question.

### Gotchas the 4a work exposed

1. **`/api/v1/health/` pings every `SQLALCHEMY_BINDS` entry** — including
   `system_status`, which is still pointing at the dev container default
   (unreachable from the test env). The smoke test hits `/api/v1/health/live`
   instead (in-process, no DB pings).
2. **`auth_client` works end-to-end for benkirk.** `TestingConfig.DEV_ROLE_MAPPING`
   gives `benkirk → admin` without any `role_user` DB rows, so the `/api/v1/users/me`
   auth_client smoke test returns 200 + `{"username": "benkirk"}` directly.
3. **`create_app()` is expensive** (registers all blueprints, initializes
   Flask-Login, Flask-SQLAlchemy, Flask-Caching, audit events). The `app`
   fixture is session-scoped so one app per xdist worker.
4. **`FLASK_SECRET_KEY` is required.** The `app` fixture sets it to
   `'test-secret-key'` via `os.environ.setdefault`. Won't clobber a
   real value if the user happens to have one set.
5. **`SQLALCHEMY_ENGINE_OPTIONS` is a replacement, not a merge** — if the
   test fixture overrides it, the production pool defaults
   (pool_size=10, pool_recycle=3600, pool_pre_ping=True) are lost.
   Acceptable for tests (one connection, no pool). Just don't forget
   to re-add `pool_pre_ping=True` if a test fixture needs it.

---

## Phase 4 execution plan

### 4a — Infrastructure: `create_app()` refactor + fixtures ✅ DONE

Shipped in the Phase 4a commit:
- `src/webapp/run.py::create_app()` — added `*, config_overrides: dict | None = None`
  kwarg and a 3-line merge block just before `db.init_app(app)`.
- `new_tests/conftest.py` — added session-scoped `app` fixture (uses
  `create_app(config_overrides={'SQLALCHEMY_DATABASE_URI': test_db_url})`),
  function-scoped `client`, function-scoped `auth_client` (benkirk via
  Flask-Login session cookie + `TestingConfig.DEV_ROLE_MAPPING`).
- `new_tests/unit/test_webapp_smoke.py` — 2 tests:
  - `test_liveness_endpoint_returns_200(client)` — unauthenticated GET
    `/api/v1/health/live` (pure in-process probe, no DB pings).
  - `test_auth_client_is_logged_in_as_benkirk(auth_client)` — GET
    `/api/v1/users/me`, asserts 200 + `username == "benkirk"`.
- Count: **955 passed, 22 skipped** in `new_tests/` (was 953 + 2 new).
- Legacy: **446 passed, 30 skipped, 2 xpassed** — zero change.

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

**Strategy:** Add an `app_with_shared_connection` fixture to
`new_tests/conftest.py` that uses the `creator=`/`StaticPool` pattern from
the "Phase 4 architecture" section above — Flask-SQLAlchemy's `db.session`
and the raw `session` fixture share one connection, one outer transaction.
Factory rows are visible through routes, route commits become SAVEPOINT
releases, teardown rolls back.

With that in place, the 4d pattern is:
1. `factory.make_user()` / `make_project()` / etc. via the raw `session`.
2. Issue the test-client POST — the view runs through `management_transaction`
   which calls `session.commit()` (= SAVEPOINT release in our bridged mode).
3. Verify via a `db.session` query from within the same app context OR
   via the raw `session` — they see the same data because they share a
   connection.
4. Teardown rolls back the outer transaction; nothing leaks.

**Optional fallback if bridging turns out to be fragile:** drop these
three files as redundant. `test_charge_endpoints` overlaps heavily with
`test_manage_summaries` (Phase 3, 53 tests, already ported).
`test_member_management` overlaps with `test_management_functions`
(Phase 3, 14 tests, already ported). Only HTTP-layer concerns (route
wiring, marshmallow input validation, error formatting) are unique to
these files, and those are already covered by the `TestInputSchemas`
classes ported in Phase 3. This is the escape hatch if the bridged
fixture misbehaves, not the plan.

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

### Flask-SQLAlchemy config injection goes through `create_app(config_overrides=)`

`flask_sqlalchemy.SQLAlchemy()` does not support swapping the engine
*after* `db.init_app(app)` — but you don't need to. As of Phase 4a,
`src/webapp/run.py::create_app()` accepts a keyword-only
`config_overrides=` dict that's merged into `app.config` after the
defaults land but before `db.init_app(app)` runs. The test `app`
fixture uses this to inject `SQLALCHEMY_DATABASE_URI` (pointing at
mysql-test) and, when needed, `SQLALCHEMY_ENGINE_OPTIONS` with a
`creator=lambda: shared_connection` + `poolclass=StaticPool` to bind
Flask-SQLAlchemy's engine to the same connection as the raw `session`
fixture — enabling full SAVEPOINT bridging without any module-global
surgery or `sam.session.init_sam_db_defaults()` re-calls.

**Never** set `app.config[...]` after `db.init_app(app)`. Everything
goes through `config_overrides=` or the config class.

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
> Expected: HEAD is the Phase 4a commit
> (`create_app(config_overrides=...)` + `app`/`client`/`auth_client`
> fixtures + 2 webapp smoke tests). `new_tests/` should report
> **955 passed, 22 skipped**. If `samuel-mysql-test` isn't running,
> start it with `docker compose --profile test up -d mysql-test` and
> wait for TCP.
>
> Then kick off **Phase 4b**: port the 5 trivial webapp files in this
> order (simplest → most complex):
>
> 1. `tests/api/test_status_schemas.py` (10 tests, zero DB, zero auth)
> 2. `tests/api/test_api_auth.py` (10 tests, HTTP Basic decorator, no DB)
> 3. `tests/api/test_schemas.py` (18 tests, pure Marshmallow, factory data)
> 4. `tests/api/test_allocation_schemas.py` (13 tests, Marshmallow + factories)
> 5. `tests/api/test_health_endpoints.py` (22 tests, `client` +
>    `auth_client` + a new factory-built `non_admin_client` fixture)
>
> All 5 should drop in cleanly against the Phase 4a fixtures. Commit as
> one batch with a message describing each file moved. Then stop and
> wait for direction on 4c.
