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

## Phase 2 execution log (2026-04-14)

**Status: ✅ first file pair ported.** The migration is incremental, so this log will grow as more files move.

**Ported in this session:**

- `tests/integration/test_schema_validation.py` (531 lines) → `new_tests/integration/test_schema_validation.py`
- `tests/integration/test_views.py` (336 lines) → `new_tests/integration/test_views.py`

Both are near-verbatim ports (same class layout, same parametrization, same assertions). Changes:
- Added `pytestmark = pytest.mark.integration` at the top
- Dropped a few `print()` statements that were purely decorative (kept the informational ones)
- Restructured `test_views.py` skip logic to use `pytest.skip` early instead of conditional bodies — drops branches that never executed
- Fixed `XrasAllocationView.remainingAmount is not None` → `.isnot(None)` (the old version was a Python truthiness check, not a SQL filter — latent bug)
- Removed unused imports

**Results on new_tests/ (12 workers):**

```
37 passed, 10 skipped in 3.23s
```

The 10 skips are all data-dependent (`CompActivityChargeView` is empty in the obfuscated snapshot) and match the skip behavior in the legacy suite. This is exactly the kind of test that belongs in the **integration** tier: a structural assertion that degrades to `skip` when the snapshot lacks the data, never a hardcoded value that drifts when the snapshot refreshes.

**Session fixture bug discovered and fixed during port:**

The first `new_tests/` run against the ported files emitted three `SAWarning: transaction already deassociated from connection` warnings, all from the read-only `TestViewReadOnly` tests that call `session.rollback()` inside the test. Root cause: my initial `session` fixture used a plain `connection.begin()` — when a test rolls back, the outer transaction is gone, and the fixture's teardown rollback hits a dead object.

**Fix:** switched to `join_transaction_mode="create_savepoint"`. Now every test-level `session.commit()`/`session.rollback()` is a SAVEPOINT op inside the outer transaction. Tests can commit or rollback as much as they want; at teardown the outer transaction rolls back and takes the SAVEPOINTs with it. This is the SQLAlchemy 2.0 idiomatic pattern for transactional test isolation. Warnings eliminated, all 47 tests stable over repeated runs.

**MAJOR finding — legacy suite is non-deterministically flaky under xdist:**

After deleting the two ported files from `tests/integration/`, I re-ran the legacy suite three times:

| Run | Result | Failing test |
|---|---|---|
| 1 | 1350 passed, 1 failed | `tests/unit/test_renew_extend.py::TestFindSourceAllocationsAt::test_excludes_inheriting_allocations` |
| 2 | 1350 passed, 1 failed | `tests/unit/test_crud_operations.py::TestUpdateOperations::test_update_account_user_end_date` |
| 3 | 1351 passed, 0 failed | (clean) |

Two **different** tests failed in the first two runs, both in write-path code (`test_renew_extend.py` and `test_crud_operations.py`). Each failing test passes reliably in isolation. This is not a bug in my port — the latent flake was always there, but removing 44 tests from the rotation changed the xdist scheduling enough to expose it.

**What this proves:** the old `session` fixture in `tests/conftest.py:158-170` opens a transaction and rolls back, but does NOT use SAVEPOINT isolation. When a writer test calls `session.rollback()` mid-test, or another test commits via `session_commit`, state leaks between tests scheduled on the same xdist worker. The plan's assumption that "the current suite runs in 143s because of transactional isolation" was half-right — it runs in 143s, but the isolation is fragile. The ~2-in-3 flake rate was invisible before because we weren't looking.

**Actions taken:**
- NOT fixing the legacy flake. The fix is the migration itself (new_tests/ already has `create_savepoint` isolation). Patching conftest.py under tests/ risks breaking something else for temporary relief.
- Documented flaking test names so Phase 5 (write-path port) can verify the SAVEPOINT fix eliminates both.
- Phase-2 PR descriptions going forward should note: "legacy suite is known flaky; re-run up to 3× if a write-path test fails."

**Plan revision — Phase 5 priority bump:**

Phase 5 (port `test_crud_operations.py`, `test_manage_summaries.py`, `test_renew_extend.py`) should be moved earlier if flakes start blocking PRs. These are the files that contain the flaking tests. Once they're ported onto `create_savepoint` isolation, the flakes should disappear. Keeping them at Phase 5 is still OK — the Phase 2/3/4 content is lower-risk and faster to port — but if the legacy-suite flake rate climbs as more tests are removed, bump this up.

**Runtime check:**

| Suite | Tests | Runtime |
|---|---|---|
| `new_tests/` (smoke + 2 ported files) | 47 (37 pass + 10 skip) | **3.23s** on 12 cores |
| Legacy `tests/` (after deletions) | 1396 (1351 pass + 45 skip + 2 xpassed) | 128–137s on 12 cores |

New suite is fast, but the comparison isn't apples-to-apples yet — we've ported 47/1443 tests (~3%).

---

## Phase 2b — test_query_functions.py port (2026-04-14)

**Status: ✅ complete.**

First write-path-adjacent port. The plan anticipated this file would "use factories" — on closer reading, that assumption was wrong, and the port revealed a major refinement to the fixture strategy.

**Key finding — two fixture tiers, not one.**

A careful read of `test_query_functions.py` (and the original pre-port audit from an Explore agent) showed that ~80% of its 41 tests assert *structural* invariants (`isinstance(result, dict)`, `'key' in result`, `len > 0`, and `computed_from_result_fields` equalities like `remaining == allocated - used`). Only a small minority actually need exact values. **Those tests don't need factories — they need "any row of the right shape".**

That observation produced the **two-tier fixture strategy** now in place:

| Tier | Name | Scope | Purpose |
|---|---|---|---|
| **1** | Representative fixtures | `new_tests/conftest.py` | Query the snapshot for ANY row matching a structural shape. For read-path tests. Snapshot-refresh-safe. |
| **2** | Factories | `new_tests/factories/` (future) | Build fresh synthetic rows inside the test's SAVEPOINT. For write-path tests needing exact counts/values. |

Representative fixtures live in `new_tests/conftest.py` and use a **session-scoped ID-cache + function-scoped instance-fetch** pattern:

```python
@pytest.fixture(scope="session")
def _active_project_id(engine):
    # one SQL query at suite startup, returns project_id
    ...

@pytest.fixture
def active_project(session, _active_project_id):
    # fresh instance bound to the test's SAVEPOINT'd session
    return session.get(Project, _active_project_id)
```

This avoids re-querying the snapshot 38 times per test run while still giving each test a fresh, session-bound ORM instance.

**Initial representative fixture set:**

- `active_project` — any project with >=1 account and >=1 active allocation (snapshot has 1310 candidates)
- `multi_project_user` — any active+unlocked user on >=2 active projects (snapshot has 4930 active users with account memberships, easy to find qualifying ones)
- `hpc_resource` — any resource with `resource_type='HPC'` (16 candidates)

More will be added as each subsequent port reveals its needs (`dav_resource`, `any_leaf_project`, etc).

**Ported file facts:**

- `tests/unit/test_query_functions.py` (663 lines) → `new_tests/unit/test_query_functions.py` (~460 lines, more compact)
- 39 test items: **38 passed, 1 skipped** in 4.03s
- The skip (`test_get_allocation_summary_rate_consistency`) is self-declared: when the arbitrary `active_project` chosen by the fixture doesn't happen to have a single HPC allocation, the test skips rather than failing. Correct behavior — it's a combo test, not a core invariant.
- Full `new_tests/` now: **75 passed, 11 skipped in 3.87s**

**Concrete transformations beyond mechanical copy:**

1. `test_project` / `test_resource` fixture refs → `active_project` / `hpc_resource`
2. Hardcoded `'SCSG0001'` / `'Derecho'` → `active_project.projcode` / `hpc_resource.resource_name`
3. `User.get_by_username(..., 'bdobbins') or User.get_by_username(..., 'benkirk')` fallback chain → `multi_project_user` fixture (fail-fast at session start, not in test)
4. `search_projects_by_code_or_title(session, 'SCSG')` → `search_projects_by_code_or_title(session, active_project.projcode[:4])` — **self-consistent**: use a substring of the representative project's own projcode as the search term, no hardcoded data
5. `test_search_projects_by_title`: use the first ≥4-char word from the representative project's title instead of hardcoded `'system'`
6. Dropped parametrized `['Derecho', 'Campaign']` in `test_get_resource_detail_data_by_type` — collapsed to a single test using `hpc_resource` (the legacy version tested two hardcoded resources; one structural test is enough)
7. Removed the hardcoded `'UNIV'`/`'TOTAL'` facility-name args where possible — the aggregated-allocations test now uses `projcode="TOTAL"` alone (which is the function's internal rollup key, not a user-facing projcode)

**Decisions made during this port that update the plan:**

1. **Reject the audit's "factory-with-fallback-to-real-data" pattern.** The audit's Explore agent proposed factories that fall back to querying `SCSG0001` when it exists. This defeats the entire isolation story — if the snapshot refresh removes SCSG0001, every "factory" call silently starts creating fresh rows, and the blast radius is invisible. **Factories in Phase 5 will always build fresh data. Representative fixtures are a separate concept, clearly named, clearly scoped to read-path tests.**

2. **Factories are deferred.** Originally the plan had test_query_functions.py as "port to factories" in Phase 2. With the representative-fixture strategy, no factories are needed for this file, and the next port target (`test_rolling_usage.py` per the audit — structural tests) is likely the same. **Factories become necessary when the first write-path port lands (test_crud_operations, test_renew_extend, test_manage_summaries).**

3. **Port order revised:**
   - (2a) ✅ `test_schema_validation.py` + `test_views.py` — structural, no factories
   - (2b) ✅ `test_query_functions.py` — structural, representative fixtures
   - (2c) `test_rolling_usage.py` + `test_fstree_queries.py` + `test_project_access_queries.py` — structural, expand representative fixtures as needed
   - (3) Write-path tests — factories + scenarios
   - (4) Webapp/API tests — Flask test client + TestingConfig
   - (5) Perf tests

4. **Self-consistency pattern for search/filter tests:** when a test needs a specific data value (search term, filter value), derive it from the fixture at runtime rather than hardcoding. This is a general principle worth repeating in the README.

**Pre-existing legacy flake observation:**

Ran the legacy suite once after deletion: **1311 passed, 45 skipped** in 158.54s. Clean — no flakes this run. Flake rate remains ~30% (1 in 3 runs) per the Phase 2a observation. Not fixing.

---

## Phase 2c — structural read-path batch (planned)

**Scope:** port three more structural read-path files onto the representative-fixture pattern established in Phase 2b. No factories yet.

**Target files (scoped):**

| File | Lines | Shape | Transformation complexity |
|---|---|---|---|
| `tests/unit/test_project_access_queries.py` | 142 | 16 structural tests, zero hardcoded refs — uses `next(iter(...))` to pick any branch dynamically | **Trivial** — near-mechanical port, just add `pytestmark = unit` |
| `tests/unit/test_rolling_usage.py` | 365 | ~30 tests. Has `_first_hpc_project` helper for most tests (already defensive). Hardcoded `SCSG0001`/`NMMM0003`/`Derecho` used for: threshold-specific tests, subtree-rollup tests, fstree cross-check | **Moderate** — collapse `_first_hpc_project` into `active_project` fixture. Threshold tests keep hardcoded `NMMM0003/Derecho` with skip-if-missing (orthogonal feature, Phase 5 factories will cover this) |
| `tests/unit/test_fstree_queries.py` | 585 | Largest file. Spot-checks (`[:3]`, `[:5]`) — already snapshot-safe. Hardcoded `'Derecho'` throughout, hardcoded `NCGD0006` in one subtree-rollup test | **Moderate** — replace `'Derecho'` with `hpc_resource.resource_name`. Rewrite `test_ncgd0006_*` as generic subtree test using a new `any_subtree_project` fixture |

**New representative fixtures needed:**

- `any_subtree_project` — any project with ≥3 active child projects via MPTT parent_id chain. Queries once at session start, for subtree rollup tests that need "a project with children".

**Explicitly NOT ported yet:**

- Hardcoded threshold tests (`test_nmmm0003_derecho_has_threshold`, `test_use_limit_equals_prorated_times_threshold`, `test_pct_of_limit_consistent_with_charges`, `test_fstree_threshold_limit_values_match`). These need a (project, resource) combo where the resource has threshold config data — complex to discover generically, and the tests are actually covering threshold-computation logic that will be much cleaner to test with factory-built data in Phase 5. **Interim approach:** keep them referencing `NMMM0003`/`Derecho`, add `pytest.skip` if `get_project_rolling_usage()` returns no threshold data for that combo in the current snapshot. Filed in a TODO comment for Phase 5.

**Legacy-flake mitigation:**

The Phase 2a observation showed the legacy suite has a ~30% flake rate under xdist after each deletion. Strategy for the next few PRs: run the legacy suite up to 2× after each deletion; accept a clean run as proof that the port didn't introduce regressions. Document any NEW flakes (tests failing post-port that didn't fail pre-port) separately — those would be real regressions, not the pre-existing latent ones.

**Expected runtime impact:**

- ~60 tests added to `new_tests/` (142 + 365 + 585 lines worth)
- ~60 tests removed from `tests/` (same count)
- `new_tests/` total projected: ~135 tests, ~6s (3× the current 3.87s — still under 10s budget)
- Legacy total projected: ~1250 tests, ~130s

**Commit plan:**

One commit after all three files land. Commit message structure matches Phase 2b: summary + per-file notes + design decisions logged.

**Post-Phase-2c checkpoint:**

After this batch, revisit the plan and decide whether to:
(a) continue Phase 2c with more structural files (test_sam_search_cli, test_manage_facilities/organizations/resources read paths)
(b) start Phase 3 (factories + first write-path port)
(c) start Phase 4 (webapp/API test-client ports — these need the `app` fixture wired in and the DEV_ROLE_MAPPING port mentioned in Phase 0)

---

## Phase 2c execution log (2026-04-14)

**Status: ✅ complete. Three files ported in one batch.**

**Ported:**

- `tests/unit/test_project_access_queries.py` (142 lines) → `new_tests/unit/test_project_access_queries.py`
  Near-mechanical port. This file already used `next(iter(...))` for dynamic branch picking, had zero hardcoded refs, and was the smallest/cleanest of the three.

- `tests/unit/test_rolling_usage.py` (365 lines) → `new_tests/unit/test_rolling_usage.py`
  Moderate transformation. Replaced `_first_hpc_project` helper with `active_project` fixture, replaced `SCSG0001`/`NMMM0003` leaf-project refs with `active_project`, added `subtree_project` for non-leaf rollup tests. Kept `NMMM0003/Derecho` hardcoded in threshold-specific tests with `pytest.skip` fallbacks — filed as Phase 5 factory TODO.

- `tests/unit/test_fstree_queries.py` (585 lines, largest) → `new_tests/unit/test_fstree_queries.py`
  Moderate transformation plus a performance fix (see below). Replaced hardcoded `'Derecho'` throughout with `hpc_resource` fixture. Generalized `test_ncgd0006_*` subtree test to use `subtree_project` fixture (the legacy version named NCGD0006 but only asserted `adjustedUsage >= 0`, which works for any subtree project).

**New representative fixture:** `subtree_project` — any project with ≥3 active child projects (for MPTT rollup tests).

**Bug fixed in `hpc_resource` fixture:**
The initial version picked the lowest-ID HPC resource, which in the obfuscated snapshot is `Bluefire` (commissioned 2008, decommissioned 2013). It has historical allocations, so read-path tests that just check `len > 0` passed silently — but `get_fstree_data(session, 'Bluefire')` returns an empty tree because fstree only includes currently-active data. Updated the fixture SQL to filter `commission_date <= NOW()` AND `(decommission_date IS NULL OR >= NOW())`. The fixture now returns a currently-active HPC resource. Worth verifying Phase 2b tests still pass under the corrected fixture — they did (full suite green).

**MAJOR finding — cache-dependence exposed by Phase 0.**

Running the full `new_tests/` after Phase 2c's first pass clocked in at **48 seconds** — a 12× jump from the 4s Phase 2b baseline, dramatically disproportionate to the 2.3× test count growth. Investigation showed:

- `get_fstree_data(session)` walks the entire sam schema and takes ~5-15s per call with no cache.
- The legacy suite's test_fstree_queries.py had ~50 test methods calling this function directly. Under the pre-Phase-0 bug, tests ran with DevelopmentConfig's cache active (`ALLOCATION_USAGE_CACHE_TTL=3600`), so 49 of 50 calls hit the LRU cache and returned in microseconds. Under TestingConfig (post-Phase-0), the cache is properly disabled (TTL=0), and each test pays the full query cost.
- In other words: **a significant chunk of the legacy suite's speed was accidental cache warming from tests running under the wrong config**. The Phase 0 bug fix didn't slow down the TESTS themselves — it revealed that the tests were always expensive, we just weren't paying.

**Mitigation:** added **module-scoped fixtures** to `test_fstree_queries.py`:

```python
@pytest.fixture(scope='module')
def fstree_all(engine):
    with _throwaway_session(engine) as s:
        return get_fstree_data(s)   # one call per module, read-only dict shared
```

Same pattern for `fstree_hpc`, `project_fs_all`, `project_fs_hpc`, `user_fs_all`, `user_fs_hpc`. All 35+ test methods rewritten to consume the cached result instead of calling the query directly.

**Runtime impact of the fix:**

| State | new_tests/ runtime |
|---|---|
| Before caching fix | 48.07s |
| After module-scoped caching | 24.35s |
| (Pre-Phase-2c baseline) | 3.87s |

The remaining ~20s is xdist-related: pytest-xdist workers don't share fixture state, so every worker that happens to collect a fstree test re-runs the module fixture. With 12 workers all collecting fstree tests, that's ~12 uncached `get_fstree_data` calls.

**Not fixing further** (for now). 24s is well under budget — per-test wall time is 143ms, matching the legacy baseline of 103ms. Diminishing returns to chase lower. A possible future optimization:
- Use `--dist loadfile` or `@pytest.mark.xdist_group('fstree_queries')` to force all fstree tests to one worker, reducing the module-fixture cost from 12× to 1×. Filed as a TODO.

**Legacy runtime dropped significantly:**

| State | Legacy `tests/` runtime |
|---|---|
| Phase 2b (1311 pass) | 158.54s |
| Phase 2c (1213 pass, -98) | **77.82s** |

The 70-second drop from removing 98 tests = ~715ms/test — **7× the suite average of 103ms/test.** Confirms the fstree tests were the biggest cache beneficiaries and the biggest cost drivers under TestingConfig. Removing them from the legacy suite (and into the cached new_tests/ versions) is a net speed win.

**Results:**

| Suite | Tests | Runtime |
|---|---|---|
| `new_tests/` | 172 passed, 10 skipped | 24.35s |
| Legacy `tests/` | 1213 passed, 45 skipped, 2 xpassed | 77.82s |
| **Combined** | 1395 total | ~102s |

**Port progress:** 172 / 1443 = ~12% of original. Five files ported. Three file-deletions per commit is the new cadence.

**Plan revisions:**

1. **Performance cliff documented:** every expensive query function used by 10+ tests should get a module-scoped fixture cache at port time. Will need this pattern for `test_manage_summaries.py` (charge aggregations), `test_allocations_performance.py` (dashboard data), and likely the webapp dashboard tests in Phase 4.

2. **xdist-group optimization deferred:** `pytest-xdist` has `--dist loadgroup` which honors `@pytest.mark.xdist_group('name')` markers to force test co-location on a worker. Would drop fstree tests from 24s to maybe 5-7s. Not worth doing mid-migration; revisit in Phase 6 when consolidating.

3. **Cache-dependence finding has Phase 4 implications:** the webapp API tests (tests/api/) call expensive query functions via Flask routes. They will be slow under TestingConfig unless we similarly cache results at the fixture level. Plan for this when Phase 4 starts.

---

## Empirical baseline (measured 2026-04-14)

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
