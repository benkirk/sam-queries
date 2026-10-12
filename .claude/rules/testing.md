---
paths:
  - "tests/**/*"
  - "pytest.ini"
---

# Testing mechanics

Loaded with any test. Run commands, outbound-closed and isolation are in the root `CLAUDE.md`; suite size and timings in `docs/TESTING.md`.

- **pytest.ini gates**: `-m "not perf"` — the `perf` tier runs only on request
  (`pytest -m perf -n 0`) — plus `--maxfail=5`, 300 s per-test timeout, `-n auto`.
  The perf tier owns `tests/perf/baselines.json` for query-count limits. The
  former `stress` tier now runs in the default suite as
  `tests/api/xras_audit_rows/` (xdist-safe; rows cleaned up by captured PK), and
  its `scenarios.json` records what each scenario expects the `xras_action_log`
  row to say.
- **Domain markers are derived from the directory**, not hand-applied:
  `pytest_collection_modifyitems` tags each `tests/unit/<domain>/` test with its
  domain marker (`gate` for `gates/`), and `tests/api`/`tests/integration` with
  `api`/`integration`. So `pytest -m xras` (or gate/notify/tasks/charts/cli/
  webapp/models/queries/manage) selects a directory and cannot drift. Registered
  in `pytest.ini`; identical fixtures live in the domain's `conftest.py`.
- **Route handlers use Flask-SQLAlchemy's `db.session`** (its own connection) —
  they only see committed snapshot rows, and route-level writes would COMMIT.
  House convention: HTTP-layer tests cover auth/validation/404/render smoke;
  happy-path writes are covered at the model layer.

**Test data — two tiers (never blend inside one helper; composing in a test is fine):**
- **Layer 1 — representative fixtures** (`tests/conftest.py`): `active_project`,
  `multi_project_user`, `hpc_resource`, `any_*` — pick ANY snapshot row of a
  structural shape; survives obfuscated-snapshot refreshes.
- **Layer 2 — factories** (`tests/factories/`): `make_user`, `make_project`,
  `make_allocation`, ... — `session` first positional arg, auto-builds the FK
  graph, flushes, returns the instance. For write-path tests needing exact values.

**The `system_status` tier**: per-worker SQLite tempfile bound at
`SQLALCHEMY_BINDS['system_status']`; schema via
`db.create_all(bind_key='system_status')` in the session-scoped `app` fixture;
per-test isolation via `DELETE FROM`. **Critical**: `FLASK_ACTIVE=1` is set in
`pytest_configure` (not the fixture) because `system_status.base.StatusBase`
resolves at import time — without it, module-level
`from system_status import DerechoStatus` during collection binds to a
standalone declarative_base and the bind routing never engages.

**Route-map parity**: `tests/unit/gates/test_route_map_parity.py` pins all dashboard
`(endpoint, rule, methods)` triples to `tests/unit/snapshots/`; regen with
`ROUTE_MAP_REGEN=1` and commit the diff when routes intentionally change.
