# Testing Guide

## Overview

The SAM test suite has **9,296 collected tests** (measured 2026-09-18). The
default run — everything except the gated `perf` tier — is **9,239 tests in
~90 seconds** on a laptop with pytest-xdist parallelism.

Regenerate both numbers with `pytest --collect-only -q | tail -1` (default run)
and `pytest --collect-only -q -m "" | tail -1` (everything).

One tier is gated **off** by default and runs only when asked for:

| tier | size | command | what it is |
|---|---|---|---|
| `perf` | 57 | `pytest -m perf -n 0` (or `make perf`) | query-count and latency baselines |

`perf` **requires** `-n 0`: `pytest-benchmark` is disabled under xdist, and a
query-count baseline is meaningless under concurrent load. The XRAS audit-row
scenarios in `tests/api/xras_audit_rows/` are **not** gated — they run in the
default suite, xdist-safe because their `action_log` fixture reads and deletes
by the PKs the route mints (reasoning on the fixture in `tests/xras_audit.py`);
`scenarios.json` beside them records what each scenario expects the
`xras_action_log` row to say.

> This file is the single source of truth for suite size and timings —
> other docs link here rather than restating numbers.

All tests run against an **isolated test container**: `mysql-test` (host
port 3307) by default, or `postgres-test` (host port 5434) for the second
backend — see [Two backends](#two-backends) below. A hard safety guard in
`tests/conftest.py` refuses to run against any other target — there is no
way for a stray run to touch production or dev data.

`tests/unit/` is split by domain, and each test's marker is derived from its
directory (below), so `pytest -m xras` and its siblings select a directory and
cannot drift out of step with the layout:

```
tests/
  conftest.py              # Safety guard, engine/session, Flask app/client fixtures
  factories/               # Layer-2 builder functions (make_user, make_project, ...)
  unit/<domain>/           # gates xras notify tasks charts cli webapp models
                           #   queries manage (marker == directory; gates -> -m gate).
                           #   snapshots/ is fixture JSON, not tests
  integration/             # Schema validation, views, status tier, CLI smoke (-m integration)
  api/                     # REST endpoints + schemas; xras_audit_rows/     (-m api)
  perf/                    # Query-count regression + latency benchmarks (gated, -m perf)
```

Shared fixtures live in each domain's `conftest.py`, and a new file dropped into
a domain directory inherits the marker automatically.

---

## Quick Start

```bash
# One-time: start the isolated test container
docker compose --profile test up -d mysql-test

# Fast iteration (parallel, no coverage) — ~90s
source etc/config_env.sh && pytest

# One domain (marker == directory: gate/xras/notify/tasks/charts/cli/webapp/models/queries/manage)
pytest -m xras            # or, equivalently, by path: pytest tests/unit/xras

# With coverage (branch coverage + the fail_under floor come from pyproject.toml)
pytest --cov=src --cov-report=html

# Performance regression tests (serial, ~26s)
make perf
# or: pytest -m perf -n 0 -v
```

`perf` is gated **off** by default via `addopts` (`-m "not perf"`) and runs only
when asked for. Its declaration file `tests/perf/baselines.json` holds the
query-count limits the tests read. The audit-row scenarios in
`tests/api/xras_audit_rows/` run in the default suite; `scenarios.json` beside
them records what each scenario expects the `xras_action_log` row to say, and a
scenario with no manifest entry fails rather than running unspecified.

If `SAM_TEST_DB_URL` is unset or points at anything other than
`127.0.0.1:3307`, pytest aborts with `REFUSING TO RUN tests against
this database` — by design.

---

## Architecture

### Safety Guard

`pytest_configure` in `tests/conftest.py` runs before any fixture or
test module. It:

1. Requires `SAM_TEST_DB_URL` to be set.
2. Parses the URL and checks `(host, port)` against an allowlist:
   `127.0.0.1:3307`, `localhost:3307`, `mysql-test:3306`.
3. Aborts with exit code 2 if the target is not allowed.

This is the single most important piece of test infrastructure. It
guarantees that no combination of env vars, CI config errors, or
human mistakes can route test queries to a production or dev database.

### Per-Test Isolation: SAVEPOINT Rollback

Every test gets its own SQLAlchemy session via the `session` fixture:

```
engine (session-scoped)
  └── raw connection → BEGIN outer transaction
        └── Session(bind=connection, join_transaction_mode="create_savepoint")
              └── test code: session.add() / session.flush() / session.commit()
                    ↑ all are SAVEPOINT operations inside the outer transaction
              └── teardown: rollback outer transaction — nothing escapes
```

This is what lets 12 xdist workers share one `sam_test` database without
stepping on each other. Write-path tests can call `session.commit()` —
it becomes a SAVEPOINT release, not a real commit. At teardown, the
outer transaction rolls back and takes everything with it.

### Flask App / Client Fixtures

The `app` fixture (session-scoped) creates a Flask application pointing
at the test database via `create_app(config_overrides=...)`. The
`system_status` bind points at a per-worker SQLite tempfile.

```
app (session-scoped)
  ├── client (function-scoped)        — unauthenticated test client
  ├── auth_client (function-scoped)   — logged in as benkirk (admin role)
  ├── non_admin_client                — logged in as a non-admin user
  ├── api_key_client                  — HTTP Basic Auth for collector routes
  └── status_session                  — per-test session for system_status
```

`TestingConfig` is loaded via `FLASK_CONFIG=testing`. Key properties:
- `TESTING = True`, `WTF_CSRF_ENABLED = False`
- `ALLOCATION_USAGE_CACHE_TTL = 0` (caching disabled)
- `USER_PERMISSION_OVERRIDES['benkirk']` (in `webapp.utils.rbac`) gives `benkirk` the full Permission set
- `NullCache` for Flask-Caching (no `@cache.cached` interference)

---

## Test Data Strategy — Two Tiers

### Layer 1: Representative Fixtures

Session-scoped queries pick ANY row from the snapshot matching a
structural shape. Tests built on this layer survive snapshot refreshes
as long as one row of the required shape exists.

| Fixture | Shape | Used by |
|---------|-------|---------|
| `active_project` | Project with >= 1 active allocation | Query functions, CLI, dashboard |
| `multi_project_user` | Active user on >= 2 active projects | User dashboard, perf tests |
| `hpc_resource` | Currently-active HPC resource | Fstree, allocations, perf |
| `subtree_project` | Project with >= 3 active children | MPTT rollup tests |
| `any_facility`, `any_panel`, ... | First row of type X | `.update()` contract tests |

Pattern: session-scoped ID lookup + function-scoped `session.get()`:

```python
@pytest.fixture(scope="session")
def _active_project_id(engine):
    # one SQL query at suite startup
    ...

@pytest.fixture
def active_project(session, _active_project_id):
    return session.get(Project, _active_project_id)
```

### Layer 2: Factories

Plain builder functions in `tests/factories/` that construct fresh
synthetic rows inside each test's SAVEPOINT. Used by write-path tests
that need exact counts/values.

```python
from factories.core import make_user
from factories.projects import make_project

def test_add_member(session):
    user = make_user(session)
    project = make_project(session)
    # ... test logic with known-state data
```

Each builder auto-builds the minimum FK graph it needs, calls
`session.flush()`, and returns the flushed instance. Factories never
fall back to snapshot data — isolation is absolute.

**Rule:** never blend Layer 1 and Layer 2 inside a single helper. But a
test may compose both: `active_project` for a read-only graph and
`make_user(session)` for a fresh user.

---

## Performance Regression Tests (`tests/perf/`)

Gated behind `@pytest.mark.perf` and excluded from the default run via
`-m "not perf"`. Must run serially (`-n 0`) — `pytest-benchmark` is disabled
under xdist.

### What They Guard Against

Recurrence of the specific performance bugs profiling in `utils/profiling/` found:

- **N+1 per-row queries** in allocation summaries (52,923 queries → 42)
- **Cascade-loading explosions** — `joinedload(Project.lead)` dragging in `User.accounts` etc.
- **Lazy-load regressions** during template rendering and JSON serialization
- **Per-project loop fanout** on the user dashboard (288 queries → 36)
- **Duplicate allocation fetches** (two full passes → one + Python aggregation)

### Three Test Files

**`test_query_counts.py`** — 9 function-level tests. Each calls a query
function, counts SQL queries via SQLAlchemy engine events, and asserts
the count stays at or below a baseline from `baselines.json`.

**`test_route_query_counts.py`** — 7 route-level tests. Hit actual
Flask routes via `auth_client.get(...)` and count ALL queries through
the full stack (data fetch + template render + JSON serialization).
These catch lazy-load regressions invisible to function-level tests.

| Route | Measured | Baseline | Guards against |
|-------|----------|----------|----------------|
| `GET /user/` | 45 | 65 | Cascade-suppression regression |
| `GET /allocations/` | 13 | 20 | Summary pipeline |
| `GET /allocations/?show_usage=true` | 54 | 80 | The 52K-query N+1 scenario |
| `GET /admin/htmx/organizations-card` | 311 | 450 | Template cascade explosion |
| `GET /admin/htmx/institutions-fragment` | 13 | 25 | Institution list load |
| `GET /admin/htmx/institutions-fragment?show_users_projects=1` | 130 | 200 | Selectinload chunking fanout |
| `GET /api/v1/fstree_access/` | 44 | 65 | Serialization lazy loads |
| `GET /api/v1/projects/<projcode>` | 33 | 45 | Contract-graph N+1 during `jsonify()` |

**`test_dashboard_latency.py`** — 3 `pytest-benchmark` smoke tests for
wall-time order-of-magnitude regressions.

### Query Counting Infrastructure

`tests/perf/_query_count.py`'s `SQLStats` class hooks SQLAlchemy's
`before_cursor_execute` / `after_cursor_execute` events to count and time every
query. Two fixtures expose it: `count_queries` (the standalone `engine`,
function-level tests) and `route_count_queries` (`db.engine`, Flask routes).

### Re-Baseline Workflow

When you intentionally improve a query pattern, run `pytest -m perf -n 0 -v`,
read the actual-vs-baseline count from the failure, update
`tests/perf/baselines.json`, and commit both together. When the count goes UP
unexpectedly — fix the regression first.

---

## CI Integration

### `sam-ci-docker.yaml`

The primary CI workflow:

Job `pytest`:

1. Builds and starts all containers including `mysql-test` (via `--profile test`)
2. Waits for both MySQL services to accept TCP connections
3. Runs `pytest --cov=src` inside the webapp container (branch coverage and the
   `fail_under` floor are configured in `pyproject.toml`)
4. Runs the gated `perf` tier, `if: always()` — `pytest -m perf -n 0`. The XRAS
   audit-row scenarios run inside step 3, as part of the default suite.
5. Uploads coverage report as a GitHub Actions artifact

Job `pytest-postgres`: the same build and start, then
`scripts/ci/wait-for-postgres.sh`, then `make -C containers/sam-sql-dev
clone-pg-test` inside the webapp container with `PG_TEST_SOURCE_URL`
(`mysql-test:3306`), `PG_TEST_HOST` and `PG_TEST_PORT` (`postgres-test:5432`)
overriding the loader's localhost defaults, then the default tier with
`SAM_TEST_DB_URL` pointing at `postgres-test`. No coverage upload.

### `ci-staging.yaml`

Staging merge gate — the same two jobs (`test` and `test-postgres`), `pytest`
without coverage.

### Configuration

Root `pytest.ini`:

```ini
addopts = -v --strict-markers --tb=short --maxfail=5 -n auto -m "not perf"
markers =
    gate / xras / notify / tasks / charts / cli / webapp / models / queries /
    manage / api / integration / perf / mysql_only / postgres_only
filterwarnings = ignore::pytest_benchmark.logger.PytestBenchmarkWarning
timeout = 300
```

The `-m "not perf"` in `addopts` keeps the one gated tier out of the default
suite. The domain markers (`gate`…`integration`) are **auto-applied by
directory** in `pytest_collection_modifyitems` — do not hand-apply them; `perf`,
`mysql_only` and `postgres_only` are hand-applied. The `filterwarnings`
suppresses the "benchmarks disabled under xdist" noise.

---

## Two backends

The default tier runs on MySQL and on a Postgres copy of the same obfuscated
snapshot, so a query that only works on one backend cannot land unnoticed
(`docs/plans/implemented/POSTGRES_MIGRATION.md`, Stage 3). The Postgres copy is built by
the loader from `mysql-test`, not restored from a dump:

```bash
make -C containers/sam-sql-dev pg-test-up clone-pg-test   # ~35 s
make pytest-pg                                             # the default tier on 5434
make docker-pytest-pg                                      # the same inside the stack, as CI does
```

`SAM_TEST_DB_URL` selects the backend; the allowlist admits
`127.0.0.1:5434`, `localhost:5434` and `postgres-test:5432` for it.

- **Markers.** `mysql_only` and `postgres_only` skip a test on the other
  backend (`tests/conftest.py` applies them in `pytest_collection_modifyitems`,
  reading the backend through `tests/_backends.py`). The MySQL drift gates
  (`test_schema_validation.py`) are `mysql_only` by design. The rare test whose
  expectation differs reads `engine.dialect.name`.
- **Fixture SQL is portable.** Raw fixture queries compare booleans to
  `TRUE`/`FALSE`, never `1`/`0`, and quote camelCase identifiers through the
  engine's `identifier_preparer`.
- **`tests/postgres_expected_failures.txt`** is the burn-down list: one node id
  (or a `file::Class` prefix) per line with a `# reason`. On the Postgres
  target each entry becomes `xfail(strict=True)`, so a test that starts passing
  **fails the run** until its line is removed, and
  `tests/unit/models/test_postgres_expected_failures.py` fails on an entry that no
  longer names a collected test. The `sam-dev` deployment waits for the list to
  be empty. Same house pattern as `tests/perf/baselines.json`.
- The `perf` tier stays MySQL-only: its baselines are MySQL measurements.

---

## Writing Tests

### Which tier?

Pick the `tests/unit/<domain>/` directory by subject; the marker follows it.

| Testing... | Put it in | Use |
|------------|-----------|-----|
| ORM model properties, write ops | `tests/unit/models/` | `session` + fixtures / factories |
| Read-side query functions | `tests/unit/queries/` | `session` + representative fixtures |
| Multi-entity writes (`sam.manage`) | `tests/unit/manage/` | `session` + factories |
| CLI commands | `tests/unit/cli/` | `CliRunner` + mock session |
| Webapp routes + template rendering | `tests/unit/webapp/` | `auth_client` + status code checks |
| XRAS / notifications / tasks / charts | `tests/unit/{xras,notify,tasks,charts}/` | domain fixtures |
| Source-scanning lint / contract gates | `tests/unit/gates/` | AST / file scans |
| Schema validation (ORM vs DB drift) | `tests/integration/` | `engine` + `SHOW CREATE TABLE` |
| API endpoints | `tests/api/` | `auth_client` + JSON assertions |
| Query-count regression | `tests/perf/` | `count_queries` + `baselines.json` |

### Conventions

- Use `Model.is_active` (hybrid property) — never raw column comparisons
- Use `User.get_by_username()` / `Project.get_by_projcode()` for lookups
- Use `datetime.now()` (naive) — database uses naive datetimes
- Derive test data from fixtures at runtime, don't hardcode `'SCSG0001'`
  or `'benkirk'` — the one exception is `benkirk` which is preserved
  across snapshot refreshes for auth fixture purposes

### Running Specific Areas

```bash
pytest -m xras                                    # one domain (marker == directory)
pytest tests/unit/queries/test_query_functions.py -v      # query functions
pytest tests/integration/test_schema_validation.py # ORM/DB drift
pytest tests/api/ -v                              # all API tests
pytest tests/unit/cli/test_sam_search_cli.py -v       # CLI integration
make perf                                         # performance suite
```

---

## Browser tier (`e2e/`) — front-end regression net

Until this tier the Python suite had zero coverage of the ~3,000 lines of
dashboard JavaScript. PR #378 fixed two bugs that nothing could have caught: a
dead edit pencil (its modal shell was never included on the page) and a scroll
overshoot past a card title. See
[`docs/plans/implemented/FRONTEND_TEST_NET.md`](plans/implemented/FRONTEND_TEST_NET.md) for the full
decision record, including why a JS unit harness (vitest/jsdom) was rejected.

Two complementary layers, both driven by pytest:

| Layer | Where | Catches |
|---|---|---|
| Shell contracts | `tests/unit/gates/test_modal_shell_contract.py` | dangling `data-bs-target` — **completely silent in the browser** |
| Console sweep | `e2e/` (Playwright) | dangling `hx-target`, uncaught JS exceptions, script-order breaks |

Neither subsumes the other. htmx *does* `console.error("htmx:targetError")` on a
dangling `hx-target`, so the browser catches that — but a dangling
`data-bs-target` produces no console output at all, so a Bootstrap-only
affordance would slip past a browser-only net entirely.

### Why `e2e/` lives outside `tests/`

`tests/conftest.py`'s `pytest_configure` guard hard-exits unless
`SAM_TEST_DB_URL` points at the allowlisted `mysql-test` container. Browser
tests talk HTTP and never touch the DB. Because conftest files are only loaded
from a test file's ancestor directories, keeping `e2e/` a *sibling* of `tests/`
means the guard is never loaded and never has to be weakened — and bare
`pytest` never collects it (`testpaths = tests`). Same reasoning as
`utils/parity/` below. Nothing under `e2e/` may import `sam`, `webapp` or
`system_status`; that boundary is what lets CI install a bare Python plus
`pytest-playwright` instead of building a conda environment.

### Running it locally

```bash
pip install -e ".[e2e]"          # one-time; not part of [test] on purpose
playwright install chromium      # one-time

make docker-up                                   # stack must be running
make e2e                                         # -> :7050 (gunicorn/prod target)
make e2e SAM_E2E_BASE_URL=http://localhost:5050  # -> dev server
```

The sweep logs in once as `benkirk` through the stub form and reuses the
cookie — the same admin-equivalent identity the `auth_client` fixture uses, and
the one username the obfuscated snapshot preserves. Its route list is *derived*
from `tests/unit/snapshots/dashboard_route_map.json`, so a new top-level tab
enters the sweep automatically.

### The flake rule

E2E flake is the real long-term cost of this tier, so it is budgeted up front:
**no retries** (`--reruns`/`--retries` are deliberately absent from
`e2e/pytest.ini`), auto-waiting only — never a bare `sleep` as a substitute for
a wait condition — and a flaky sweep route gets *fixed or deleted*, never
retried. If flake ever becomes chronic the fallback is `continue-on-error: true`
on `browser-smoke.yaml` alone, which is cheap precisely because it is its own
workflow; letting people learn to ignore a red check is not an option.

### CI

`.github/workflows/browser-smoke.yaml` — checkout → `setup-python` →
`pip install -e ".[e2e]"` → `make docker-up` → `make e2e` → `make docker-down`.
It runs on PRs to `main`/`staging` (with the usual `docs/**` `paths-ignore`) and
on `workflow_dispatch`. It raises `RATELIMIT_AUTHED` in `.env` first: the sweep
is a single rate-limit actor against a **global** 200/min default, and a 429
would surface as an htmx error indistinguishable from a real regression.

---

## Related: ad-hoc parity checks (not in the test suite)

Legacy-vs-new API parity verification lives outside `tests/` because it
requires the UCAR VPN and hits live production hosts (`sam.ucar.edu` and
`samuel.k8s.ucar.edu`). Placing it under `tests/` would collide with the
safety guard that restricts the suite to the `mysql-test` container.

It is a standalone CLI at `utils/parity/check_legacy_apis.py` with its
own README — see [`utils/parity/README.md`](../utils/parity/README.md).
The script fetches both APIs, runs ~28 comparison rules (subset checks,
GID/UID/gecos equality, allocation amounts, ±5% adjusted-usage
tolerance, etc.), and exits 0/1/2 based on whether parity holds,
mismatches were found, or preconditions were missing.

---

## Troubleshooting

**"REFUSING TO RUN tests against this database"** — `SAM_TEST_DB_URL`
is missing or points at the wrong host/port. Start the test container
and export the URL:

```bash
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
```

**Perf test failure** — check whether the query count went up
intentionally (new feature) or accidentally (N+1 regression). Update
`baselines.json` or fix the regression accordingly.

**Flaky write-path test under xdist** — SAVEPOINT isolation handles
most concurrency. If a test legitimately needs committed state visible
to other sessions, it may need a dedicated fixture. File an issue.

**Schema validation failure** — the database schema changed. Update
the ORM model to match (database is source of truth), then re-run.
