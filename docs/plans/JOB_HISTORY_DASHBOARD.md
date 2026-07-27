# Job History Dashboard — SAM-side implementation (Session 2 handoff)

**Status (2026-07-27): IMPLEMENTED.** Plugin side landed on PR #99 (tip
`24a35ed`; round-2 memory dims moved it to `e07238f` — see
`JOB_HISTORY_FOLLOWUPS.md`); SAM Commits 1–7 are on `job_history_expansion` as planned, with
the deltas recorded in *As-built notes* below. The approved plan of record
(full rationale, plugin-side commit details) is
[`JOB_HISTORY_DRILLDOWN.md`](JOB_HISTORY_DRILLDOWN.md); where the two differ,
THIS doc reflects what actually landed.

## As-built notes (deltas from the plan below)

- **Metric pills landed on ALL aggregation tabs**, not just By User: the
  three histogram tabs also toggle Jobs / CPU-hours / GPU-hours (the chart
  generator already supported it; one shared partial).
- The card carries the resource-details page's **date window** (`start`/
  `end`) into every tab so aggregations stay bounded by default.
- `?scope=` subtree re-rooting is honored by **every** project fragment
  (table + all aggregations + explorer) via `_tree_projcodes` →
  `_scope_project`, not only the explorer.
- The per-job fragment's hidden filter form now round-trips the **full**
  extended filter set (`roundtrip_params`) so explorer pagination/sort
  keeps name-glob/bounds/wait filters; the fk-picker's `user_id` resolves
  to a username in `_resolve_user_filter`.
- The jobs table body is one shared `_jobs_table_response(mode=…)`
  (project | machine | user) rather than three copies.
- The By User table appends an inert **"Other (beyond top N)"** row from
  the pre-truncation totals (limit = 25 rows; pie keeps ≤ 9 + Other).
- Machine-wide count uses the plugin's `jobs_count` (no SAM-summary fast
  path — that table is per-project and the operator surfaces are gated +
  low-volume).
- `sam-admin cache --category` and the API/HTMX category sets gained
  `jobs`; `tests/api/test_admin_cache.py`'s pinned category set updated.
- Histogram bucket tables render inside a collapsed `<details>` under the
  SVG ("Bucket counts").

## How to resume

1. Branch: `job_history_expansion` (this repo). Plugin repo:
   `~/codes/hpc-usage-queries/devel`, branch `jobs_plugin_search_drilldown`
   = PR #99, **tip `e07238f5f77317ad36d67f111b92e934eae07528`** (round-2
   memory dims; round-1 contract below was verified at `24a35ed`).
2. Rebuild against the pinned sha (both, before any webapp/pytest work):
   ```bash
   HPC_USAGE_QUERIES_REF=e07238f5f77317ad36d67f111b92e934eae07528 docker compose build webdev
   HPC_USAGE_QUERIES_REF=e07238f5f77317ad36d67f111b92e934eae07528 source etc/config_env.sh
   make print-env-hash   # confirm the hash-keyed conda-env rebuilt
   ```
3. Webapp: `docker compose up webdev --watch` → http://localhost:5050 (stub
   Quick Login). Tests: `docker compose --profile test up -d mysql-test`,
   `export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'`.
   **Running pytest directly is authorized for this track** (both repos).
4. Merge order at the end: plugin PR #99 → staging (then #98 staging→main);
   SAM PR (this branch → staging) only after.

## As-landed plugin contract (verified, tip 24a35ed)

Everything importable from the package root: `COLUMNS`, `DEFAULT_COLUMNS`,
`VERBOSE_COLUMNS`, `project_row`, `JobQueries`, `get_engine`, `get_session`.

**Shared filter kwargs** on `jobs_search` / `jobs_count` / `jobs_facets` /
`jobs_histogram` / `jobs_usage_by` (all keyword-only, all `Optional`):
`start, end` (site-local dates, filter `Job.end`), `user`, `account`
(str | Sequence — sequence → `IN`; `[]` means NO ROWS), `queue`, `qos`,
`exit_status` (PBS exit CODE as text, `'0'` = success), `job_id`,
`name` (str | Sequence glob, `[]` means NO FILTER), `ignore_case`,
`min/max_eligible_secs`, `min/max_nodes`, `min/max_cpus`, `min/max_gpus`,
`min/max_elapsed` (seconds), `min/max_reqmem` (bytes). All range bounds
inclusive + NULL-strict.

- `jobs_search(..., columns, limit, offset, sort_by, sort_dir)` → `list[dict]`
- `jobs_count(...)` → `int`
- `jobs_facets(..., facets=('queue','qos','exit_status'), self_exclude=True,
  limit=None)` → `{dim: [{'value','count'}]}` (v1 UI defers facet chips)
- `jobs_histogram(dimension, ...)` → dimension ∈
  `wait | nodes | cpus | gpus | memory | duration` (`memory` = REQUESTED
  bytes, `reqmem`):
  ```python
  {"dimension": "wait", "column": "eligible_secs", "unit": "seconds",
   "min_param": "min_eligible_secs", "max_param": "max_eligible_secs",
   "buckets": [{"label": "<1m", "lo": 0, "hi": 59,
                "job_count": 12, "cpu_hours": 190.0, "gpu_hours": 0.0}, ...],
   "null_count": 2481,      # rows matching filters with NULL column
   "total_count": 40213}    # == jobs_count(**filters), guaranteed
  ```
  Full bucket vector in band order, zeros included → stable chart x-axes.
  Bar drill-down: `jobs_search(**{min_param: lo, max_param: hi})`, omit max
  when `hi is None`. **Never hardcode the dimension→kwarg map** — use
  `min_param`/`max_param` from the envelope.
- `jobs_usage_by(dimension, ..., limit=None)`:
  ```python
  {"dimension": "user",
   "rows": [{"value": "alice", "job_count": 812,
             "cpu_hours": 91234.5, "gpu_hours": 120.0}, ...],  # hours desc
   "totals": {"job_count": ..., "cpu_hours": ..., "gpu_hours": ...}}
  ```
  `totals` is pre-truncation → pie "Other" slice = totals − Σ rows.
  No self-exclusion; `account` scoping always applies (security boundary).

**Costs (dev PG, 308k-row month, machine-wide, warm):** histogram ~570 ms,
usage_by ~545 ms, facets ~150 ms, unbounded window ~200 s. Always bound the
window; the SAM cache layer (below) is mandatory for the card fragments.

**Wait-data facts for UI copy:** use `eligible_secs` (never start−submit);
NULL = unmeasured, not zero; Derecho waits recorded only from
**2025-01-07 17:47:50 UTC** (Casper complete). Caption when
`null_count > 0`: "N jobs in this range have no wait measurement (queued
before eligible-time collection began, early 2025 on Derecho) and are
excluded."

## Breaking renames SAM must absorb (Commit 1, same deploy as plugin)

1. `COLUMNS` import — `job_history.cli.search.columns` is GONE:
   `from job_history import COLUMNS` at `src/webapp/jobs/routes.py:312`
   (`_load_column_specs`).
2. `has_gpus` kwarg REMOVED → `min_gpus`/`max_gpus`:
   `src/webapp/jobs/service.py` (~6 sites incl. the fast-path predicate at
   :195 — switch it, never drop it). Do NOT touch the unrelated `has_gpus`
   in `system_status/`, `dashboards/charts.py`, `status/` blueprint+templates.
3. `status` → `exit_status` (kwarg AND row/COLUMNS key):
   `service.py:137`, `routes.py:68` (`_VERBOSE_EXTRAS`) and `:172`,
   `src/cli/accounting/commands.py:1788` (`JOB_COLUMNS`), plus the
   `jobs_fragment.html` filter field. DB column/ORM attr unchanged upstream.

Newly surfaceable columns: `queued`, `eligible_secs`, `run_count`.

## Decisions already made (do not re-litigate)

- Card = **5 tabs**: Jobs · By User (usage pie; hidden in user mode) ·
  Wait Times · Job Sizes (nodes/cpus/gpus/memory pills) · Durations.
- New global permission `VIEW_ALL_JOB_DATA` (mirrors
  `VIEW_ALL_FILESYSTEM_DATA`; auto-joins operator bundles via `ALL_VIEW`;
  NOT facility-scoped).
- Machine-wide routes say `machine/<machine>` (plugin machines `derecho`,
  `casper`), not `resource/` — `derecho` covers SAM's Derecho + Derecho GPU.
- ONE SAM PR to staging, ordered commits below.
- Facet chips, By-Project user-mode tab, clickable histogram bars: deferred
  (record in this doc's follow-ups on completion).

## Commit series (structural template = `src/webapp/disk_scans/` throughout)

### Commit 1 — absorb plugin breaking renames (mechanical, UI-identical)
Files: `src/webapp/jobs/routes.py`, `src/webapp/jobs/service.py`,
`src/cli/accounting/commands.py`, `templates/dashboards/user/partials/jobs_fragment.html`,
`tests/unit/test_webapp_jobs.py` (mock kwargs), `tests/unit/test_sam_search_jobs_cli.py`.

### Commit 2 — RBAC + config + session
- `src/webapp/utils/rbac.py`: `VIEW_ALL_JOB_DATA = "view_all_job_data"`
  right after `VIEW_ALL_FILESYSTEM_DATA` (:110), mirrored comment block.
- `src/webapp/config.py`: `JOB_HISTORY_STATEMENT_TIMEOUT_MS` (default 60000).
- `src/webapp/jobs/session.py`: `_attach_application_name` →
  `_apply_connection_settings(engine, app_name, *, statement_timeout_ms)`
  (copy the autocommit-toggle body from `disk_scans/session.py:184-221`).
- `src/webapp/jobs/service.py`: `job_history_machines()` → sorted engine
  keys when enabled (analogue of `scan_capable_resources()`).
- Tests: `test_view_all_job_data_grants` (in nusd/csg/ssg bundles, NOT in
  sureshm's WNA facility set — mirror `test_webapp_disk_scans.py:1594`),
  statement-timeout listener, machines helper.

### Commit 3 — service mode families + extended filters + TTL cache
- `service.py`: one `_plugin_filter_kwargs` normalizer (wraps
  `_resolve_queue_and_qos` + flat passthrough of the full filter set);
  `search_jobs`/`count_jobs` keep project pinning and gain the extended
  filters (SAM-summary count fast path ONLY when every extended filter is
  None: qos, exit_status, name, all min/max bounds);
  `search_jobs_machine`/`count_jobs_machine` (NO account filter — docstring
  warns caller must be VIEW_ALL_JOB_DATA-gated);
  `search_jobs_user`/`count_jobs_user` (hard-pin `user=username`, raise on
  empty username, reject a user kwarg in filters);
  cached `jobs_histogram(machine, dimension, *, account_projcodes=None,
  username=None, **filters)` and `jobs_usage_by_user(machine, *, limit=50,
  **filters)` (plugin `jobs_usage_by('user', …)`; "Other" = totals − Σrows).
- NEW `src/webapp/jobs/cache.py` — clone `disk_scans/cache.py` minus the
  scan-date signature (pure TTL): bucket `historical` (`JOBS_CACHE_TTL`
  21600 s / 256 entries; windows with `end < today`) and `recent`
  (`JOBS_RECENT_CACHE_TTL` 900 s / 128; window touches today). Cache ONLY
  the aggregations; paged search + counts stay uncached.
  Key: `(query_type, machine, sorted(normalized opts))`.
- `src/webapp/caching/__init__.py`: register in `adapters()`,
  `stats()['jobs']`, `clear('jobs')`; admin configuration card category.
- NEW `tests/unit/test_webapp_jobs_cache.py`.

### Commit 4 — chart generators + sentinel handler
- `src/webapp/dashboards/charts.py` (near the disk pie section):
  `generate_jobs_histogram(hist, *, metric='jobs')` — single-series bars
  from `hist['buckets']` (metric picks `job_count`/`cpu_hours`/`gpu_hours`),
  `@caching.chart_cached(name='jobs_histogram', maxsize=128, key_fn=...)`
  hashing (labels, metric values, dimension, null_count, metric),
  `_empty_state('No jobs in this range')` when all-zero;
  `generate_jobs_user_pie_chart(entity_data, metric='cpu_hours')` — reuse
  `_pie_cumulative_keep` (0.90 share / 9 cap), wedge+legend
  `set_url(f'#job-user-{username}')`, inert "Other" slice built from
  totals − Σ rows.
- `src/webapp/static/js/svg-chart-links.js`: `#job-user-` prefix →
  existing `openEntityRow` with `data-job-user` rows scoped to `.tab-pane`.
- NEW `tests/unit/test_webapp_jobs_charts.py`.

### Commit 5 — 5-tab card (project mode) on resource details
- NEW templates under `templates/dashboards/user/partials/`:
  `jobs_card.html` (mode-parameterized `project|machine|user`, params
  `mode, cid, tablist_id, machine, load_trigger` + project-only
  `projcode, scope`; structural clone of `disk_scans_card.html:25-43`
  URL-resolution block + tab strip; "Open full view ↗" per mode; By User
  omitted when `mode == 'user'`);
  `_jobs_macros.html` (`jobs_disabled/error/empty`, `mode_badge`,
  `exit_status_badge`: 0 → green "Success", N → red "Failed (N)", None →
  muted em-dash, raw code in title=);
  `jobs_by_user.html` (metric pills Jobs/CPU-h/GPU-h via `?metric=`,
  `{{ pie_svg | safe }}`, rows `tr[data-job-user=…]` with collapse drill →
  mode's Jobs fragment filtered `?user=…`, hidden params form);
  `jobs_histogram.html` (shared by Wait Times / Job Sizes / Durations;
  dimension pills nodes/cpus/gpus/memory on Sizes only; Wait pins
  `dimension='wait'`, Durations pins `'duration'`; bucket table under the
  SVG; the null_count caption).
- `src/webapp/jobs/routes.py`: project fragments `by_user_fragment` /
  `wait_times_fragment` / `job_sizes_fragment` / `durations_fragment`;
  `_parse_job_filters()` (whitelisted GET parse, wait-hours→secs at the
  boundary); shared `_render_by_user` / `_render_histogram` helpers taking
  explicit scope args (model: `disk_scans/routes.py` ctx helpers). Jobs tab
  reuses `jobs.jobs_fragment` unchanged.
- `templates/dashboards/user/resource_details.html`: collapsible "Job
  History" card after Usage by User (~:511), `{% if jobs_machine %}`, with
  `{% with mode='project', cid='jobs-hist', tablist_id='jobsCardTabs',
  machine=jobs_machine, load_trigger='shown.bs.tab once,
  shown.bs.collapse once from:#collapseJobs' %}`.
- `src/webapp/dashboards/user/blueprint.py`: delete the inline machine
  resolution at :617-623 in favor of `_resolve_jobs_machine` (:686).

### Commit 6 — explorer full view + machine mode + Status tab
- NEW `templates/dashboards/user/jobs_explore_page.html` +
  `partials/_jobs_filters.html` (macro `jobs_filters(form_id, fragment_url,
  target_id, filters, user_search_url, machine, scope, per_page_options,
  mode)`; sidebar: date range, `fk_search_field` user picker (project/
  machine modes only), queue text, QoS select from `list_qos_names`, exit
  code text ("0 = success" hint), name glob + ignore-case switch,
  min/max nodes/cpus/gpus, wait-hours range, rows selector; wrapped in
  `.filter-sidebar`; clone of `disk_scans_directories_page.html` +
  `_disk_scans_dir_filters.html`). Project mode: `?scope=<child>` re-root
  with the same same-tree_root validation as `disk_scans/routes.py:70-84`.
- `jobs/routes.py`: `explore_page` (project) + machine family
  `/machine/<machine>/{jobs,by-user,wait-times,job-sizes,durations,explore}`
  gated `@require_permission(Permission.VIEW_ALL_JOB_DATA)`; `<machine>`
  validated against `job_history_machines()` → 404 unknown.
- `jobs_fragment.html`: tolerate `project=None` (machine/user modes).
- Status tab: `dashboards/status/blueprint.py` route `job_history`
  (mirror `filesystem_scans` :203-212) + `_page_context` gains
  `job_history_machines`; `templates/dashboards/status/job_history_page.html`
  + `partials/job_history.html` (nav-pill per machine, card mode='machine',
  first pane `load once`, clones of the filesystem_scans pair);
  `base_status.html` page_tabs entry (icon `fas fa-list-check`, visible =
  machines AND `has_permission(VIEW_ALL_JOB_DATA)`); `utils/nav.py`
  `_can_view_job_history` + status-section item.
- Route-map snapshot regen: `ROUTE_MAP_REGEN=1 pytest
  tests/unit/test_route_map_parity.py` (adds `status_dashboard.job_history`).

### Commit 7 — My Jobs (user mode)
- `jobs/routes.py`: user family
  `/user/<machine>/{jobs,wait-times,job-sizes,durations,explore}`,
  `@login_required` ONLY; `_user_ctx` pins
  `forced_user=current_user.username`; render helpers overwrite any
  client-supplied `user` (mirror `disk_scans/routes.py:361-377,392-395`).
- `dashboards/user/blueprint.py`: `my_jobs` route at `/user/jobs` (404
  unless machines; mirror `my_data` :155-162) + `_page_context` keys
  `my_jobs_available`, `job_history_machines`.
- NEW `templates/dashboards/user/my_jobs.html` + `partials/my_jobs_card.html`
  (machine nav-pills, card mode='user'; clones of my_data pair);
  `base_user.html` "My Jobs" tab (icon `fas fa-list-check`, visible:
  `my_jobs_available`); `nav.py` `_my_jobs_available`.
- Route-map regen (adds `user_dashboard.my_jobs`).
- Tests: client `?user=` ignored in ALL user fragments (mirror
  `test_user_directories_pins_owner_ignoring_query`), 404 when no machines,
  tab visibility, explorer omits user picker.

### Commit 8 — docs
Update THIS file to as-built status (final route table, contract drift,
deferred follow-ups); `src/webapp/README.md` `jobs/` line.

## Route table (all new; `jobs` bp prefix `/dashboards/user/jobs`, all `@login_required`)

| Endpoint | Rule | Extra gate |
|---|---|---|
| `jobs.{by_user,wait_times,job_sizes,durations}_fragment`, `jobs.explore_page` | `/<projcode>/{by-user,wait-times,job-sizes,durations,explore}` | `@require_project_access` |
| `jobs.{jobs,by_user,wait_times,job_sizes,durations}_machine_fragment`, `jobs.explore_machine_page` | `/machine/<machine>/{jobs,by-user,wait-times,job-sizes,durations,explore}` | `@require_permission(VIEW_ALL_JOB_DATA)` |
| `jobs.{jobs,wait_times,job_sizes,durations}_user_fragment`, `jobs.explore_user_page` | `/user/<machine>/{jobs,wait-times,job-sizes,durations,explore}` | user pinned server-side |
| `status_dashboard.job_history` | `/status/job-history` | `VIEW_ALL_JOB_DATA` (+ tab visible w/ machines) |
| `user_dashboard.my_jobs` | `/user/jobs` | 404 unless machines available |

Only the last two touch the route-map parity snapshot (the `jobs` blueprint
is not pinned).

## Test inventory

Extend `tests/unit/test_webapp_jobs.py` (TestingConfig keeps
`JOB_HISTORY_MACHINES=[]`; mock via the file's existing monkeypatch
patterns). New: `test_webapp_jobs_cache.py`, `test_webapp_jobs_charts.py`.
Per-commit lists are embedded above. Full sweep before the PR:
`pytest tests/unit/test_webapp_jobs.py tests/unit/test_webapp_jobs_cache.py
tests/unit/test_webapp_jobs_charts.py tests/unit/test_webapp_disk_scans.py
tests/unit/test_rbac.py tests/unit/test_route_map_parity.py`, then full
`pytest`.

## Playwright smoke checklist (Session 2, webdev :5050)

Personas via Quick Login: `benkirk` (full operator), a plain project user,
`sureshm` (WNA facility-scoped — must NOT see operator surfaces),
`dlawren` (PROJ_TREE_LEAD).

1. Resource details (`/user/resource-details/SCSG0001?resource=Derecho`):
   Job History card present, collapsed; expand → 5 tabs lazy-load on first
   show only; Jobs tab pagination/sort; By User pie wedge click expands the
   matching user row; Wait/Sizes/Durations pills re-fetch; Sizes dimension
   pills switch; wait caption absent post-2025 (Derecho) — check a 2024
   Casper window shows counts, and a 2024 Derecho window shows the caption.
2. "Open full view ↗" lands on the explorer with filters carried; project
   mode `?scope=<child>` re-roots; deep-link with filters reloads state.
3. `/status/job-history`: visible+working for benkirk (derecho/casper
   pills); tab hidden AND direct URL 403 for plain user and for sureshm.
4. `/user/jobs`: shows own jobs only; appending `?user=<other>` to fragment
   URLs changes nothing; tab hidden when no unix identity is irrelevant
   here (username always exists) but hidden when machines unavailable.
5. Admin → Configuration: `jobs` cache buckets listed; clear works.
6. `docker compose exec` psql: `pg_stat_activity` shows
   `sam-webapp:…:job_history:<machine>` tagging and `statement_timeout`.

## Deferred follow-ups (confirmed deferred as of 2026-07-27)

- `jobs_facets` filter chips in the explorer (cost: self-exclusion plan
  flips measured 4.5×; needs a default window policy first).
- By-Project tab in user mode (needs nothing new server-side —
  `jobs_usage_by('account', user=<me>)` — UI decision only).
- Clickable histogram bars (drill via `min_param`/`max_param` envelope,
  mirroring the disk band-drill; the envelope's self-describing
  min/max_param fields are already threaded through to the template
  context, so this is chart + JS work only).
- `memory_used` histogram dimension upstream if ever asked (one spec row).
- Explorer inputs for elapsed/reqmem bounds (`min/max_elapsed`,
  `min/max_reqmem` are already accepted by the service normalizer; only
  panel fields + roundtrip keys are missing).
- **Pre-existing, discovered via CI**: `RedisTTLAdapter` namespaces keys
  and `clear()`/`info()` scans by `prefix` only, and the usage cache AND
  both fs_scans buckets all sit on the default `'usage:'` prefix — so on
  Redis, `clear('usage')` / `clear('scans')` wipe each other's entries
  and their `info()` counts merge. The jobs buckets now pass distinct
  `prefix=<name>:` values; migrating fs_scans/usage the same way is a
  separate PR (existing 'usage:' keys orphan until TTL at cutover).
