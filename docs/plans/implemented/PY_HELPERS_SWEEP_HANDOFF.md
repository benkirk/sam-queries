# Py helper sweep: handoff

**Status: done 2026-10-04 on `sweep-py-helpers-2026-10`, draft PR against `staging`. Record: ledger entry 4.**
Branch `sweep-py-helpers-2026-10` off `origin/staging`. Ledger entry 4 records area `py` with end
commit = the `origin/staging` head the branch starts from. Ship as a draft PR against `staging`.

This is the fourth run of `.claude/skills/unplanned-city-sweep/` (area mode, `py`). The focus is
helpers written more than once that warrant one home. Ben picked every finding below on
2026-10-04. The shape matches the js (#722) and css (#723) runs: skill friction first, one commit
per finding, then the ledger close-out. Every `file:line` here comes from the planning read at
`0515333f`/`75f89900`, so re-verify each before editing.

## What the planning run measured

- **`dup-functions`** (identical bodies of 40+ AST nodes): 9 groups. At a 15-node floor it finds
  29, but most are ORM hybrids and properties, not helpers.
- **jscpd limited to Python:** 135 clones, 1.16% of lines. Python duplication is low.
- **Module-level names defined in 3+ modules** surfaced the real families the AST detector misses
  (env config readers, date parsers, text cleaners). Three read-only passes read every group.

## Skill friction (commit A, first)

| # | friction | fix |
|---|---|---|
| F1 | `--area py --jscpd` scans all of `src/`, templates/JS/CSS included (888 files, 321 clones) | `run_jscpd` passes jscpd `--format` per area: `py`→python, `js`→javascript, `css`→css, `templates`→markup |
| F2 | `dup-functions` misses small helpers (env readers, date parsers sit under the 40-node floor) | new `py-dup-names` detector in `scripts/sweep_inventory.py`: module-level function names, leading `_` stripped, defined in 3+ modules; skip generic names (`main`, `register`, `get`, `create`, `update`, `run`, `handle`, `index`, `validate`, `setup`, `init_app`). Fixture test in `tests/unit/gates/test_sweep_inventory.py`; add the header to the real-tree test; name it in the skill's Lift pass |

## Findings, ranked

### 1. Form date hooks: consolidate

**The problem:**
- 11 `@post_load coerce_and_validate_dates` hooks in `src/sam/schemas/forms/`, in 5 variants:

  | variant | sites | fields | range check |
  |---|---|---|---|
  | A | `facilities.py:54`, `orgs.py:130`, `orgs.py:154`, `user.py:59`, `user.py:113`, `user.py:265` | `start_date`/`end_date` | default message |
  | B | `resources.py:21`, `resources.py:64` | `commission_date`/`decommission_date` | custom message |
  | C | `user.py:148` | `new_start_date`/`new_end_date` | uses `data[...]` (required field) |
  | D | `user.py:174` | `new_end_date` | none, normalize only |
  | E | `operational.py:39` | `start_date`/`end_date` | wraps `ValueError` into `ValidationError` |

- Near-copies with other names: `resources.py:88 coerce_dates`, `operational.py:74 coerce_end_date`
  and the two `_normalize` hooks in `account_requests.py`. Look at them; fold them in only if they
  fit.
- `HtmxFormSchema.normalize_end_date` (`forms/__init__.py:117`) lazily imports
  `webapp.api.helpers.parse_input_end_date`. That is `sam` importing `webapp`.
- It raises a plain `ValueError` on a malformed string, and every end-date field is an `f.Str`.
  `FormHandler.handle` (`webapp/utils/form_handler.py:196`) catches only `ValidationError`, so 9 of
  the 11 hooks turn a malformed end date into a **500**.

**The fix:**
- A new stdlib-only `src/sam/dates.py` with `parse_ymd(s)` (midnight) and
  `parse_ymd_end_of_day(s)` (23:59:59). `webapp/api/helpers.py:45,53` re-exports them as
  `parse_input_start_date` / `parse_input_end_date`.
- `normalize_end_date` raises `ValidationError`.
- A mixin on the base, declared per schema, e.g. `_date_range = ('start_date', 'end_date', msg)`
  and `_end_only = 'new_end_date'`, replaces the hooks. `assert_date_range` (`forms/__init__.py:130`)
  stays the range check.

**Behavior change:** a malformed end date gives a form error instead of a 500. Stored values do
not change.

**Do not put the parsers in `sam/fmt.py`.** It imports `config`, which collides with the webapp boot
import order (see the `system_status/timeutil.py` docstring).

### 6. `active_at` parsing drift (do right after 1)

The same `datetime.strptime(x, '%Y-%m-%d')` appears in `webapp/dashboards/allocations/blueprint.py`
at 521, 712, 1020, 1092 and 1370, and in `admin/projects_routes.py` at 875, 1568 and 1738–1739, each
with its own fallback. Move them to `sam.dates.parse_ymd` and **keep each site's fallback**. Also
check `disk_scans/routes.py:143` and `xras/_shared.py:287 _date`. Leave `sam/xras/handlers/_fields.py`
alone: it reproduces legacy error strings on purpose.

### 2. Scope-filter clause: lift

**The problem:** "`None` or `'TOTAL'` → no filter; list → `IN`; scalar → `=`" is written out about 18
times: `src/sam/queries/allocations.py` (182–206, 649–672, 816–840 and more) and
`src/sam/queries/charges.py:125–142`.

**The fix:** one `apply_scope_filter(query, column, value)` in `sam/queries`. It replaces only the
filter shape `if x and x != "TOTAL": in_/==`. The bare `if x != "TOTAL":` lines are group-by
decisions; leave them alone. Keep the `if x` truthiness, which also skips an empty list or string.

**Expected effect:** the SQL should be identical. The parity capture proves it (see Verification).

### 3. Env config readers: partial consolidate

**Semantics today** (all read live on every call, none logs):

| | `integration/_config.py` | `notify/config.py` | `queries/allocation_state.py` | `caching/buckets.py` |
|---|---|---|---|---|
| source in an app | `app.config`, then env, then default | same, identical body | same | `app.config`, then default; never env |
| Flask-free | env | env | env | `ImportError` (catches `RuntimeError` only) |
| bool | `strip().lower()` in `1/true/yes/on`; real bools pass | identical | same, one line | n/a |
| bad int | default | default | default | raises `ValueError` |
| int ≤ 0 | default (positive only) | kept | kept | kept |

**The fix:**
- Fold notify (`notify/config.py:27,46,53`) and allocation_state (`allocation_state.py:167,176,181`)
  into `integration._config`, whose docstring already plans this.
- First give its int reader a `minimum=` option, because 0 is meaningful:
  `READ_MODEL_PATCH_MAX_TREES=0` is pinned by `test_allocation_state_gate.py:237`, and buckets use 0
  to disable.
- Keep the tests: `tests/unit/notify/test_notify_config.py` pins the truthy list, and blank or bad
  ints fall back.
- In `src/scheduling`, fold `xras_sweep._positive_int` (5 callers) into
  `_notice_common.positive_int_env`, and keep it env-only with an injectable dict. Watch
  `env or os.environ` vs `env if env is not None`: an empty `{}` differs between them.

**Leave alone:**
- `buckets.py`: folding it gains env fallback and error tolerance, which is a behavior change. Open
  item. `tests/unit/webapp/test_allocations_performance.py:848,859` patches
  `buckets._config_int`.
- `webapp/config.py` and `src/config.py`: they read at import time, which is a different job.

### 4. Status models: staged-name accessors

**The problem:** 13 identical getter/setter pairs. `system_name` appears 6 times; the others are
`queue_name`, `filesystem_name`, `node_name` and the rest. They live in
`src/system_status/models/{filesystems,login_nodes,outages,queues,user_proj_queues}.py`. Each
getter returns `self.__dict__['_pending_<name>']` if set, else `self.<rel>.name`, and each setter
stages the value.

**The listener:** `src/system_status/queries/lookups.py` pops the staged values in `before_flush`,
plus a synchronous resolver at about line 283.

**The fix:** one descriptor, e.g. `staged_name('system')` using `__set_name__` for the pending key,
next to the listener's contract.

**Expected effect:** no behavior change. The collector and status tests cover it.

### 5. Small lifts (one commit each)

- **5a Chart cache.** `webapp/caching/redis_chart.py:157 chart_cached_redis` is a byte-copy of
  `webapp/caching/chart.py:106 chart_cached`. Widen the type hint to `CacheBase`, delete the Redis
  copy, and have the facade (`webapp/caching/__init__.py:100–109`, the only caller) use
  `chart_cached` for both backends.
- **5b Organization search.**
  - `_search_orgs_for_project` (`admin/projects_routes.py:318`) and `_search_organizations_fk`
    (`admin/resources_routes.py:591`) are byte-identical; `sam/queries/mnemonic_console.py:133` is a
    third copy. Add `Organization.search_by_pattern(session, q, limit)`, matching
    `Project.search_by_pattern` (`sam/projects/projects.py:147`) and `Contract.search_by_pattern`.
  - Add `limit=` to `sam.queries.projects.search_projects_by_code_or_title`. Its 3 callers load every
    match and then slice `[:10]`: `projects_routes.py:348`, `allocations/blueprint.py:1458` and
    `notifications_routes.py:419`. `events_routes._search_projects` is a scoped variant; leave it.
- **5c Text cleaner.**
  - `strip_or_none(value, width=None)` in a new `sam/text.py` replaces nsf `_clean`
    (`integration/awards/nsf.py:83`), xras_requests `_text` (`queries/xras_requests.py:86`, 17
    refs, imported privately by `xras/modals.py`) and account_requests `_clean` (32 refs, the
    `width` clip).
  - Keep `xras/extractors._clean` local: it is on the legacy path and its docstring explains why.
  - Not duplicates: mnemonic_console `_clean` and xras_remediation `_clean`.
- **5d Private → public names (renames in place):**
  - `webapp.utils.project_permissions._is_project_steward` (3 importers plus a test)
  - `sam.xras.extractors._best_institution` / `_best_organization`
  - `sam.queries.dashboard._build_user_projects_resources_batched` (plus 2 tests)
  - `cli.accounting.dates._resolve_accounting_dates` / `_validate_accounting_dates`
  - `webapp.dashboards.charts.dualpanel._to_display_tz`, moved to matplotlib-free `charts/series.py`
  - the byte-identical `_require` in `sam/notify/addressing_store.py:90` and `template_store.py:77`,
    which becomes one shared helper in `sam.notify`

  Also drop the duplicated volume paragraph in the `drop_already_notified` wrapper docstrings
  (`expiration_notices.py:356–376`, `xras_notices.py:378–397`).
- **5e XRAS wire dates.** `sam/queries/xras_requests.py:60 _as_date` becomes public in `sam/dates.py`,
  e.g. `parse_wire_date`. It is a superset of `sam/xras/preflight.py:74 _parse_date`. Point preflight
  and these inline `date.fromisoformat(str(x)[:10])` sites at it: `sam/queries/xras_accounts.py:534`,
  `webapp/dashboards/allocations/xras/_shared.py:517` and `scheduling/tasks/xras_sweep.py:285`.

## Dropped (record in the ledger)

- `_iso` ×3 (`cli/tasks/builders.py:93`, `sam/queries/fstree_access.py:276`,
  `sam/queries/queue_access.py:28`): a one-liner, and two copies sit on the legacy byte-exact path.
- The NSF and USAspending `_parse_date`: each reads its own vendor's wire format.
- `_read_model_rows` ×3: the fstree copy decides which path serves the legacy Java-shaped output.
- `get_cache_adapter` ×3: thin facades with different default buckets.
- `drop_already_notified` wrappers: deliberate (docstring fix only, in 5d).
- `institutions_fragment`, `page_context`, `parse_filters` / `_filters`: name collisions.
- `refresh_cache` ×7: legacy blueprints, additive changes only.
- ORM `is_active` hybrid repeats: per-model semantics.
- `sam.session.get_session` / `system_status.session.get_session` twin: low value.

## Open (ledger)

- `buckets.py` env handling (see 3).
- `on` vocabulary drift: `READ_MODEL_ENABLED=on`, `MAIL_USE_TLS=on` and `NOTIFY_ENABLED=on` read as
  true in the CLI readers but false in `webapp/config.py`, whose import-time tuple lacks `on`.
  Ben's call: record it, don't fix it here.
- `webapp/api/v1/status.py` (around L86, 426, 433, 613) builds timezone-aware datetimes via
  `fromisoformat(s.replace('Z', '+00:00'))`, but `system_status` stores naive UTC. Check whether
  the model layer strips the timezone.
- jscpd clones not read: `sam/xras/handlers/adjustment.py` / `supplement.py`,
  `sam/summaries/archive_summaries.py` / `disk_summaries.py`, the `cli/*/display.py` pairs.
- `sam` imports `webapp.extensions` in `sam/schemas/__init__.py` and `sam/base.py`. The
  "sam never imports webapp" rule is not gated.

## Verification

- **Findings 2, 3 and 5b** (query or config output could move):
  - Run a parity capture of the affected functions before and after on MySQL **and** Postgres:
    `get_allocation_summary` (each scope as None / "TOTAL" / scalar / list), the transactions query,
    the charges queries, and both searches. Dump JSON and diff it.
  - Scratch scripts go untracked under `utils/profiling/`.
  - Run `pytest -m perf -n 0` for query counts.
- **Finding 1:** unit tests for the mixin (range error, end-only normalize, malformed end date →
  `ValidationError`), plus the existing form and route tests (`tests/api/test_member_management.py`,
  `tests/unit/webapp/test_contract_create_modes.py`).
- **Every commit** passes its own tests plus these gates: `test_sweep_inventory`, `test_docs`
  (comment budget), `test_notify_import_graph`, `test_route_map_parity`, `test_chart_module_boundaries`.
- **At the end:** run the full suite on MySQL; CI covers Postgres.

## Progress

- [x] Branch `sweep-py-helpers-2026-10` off `origin/staging`
- [x] Commit A: F1 (jscpd `--format` per area) + F2 (`py-dup-names`), fixture tests, skill text
- [x] Commit 1: `sam/dates.py`, form date mixin, `normalize_end_date` raises `ValidationError`
- [x] Commit 6: `active_at` parsing on `sam.dates`
- [x] Parity capture (before), MySQL and Postgres
- [x] Commit 2: `apply_scope_filter`
- [x] Commit 3: env readers folded into `integration._config` (+ scheduling `positive_int_env`)
- [x] Commit 4: status `staged_name` descriptor
- [x] Commits 5a–5e: chart cache, organization search + `limit=`, `strip_or_none`, public names, wire dates
- [x] Parity capture (after) matches; perf tier green
- [x] Close-out: ledger entry 4 (area py, end commit = branch base), whole-tree metrics row with a
      `py-dup-names` column, dropped + open as above, clear the py untriaged bullets this covers
- [x] Draft PR against `staging`
