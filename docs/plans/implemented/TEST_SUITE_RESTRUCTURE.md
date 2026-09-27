# Test suite restructure — handoff

**Status:** planned 2026-09-18, not started. One PR, a series of commits, `--base staging`.
**For:** a fresh session. Everything needed is in this file; the research is not repeated
elsewhere. Read "Findings" once, then execute "Commit series" in order.

## Why

The suite is green and comprehensive, but has become unreadable to its owner, and test
failures get fixed reflexively without understanding. The evaluation asked: is an
overhaul warranted, *only if* coverage is preserved and the result is more
comprehensible? Answer: no rewrite. A move-only reorganization, plus a short list of
targeted shared-state fixes. **No test bodies are rewritten except where named below.**

## Findings (measured 2026-09-18 on `events_followups`)

**Size and health.** Default tier: 9,174 passed, 43 skipped, 1 xfailed, 2m25s wall on a
laptop (`docs/TESTING.md` still says ~67 s). ~106k test LOC against ~110k `src/` LOC.
`tests/unit/` is one flat directory: 253 files, 89k LOC — 85% of all test code.

**`tests/unit` is unit tests in name only.** Static AST classification of `tests/unit`:

| What the file exercises | files | tests | LOC |
|---|---|---|---|
| Flask app / test client | 83 | 2,352 | 35.1k |
| DB session (model/query/manage) | 95 | 2,599 | 37.8k |
| pure python | 49 | 945 | 9.6k |
| Click `CliRunner` | 12 | 179 | 3.0k |
| pure source-scanning gate | 14 | 142 | 3.1k |

Only ~63 files are unit tests in the strict sense. This is by design (`docs/TESTING.md`
§ Which tier? routes webapp tests there); the name and flatness are the problem, not the
contents.

**Markers no longer describe anything.** `unit` is on 166 files regardless of what they test;
`webapp` is on 1 file out of 114 HTTP-class files; 111 of 303 files carry no tier
marker. Only `perf`, `stress`, `mysql_only`, `postgres_only` do real work.

**Gates are invisible.** 14 pure source-scanning files (`test_docs`, `test_css_tokens`,
`test_collapse_trigger_rows`, `test_notify_templates`, `test_notify_import_graph`,
`test_chart_module_boundaries`, `test_template_csp_lint`, `test_chart_fonts`,
`test_sqlcompat`, `test_fa_modern_classes`, `test_xras_incoming_boundary`,
`test_action_cells_nowrap`, `test_no_fstring_sql`, and borderline
`test_xras_admin_client`) are interleaved alphabetically with behavior tests. ~18 more
files mix gate and behavior tests. A red gate is a repo lint rule; nothing in the name
says so.

**Duplication is low** — ~870 redundant LOC (<1%). Top re-declared fixtures: `ledger` ×9,
`runner` ×8, `ctx` ×8, `status_engine` ×5, `transport` ×5, `mapped_resource` ×5
(identical), `mock_db_session` ×4, `wire` ×4, `view_only_client` ×3, `_make_task_run` ×3.
There is no conftest under `tests/unit` or `tests/api`, which is why.

**Large files.** Two are flat and need splitting: `test_webapp_jobs.py` (4,731 lines,
261 tests, **0 classes**, ~20 comment-banner sections, 21 helpers) and
`test_webapp_disk_scans.py` (2,364, 115 tests, 1 class). `test_xras_remediations.py`
(40 classes), `test_query_functions.py` (15), `api/test_xras_access.py` (17) are well
structured — leave them.

**The stress tier.** 22 tests, 2.3 s. "Slow" and "serial" are both wrong — it is
documented xdist-safe (`tests/xras_audit.py` cleans up by captured PK). CI already runs
it on every PR (`.github/workflows/sam-ci-docker.yaml` ~line 223, `if: always()`), so a
manual `make stress` duplicates CI. About 14 of the 22 are covered **nowhere else**:
`_fit_error_messages` on a ~106 KB error list, `_fit_payload` oversize refusal ("CANNOT
BE REPLAYED", NULL `action_type`), astral (4-byte) unicode into utf8mb3 columns, the
unmapped-path 2×TEXT body and emoji-in-path, ORM-vs-DDL width parity for
`xras_action_log`, disabled-vs-unmatched park rows being distinguishable, `action_id` /
`service` on repeat posts, Renewal→reviewable 422. These need real MySQL under
`STRICT_TRANS_TABLES` (errors 1406/1366). Production monitoring does **not** replace
them: `scripts/cirrus_watch.sh` classifies rows that exist; a lost audit row is exactly
what it cannot see. ~8 are duplicated by the default tier (listed in C1). History: the
tier found the pre-cutover `resources[].key` bug (now guarded by
`test_xras_wire_vocabulary.py`); nothing since 2026-08-24.

**What keeps failing** (249 PRs, 1,439 pre-squash commits since 2026-05-15; CI history
only back to 2026-09-04: SAM Test Suite 15/191 red, Staging CI 12/123, Browser Smoke
5/186; six of the 12 identified-cause failures were *exactly one test* of ~8,700):

| # | Category | Verdict |
|---|---|---|
| 1 | Shared state under xdist: committed fixed identifiers, deadlocks, shared Redis (#460, #471, #575, #543, #484) | ~90% test-design noise |
| 2 | Laptop passes / CI fails: core count, timezone, files missing from image (#424, #534, #550) | half real packaging bugs |
| 3 | Snapshot / LFS blob drift (24 blob commits in 14 PRs) | 60% expected friction, 40% tests leaning on snapshot contents |
| 4 | Wall-clock / day boundary (#565; `test_one_now_serves_the_whole_page` **still open**) | ~70% noise |
| 5 | Golden pins: route map (31 PRs), `test_modal_shell_contract` (25 PRs), fingerprints, baselines | ~95% intentional acknowledgement — working as designed |
| 6 | Postgres tier (since #551) | too new to call |
| 7 | Stale string assertions after intentional UI change (#523, #334, #466) | acknowledgement via brittle matching |
| 8 | Browser smoke: fragment swept as a page (recurring) | ~70% noise |

Real regressions were mostly caught by the Playwright smoke and parity harnesses, not
the unit tier. Each shared-state repair added a local lock or tolerance rather than
removing the shared state; `serial_file_lock` has three lock names and five user files.

**Most shared-state plumbing already exists — do not rebuild it:**
- `tests/factories/_seq.py` is worker-aware (`PYTEST_XDIST_WORKER` tag in `next_seq`,
  `next_int`, disjoint `next_date` slices). The mnemonic factory partitions a 1,296-wide
  space by worker count (411e15d7).
- `pytest_configure` (`tests/conftest.py` ~134–155) already rewrites `CACHE_REDIS_URL`
  to a per-worker logical DB (a2ea29fd). No key-prefix work is needed.
- CI runs pytest inside the `webapp` container, which sets `TZ=America/Denver`
  (`compose.yaml:62`). Only bare laptop runs are unpinned.
- `tests/xras_audit.py` is the reference committed-row cleanup: capture PKs, delete one
  PK at a time, descending (self-FK), **never a range delete** — an open-ended range
  takes a gap lock that deadlocks concurrent inserts.
- No freezegun/time-machine is installed; the existing clock seam is `active_at`
  (`src/sam/queries/dashboard.py:165`, `:350`).

## Decisions already made (do not reopen)

- Layout is **`tests/unit/<domain>/`**: `gates/ xras/ notify/ tasks/ charts/ cli/
  webapp/ models/ queries/ manage/`. `tests/api`, `tests/integration`, `tests/perf`,
  `e2e/` untouched. `tests/unit/snapshots/` stays where it is (`e2e/conftest.py` reads
  the route map from that path).
- One PR, one commit per step, green at every commit.
- Items C1–C5 are committed work; C6 is an evaluation whose outcome is recorded in the
  PR; C7 (docs) is last.
- Stress: delete the gate, keep the tests.

## Commit series

Branch from `origin/staging` (local branches are stale). Never write the bare skip-ci
tokens in a commit message or the PR body — see `CLAUDE.md` § Skipping CI.

### C1. Fold the stress tier into the default suite
- `pytest.ini`: addopts becomes `-m "not perf"`; drop the `stress` marker and its comment.
- `git mv tests/stress/*` → `tests/api/xras_audit_rows/` (keep `scenarios.json`, the
  conftest, and the relative import `from .test_audit_row_survives import ASTRAL, TEXT_LIMIT`);
  remove `pytestmark = pytest.mark.stress`.
- Delete the ~8 duplicated tests **only after opening each twin and confirming the
  assertion is the same**; keep anything merely near a twin. Twins: supplement
  additivity → `test_xras_supplement_handler.py::TestItIsAdditive`; co-PI spelling →
  `test_xras_roster.py:59-72`; Advance/Renewal/Date-Adjustment selector →
  `test_xras_dispatch.py:126-156, 325-366, 470`; disabled-type park, Transfer
  `projcode_result`, `outcome_reason` → `tests/api/test_xras_access.py:778-1028`;
  over-long actor / action_type → `test_xras_access.py:1550, 1603`.
- Remove the `stress:` Makefile target and the stress step in `sam-ci-docker.yaml`.

### C2. Move `tests/unit/` into domain subdirectories (move-only)
- **The mapping is a reviewed artifact.** Build an `old → new` TSV and paste it into the
  PR body. Prefix rules place ~156 files: `xras|mnemonic` → xras (37);
  `notify|notification|expiration` → notify (17); `task` → tasks (12);
  `chart|theme|layout` → charts (9); cli (13); webapp (50:
  `webapp|admin|htmx|nav|rbac|oidc|auth|form|flask|request|allocations_|status|account_|event|…`);
  gates (18 = the 14 pure scanners + `test_modal_shell_contract`, `test_static_assets`,
  `test_vendor_assets`, `test_route_map_parity`). The other **97** are older
  core/ORM/query/manage files: assign by what they import — `sam.queries.*` → queries,
  `sam.manage.*` → manage, ORM/enums/fmt/session → models. Ambiguous → `models/`; do not
  invent an eleventh directory. Mixed gate+behavior files (the `values.yaml` greps in
  `test_task_*`) stay with their domain.
- `git mv` only. No `__init__.py` files (pytest prepend import mode; basenames are
  currently unique across `tests/` — keep them unique).
- **Known breakage, fix in the same commit:**
  1. 42 files compute the repo root as `Path(__file__).parents[2]` (plus 6 `parents[1]`,
     3 `.parent.parent.parent`). Add `REPO_ROOT` / `SRC` / `SNAPSHOTS` to a new
     `tests/_paths.py` and replace the depth arithmetic, so the next move cannot break it.
  2. `tests/unit/test_award_search.py:32` imports from
     `tests.unit.test_award_providers` — move `NSF_PAYLOAD` / `_provider` to a shared
     helper module beside them.
  3. `test_docs.py::test_cited_paths_exist` goes red: ~97 citations of `tests/unit/...`
     in non-record `.md` files (README.md, CLAUDE.md, docs/TESTING.md, src READMEs); its
     tail-match does not bridge a new directory. Rewrite from the TSV with a throwaway
     script (do not commit it). `docs/plans/**` and `**/implemented/**` are records and
     exempt — leave them alone.
  4. ~400 comment citations in `src/**/*.py`, `e2e/*.py`, `helm/values.yaml` are checked
     by **no** gate and would rot silently. Same script, same commit.
  5. Self-paths hardcoded inside tests: `PROSE_EXEMPT`, `CITED_PATH_EXEMPT`,
     `PHRASING_EXEMPT` in `test_docs.py`; any consumer of `HTMX_FRAGMENT_SHELL_DEPS`.
- **Proof it is move-only:** `pytest --collect-only -q | sed 's#.*::##' | sort` before
  and after — identical multiset of test names.

### C2b. Split the two flat files (move-only)
`test_webapp_jobs.py` → ~6 files under `tests/unit/webapp/jobs/` along its existing
comment banners; its 21 helpers go to that directory's `conftest.py` (fixtures) and
`_helpers.py` (plain functions). `test_webapp_disk_scans.py` → 3–4 files under
`tests/unit/webapp/disk_scans/`. Same collect-only proof.

### C3. Per-domain conftests absorb identical fixtures
`tests/unit/{tasks,notify,cli,xras}/conftest.py` take **identical copies only**:
`ledger`, `ctx`, `wire`, `transport`, `status_engine`, `_make_task_run` → tasks/notify;
`runner`, `mock_db_session` → cli; `mapped_resource`, `view_only_client` → xras.
Same-named helpers with different bodies (`_payload`, `_message`) stay local. ~600 LOC
out. Do not split the root `tests/conftest.py` — it is cohesive and the guards must
stay at the root.

### C4. Markers by directory, not by hand
Extend the existing `pytest_collection_modifyitems` in the root conftest (the hook that
applies `tests/postgres_expected_failures.txt`): `tests/unit/gates/` → `gate`; any other
`tests/unit/<d>/` → `<d>`; `tests/api/` → `api`; `tests/integration/` → `integration`.
Register them in `pytest.ini`; delete the hand-applied `unit` / `webapp` / `smoke` /
`integration` `pytestmark` lines and marker entries. Keep `perf`, `mysql_only`,
`postgres_only`. `pytest -m gate` and `pytest -m xras` then work and cannot drift.

### C5. Shared-state fixes (targeted)
| Offender | Fix |
|---|---|
| `test_notifications_queries.py:272-282` `test_one_now_serves_the_whole_page` — open flake, `assert 1.0 < 1`: `when` is evaluated per loop iteration and `creation_time` truncates to whole seconds | compute `when` once outside the loop; take projcodes from `next_seq` instead of fixed `PPPP0001/2` |
| `test_task_xras_sweep.py` fixed PKs (`999xxx`, `771003`, `535388`) under `serial_file_lock('xras_sweep_fixed_pks')` | `make_xras_opportunity_mapping` (`tests/factories/xras.py:165-189`) already derives an id when `opportunity_id is None` — use it; worker-namespace 535388 (assertions at ~:1121/:1135 are identity checks). The table-wide pre-delete at ~:1109 and this lock then go away. |
| `test_xras_accounts_card.py` / `test_xras_accounts_query.py` committed `placeholder38-user-00038`, `realname38`, `NCAR4227` rows | load `tests/fixtures/xras/actions/new_ncar4227_failed.json` through a helper that substitutes a `_seq` worker-tagged username + request number; ~20 assertions reference the helper's constants. Removes the cross-file `absent` → `inactive` hazard and this lock. **If this passes ~150 changed lines, stop and keep the lock** — it works; say so in the PR. |
| committed-row cleanup hand-written three times (card fixtures, `test_xras_remediation_service.py`, `tests/xras_audit.py`) | one `committed_rows` fixture in the root conftest: register ORM objects post-commit, delete by PK descending at teardown. Migrate the hand-rolled copies. |
| bare laptop runs have no pinned zone | `pytest_configure`: `os.environ.setdefault('TZ', 'America/Denver'); time.tzset()` |
| `read_model_table` lock | **leave.** `AccountAllocationState.bulk_replace` (`src/sam/summaries/allocation_state.py:78-97`) rebuilds the whole table and cannot be namespaced from the test side. See C6. |

Why committed rows exist at all: routes read through Flask-SQLAlchemy's `db.session` on
its own connection, so only committed rows are visible to them.

### C6. Evaluate — record the outcome in the PR
`test_read_model_readers` (97 s CPU), `test_task_refresh_allocation_state` (85 s),
`test_allocations_performance` (83 s) are about a third of suite CPU. The first two
serialize on `read_model_table` and produced three distinct fragilities in ten days (row
order, deadlock, day boundary). Measure per-fixture `--durations`; check whether the
snapshot-wide `_feed()` can be built once per module inside the lock, given SAVEPOINT
isolation rolls back per test. Land it only if small and it cuts ≥40 s CPU; otherwise
write the numbers into the PR and stop. Do **not** add a scope argument to
`bulk_replace` here — that is a `src/` change with its own review.

### C7. Docs, last, against the final tree
- `docs/TESTING.md`: rewrite Overview, "Which tier?", "Running specific areas" around
  the directory map and `-m <domain>`; remove the stress section; fix the `~67s` figure;
  add one line on where a new test file goes and that identical fixtures belong in the
  domain conftest.
- `CLAUDE.md` § Code Organization tests block and § Testing (the `pytest.ini` gates
  line; the scenarios file is now `tests/api/xras_audit_rows/scenarios.json`).
- `README.md` structure tree — the two trees drift and nothing checks them; update both.
- `docs/README-k8s.md` if it names the stress tier.

## Out of scope
Rewriting test bodies; a parametrization sweep (median test is 7–13 lines, no large win);
splitting the root conftest; splitting the three well-structured large files; `e2e/`;
`tests/perf/`; any `src/` behavior change.

## Verification
1. After every commit: `source etc/config_env.sh && pytest` against mysql-test (:3307).
2. After C2 and C2b: collect-only test-name multiset identical before and after.
3. After C1 and C5: `pytest tests/api/xras_audit_rows tests/unit/xras tests/unit/tasks -n auto`
   five times consecutively with no collision or deadlock; row counts of
   `xras_action_log`, `xras_remediation_event`, and `users WHERE unix_uid >= 999000000`
   equal before and after (no leaked committed rows).
4. Once at the end: `make pytest-pg` (:5434); `make perf`;
   `TZ=UTC pytest tests/unit/xras tests/unit/notify`; `pytest -m gate` alone.
5. Coverage parity: `pytest --cov=src` total on the branch vs `origin/staging` must not drop.
6. PR CI: `sam-ci-docker` (MySQL and Postgres jobs), `ci-staging`, `browser-smoke`.

## Could not be determined
How often the source-scanning gates fire as false positives — trips are fixed inside
feature commits and leave no trace. CI history before 2026-09-04 has aged out.
