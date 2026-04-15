# Resume — Test Suite Replacement Migration

**Purpose of this file:** bootstrap a fresh Claude Code session so it can pick up the test-suite refactor mid-flight. Everything you need to continue is in this document; the long-form plan lives at `/Users/benkirk/.claude/plans/composed-doodling-stearns.md` (also mirrored in-repo at `docs/plans/REFACTOR_TESTING.md`).

---

## TL;DR

You are migrating a 1400-test pytest suite from `tests/` to `new_tests/` on branch **`tests_refactor`**. Phases 0, 1, 2a, 2b, 2c, 2d, and **2e** are done and committed. **Phase 3 (factories + first write-path port) is next.**

Quick state check:

```bash
git log --oneline -8           # Should show 9a8a176 "Phase 2e" as HEAD-ish
git status                     # Should be clean except for untracked (follow_up.txt, legacy_sam, old_plan.md)
ls new_tests/                  # conftest.py, pytest.ini, README.md, unit/, integration/
```

Run the suite to confirm everything still works:

```bash
# Prereq: mysql-test container must be running
docker compose --profile test up -d mysql-test

# new_tests — all ports so far
SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
  pytest -c new_tests/pytest.ini new_tests/

# Legacy (still has ~600 tests to port)
source etc/config_env.sh && pytest -n auto --no-cov
```

Expected at checkpoint:
- **new_tests/**: 784 passed / 22 skipped in ~38s
- **Legacy tests/**: 597 passed / 34 skipped / 2 xpassed in ~54s
- **Combined port progress: ~57%**

The remaining 597 legacy tests are all either write-path (Phase 3), webapp/Admin (Phase 4), or perf (Phase 5) — no read-only stragglers left on the unit side.

---

## Why this migration

The legacy suite has five fundamental problems (see the plan for the long version):

1. **It ran under `DevelopmentConfig`, not `TestingConfig`.** `tests/conftest.py:267` set `FLASK_ENV='testing'` but `src/webapp/config.py:148` reads `FLASK_CONFIG`. Result: dev API keys, dev role mapping, and the full 3600s cache TTL were silently active in tests. **Fixed in Phase 0.**
2. **Tests run against whatever `SAM_DB_SERVER` points at in `.env`** — no hard fence against prod. **Fixed in Phase 1** with a dedicated `mysql-test` compose service on port 3307 + an allowlist guard.
3. **Latent xdist flakes in write-path tests** — the old `session` fixture uses plain `begin()`+`rollback()` without SAVEPOINT isolation, so concurrent writer tests can bleed state through. Observed: ~30% flake rate after removing files, different test fails each run. **Fixed in new_tests/** via `join_transaction_mode="create_savepoint"`.
4. **Data dependency on magic names** (`benkirk`, `SCSG0001`, `Derecho`, `NMMM0003`) — every snapshot refresh breaks brittle quantitative assertions. **Fixed in Phase 2b** with the representative-fixture pattern.
5. **Cache-dependent speed** — the suite was fast because `get_fstree_data()` results were cached under the wrong config. Disabling caches (correctly) in TestingConfig exposed that fstree tests take ~1s each uncached. **Mitigated in Phase 2c** with module-scoped fixture caching.

---

## Key architectural decisions

### Two-tier fixture strategy

| Tier | Where | When to use |
|---|---|---|
| **Layer 1 — Representative fixtures** | `new_tests/conftest.py` | Read-path tests. Fetch ANY row of the required shape from the snapshot. Snapshot-refresh-safe as long as at least one row of each shape still exists. |
| **Layer 2 — Factories** | `new_tests/factories/` (Phase 3) | Write-path tests. Build fresh synthetic rows inside the test's SAVEPOINT. Never fall back to snapshot data. |

**Never blend them.** A factory that "reuses SCSG0001 if it exists, otherwise creates one" silently loses its isolation story when the snapshot changes. Factories always build fresh. Representative fixtures always query the snapshot. Clear separation.

### Session isolation

`new_tests/conftest.py::session` uses `join_transaction_mode="create_savepoint"`. Every test-level `session.commit()` / `session.rollback()` becomes a SAVEPOINT op inside the outer transaction. Tests can commit or rollback mid-test without tearing down the fixture's rollback-at-teardown. This is what makes the `.update()`-contract tests from `test_manage_*.py` portable without factories.

### DB isolation

- **Host port 3307** → `samuel-mysql-test` container (dedicated test DB)
- **Host port 3306** → `samuel-mysql` container (dev stack, shared)
- **Allowlist guard** in `new_tests/conftest.py::pytest_configure` aborts with exit code 2 unless `SAM_TEST_DB_URL` points at `(127.0.0.1|localhost, 3307)` or `(mysql-test, 3306)`. Prevents any accidental run against prod.

### Legacy suite flake

The legacy suite has a **pre-existing ~30% flake rate** under xdist because its `session` fixture doesn't use SAVEPOINT isolation. Known flaky tests observed so far:
- `tests/unit/test_renew_extend.py::TestFindSourceAllocationsAt::test_excludes_inheriting_allocations`
- `tests/unit/test_crud_operations.py::TestUpdateOperations::test_update_account_user_end_date`

**Not fixing the legacy.** The fix is the migration itself — once the write-path files are ported to use SAVEPOINT, the flakes disappear. For now, if the legacy suite fails during a regression check, re-run up to 2× before investigating.

---

## What's committed (branch `tests_refactor`)

```
9a8a176 Phase 2e — finish structural read-path ports (HEAD)
522e020 checkpointing resume (Resume.md committed by user)
4d8e405 Phase 2d — bulk structural read-path ports
e7799ba commit plan
6b2b0d6 commit plan  (actually Phase 2c content: rolling_usage + fstree_queries + project_access_queries)
d870f45 Phase 2b — test_query_functions + representative fixtures
0bbc737 commit plan
53d8dde Phase 0-2 checkpoint (TestingConfig fix, mysql-test container, run-webui-dbg.sh rewrite, new_tests skeleton, first schema+views port)
```

**NOTE on commit labeling:** several commits are labeled "commit plan" — those are auto-commits from a user slash command that landed work before the `git commit` I was about to run. Treat `6b2b0d6` as Phase 2c, `e7799ba` as a plan-file update, and `522e020` as Resume.md. All contain real work.

---

## Directory map

```
new_tests/
├── README.md                                  # How to run the new suite
├── pytest.ini                                 # -c flag required; isolated testpaths
├── conftest.py                                # Safety guard + session + representative fixtures
├── unit/
│   ├── test_accounting_models.py              # Contract.is_active tests
│   ├── test_active_interface.py               # universal is_active / is_active_at() (Phase 2e)
│   ├── test_basic_read.py                     # broad ORM smoke reads (Phase 2e)
│   ├── test_charging_models.py                # Factor / Formula reads (Phase 2e)
│   ├── test_cli_jupyterhub.py                 # mocked JupyterHub CLI
│   ├── test_directory_access_queries.py       # group/user populator (Phase 2e)
│   ├── test_email_notifications.py            # mocked SMTP + templates (Phase 2e)
│   ├── test_fmt.py                            # sam.fmt unit tests (zero DB)
│   ├── test_fstree_queries.py                 # module-scoped cached fstree data
│   ├── test_manage_facilities.py              # .update() contract tests
│   ├── test_manage_organizations.py           # .update() contract tests
│   ├── test_manage_resources.py               # .update() contract tests
│   ├── test_notification_enhancements.py      # mocked SMTP (zero DB)
│   ├── test_orm_descriptors.py                # ORM class introspection (zero DB)
│   ├── test_project_access_queries.py         # structural reads, dynamic branch
│   ├── test_project_models.py                 # ProjectCode/FosAoi/ResponsibleParty
│   ├── test_project_permissions.py            # pure mocks (zero DB)
│   ├── test_query_functions.py                # representative fixtures
│   ├── test_rolling_usage.py                  # subtree_project + threshold test caveats
│   ├── test_sam_search_cli.py                 # CLI with mocked session
│   ├── test_security_models.py                # ApiCredentials reads
│   └── test_smoke.py                          # infrastructure validation
└── integration/
    ├── test_schema_validation.py              # ORM ↔ MySQL alignment
    └── test_views.py                          # XRAS / CompActivityCharge read-only
```

### Representative fixtures currently defined (new_tests/conftest.py)

Shape-constrained (session-cached ID + function-scoped `session.get()`):
- `active_project` — any Project with active allocations (1310 candidates in snapshot)
- `subtree_project` — any Project with ≥3 active child projects
- `multi_project_user` — any active user on ≥2 active projects
- `hpc_resource` — any currently-active HPC Resource (commissioned AND not yet decommissioned)

Simple "any row" (function-scoped `.first()` + skip fallback):
- `any_facility`, `any_panel`, `any_panel_session`, `any_allocation_type`
- `any_organization`, `any_institution`, `any_aoi`, `any_aoi_group`
- `any_contract`, `any_contract_source`, `any_nsf_program`
- `any_resource`, `any_resource_type`, `any_machine`, `any_queue`

### Module-scoped expensive caches (in specific test files)

`test_fstree_queries.py` defines: `fstree_all`, `fstree_hpc`, `project_fs_all`, `project_fs_hpc`, `user_fs_all`, `user_fs_hpc`. These cache the result of expensive `get_fstree_data()` / `get_project_fsdata()` / `get_user_fsdata()` calls once per module. Without them, the file runs in ~20s instead of ~3s.

---

## Gotchas to remember

### `benkirk` is deliberately preserved in the snapshot

The obfuscated DB rewrites most usernames to `user_<hex>` but **`benkirk` is preserved unmodified** as a named test account. See `~/.claude/projects/-Users-benkirk-codes-project-samuel-main/memory/project_test_db_fixtures.md`. Use `benkirk` directly when a test needs a specific guaranteed-known username; use `multi_project_user` fixture when any active multi-project user will do.

**Why this matters:** `src/cli/user/commands.py:37` does `pattern.replace('%','').replace('_','')` before passing to `User.search_users`. A substring-match against an obfuscated `user_<hex>` username becomes `userhex` and matches nothing. Pattern-search CLI tests have to target `benkirk`.

### fstree query cost and TestingConfig

`TestingConfig` disables the LRU cache (`ALLOCATION_USAGE_CACHE_TTL=0`). Every call to expensive query functions — `get_fstree_data`, `get_user_dashboard_data`, `get_project_dashboard_data`, `get_resource_detail_data` — pays the full cost. **When porting tests that call these, add a module-scoped fixture cache** (see `test_fstree_queries.py` for the pattern).

### `.update()` contract tests are NOT write-path tests

`test_manage_facilities.py`, `test_manage_organizations.py`, `test_manage_resources.py` all follow: fetch real row from snapshot → call `.update()` → `session.rollback()`. Under SAVEPOINT isolation this is completely safe. **Don't defer these to Phase 3 — they belong in Phase 2 with representative fixtures.**

### Legacy post-rollback re-assertions are stale

Legacy code like `assert f.description == original` AFTER `session.rollback()` is a smell. Under SAVEPOINT isolation the Python object is still attached, so the re-assertion is either a tautology or wrong depending on session state. **Drop these during port.**

### DEV_ROLE_MAPPING in TestingConfig is a Phase 4 TODO

Phase 0 mirrored `DEV_ROLE_MAPPING` from `DevelopmentConfig` into `TestingConfig` because 18 webapp route tests needed `benkirk → admin` role resolution. This is a test-design smell — tests are depending on a dev shortcut instead of real role tables. Phase 4 (webapp tier) should replace this with factory-built role grants.

---

## Critical environment / invocation commands

```bash
# Activate conda env + load .env
source etc/config_env.sh

# Start the test MySQL container (~90s first time for dump restore; ~5s thereafter)
docker compose --profile test up -d mysql-test

# Wait for MySQL to accept TCP — the healthcheck lies during init, use mysqladmin ping
until mysqladmin ping -h 127.0.0.1 -P 3307 -u root -proot --silent 2>/dev/null; do sleep 2; done

# Run new_tests/
SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
  pytest -c new_tests/pytest.ini new_tests/

# Run legacy
pytest -n auto --no-cov

# Run both, see combined status
pytest -n auto --no-cov tests/ && \
  SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' \
    pytest -c new_tests/pytest.ini new_tests/

# Debug webapp against the test DB (port 5051, safe from collision with dev stack on 5050)
PORT=5051 utils/run-webui-dbg.sh
```

---

## Phase 3 — what's next

### Scope

Build the factory module so write-path tests have somewhere to go. Validate the design by porting **one small file end-to-end**, then iterate.

### Factory design (plain builder functions, not factory_boy classes)

Location: `new_tests/factories/` — one module per domain:

```
new_tests/factories/
├── __init__.py         # re-export public builders
├── _seq.py             # next_seq(prefix) → 'FCT0001' etc.
├── core.py             # make_organization, make_user, make_api_credentials
├── resources.py        # make_resource_type, make_resource, make_machine, make_queue
├── projects.py         # make_facility, make_project
└── accounting.py       # make_account, make_allocation
```

Principles (from the plan, validated over Phases 2b-2d):
1. **Never fall back to snapshot data.** `make_user()` always builds a fresh User row. Tests that want "any user from the snapshot" use `multi_project_user` instead.
2. **Build the minimum required graph.** `make_user()` auto-builds an Organization if `org=` isn't supplied. `make_project()` auto-builds a Facility.
3. **Predictable data** — no randomness. `_seq.py`'s counter ensures unique identifiers within a test without introducing nondeterminism.
4. **Factories take `session` as first argument.** They run inside the test's SAVEPOINT; rollback catches everything.

### First port target: `test_wallclock_exemptions.py`

146 lines, ~12 tests, 7 `WallclockExemption.create()` calls. Shallow dependency graph: needs `User` + `Queue`. Good proving ground because:
- `WallclockExemption` is simple (no multi-step flows)
- Tests assert on the created row (`assert wce.wallclock_exemption_id is not None`, etc.) — good for validating the factory + SAVEPOINT interaction
- Small enough to iterate on the factory API without massive rework

### Port workflow

1. Read `tests/unit/test_wallclock_exemptions.py` to understand the API
2. Read the `WallclockExemption`, `User`, `Queue`, `Machine`, `Resource` ORM models to understand FK requirements
3. Build `new_tests/factories/core.py` and `new_tests/factories/resources.py` (minimum: `make_user`, `make_organization`, `make_queue`, `make_machine`, `make_resource`, `make_resource_type`)
4. Create `new_tests/unit/test_factories.py` as a smoke test for each builder:
   ```python
   def test_make_user_creates_flushable_row(session):
       u = make_user(session)
       session.flush()
       assert u.user_id is not None
       assert u.active is True
   ```
5. Port `new_tests/unit/test_wallclock_exemptions.py` to use the factories
6. Iterate on the factory API until tests pass
7. Delete `tests/unit/test_wallclock_exemptions.py`
8. Run both suites, verify counts match
9. Commit

### Phase 3 follow-ups (same branch, subsequent sessions)

Once the factory design is stable:
- `test_transaction_context.py` (78 lines) — tests commit/rollback semantics, will stress-test SAVEPOINT pattern
- `test_management_functions.py` (312 lines) — needs full User+Project+Account+Allocation graph, so more factories

Then (biggest), Phase 3b/c/d:
- `test_crud_operations.py` (692 lines) — broad CRUD coverage
- `test_manage_summaries.py` (710 lines) — needs charge summary factories (CompChargeSummary, DavChargeSummary, etc.)
- `test_renew_extend.py` (727 lines) — needs `scenario_allocation_tree()` composer
- `test_audit_logging.py` — uses `session_commit` explicitly
- `test_new_models.py` (if present — 51 tests per earlier audit)

---

## Remaining files NOT yet ported

**Phase 2e is DONE.** All the structural read-path files have landed. What's left in `tests/unit/` splits cleanly into Phase 3 (write-path + factories), Phase 4 (webapp/Admin/OIDC), and Phase 5 (perf):

| File | Lines | Phase | Why |
|---|---|---|---|
| `test_wallclock_exemptions.py` | 146 | **3 (first port)** | 11 `.create()` calls, shallow FK graph (User+Queue) — smallest factory validation |
| `test_transaction_context.py` | 78 | 3 | Tests commit/rollback semantics explicitly |
| `test_management_functions.py` | 312 | 3 | `add_user_to_project` etc, needs full User+Project+Account+Allocation graph |
| `test_crud_operations.py` | 692 | 3b | Broad CRUD coverage |
| `test_manage_summaries.py` | 710 | 3c | Needs charge summary factories |
| `test_renew_extend.py` | 727 | 3d | Needs `scenario_allocation_tree()` composer |
| `test_audit_logging.py` | ? | 3 | Uses `session_commit` explicitly |
| `test_allocation_tree.py` | ? | 3 | Hardcoded Allocation ID 6077 — replace with factory-built tree |
| `test_new_models.py` (if present) | ? | 3b | 51 tests per earlier audit; mix of reads/writes |
| `test_oidc_auth.py` | 607 | **4** | Flask routes + Provider unit tests |
| `test_admin_defaults.py` | 318 | **4** | Flask Admin ModelView — needs `app` fixture |
| `test_allocations_performance.py` | 937 | **5** | Performance tier |

**Also note** — `tests/api/` and `tests/integration/` still have their own contents. `tests/integration/` has `test_legacy_api_parity.py`, `test_sam_search_cli.py` (moved/subset), `test_status_dashboard.py`, `test_status_flow.py`. `tests/api/` has ~14 webapp API endpoint tests. All Phase 4.

**Post-migration cleanup:** when the last file moves, delete `tests/conftest.py`, `tests/fixtures/`, and `tests/` itself. Rename `new_tests/` → `tests/`. Remove `Resume.md`.

---

## Plan file contents summary

The full plan file at `/Users/benkirk/.claude/plans/composed-doodling-stearns.md` (~1200 lines) contains detailed execution logs for every phase — including the empirical findings, revisions made during execution, and design decisions. Key sections:

- **Phase 0 execution log** — TestingConfig bug + DEV_ROLE_MAPPING finding
- **Phase 1 execution log** — mysql-test container, port parameterization, run-webui-dbg.sh rewrite
- **Phase 2a execution log** — first port (schema_validation + views), SAVEPOINT discovery, legacy xdist flake
- **Phase 2b execution log** — test_query_functions + representative fixtures, two-tier strategy decision
- **Phase 2c execution log** — test_rolling_usage + test_fstree_queries, cache-cliff finding, module-scoped caching
- **Phase 2d planning** — classification of remaining unit tests, `.update()` contract insight
- **Phase 3 planning** — factory module design, first-wave factories, test_wallclock_exemptions port plan

Read the plan file if you need rationale for why a particular choice was made.

---

## Working principles from this user

- **Two-tier test data strategy** — user approved this explicitly in Phase 2b. Don't blend snapshot data with synthetic factory data.
- **Test ordering flakes are not my problem to fix in legacy** — the migration IS the fix. Re-run legacy tests up to 2× during regression checks.
- **The user commits plan-file changes via a slash command** — Phase 2c work landed under commit `6b2b0d6 "commit plan"` before my own `git commit` fired. Don't panic if a commit fails with "nothing added to commit" after staging — check if the user's slash command grabbed it first.
- **User runs pytest by hand** (memory: `feedback_testing.md`). OK to run tests from Bash when actively porting/iterating, but don't run them unnecessarily.
- **Keep port commits reasonably sized** — 3-5 files per commit is the rhythm. One commit per "batch" with a detailed message summarizing what moved and why.
- **Use `docker compose --profile test up -d mysql-test`, not `docker compose up`** — the test container is behind a profile to avoid starting with the dev stack.
- **Only commit when explicitly asked** (project convention). The user asks for "checkpoint" commits at natural breakpoints.

---

## Starting prompt for the next session

Paste this into a fresh Claude Code session to bootstrap:

> Continuing a test-suite refactor I've been working on with you. Please read `/Users/benkirk/codes/project_samuel/devel/Resume.md` first — that has the full state of the migration, what's committed, and what Phase 3 looks like. Then confirm the current state by running:
>
> ```bash
> git log --oneline -6
> docker compose ps
> SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' pytest -c new_tests/pytest.ini new_tests/ 2>&1 | tail -5
> ```
>
> If mysql-test isn't running, start it with `docker compose --profile test up -d mysql-test` and wait for TCP. Then kick off **Phase 3**: build the factory module (`new_tests/factories/`) and port `test_wallclock_exemptions.py` as the first write-path validation.
