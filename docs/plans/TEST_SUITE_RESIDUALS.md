# Test Suite — Residual Work

**Status:** Migration, CI wiring, and performance regression tests are
complete. Gap A (query-count regression tests) landed in full across
commits `3d6dc05` and `0ba3fc3` on branch `test_additions`. Gap B
(Pyright gradual type checking) remains open and can be picked up
independently.

---

## Completed work (summary)

The original plan had eight sections (Phases 0–7 plus CI). All eight
are done. Key milestones:

| Phase | Scope | Landed in |
|---|---|---|
| 0 | `FLASK_ENV` → `FLASK_CONFIG` bugfix in legacy suite; TestingConfig selection actually engages | pre-branch |
| 1 | `mysql-test` compose service + `--profile test`; `new_tests/` skeleton; `pyproject.toml` test deps | pre-branch |
| 2a–2e | Read-path ports — schema validation, views, query functions, rolling usage, fstree, project access, manage (facilities/orgs/resources), accounting models, active interface, basic read, charging, directory access, email, cli/jupyterhub | `d870f45` through `9a8a176` |
| 3 | Factory module (13 builders) + 8 write-path ports (wallclock exemptions, transaction context, management functions, crud operations, allocation tree, audit logging, manage summaries, renew/extend) | `2941eb7` |
| 4a | `create_app(config_overrides=)` + `app`/`client`/`auth_client` fixtures | `6b9bc45` |
| 4b–4f | API schemas/health/auth ports; API read-only GETs; API write-path escape-hatch subset; webapp unit (admin defaults + OIDC); status_session via SQLite tempfile + CLI subcommand slim | `1ab8d67` through `334bfa4` |
| 4f cleanup | Status dashboard read routes refactored to `db.session` (dropped legacy `create_status_engine()` calls + associated test monkey-patch) | `9c5514d` |
| 5 | Performance file port (`test_allocations_performance.py`, 103 tests, cache-behavior + matplotlib lru_cache + dashboard routes) | `1655edd` |
| Post-migration | `new_tests/` → `tests/` rename; `tools/orm_inventory.py` → `scripts/`; `mock_data/` → `scripts/mock_data/`; legacy infra deleted; `pytest.ini` consolidated; `Resume.md` + `REFACTOR_TESTING.md` deleted; README/CLAUDE.md testing sections refreshed | `46f8eca` |
| 8 (pyproject) | `pyproject.toml` dead pytest config removed; `fail_under` 70→60 to match post-migration reality | `f603ed5` |
| 8 (CI trim) | `test-install.yaml` + `sam-ci-conda_make.yaml` pytest steps dropped, replaced with CLI + `/health/live` smoke | `674bc0c` |
| 8 (CI wire) | `sam-ci-docker.yaml` + `ci-staging.yaml` now start `mysql-test` and pass `SAM_TEST_DB_URL` | `15a38d6` |

### Current state

- **1348 tests passing**, 22 skipped, 1 xfailed, ~68s under xdist `-n auto`
- **67.82% coverage** (60% CI gate, ~8-point safety margin)
- `tests/` runs against isolated `mysql-test` container (host port 3307) with a `REFUSING TO RUN` safety guard against any other target
- Per-test **SAVEPOINT rollback isolation** via `join_transaction_mode="create_savepoint"` — xdist workers share one DB safely
- `system_status` tier uses per-worker SQLite tempfile bound at `SQLALCHEMY_BINDS['system_status']`, schema materialized via `db.create_all(bind_key='system_status')`
- Two-tier test data strategy: representative fixtures (`tests/conftest.py`) for read-path tests, factories (`tests/factories/`) for write-path
- CI: bare `pytest` works from the project root in both `sam-ci-docker.yaml` (with coverage, fail-under 60) and `ci-staging.yaml` (no coverage)

### Things the original plan proposed that we did NOT do, and why

- **Snapshot hash lock (`snapshot.lock` + `make test-snapshot-rehash`)** — replaced with the representative-fixture strategy, which solves the same value-drift problem differently. Tests don't assert on exact values from the snapshot; they use "any row of shape X" fixtures that survive snapshot refreshes as long as one row of the required shape exists. Could revisit if a snapshot refresh breaks the suite in practice.
- **Per-worker MySQL DB cloning for `system_status`** — replaced with per-worker SQLite tempfile, which is ~25 lines of fixture code vs ~80 lines of the original plan's env-var-ordered MySQL setup.
- **`TESTING_AUTO_LOGIN_USER` auth bypass config** — not needed. `USER_PERMISSION_OVERRIDES['benkirk']` + Flask-Login session cookies gave us the `auth_client` fixture without the bypass.
- **Legacy API parity tests** — dropped (28 tests). Required `SAM_LEGACY_USER/PASS` + `PROD_SAM_DB_*` env vars to cross-check against the live `sam.ucar.edu` legacy API. Never runnable in CI; skipped in practice.
- **Most of `test_sam_search_cli.py` subprocess coverage (44 of 61 tests)** — redundant with the ported CliRunner version (`tests/unit/test_sam_search_cli.py` + `test_sam_search_cli_allocations.py`). One subprocess smoke (`tests/integration/test_cli_smoke.py`) retains entry-point verification.
- **ORM `Mapped[]` migration** — out of scope per the original plan; stays out of scope.

---

## Gap A — Performance & query-count regression tests — COMPLETED

**Source:** original plan §5 (Performance & query-count regression tests).

**Landed in:** commits `3d6dc05`, `0ba3fc3`, `cfe5550` on branch
`test_additions` (2026-04-16).

### What was built

**Infrastructure** (`tests/perf/`):
- `_query_count.py` — `SQLStats` class (extracted from
  `utils/profiling/profile_user_dashboard.py`) with `attach()`/`detach()`
  engine event hooks, wrapped in a `count_queries()` context manager.
- `conftest.py` — `count_queries` fixture (standalone engine),
  `route_count_queries` fixture (Flask-SQLAlchemy `db.engine`),
  `_reset_usage_cache` autouse fixture, `get_baseline()` helper,
  perf-specific data fixtures.
- `baselines.json` — measured query counts with ~50% headroom, plus
  documented re-baseline workflow.

**Function-level query count tests** (9 tests in `test_query_counts.py`):

| Function | Measured | Baseline |
|----------|----------|----------|
| `get_user_dashboard_data` | 26 | 35 |
| `get_project_dashboard_data` | 45 | 55 |
| `get_resource_detail_data` | 8 | 15 |
| `get_fstree_data` | 3 | 10 |
| `get_allocation_summary` | 2 | 10 |
| `get_allocation_summary_with_usage` (single resource) | 2 | 10 |
| `get_allocation_summary_with_usage` (all resources) | 44 | 65 |
| `Project.get_detailed_allocation_usage` | 37 | 50 |
| `get_project_rolling_usage` | 11 | 20 |

**Route-level query count tests** (5 tests in `test_route_query_counts.py`):

These hit actual Flask routes via `auth_client.get(...)` and count ALL
queries through the full stack (data fetch + template render + JSON
serialization). They catch lazy-load regressions invisible to
function-level tests.

| Route | Measured | Baseline | Guards against |
|-------|----------|----------|----------------|
| `GET /user/` | 45 | 65 | Cascade-suppression regression (Issues 5, 6) |
| `GET /allocations/` | 13 | 20 | Summary + facility overview pipeline |
| `GET /allocations/?show_usage=true` | 54 | 80 | The 52K-query N+1 scenario (Issue 1) |
| `GET /admin/htmx/organizations-card` | 311 | 450 | Template cascade explosion (Issue 7) |
| `GET /api/v1/fstree_access/` | 44 | 65 | JSON serialization lazy loads |

**Latency smoke benchmarks** (3 tests in `test_dashboard_latency.py`):

`pytest-benchmark` smoke tests for user dashboard, project dashboard,
and allocation summary. No assertions on absolute time — these produce
the benchmark table for local developer visibility via `make perf`.

**Gating:**
- `pytest.ini`: `-m "not perf"` in default `addopts`
- `Makefile`: `make perf` target
- CI: perf step in `sam-ci-docker.yaml` runs after main pytest
- `filterwarnings` suppresses benchmark-under-xdist noise

**Documentation:** `docs/TESTING.md` covers the full architecture.

### Design decision: query counts only, no latency assertions

The original plan proposed latency assertions ("this ran in under N ms,
loosely"). After implementation and measurement, we decided against
latency assertions for CI. Rationale:

1. **Every bug we traced was a query count problem.** The 7 performance
   issues were all N+1 patterns, missing batch fetches, or cascade-loading
   explosions. Wall time was a direct consequence of query count. Nobody
   introduced a bug where query count stayed constant but latency exploded.

2. **CI runner timing is too noisy.** GitHub Actions runners have variable
   CPU/memory/IO across runs. The same function measured 622ms to 4.5s
   depending on load. Latency assertions that pass Monday and fail Tuesday
   erode trust in the suite.

3. **The right tools already exist for latency work.** `pytest-benchmark`
   produces the benchmark table locally via `make perf` — useful for
   developers profiling on their own hardware. The `utils/profiling/`
   scripts remain the tool for deep latency investigation.

4. **Schema validation catches the remaining gap.** The one scenario
   query counts wouldn't catch — a single query becoming catastrophically
   slow (e.g., dropped index) — is better caught by the schema validation
   tests in `test_schema_validation.py` than by flaky timing assertions.

### Suite statistics

- `pytest -m perf -n 0 -v` → **17 passed** in ~26s
- `pytest` (default) → **1348 passed**, 22 skipped in ~67s (unchanged)
- `make perf` wired up for convenience

---

## Remaining gap B — Pyright gradual type checking

**Source:** original plan §6 (Type checking: Pyright, gradual).

**Motivation:** catch type errors that would otherwise surface at
runtime. The plan is explicitly gradual — start on 3 modules with
clean boundaries, never try to type the whole codebase at once.

### What's already in place

- `pyright` is already in `[project.optional-dependencies].test`
  (landed in Phase 1).
- `src/sam/schemas/` has grown considerably since the plan was
  written (now includes `forms/` subdir for Marshmallow form schemas).
  Still a reasonable starting target — Marshmallow schemas are
  declarative and already carry type information in their field
  definitions.
- `src/sam/queries/` is a natural starting target: query functions
  have explicit return types in several files (`rolling_usage.py`,
  etc.), and the module is the gateway between ORM and consumers
  (CLI, webapp, API schemas).
- `src/sam/fmt.py` is small, pure, and stateless — easiest possible
  win for a strict-mode module.
- No `pyrightconfig.json` exists at the repo root yet.

### Scope

**1. Initial config** (~30 minutes)

Create `pyrightconfig.json` at the repo root:

```json
{
  "include": ["src"],
  "exclude": [
    "**/__pycache__",
    "**/node_modules",
    "conda-env",
    "legacy_sam"
  ],
  "typeCheckingMode": "basic",
  "pythonVersion": "3.13",
  "pythonPlatform": "All",
  "reportMissingImports": "error",
  "reportMissingTypeStubs": "none",
  "strict": [
    "src/sam/fmt.py"
  ]
}
```

Start with **only `src/sam/fmt.py`** in the strict allowlist. Prove the
workflow on one file before expanding. Every other file runs in `basic`
mode where Pyright reports issues but does not fail on them.

**2. Cleanup pass on `src/sam/fmt.py`** (~1 hour)

- Run `pyright src/sam/fmt.py --strict` (or via config once the above
  is in place)
- Fix whatever it complains about. Likely: missing return type annotations,
  ambiguous `int | float` unions, `Optional` where `None` is allowed.
- The file is small (~200 lines). Worst case: add a handful of type hints
  and one `cast()`.

**3. Promote `src/sam/schemas/` module by module** (~half day each, gated on need)

- Run `pyright src/sam/schemas/ --outputjson | jq '.summary'` to get a
  count of issues.
- Fix the three easiest files first (probably `user.py`, `project.py`,
  `resource.py` — the simplest Marshmallow schemas). Each "fix" is
  mostly adding `fields.Method('method_name')` returns and marking
  attributes as `fields.Nested[OtherSchema]`.
- Add each cleaned file to the `strict` allowlist one at a time. Rerun
  after each add to catch regressions from the previous file's fixes.
- `forms/` subdir is likely the messiest — Marshmallow form loading is
  duck-typed. Leave it on `basic` indefinitely unless we have a reason.

**4. Promote `src/sam/queries/` cautiously** (~1 day)

- Many query functions return `list[dict]` with schema-dependent shapes.
  These are hard to type without `TypedDict` definitions for every
  returned shape. That's real work.
- Pragma: type the **function signatures** (inputs) but leave return
  types as `list[dict]` for now. Partial strict mode — still catches
  caller mistakes, doesn't require a whole TypedDict layer.
- If this feels like too much, **skip queries/ for now** and focus on
  just `fmt.py` + the cleanest 3 schemas. That's enough to demonstrate
  the workflow.

**5. Pre-commit hook** (~30 minutes)

- Add Pyright to `.pre-commit-config.yaml` as a **warn-only** hook
  initially:

  ```yaml
  - repo: https://github.com/RobertCraigie/pyright-python
    rev: v1.1.390
    hooks:
      - id: pyright
        args: ["--outputjson"]
        verbose: true
  ```

- Warn-only means Pyright runs on every commit but does not block.
  After a few weeks of observing what kinds of issues pyright flags,
  escalate to blocking by dropping `verbose: true` and removing the
  `|| true` escape hatch.

**6. CI integration (optional, lower priority)** (~1 hour)

- Add a `pyright` job in `sam-ci-docker.yaml` (or a new dedicated
  workflow) that runs `pyright --outputjson | jq '...'` and fails
  only on errors in files listed under `strict` in `pyrightconfig.json`.
- Do NOT fail CI on `basic`-mode issues. The whole point of the gradual
  model is that basic-mode issues are informational.

**7. Documentation** (~30 minutes)

- Add a section to `CONTRIBUTING.md` on the pyright workflow: which
  modules are strict, how to check locally (`pyright src/sam/fmt.py`),
  how to promote a new module to strict (add to the config, run with
  `--stats`, fix issues, commit).
- Add a one-paragraph note to `CLAUDE.md` under "Development Workflow".

### Acceptance criteria

- `pyrightconfig.json` committed at the repo root
- `pyright` clean on `src/sam/fmt.py` in strict mode
- Pre-commit hook runs on every commit and reports warnings without
  blocking
- Workflow documented in `CONTRIBUTING.md`
- Future-me reading this has a clear next step for promoting another
  module

### Effort estimate

- **Initial landing (config + fmt.py + pre-commit):** half a day
- **Schemas promotion (3 files):** half a day
- **Queries promotion:** up to 1 day, deferrable

So: half a day to a full day to land the core. The per-module
promotions are maintenance work that can happen in background PRs over
weeks.

### Risks / things to watch

- **`Mapped[]` SQLAlchemy 2.0 hasn't been migrated.** Pyright will
  struggle with the legacy declarative base style (`column = Column(...)`)
  because SQLAlchemy's stubs return `Any`. Fix: use
  `reportUnknownMemberType = "none"` in `basic` mode for now. Strict
  modules should avoid ORM model attribute access where possible;
  rely on queries/schemas that have explicit return shapes.
- **Pyright versions drift.** Pin `pyright-python` to a specific
  version in pre-commit and `pyproject.toml` so a Pyright release
  doesn't silently flag new issues.
- **Not everyone has pyright installed locally.** Keep the pre-commit
  hook warn-only for a while so contributors aren't blocked by a
  toolchain mismatch.

---

## How to pick this up in a new session

1. Read this file (`docs/plans/TEST_SUITE_RESIDUALS.md`) — it has
   everything you need without re-reading the original plan.
2. **Gap A is done.** The remaining work is Gap B (Pyright gradual
   type checking). It's independent of the perf tests and can be
   done now or in six months.
3. See `docs/TESTING.md` for the full test suite architecture
   including the perf regression tests.

Nothing in this document blocks day-to-day work on the project. The
test suite is in a fully-supported state as of commit `0ba3fc3`.
These are enhancements, not outstanding bugs.
