# Test Suite Replacement — Plan

## Phase 0 execution log (2026-04-14)

**Status: ✅ complete.**

Changes landed (not yet committed):
- `tests/conftest.py:267`: `FLASK_ENV` → `FLASK_CONFIG`, plus a regression assertion that `app.config['ALLOCATION_USAGE_CACHE_TTL'] == 0` (only TestingConfig sets that to 0, so it's a cheap observable marker for "the right config class was loaded").
- `tests/integration/test_legacy_api_parity.py:238`: same fix + same assertion.
- `src/webapp/config.py` (TestingConfig): added `DEV_ROLE_MAPPING` mirroring the DevelopmentConfig entry. **This was unanticipated.**

**Unanticipated finding — tests reach into dev-only role config:**
The first run after the fix produced 18 failures, all 403 Forbidden on `test_allocations_performance.py` routes using the `auth_client` fixture. Root cause: `src/webapp/run.py:172` loads users via `AuthUser(sam_user, dev_role_mapping=app.config.get('DEV_ROLE_MAPPING'))`. The role mapping lived only on `DevelopmentConfig`; `TestingConfig` had none, so `benkirk` had zero roles, and every permission-gated route failed.

The pragmatic fix for Phase 0 is to mirror `DEV_ROLE_MAPPING` into `TestingConfig` — done. **But this is a test-design smell worth calling out:** tests are depending on a config shortcut that exists for local dev convenience, not on the actual role/permission tables in the SAM database. The proper fix belongs in Phase 4 (webapp test layer): seed role grants in the test DB via factories, delete `DEV_ROLE_MAPPING` from `TestingConfig`. File this as a Phase 4 task.

**Correction to an earlier empirical claim:**
The two `tests/unit/test_oidc_auth.py` warnings about `API_KEYS_*` are **not** symptoms of the FLASK_CONFIG bug. They're emitted by tests inside `TestProductionConfigOIDCValidation` that deliberately invoke `ProductionConfig.validate()` to verify its error paths — the warning is a collateral emission from `validate()` itself. Harmless and expected. My earlier note in the Empirical baseline section claiming otherwise was wrong; leaving the warning as test-side noise to be suppressed in Phase 4 if desired.

**Post-fix measurement:**
- 1386 passed, 53 skipped, 2 xpassed in **142.00s** on 12 cores
- Baseline was 143.41s — the fix is runtime-neutral
- `app.config['ALLOCATION_USAGE_CACHE_TTL'] == 0` assertion passes, confirming TestingConfig is now loaded

**Phase 0 suggests a Phase 4 backlog item:** audit every place tests read from `app.config` for dev-mode conveniences (DEV_ROLE_MAPPING is the known one; there may be others). Any such dependency is a test that passes because of a shortcut, not because the real feature works.

---

## Phase 1 execution log (2026-04-14)

**Status: ✅ complete.**

Changes landed (not yet committed):

- `compose.yaml`: added `mysql-test` service under `profiles: [test]`, host port `3307`, separate `samuel-mysql-test-data` volume, same obfuscated dump mount. Starts only via `docker compose --profile test up -d mysql-test` and never co-runs with the default dev stack.
- `src/webapp/run.py:229-236`: parameterized the Flask dev-server port via `WEBAPP_PORT` env var, default `5050` for backwards compat.
- `utils/run-webui-dbg.sh`: rewritten. New defaults: `PORT=5051`, points at `mysql-test` via `SAM_DB_SERVER=127.0.0.1:3307`, runs `FLASK_CONFIG=development` (keeps Werkzeug debugger), has port-collision check via `lsof` and a `mysqladmin ping` sanity check for the test container. Escape hatch: `USE_DEV_DB=1` to fall back to whatever `.env` configures.
- `pyproject.toml`: added `factory-boy`, `faker`, `pytest-benchmark`, `pyright` to `[project.optional-dependencies].test`.
- **`new_tests/` skeleton created:**
  - `new_tests/README.md` — invocation guide + layout map.
  - `new_tests/pytest.ini` — isolated from root (testpaths = new_tests, no --cov by default, `-n auto`, custom markers: smoke/unit/integration/webapp/perf).
  - `new_tests/conftest.py` — hard safety allowlist (`pytest_configure` aborts with exit code 2 unless `SAM_TEST_DB_URL` points at `(127.0.0.1|localhost, 3307)` or `(mysql-test, 3306)`); session/engine fixtures with transactional rollback isolation.
  - `new_tests/unit/test_smoke.py` — 4 infrastructure smoke tests (engine target, table count, user count, rollback scratch table).

**Plan revision made during execution:**

The original plan said `utils/run-webui-dbg.sh` should set `FLASK_CONFIG=testing`. In practice that loads `TestingConfig` which has `DEBUG=False`, disabling the Werkzeug debugger — which is the whole point of a "debug" launcher. Revised: script uses `FLASK_CONFIG=development` (keeping `DEBUG=True` and the debugger) but overrides `SAM_DB_*` env vars to point at the isolated `mysql-test` container. This preserves the debugging experience AND the production-write safety. Updated `new_tests/README.md` and the critical-files section accordingly.

**Validated end-to-end:**

| Scenario | Exit code | Behavior |
|---|---|---|
| `pytest` with no `SAM_TEST_DB_URL` | `2` | Aborts in `pytest_configure` with "requires SAM_TEST_DB_URL" message |
| `SAM_TEST_DB_URL=...127.0.0.1:3306` (dev DB) | `2` | Aborts with "REFUSING TO RUN" + allowed-targets list |
| `SAM_TEST_DB_URL=...127.0.0.1:3307` (test DB) | `0` | 4 smoke tests pass in **~2.4s** |
| Legacy `pytest` (full suite) | `0` | 1386 passed / 53 skipped / 2 xpassed in **147.85s** (baseline 143.41s — within noise) |

**Test container facts:**

- First build + snapshot restore took ~90s (46s image pull + ~44s xz decompress + SQL apply + ANALYZE TABLE). Subsequent `docker compose --profile test up -d mysql-test` starts from the volume — near-instant.
- Restored `sam` = 104 tables, 27,807 users. `system_status` = 9 tables. Matches dev stack.
- Healthcheck is a known liar during init (uses unix socket inside container) — same TCP-false-positive issue documented in `sam-ci-docker.yaml:107`. The `run-webui-dbg.sh` sanity check and `new_tests/README.md` `until mysqladmin ping` pattern both work around it. If the new_tests CI workflow ever flakes, this is where to look.

**Phase 1 surprise:**

Bumping `pyproject.toml` dev deps triggered a full `make conda-env` / `pip install` chain on the next `source etc/config_env.sh`, because line 35 of that script re-runs `make conda-env` on every source. Not a blocker — one-time cost per dep change. Flagged as a possible optimization (skip re-install if deps unchanged) but out of scope.

**pytest-benchmark noise:**

The new plugin emits 12 `PytestBenchmarkWarning: Benchmarks are automatically disabled because xdist plugin is active` messages on every run (one per worker). That is correct behavior — benchmarks shouldn't run under xdist fan-out. The plan's gating via `-m perf` in a separate serial invocation handles this, but the warnings are visually noisy. Add `filterwarnings = ignore::pytest_benchmark.logger.PytestBenchmarkWarning` to `new_tests/pytest.ini` when we actually start adding perf tests.

---

## Empirical baseline (measured 2026-04-14)

- `source etc/config_env.sh && pytest -v -n auto` on 12 cores: **1386 passed, 53 skipped, 2 xpassed in 143.41s (2:23)**. CLAUDE.md says 380 tests / 32s — stale by ~3.6× in count and ~4.5× in runtime. The suite has grown substantially since the doc was written.
- Per-test wall time: 143s ÷ 1386 ≈ 103ms/test (dominated by MySQL round-trips on a 12-worker xdist fan-out). This is not pathological for a DB-backed suite, but it sets expectations: the full suite will never run in seconds.
- With coverage: extrapolating the 32s→97s historical 3× multiplier, expect **~7 minutes with `--cov`**. Tolerable in CI, too slow for inner dev loop — argues for keeping a `--no-cov` default in dev and `--cov` only in CI.
- Warning surfaced: `tests/unit/test_oidc_auth.py::TestProductionConfigOIDCValidation::test_missing_oidc_vars_raises` (and one sibling) emits `UserWarning: No API_KEYS_* environment variables are set`. This is the TestingConfig bug showing its face — tests are calling `ProductionConfig.validate()` with a half-populated env because the `FLASK_ENV`/`FLASK_CONFIG` mismatch means there's no consistent config context. Not a blocker, but corroborates decision #2.
- 53 skipped tests — worth auditing during migration. Skipped tests rot quickly.

**Scale implications for the migration:**
- "Port test_query_functions.py" is not a morning task. The old suite has 1386 tests across 53 files — porting averages ~25 tests/file, and factory-driven rewrites run ~2× the effort of a copy. Phase 2 and Phase 5 are multi-week slogs.
- Per-file ports (one PR each, as the plan specifies) give real incremental signal. Don't batch.
- The `mysql-test` container needs to hold up under 12 concurrent xdist workers hammering it. That is the dominant runtime cost today, and it will not improve in the new suite — in fact, it sets the **floor** for new_tests runtime.

---

## Context

The current test suite (53 files, **1386 tests**, `tests/unit/`, `tests/integration/`, `tests/api/`) grew incrementally alongside features and now shows unplanned-city symptoms:

- **It runs against whatever `sam` DB the `.env` points at.** In production, this is unacceptable: a misrouted commit could corrupt the real allocations database. There is no hard fence between "test" and "prod".
- **It does not actually use `TestingConfig`.** `tests/conftest.py:267` sets `FLASK_ENV='testing'`, but `src/webapp/config.py:148` reads `FLASK_CONFIG`. Since `FLASK_CONFIG` is unset during `pytest`, the app loads `DevelopmentConfig` — the `app.config['TESTING'] = True` override on line 274 only flips one flag, it does not swap the class. So dev-only code paths (dev API keys at `config.py:74`, hardcoded role map, cache behavior) are active in tests.
- **Test data is hardcoded to live production values** (`benkirk`, `SCSG0001`, `Derecho`). Quantitative assertions drift as the underlying obfuscated dump is refreshed. Magic dates like `2099`/`2100-11-26` appear in `tests/mock_data/status_mock_data.json` because there is no authoritative fixture strategy.
- **No type checking, no query-count regression tests, no benchmarks** — but the project just went through a major ORM perf push (`caf08f2`, `f3eb4cb`, `6c54943`, `5178345`, `e89eceb`) with profiling scripts living in `utils/profiling/` that the test suite cannot see.
- **`utils/run-webui-dbg.sh` collides with `docker compose up webdev`** on host port `5050`, so Claude cannot launch the webapp for interactive debugging while the user has the stack running.

**Intended outcome:** a new, planned test suite under `new_tests/` that we port to incrementally. It runs against a **dedicated, isolated test database**, loads the correct `TestingConfig`, centralizes fixture data, adds query-count & latency regression guards, supports type checking, and lets Claude launch the webapp on an alt port pointed at the test DB without touching the user's running stack. Old `tests/` stays green until each area is ported; then it is deleted wholesale.

---

## Design Decisions

### 1. Test database: dedicated MySQL container on an alternate port

**Decision:** add a `mysql-test` service to `compose.yaml` with its own volume, host port `3307`, and the same obfuscated dump mount. It lives under a compose profile (`--profile test`) so `docker compose up` without the profile does not start it.

**Why not SQLite in-memory:** SAM uses MySQL-specific features that SQLite does not reproduce faithfully — `BIT(1)`, `TINYINT(1)` boolean mappings, `TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE`, `MEDIUMINT`, the XRAS read-only views, and bcrypt behavior via `api_credentials`. SQLite tests would be green while prod is broken. The whole point is to validate real schema.

**Why not a named DB in the same MySQL:** cheaper, but does not solve the safety problem. A misrouted `.env` can still point the "test" connection at prod.

**Hard safety fence** — in `new_tests/conftest.py`, before any fixture runs:

```python
TEST_DB_ALLOWLIST = {('127.0.0.1', 3307), ('localhost', 3307), ('mysql-test', 3306)}
def _verify_test_db(engine):
    host, port = engine.url.host, engine.url.port
    if (host, port) not in TEST_DB_ALLOWLIST:
        pytest.exit(f"REFUSING TO RUN: test DB is {host}:{port}, not in allowlist. "
                    f"Check .env or start the test container with "
                    f"`docker compose --profile test up mysql-test`.", returncode=2)
```

This runs in `pytest_configure`. If anyone accidentally runs `pytest` against prod, it aborts before a single query executes.

**xdist isolation strategy (revised after empirical measurement):** the initial instinct was per-worker DB cloning (`sam_test_gw0`, `sam_test_gw1`, …). With 1386 tests on 12 workers and a ~200MB dump, a full clone per worker would add 5–10 minutes of session startup — destroying the 143s budget. **Reject per-worker cloning for the SAM schema.** Instead:

1. **One shared `sam_test` database** in the test container, restored once per session from the snapshot. Read-only for the vast majority of tests.
2. **Transactional rollback isolation** (the pattern the old `tests/conftest.py` already uses): each `session` fixture opens a transaction + `begin_nested()` savepoint, rolls back at test exit. Writers see their own data but nothing escapes the test. This is why the current suite runs in 143s rather than hours.
3. **Per-worker isolation only where DDL needs it** — the existing `system_status_test_gw<N>` per-worker pattern (for the smaller `system_status` DB) stays as-is. Apply the same trick elsewhere only if a specific test needs DDL-level isolation.
4. **Migration of `session_commit`-dependent tests** (`test_manage_summaries.py`, `test_transaction_context.py` et al.) is a known cost of Phase 5. Most should migrate to factory + rollback; a handful legitimately test commit semantics and need a separate fixture that commits to a worker-scoped schema (falling back to the cloned-per-worker pattern for those tests only).

This keeps session setup to ~1 restore (cold: 30–60s) and per-test overhead near zero. Total new-suite runtime target is **≤ 143s parallel / ≤ 7 min with coverage** — matching the current baseline, not beating it. Beating it is out of scope.

### 2. Fix `TestingConfig` selection

The one-line bug in `tests/conftest.py:267`: change `FLASK_ENV` → `FLASK_CONFIG`. Verify by asserting `app.config.__class__.__name__ == 'TestingConfig'` in an `app` fixture. Fix this in the **old** suite immediately (separate small commit) — it is a real latent bug, not migration work.

### 3. Fixture data: pinned snapshot + factories

Two layers, because the tension between "real-world representativeness" and "quantitative stability" cannot be resolved with one layer:

1. **Pinned obfuscated snapshot.** The current dump at `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz` becomes a versioned test asset: hash it, record the hash in `new_tests/fixtures/snapshot.lock`, and have `conftest.py` refuse to run if the loaded DB does not match the hash. When we intentionally refresh the snapshot, we bump the lock, regenerate expected values, and commit them together. This directly addresses the "values shift" pain — shifts become explicit events, not silent drift.
2. **Factories for synthetic data.** `factory_boy` + `faker`, with one factory per ORM model under `new_tests/factories/`. Tests that need to assert exact counts or verify edge cases (expired allocations, specific charge totals, inactive users) build their own data inside a transaction that rolls back at test exit. No reliance on `benkirk` or `SCSG0001` existing.

Integration tests (legacy parity, dashboard rendering) keep using the pinned snapshot. Unit tests for `sam.queries`, `sam.manage`, and schemas use factories. This is the split that makes the suite both trustworthy and stable.

**Snapshot refresh workflow** (the user expects to do this periodically): regenerating `sam-obfuscated.sql.xz` is a first-class, well-supported event, not an accident:

1. New dump is committed (or replaces the LFS pointer).
2. Run `make test-snapshot-rehash` — computes the new SHA256, writes it to `new_tests/fixtures/snapshot.lock`, and runs `pytest -c new_tests/pytest.ini -m integration --snapshot-refresh`. The `--snapshot-refresh` flag tells affected integration tests to emit a diff instead of failing, so any structural assertions that shifted (new user count, allocation totals) are surfaced as one batch.
3. Developer reviews the diff, updates any expected values that legitimately changed, commits both `snapshot.lock` + test updates + the new dump in a single "refresh snapshot YYYY-MM-DD" commit.
4. Unit tests (factory-driven) are unaffected by snapshot refreshes — that is the whole point of the split.

This makes the refresh a ~15-minute chore instead of the current week-of-mystery-failures.

### 4. Webapp debugging from Claude

Replace `utils/run-webui-dbg.sh` with a parameterized script that:
- Defaults `PORT=5051` (not `5050`)
- Defaults `SAM_DB_SERVER=127.0.0.1`, `SAM_DB_PORT=3307` (the test container)
- Sets `FLASK_CONFIG=testing`
- Exits with a clear error if port `5051` is already bound

Remove the hardcoded `port=5050` at `src/webapp/run.py:231` — read from `WEBAPP_PORT` env var, default 5050. This lets `docker compose up webdev` keep its 5050, lets production keep its 7050→5050 mapping, and gives Claude an isolated 5051 against the test DB.

**Webapp feature tests that currently require manual QA** — add a new `new_tests/webapp/` tier using Flask's test client plus `pytest-flask` against the dedicated test container. Auth bypass via a new `TESTING_AUTO_LOGIN_USER` in `TestingConfig` (NOT the `DEV_*` variant, which stays dev-only). Render-level assertions use `pyquery` or `beautifulsoup4` so we can check that "this project card shows the expected allocations, this nav link exists, this badge is green" — the things you currently click through manually.

### 5. Performance & query-count regression tests

Add `new_tests/perf/` with two kinds of guard:

- **Query count.** Use the existing `sqlalchemy.event.listen('before_cursor_execute')` pattern already in `utils/profiling/profile_user_dashboard.py`. Wrap it in a `count_queries` context manager fixture. Tests like: `with count_queries() as c: dashboard.get_user_dashboard_data(session, user); assert c.total <= 25`. Baselines live in `new_tests/perf/baselines.json`, committed. Regressions fail the test; improvements require a baseline bump in the same commit as the code change.
- **Latency.** `pytest-benchmark` plugin. Lower bar — a few smoke benchmarks for the hot paths (`get_user_dashboard_data`, `Project.get_detailed_allocation_usage`, charge summary aggregation). Not in the default `pytest` run; gated behind `pytest -m perf` or a dedicated `make perf` target so normal dev loops keep the current ~143s runtime rather than adding benchmark overhead on every run.

The recent ORM commits (joinedload collapsing, charge batching, `DashboardResource` TypedDict) are the exact kind of work that should have landed with a query-count regression test. Going forward, this becomes the pattern.

### 6. Type checking: Pyright, gradual

**Verdict: yes, value added, but only if gradual.** Trying to type the whole `src/` at once drowns the signal in noise.

- Add `pyrightconfig.json` at repo root with `strictMode` off by default and a per-module allowlist of `strict` directories.
- Start strict on: `src/sam/schemas/`, `src/sam/queries/`, `src/sam/fmt.py`. These have the cleanest boundaries and the highest leverage (schemas are already declarative; query functions have explicit types already in `rolling_usage.py`).
- Leave `src/webapp/dashboards/` and `src/cli/` on basic mode until the test migration is done. Route handlers can adopt types later.
- Add `pyright` to pre-commit as a warn-only check initially, escalate to blocking on a per-module basis as each module is cleaned up.
- No `mypy` — one type checker is enough, and Pyright is faster and has better SQLAlchemy 2.0 `Mapped[]` support. But `Mapped[]` migration on ORM models is a separate project — do **not** fold it into this one.

### 7. Incremental migration via `new_tests/`

The user's proposal is right. Specifics:

- `new_tests/` gets its own `conftest.py`, own fixtures, own pytest config (a second `[tool.pytest.ini_options]` section is not valid — use a `new_tests/pytest.ini` and invoke as `pytest -c new_tests/pytest.ini`).
- Both suites run in CI in parallel: `make test` runs both, `make test-legacy` runs only old, `make test-new` runs only new.
- Coverage is combined across both (pytest-cov supports this via `--cov-append`).
- Porting order: highest-value-lowest-risk first. Recommended sequence:
  1. `tests/unit/test_schema_validation.py` + `tests/integration/test_views.py` — pure schema checks, no mocking, trivial port. Also validates the `mysql-test` container + allowlist guard end-to-end before any real porting begins.
  2. `tests/unit/test_query_functions.py` — coverage booster. Port to factories.
  3. `tests/api/` (14 files) — Flask client tests, ported to proper `TestingConfig`. Biggest validation of decision #2 — the OIDC-validation warnings surfaced in the empirical run should disappear under real TestingConfig.
  4. `tests/unit/test_allocations_performance.py` (937 lines) — becomes the first `new_tests/perf/` citizen. Baseline numbers come from this port.
  5. `tests/unit/test_crud_operations.py`, `test_manage_summaries.py`, `test_renew_extend.py` — write-path tests. These are the ones that currently rely on `session_commit` and the rollback-fixture fallback; they need the most care. Factory-driven rewrites are ~2× the effort of copies.
  6. Everything else (legacy parity, dashboard, status flow, CLI integration, ~20+ more files).
- Each port is a separate PR. Old file is deleted in the same PR as the new one lands. No long-lived duplication.
- **Audit the 53 skipped tests during Phase 2.** Skipped tests rot; some may have been skipped for reasons that no longer apply, others may be protecting against bugs that were fixed. Each one gets a decision: port, delete, or xfail-with-reason.

### 8. CI workflow cleanup

Four `.github/workflows/*.yaml` files currently run pytest. Only two should continue to:

| Workflow | Current test step | Recommended action |
|---|---|---|
| `sam-ci-docker.yaml` | `docker compose exec webapp pytest tests/ --cov --cov-fail-under=60` (primary CI, coverage) | **Keep and evolve.** Canonical test job. Evolve to run `pytest tests/ new_tests/` with combined coverage during migration. At Phase 6, runs only `new_tests/`. Also needs `docker compose --profile test up -d mysql-test` before the pytest step once the dedicated test container lands. |
| `ci-staging.yaml` | `docker compose exec webapp pytest tests/ -n auto --no-cov` | **Keep and evolve.** Staging merge gate. Same migration path. |
| `test-install.yaml` | After running `install.sh`: `docker compose exec webapp pytest tests/ -n auto` (line 179) | **Drop the pytest step.** The job of this workflow is to verify `install.sh` produces a working install, not to re-run the test suite. Replace with a smoke check: `docker compose exec webapp sam-search user csgteam` plus a `curl -f http://localhost:7050/api/v1/health` call. Keeps the workflow focused, removes duplication with `sam-ci-docker.yaml`. |
| `sam-ci-conda_make.yaml` | After `make docker-up`: `pytest -v -n auto` **from the host conda env** (lines 87–90) | **Drop the pytest step.** The only workflow that runs pytest *outside* the webapp container — against a Python environment nobody ships. Duplicates `sam-ci-docker.yaml`'s coverage. Keep the `sam-search` / `sam-status` CLI smoke steps already there (lines 76–86); those are the meaningful proof that the conda/pip install path produces a working env. That is the job this workflow should do. |

**Non-test workflows** (not touched): `mega-linter.yaml`, `ci-staging.yaml`'s lint/secret-scan/terraform jobs, `manually-clean-action-log.yaml`, `cron-clean-action-log.yaml`, `clean-ghcr.yaml`, `build-images-cirrus-deploy.yaml`, `deploy-staging.yaml`.

**Phase gating** for these edits:
1. **Immediate, independent commit** (does not block the migration): drop pytest from `test-install.yaml` and `sam-ci-conda_make.yaml`, replace with smoke checks. This is a "right tool for the job" cleanup.
2. **Per-phase**: update `sam-ci-docker.yaml` and `ci-staging.yaml` to start the `mysql-test` compose service and run both suites in parallel during the migration, then collapse to `new_tests/` only at Phase 6.

---

## Critical Files

**Must modify (old suite, immediate fixes):**
- `tests/conftest.py:267` — `FLASK_ENV` → `FLASK_CONFIG` (one-line bug fix, separate commit)
- `src/webapp/run.py:231` — hardcoded `port=5050` → `os.getenv('WEBAPP_PORT', 5050)`
- `.github/workflows/test-install.yaml:179` — drop pytest step, replace with CLI smoke + health curl
- `.github/workflows/sam-ci-conda_make.yaml:87-90` — drop `pytest -v -n auto` step, keep CLI smoke tests already present

**Must create:**
- `compose.yaml` — add `mysql-test` service under `profiles: [test]`, port `3307:3306`, same backup mount, separate volume
- `new_tests/conftest.py` — DB allowlist guard, snapshot hash check, `TestingConfig` app factory, factory_boy session fixture
- `new_tests/pytest.ini` — parallel (`-n auto`), coverage (`--cov=src --cov-append`), markers (`perf`, `webapp`, `integration`)
- `new_tests/factories/` — `factory_boy` factories for User, Project, Account, Allocation, Resource
- `new_tests/fixtures/snapshot.lock` — pinned SHA256 of `sam-obfuscated.sql.xz`
- `new_tests/perf/_query_count.py` — reusable `count_queries` context manager (pull from `utils/profiling/profile_user_dashboard.py`)
- `new_tests/perf/baselines.json` — committed query-count and latency baselines
- `utils/run-webui-dbg.sh` — rewrite: parameterized port (default 5051), test DB, `FLASK_CONFIG=testing`, port-collision check
- `pyrightconfig.json` — strict on `src/sam/schemas/`, `src/sam/queries/`, `src/sam/fmt.py`; basic elsewhere

**Must reuse (do not rewrite):**
- `utils/profiling/profile_user_dashboard.py` — the `event.listen` / `_SQLStats` pattern is already correct; extract it into `new_tests/perf/_query_count.py` verbatim
- `tests/fixtures/test_config.py` — the `create_test_engine` + session factory scaffolding is fine; port forward, just point it at the new test container
- `src/webapp/config.py:122` `TestingConfig` — already exists, just needs to actually be loaded (see decision #2)

**Must not touch in this plan:**
- ORM `Mapped[]` migration — separate project
- Production `compose.yaml` services — `webapp`, `webdev`, `mysql` untouched, only additive
- Existing `tests/` — stays green until its last file is ported out

---

## Verification

**Per-PR (each port):**
1. `pytest tests/ --no-cov` — legacy suite still green.
2. `pytest -c new_tests/pytest.ini --no-cov` — new suite green.
3. `docker compose --profile test up mysql-test -d && pytest -c new_tests/pytest.ini` — full run against dedicated container.
4. Deliberately misconfigure `.env` to point `SAM_DB_SERVER` at prod, re-run — the allowlist guard must abort with exit code 2.

**After decision #2 fix (TestingConfig bug):**
- Add a test: `assert type(app.config).__name__ == 'TestingConfig'`. This must pass in `new_tests/` and we should also add it to the old suite as a regression guard.

**After decision #4 fix (port parameterization):**
- Start `docker compose up webdev` (binds 5050) and simultaneously run `utils/run-webui-dbg.sh` — the dbg script binds 5051, both serve, no collision. Claude can hit `http://localhost:5051` to exercise webapp features.
- Hit a dashboard route on 5051; confirm via logs that it is talking to `mysql-test` on 3307, not the main `mysql` container on 3306.

**After decision #5 lands (perf guards):**
- Baseline a known-good `get_user_dashboard_data` call for `bdobbins` at current `main` — commit baseline.
- Intentionally introduce an N+1 regression (drop a `joinedload`), re-run — perf test must fail with a clear diff message.

**End-of-migration (when `tests/` is empty):**
- Combined coverage ≥ 77.47% (current baseline per CLAUDE.md; re-measure at Phase 0 to get the real current number) — no regressions.
- `pytest -c new_tests/pytest.ini -n auto` runtime **≤ 150s** (empirical legacy baseline is 143s on 12 cores; the budget allows ~5% headroom for the allowlist guard + snapshot hash check + slightly heavier fixture setup).
- `pytest --cov` runtime **≤ 8 min** (projected legacy is ~7 min; same 5% headroom).
- `pyright` clean on the strict allowlist.
- Zero pytest warnings about `API_KEYS_*` or `ProductionConfig.validate()` — the OIDC-warning symptom is fully resolved.
- Delete `tests/`, delete legacy `conftest.py`, rename `new_tests/` → `tests/`.
- Update CLAUDE.md test-count and runtime numbers so the doc stops lying to future Claude.
