# Testing Guide

## Overview

The SAM test suite has **~1,400 tests** across five tiers, running in
**~67 seconds** on a laptop with pytest-xdist parallelism. A separate
**17-test performance suite** runs serially in ~26 seconds and is gated
behind `pytest -m perf`.

All tests run against an **isolated `mysql-test` container** (host port
3307). A hard safety guard in `tests/conftest.py` refuses to run against
any other target — there is no way for a stray run to touch production
or dev data.

```
tests/
  conftest.py              # Safety guard, engine/session, Flask app/client fixtures
  factories/               # Layer-2 builder functions (make_user, make_project, ...)
  unit/                    # ORM, queries, CLI, webapp unit, cache behavior
  integration/             # Schema validation, views, system_status, CLI smoke
  api/                     # REST API endpoints + Marshmallow schema tests
  perf/                    # Query-count regression + latency benchmarks (gated)
```

---

## Quick Start

```bash
# One-time: start the isolated test container
docker compose --profile test up -d mysql-test

# Fast iteration (parallel, no coverage) — ~67s
source etc/config_env.sh && pytest

# With coverage
pytest --cov=src --cov-report=html --cov-fail-under=60

# Performance regression tests (serial, ~26s)
make perf
# or: pytest -m perf -n 0 -v
```

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
- `DEV_ROLE_MAPPING` gives `benkirk` the admin role
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

Gated behind `@pytest.mark.perf` and excluded from the default `pytest`
run via `-m "not perf"` in `pytest.ini`. Must run serially (`-n 0`)
because `pytest-benchmark` is disabled under xdist.

### What They Guard Against

The test suite was built to prevent recurrence of 7 specific performance
bugs identified by profiling scripts in `utils/profiling/`:

- **N+1 per-row queries** in allocation summaries (52,923 queries → 42)
- **Cascade-loading explosions** where `joinedload(Project.lead)` dragged
  in `User.accounts`, `User.email_addresses`, etc.
- **Lazy-load regressions** during template rendering and JSON serialization
- **Per-project loop fanout** on the user dashboard (288 queries → 36)
- **Duplicate allocation fetches** (two full passes → one + Python aggregation)

### Three Test Files

**`test_query_counts.py`** — 9 function-level tests. Each calls a query
function, counts SQL queries via SQLAlchemy engine events, and asserts
the count stays at or below a baseline from `baselines.json`.

**`test_route_query_counts.py`** — 5 route-level tests. Hit actual
Flask routes via `auth_client.get(...)` and count ALL queries through
the full stack (data fetch + template render + JSON serialization).
These catch lazy-load regressions invisible to function-level tests.

| Route | Measured | Baseline | Guards against |
|-------|----------|----------|----------------|
| `GET /user/` | 45 | 65 | Cascade-suppression regression |
| `GET /allocations/` | 13 | 20 | Summary pipeline |
| `GET /allocations/?show_usage=true` | 54 | 80 | The 52K-query N+1 scenario |
| `GET /admin/htmx/organizations-card` | 311 | 450 | Template cascade explosion |
| `GET /api/v1/fstree_access/` | 44 | 65 | Serialization lazy loads |

**`test_dashboard_latency.py`** — 3 `pytest-benchmark` smoke tests for
wall-time order-of-magnitude regressions.

### Query Counting Infrastructure

`tests/perf/_query_count.py` provides the `SQLStats` class (extracted
from `utils/profiling/profile_user_dashboard.py`). It hooks into
SQLAlchemy's `before_cursor_execute` / `after_cursor_execute` events to
count and time every query through an engine.

Two fixtures expose it:
- **`count_queries`** — attaches to the standalone `engine` (function-level tests)
- **`route_count_queries`** — attaches to `db.engine` (Flask route tests)

### Re-Baseline Workflow

When you intentionally improve a query pattern:

1. Run `pytest -m perf -n 0 -v`
2. The failure message shows the actual count vs. baseline
3. Update `tests/perf/baselines.json` with the new count
4. Commit both the code change and the updated baseline

When the count goes UP unexpectedly — fix the regression first.

---

## CI Integration

### `sam-ci-docker.yaml`

The primary CI workflow:

1. Builds and starts all containers including `mysql-test` (via `--profile test`)
2. Waits for both MySQL services to accept TCP connections
3. Runs `pytest --cov=src --cov-fail-under=60` inside the webapp container
4. On push to `main` only: runs `pytest -m perf -n 0` for performance regression checks
5. Uploads coverage report as a GitHub Actions artifact

### `ci-staging.yaml`

Staging merge gate — same container setup, runs `pytest` without coverage.

### Configuration

Root `pytest.ini`:

```ini
addopts = -v --strict-markers --tb=short --maxfail=5 -n auto -m "not perf"
markers =
    unit / integration / smoke / webapp / perf
filterwarnings = ignore::pytest_benchmark.logger.PytestBenchmarkWarning
timeout = 300
```

The `-m "not perf"` in `addopts` ensures perf tests never run in the
default suite. The `filterwarnings` suppresses the
"benchmarks disabled under xdist" noise.

---

## Writing Tests

### Which tier?

| Testing... | Put it in | Use |
|------------|-----------|-----|
| ORM model properties, query functions | `tests/unit/` | `session` + representative fixtures |
| Write operations (create/update/delete) | `tests/unit/` | `session` + factories |
| CLI commands | `tests/unit/` | `CliRunner` + mock session |
| Schema validation (ORM vs DB drift) | `tests/integration/` | `engine` + `SHOW CREATE TABLE` |
| API endpoints | `tests/api/` | `auth_client` + JSON assertions |
| Webapp routes + template rendering | `tests/unit/` | `auth_client` + status code checks |
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
pytest tests/unit/test_query_functions.py -v      # query functions
pytest tests/integration/test_schema_validation.py # ORM/DB drift
pytest tests/api/ -v                              # all API tests
pytest tests/unit/test_sam_search_cli.py -v       # CLI integration
make perf                                         # performance suite
```

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
