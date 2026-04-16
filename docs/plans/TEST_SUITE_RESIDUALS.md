# Test Suite — Residual Work

**Status:** Migration and CI wiring are complete. The test refactor
described in `~/.claude/plans/composed-doodling-stearns.md` has landed
in full, except for two optional workstreams — query-count/latency
regression tests (§5) and gradual Pyright type checking (§6). This
document exists so those two can be picked up in a fresh session
without re-reading the full migration plan.

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
- **`TESTING_AUTO_LOGIN_USER` auth bypass config** — not needed. `TestingConfig.DEV_ROLE_MAPPING` + Flask-Login session cookies gave us the `auth_client` fixture without the bypass.
- **Legacy API parity tests** — dropped (28 tests). Required `SAM_LEGACY_USER/PASS` + `PROD_SAM_DB_*` env vars to cross-check against the live `sam.ucar.edu` legacy API. Never runnable in CI; skipped in practice.
- **Most of `test_sam_search_cli.py` subprocess coverage (44 of 61 tests)** — redundant with the ported CliRunner version (`tests/unit/test_sam_search_cli.py` + `test_sam_search_cli_allocations.py`). One subprocess smoke (`tests/integration/test_cli_smoke.py`) retains entry-point verification.
- **ORM `Mapped[]` migration** — out of scope per the original plan; stays out of scope.

---

## Remaining gap A — Performance & query-count regression tests

**Source:** original plan §5 (Performance & query-count regression tests).

**Motivation:** the recent ORM perf push (commits `caf08f2`, `f3eb4cb`,
`6c54943`, `5178345`, `e89eceb`) added joinedload collapsing, charge
batching, and `DashboardResource` TypedDict optimizations. None of that
work landed with regression tests. Future-me reintroducing an N+1
pattern should see it fail in CI, not surface in user reports.

### What's already in place

- `pytest-benchmark` is already in `[project.optional-dependencies].test`
  (landed in Phase 1).
- `utils/profiling/` still exists with the `sqlalchemy.event.listen
  ('before_cursor_execute')` + `_SQLStats` pattern that `profile_user_dashboard.py`
  uses interactively. That code is production-grade; extract don't rewrite.
- `tests/unit/test_allocations_performance.py` (Phase 5) tests **cache
  behavior** (Flask-Caching NullCache, matplotlib `lru_cache`, `TTLCache`
  purge/hit/miss), not query counts. Adjacent concern, complementary.
- Root `pytest.ini` already has `perf` in its `markers =` section as a
  placeholder. A `-m perf` gate is the intended way to run these (not
  in the default suite, to keep the ~68s inner-loop runtime intact).

### Scope

**1. Query-count infrastructure** (~2 hours)

- Create `tests/perf/__init__.py` + `tests/perf/_query_count.py`.
- Extract the `_SQLStats` class from `utils/profiling/profile_user_dashboard.py`
  verbatim. Wrap it in a context manager + fixture:

  ```python
  @pytest.fixture
  def count_queries():
      @contextmanager
      def _counter(engine):
          stats = _SQLStats()
          stats.attach(engine)
          try:
              yield stats
          finally:
              stats.detach(engine)
      return _counter
  ```

- Usage pattern in tests:

  ```python
  def test_user_dashboard_query_count(session, count_queries, benkirk_user):
      with count_queries(session.bind) as c:
          data = get_user_dashboard_data(session, benkirk_user)
      assert c.total <= 25, f"query count regression: {c.total} > 25 baseline"
  ```

**2. Baseline infrastructure** (~1 hour)

- Create `tests/perf/baselines.json` as `{"function_name": {"queries": N, "notes": "..."}}`.
- Add a small helper in `tests/perf/conftest.py` that reads `baselines.json`
  and exposes expected counts via a parametrized fixture. Rationale for
  JSON vs inline constants: when we deliberately improve a query pattern
  (e.g. add joinedload collapsing), the improvement fails the test until
  we update the baseline. The update lands in the same PR as the code
  change, with the `notes` field recording why the count dropped.
- Document the "re-baseline" workflow in the file's docstring: run the
  test, copy the new count into `baselines.json`, commit both.

**3. Initial baseline set** (~2 hours)

Target functions + likely baseline query counts (to be measured):

| Function / Route | Reason to baseline |
|---|---|
| `sam.queries.dashboard.get_user_dashboard_data(session, user)` | Primary target of the ORM perf push. Has `utils/profiling/profile_user_dashboard.py` as its profiling reference. |
| `sam.queries.dashboard.get_project_dashboard_data(session, project)` | Same code path, project-scoped. |
| `sam.queries.dashboard.get_resource_detail_data(session, resource)` | Adjacent query that exercises joinedload collapsing. |
| `sam.queries.fstree.get_fstree_data(session, 'Derecho')` | Most expensive single function in the suite (~5–15s when uncached). Even one query count regression is a big deal. |
| `sam.queries.allocations.get_allocation_summary(session, resource_name='Derecho', projcode='TOTAL')` | Heavy aggregation path. |
| `sam.queries.allocations.get_allocation_summary_with_usage(...)` | Same plus charge join. |
| `Project.get_detailed_allocation_usage()` | Method on a hot ORM model, called from templates. |
| `sam.queries.rolling_usage.get_project_rolling_usage(session, project)` | Used by threshold tests. |

Expected effort: half a day. Each baseline is one test: one `with` block,
one assert, one entry in `baselines.json`.

**4. Latency smoke via `pytest-benchmark`** (~1 hour)

- Create `tests/perf/test_dashboard_latency.py` with ~3 smoke benchmarks
  for the above functions, using the `benchmark` fixture that
  `pytest-benchmark` provides.
- These are smoke-only: the assertions are "this ran in under N ms,
  loosely", not "this ran in exactly N ms". Absolute timings are
  machine-dependent, so the primary value is catching order-of-magnitude
  regressions (the 50ms thing that suddenly takes 2000ms).
- Note: `pytest-benchmark` emits `Benchmarks are automatically disabled
  because xdist plugin is active` warnings when `-n auto` is in effect.
  The fix is `filterwarnings = ignore::pytest_benchmark.logger.PytestBenchmarkWarning`
  in `pytest.ini` — we filed that in the Phase 1 execution log but
  haven't added it yet because we have no perf tests yet.

**5. Gating** (~30 minutes)

- Perf tests are marked `@pytest.mark.perf` (or module-level
  `pytestmark = pytest.mark.perf`).
- Default `pytest` run excludes them via `-m "not perf"` in root
  `pytest.ini` `addopts`.
- Running perf tests uses `pytest -m perf` (overrides default) and
  forces serial execution (`-n 0` or `@pytest.mark.xdist_group('perf')`)
  since `pytest-benchmark` is disabled under xdist fan-out.
- Add a `perf` make target to `Makefile`:

  ```makefile
  perf: ## Run perf regression + benchmark suite (serial)
  	$(config_env) && source etc/config_env.sh && \
  	    SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
  	    pytest -m perf -n 0
  ```

- CI: add a new `pytest-perf` job in `sam-ci-docker.yaml` that runs
  after the main `pytest` job (dependency via `needs:`), only on push
  to `main` (not on PRs, since baselines drift is per-PR friction).
  Decide case-by-case whether a perf regression blocks merge or just
  emits a warning.

### Acceptance criteria

- `make perf` runs locally and produces baseline numbers for the 8 target
  functions
- `tests/perf/baselines.json` committed with the initial counts
- Intentionally drop a `joinedload` in `sam/queries/dashboard.py`,
  re-run `make perf`, confirm the test fails with a clear diff
  (count went from `N` to `M`, baseline is `N`, update `baselines.json`
  or fix the regression)
- `pytest` (default) runtime is unchanged (~68s), confirming the
  `-m "not perf"` gate works
- CI `pytest-perf` job lands and reports a baseline for main branch

### Effort estimate

**Half a day to one day**, single session. Low risk — everything is
additive, no changes to existing test code, no changes to source code.
The `_SQLStats` pattern already works in `utils/profiling/`.

### Potential extensions (NOT in scope for the residual plan)

- Per-view query counts via Flask request middleware (capture
  `request.endpoint` + query count in a metric table). This is a
  production feature, not a test feature.
- Query plan regression tests (`EXPLAIN` diffs). Out of scope —
  introduces a whole new dimension of flakiness because MySQL
  optimizer decisions change with statistics.

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
2. Decide which gap to tackle first. My recommendation: **gap A
   (perf tests) first**. It's more contained, delivers immediate
   regression-catching value, and doesn't touch source code.
3. If gap A lands cleanly, gap B becomes the next natural chunk.
4. Both are independent and can be done in either order, or
   interleaved, or one now and one in six months.

Nothing in this document blocks day-to-day work on the project. The
test suite is in a fully-supported state as of commit `15a38d6`.
These are enhancements, not outstanding bugs.
