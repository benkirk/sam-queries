# Job History Dashboard — bring job history to fs_scans feature parity

## Context

SAM's disk-scans (fs_scans plugin) feature set — tabbed resource-details card, standalone
"Open Full View" explorer, operator-gated Status tab, and per-user "My Data" tab — proved
out a clean architecture: a mode-parameterized card partial (`project` / `resource` /
`user`), matplotlib→inline-SVG charts with click-through sentinels, TTL/Redis caching,
and a single global `view_*` operator permission. Job history today has only a single
project-scoped collapse-drawer fragment (`jobs.jobs_fragment`) inside the resource-details
usage tables.

Goal: replicate the full fs_scans surface for job history on Derecho + Casper, backed by
the `job_history` plugin from hpc-usage-queries. The peer repo's open **PR #99**
(`jobs_plugin_search_drilldown`, head `a3ca191`, targets `staging`) is explicitly the
plugin-side groundwork (name globs, wait/resource range filters, `jobs_facets()`, plus
three deliberate breaking renames) and will be advanced in tandem.

## Decisions (made with Ben)

1. **Card = 5 tabs**: Jobs (search table) · By User (clickable usage pie) · Wait Times
   (histogram) · Job Sizes (histogram with nodes/CPUs/GPUs/memory dimension pills) ·
   Durations (dedicated elapsed-time histogram tab).
2. **Plugin aggregation support lands as additional commits directly on PR #99**
   (`jobs_histogram(dimension, …)` + by-user usage aggregation sharing
   `_apply_jobs_search_filters`).
3. **New `Permission.VIEW_ALL_JOB_DATA`** gates machine-wide explorer + Status tab
   (mirrors `VIEW_ALL_FILESYSTEM_DATA`; `view_*` naming auto-joins operator ALL_VIEW bundles).
4. **One SAM PR to staging, ordered commits**: rename absorption → service/cache →
   card → explorer → Status tab → My Jobs.
5. **Two-session execution with a hard pause after PR #99 is updated** — context gets
   cleared and the SAM-side session restarts with Playwright available for browser smoke
   testing. The restart handoff is a committed doc in the SAM repo (house convention).
6. **Claude runs the test suites directly this time** (both repos) — pytest execution is
   explicitly authorized for this work.

## Execution phasing — two sessions, hard pause between

### Session 1 (this session): plugin side + handoff
1. In `/Users/benkirk/codes/hpc-usage-queries/devel` (branch `jobs_plugin_search_drilldown`
   already checked out): implement plugin Commits 0–3 below; run
   `pytest job_history/tests/` after each commit; run the timed end-to-end measurements
   against local dev PG and write the figures into docstrings + plan doc.
2. Push the branch (updates PR #99). Do NOT merge — Ben reviews.
3. In SAM: write and commit `docs/plans/JOB_HISTORY_DASHBOARD.md` on `job_history_expansion`
   as the restart/handoff doc. It must be self-sufficient for a fresh session: the full
   SAM commit series (§SAM-side plan below), the **as-landed** plugin contract (actual
   signatures + return shapes + the pinned branch-tip sha), the route/template/cache/chart
   inventories, RBAC matrix, test inventory, Playwright smoke checklist, and "how to
   resume" preamble (branch names, rebuild commands, `make print-env-hash`).
4. Update the session memory (project memory pointing at the handoff doc + in-flight
   state), report the PR #99 tip sha to Ben, and STOP.

### Between sessions (Ben)
- Review/approve the updated PR #99 (merge can wait — local dev pins the sha).
- Rebuild local containers + env against the tip:
  `HPC_USAGE_QUERIES_REF=<sha> docker compose build webdev` and
  `HPC_USAGE_QUERIES_REF=<sha> source etc/config_env.sh`.
- Restart Claude with cleared context + Playwright; point it at
  `docs/plans/JOB_HISTORY_DASHBOARD.md`.

### Session 2 (fresh context + Playwright): SAM side
- Execute SAM Commits 1–8 from the handoff doc; run pytest directly after each commit
  (`tests/unit/test_webapp_jobs*.py` etc., full `pytest` before the PR).
- Browser verification via Playwright against webdev (:5050) using Quick Login personas:
  the manual smoke list in §Verification becomes a scripted walk (card tabs lazy-load,
  pie wedge → row expand, pills re-fetch, explorer deep links, RBAC visibility for
  operator vs plain user, My Jobs `?user=` pinning).
- Open the SAM PR to staging; plugin PR #99 merges first, SAM PR after.

## Cross-repo coordination

- PR #99's three breaking changes SAM absorbs in the same deploy:
  1. `COLUMNS` import moves to package root (`from job_history import COLUMNS`) —
     SAM site `src/webapp/jobs/routes.py:312`
  2. `has_gpus` removed → `min_gpus`/`max_gpus` — `src/webapp/jobs/service.py`
     (6 sites incl. the count-fast-path predicate); do NOT touch unrelated
     `has_gpus` in system_status/charts/status templates
  3. `status` → `exit_status` (kwarg + COLUMNS/row key) — `service.py:137`,
     `routes.py:68,172`, `src/cli/accounting/commands.py:1788` (`JOB_COLUMNS`)
- Merge order: plugin PR #99 (extended) merges first (→ staging, then #98 staging→main),
  SAM PR after; local dev pins `HPC_USAGE_QUERIES_REF=<sha>` for containers
  (`docker compose build webdev`) and the hash-keyed conda env.
- Wait-time facts baked into UI copy: use `eligible_secs` (never start−submit);
  NULL = unmeasured; Derecho waits exist only from 2025-01-07 onward → caption the
  excluded count.

## Plugin-side plan (additional commits on PR #99, `jobs_plugin_search_drilldown`)

Repo: `/Users/benkirk/codes/hpc-usage-queries/devel` (branch checked out, head `a3ca191`).
Full design details in the repo's plan doc after Commit 0. Key decisions: memory dimension
buckets **`reqmem` (requested), raw bytes**; hours always included (LEFT OUTER JOIN
`job_charges`, no opt-in flag); full bucket vector returned including zeros; NULLs counted
in-statement via a `__null__` CASE label → top-level `null_count`; by-user usage is a new
`jobs_usage_by()` (NOT `jobs_facets(include_hours=…)` — facet self-exclusion semantics are
wrong for a usage pie); API-only, no CLI surface for the new aggregations; dimension names
are SAM-facing and each histogram response is self-describing (`column`, `unit`,
`min_param`, `max_param`) so SAM never hardcodes dimension→filter maps.

### Commit 0 — `[skip-ci]` amend `docs/plans/JOB_HIST_PLUGIN_ENHANCEMENTS.md`
New §8 "Aggregation surface (added in-flight)"; supersede the "out of scope:
include_hours facet variant" bullet; add the 4 new filters to §2; placeholder rows in the
measured-cost table (filled by real measurements at the end).

### Commit 1 — complete the range-filter set: `min/max_elapsed` + `min/max_reqmem`
So duration and memory histogram bars can round-trip into `jobs_search` drill-down filters
(fs_scans band-drill precedent). Table-driven rows in `_apply_jobs_search_filters`
(no-defaults parity forces completeness); kwargs on `jobs_search`/`jobs_count`/`jobs_facets`;
CLI flags `--min/max-elapsed-hours` (hours→secs) + `--min/max-reqmem-gb` (GB→bytes) with
envelope `filters` entries; NULL-strict, inclusive, `is not None` guards. Tests incl.
`max_elapsed=0` falsy-zero guard; README filter bullets.

### Commit 2 — `jobs_histogram(dimension, *, <full filter set>)` + bucket tables
- New `(label, lo, hi)` bucket tables on `QueryConfig`: `WAIT_BUCKETS` (12 log-scaled:
  `<1m … >2d`), `DURATION_HIST_BUCKETS` (legacy 7 labels; rewrite `get_duration_buckets()`
  to derive from it — single source of truth), `NODE_HIST_BUCKETS` (derived from
  NODE_RANGES), `CPU_HIST_BUCKETS` (13 buckets, ×4 tail to >32768 — CORE_RANGES' >128
  overflow would swallow multi-node jobs), `GPU_HIST_BUCKETS` (11 buckets incl. explicit
  `"0"` — GPU_RANGES starts at 4), `REQMEM_HIST_BUCKETS` (7 GiB-boundary byte bands incl.
  the `<1GB` band the legacy CASE mislabels)
- `_bucket_case(field, buckets)`: NULL-first WHEN → `__null__`, then ascending `<= hi`
  ladder (no below-range leak into overflow); `_HISTOGRAM_SPECS` dimension table
  (`wait|nodes|cpus|gpus|memory|duration`)
- One statement: CASE label + `COUNT(Job.id)` + `SUM(cpu_hours)` + `SUM(gpu_hours)` over
  OUTER JOIN `job_charges`, through `_apply_jobs_search_filters`, `GROUP BY` the label;
  Python fold zero-fills. Returns `{dimension, column, unit, min_param, max_param,
  buckets:[{label,lo,hi,job_count,cpu_hours,gpu_hours}…], null_count, total_count}` with
  `total_count == jobs_count(**filters)` by construction.
- Tests: full-vector/zero-fill, per-dimension bucketing, hours sums (charge-less job → 0.0),
  null routing, **bounds-round-trip invariant across all 6 dimensions** (forces Commit 1),
  reqmem-not-memory, gpus-0 bucket, account-scope, one-aggregate-scan guard (no hybrid
  subqueries), signature-parity extension, offline dialect compiles of `_bucket_case`.

### Commit 3 — `jobs_usage_by(dimension, *, <full filter set>, limit=None)`
Group the integer FK from `_FACET_SPECS` (never hybrids), OUTER JOIN charges, resolve
names post-aggregation. No self-exclusion of any kind — every filter incl. `account`
always applies (docstring cross-refs `_FACET_SCOPE_DIMS`). Rows sorted
`(cpu_hours+gpu_hours)` desc; `limit` truncates post-sort; **`totals` computed before
truncation** so SAM's pie "Other" = totals − sum(rows). Returns
`{dimension, rows:[{value, job_count, cpu_hours, gpu_hours}…], totals:{…}}`. Tests incl.
account-scoping security mirror, totals invariants, one-scan guard, parity.

### Plugin verification
`pytest job_history/tests/` green; timed end-to-end on local dev PG (13M-row derecho,
one-month window) with measured figures written into the docstrings + plan-doc table;
one PG `EXPLAIN` to confirm one-statement; SAM-venv smoke with a real projcode scope.

## SAM-side plan (one PR from `job_history_expansion` → staging, 8 ordered commits)

Structural template throughout: `src/webapp/disk_scans/` + its templates. Key naming
decision: machine-wide routes use `/machine/<machine>/…` (not `resource/`) because they
key on plugin machines (`derecho` covers Derecho + Derecho GPU). The `jobs` blueprint is
NOT in the route-map parity snapshot — only the two new page routes
(`status_dashboard.job_history`, `user_dashboard.my_jobs`) need `ROUTE_MAP_REGEN=1`.

### Commit 1 — absorb plugin breaking renames (mechanical, UI-identical)
- `src/webapp/jobs/routes.py`: `_load_column_specs` → `from job_history import COLUMNS`;
  `_VERBOSE_EXTRAS` + request arg `status`→`exit_status`
- `src/webapp/jobs/service.py`: drop `has_gpus` → `min_gpus`/`max_gpus`; `status`→`exit_status`;
  count fast-path predicate switches to new kwargs (never silently drops)
- `src/cli/accounting/commands.py`: `JOB_COLUMNS` + `--status`→`--exit-status`
- `jobs_fragment.html` hidden field/badge; test mocks in `tests/unit/test_webapp_jobs.py`
- Do NOT touch unrelated `has_gpus` in system_status/charts/status templates

### Commit 2 — RBAC + config + session
- `utils/rbac.py`: `VIEW_ALL_JOB_DATA = "view_all_job_data"` after VIEW_ALL_FILESYSTEM_DATA
  (global view_*, auto-joins nusd/csg/ssg via ALL_VIEW; not facility-scoped)
- `config.py`: `JOB_HISTORY_STATEMENT_TIMEOUT_MS` (default 60000)
- `jobs/session.py`: `_attach_application_name` → `_apply_connection_settings(engine, tag, *,
  statement_timeout_ms)` (copy disk_scans autocommit-toggle body)
- `jobs/service.py`: `job_history_machines()` (sorted engine keys; analogue of
  `scan_capable_resources()`)
- Tests: `test_view_all_job_data_grants` (in nusd/csg/ssg, NOT in sureshm's WNA set),
  statement-timeout attach, machines helper

### Commit 3 — service mode families + extended filters + TTL cache
- `service.py`: shared flat filter kwargs (`start,end,user,queue,qos,exit_status,name,
  ignore_case,min/max_{nodes,cpus,gpus},min/max_eligible_secs`) via one `_plugin_filter_kwargs`
  normalizer; new `search_jobs_machine`/`count_jobs_machine` (no account filter — caller must be
  permission-gated), `search_jobs_user`/`count_jobs_user` (hard-pin `user=username`, reject
  user kwarg), plus cached wrappers `jobs_histogram(machine, dimension, …)` and
  `jobs_usage_by_user(machine, …)` (calls plugin `jobs_usage_by('user', …)`; pie "Other"
  slice value = `totals` − sum of returned rows).
  SAM-summary count fast path only when EVERY extended filter is None.
- New `webapp/jobs/cache.py` — clone of `disk_scans/cache.py` minus scan-date signature (pure
  TTL): bucket `historical` (`JOBS_CACHE_TTL` 6 h/256, for closed windows `end < today`) and
  `recent` (`JOBS_RECENT_CACHE_TTL` 15 min/128, window touches today). Cache only the
  aggregations (histogram/by-user); paged search + counts stay uncached.
- `caching/__init__.py`: register buckets in `adapters()`/`stats()['jobs']`/`clear('jobs')`;
  admin configuration card gets the category.

### Commit 4 — chart generators + sentinel handler
- `dashboards/charts.py`: `generate_jobs_histogram(hist, *, metric='jobs')` (single-series
  bars, `@caching.chart_cached(name='jobs_histogram')`, `_empty_state` when all-zero) and
  `generate_jobs_user_pie_chart(entity_data, metric='cpu_hours')` (reuses
  `_pie_cumulative_keep` 0.90/9-cap; wedge+legend `set_url(f'#job-user-{username}')`;
  inert Other slice)
- `static/js/svg-chart-links.js`: `#job-user-` prefix → existing `openEntityRow` with
  `data-job-user` rows, scoped to `.tab-pane` (same interaction as the disk pie)
- New `tests/unit/test_webapp_jobs_charts.py`

### Commit 5 — 5-tab card (project mode) wired into resource details
- New templates: `user/partials/jobs_card.html` (mode-parameterized project|machine|user,
  cid-namespaced, 5 tabs: Jobs · By User · Wait Times · Job Sizes · Durations; By User tab
  hidden in user mode, "Open full view ↗"), `_jobs_macros.html`
  (`jobs_disabled/error/empty`, `exit_status_badge` — 0=green Success, N=red Failed(N)),
  `jobs_by_user.html` (metric pills Jobs/CPU-h/GPU-h + pie + drill-down rows
  `tr[data-job-user=…]` expanding a user-filtered Jobs fragment), `jobs_histogram.html`
  (one shared partial for Wait Times, Job Sizes, AND Durations; dimension pills
  nodes/cpus/gpus/memory on Sizes only — Wait Times pins `dimension='wait'`, Durations
  pins `dimension='duration'`; NULL-wait exclusion caption when `null_count > 0`: "N jobs
  have no wait measurement — queued before eligible-time collection began, early 2025 on
  Derecho")
- `jobs/routes.py`: project fragments `by_user_fragment`/`wait_times_fragment`/
  `job_sizes_fragment`/`durations_fragment` + `_parse_job_filters()` (whitelisted GET
  parse, wait-hours→secs) + shared `_render_*` helpers; Jobs tab reuses existing
  `jobs.jobs_fragment`
- `resource_details.html`: new collapsible "Job History" card after Usage by User, gated
  `{% if jobs_machine %}`; consolidate the duplicated inline `_resolve_jobs_machine` copy in
  `user/blueprint.py:617-623`

### Commit 6 — explorer full view + machine mode + Status tab
- New: `user/jobs_explore_page.html` + `partials/_jobs_filters.html` (sidebar: date range,
  `fk_search_field` user picker [not in user mode], queue, QoS select, exit code, name glob +
  ignore-case, min/max nodes/cpus/gpus, wait-hours range, per-page; project mode gets
  `?scope=` tree re-rooting + scope badge strip). Facet chips (`jobs_facets`) deferred to
  follow-up (self-exclusion plan-flip 4.5× / unbounded ~200 s risk).
- `jobs/routes.py`: `explore_page` (project) + full machine family (`/machine/<machine>/{jobs,
  by-user,wait-times,job-sizes,durations,explore}`) gated
  `@require_permission(VIEW_ALL_JOB_DATA)`; `<machine>` validated against
  `job_history_machines()`, 404 otherwise
- `jobs_fragment.html` tolerates `project=None` (machine/user modes)
- Status tab: `status/blueprint.py` route `job_history` + `_page_context` machines;
  `base_status.html` page_tabs entry (icon `fas fa-list-check`, visible = machines AND
  permission); `partials/job_history.html` (nav-pill per machine, card mode='machine');
  `utils/nav.py` `_can_view_job_history`; route-map snapshot regen

### Commit 7 — My Jobs (user mode)
- `jobs/routes.py`: user family
  (`/user/<machine>/{jobs,wait-times,job-sizes,durations,explore}`), `@login_required`
  only; `_user_ctx` pins `forced_user=current_user.username`, render helpers overwrite any
  client-supplied `user` (mirror disk_scans pinned-owner)
- `user/blueprint.py`: `my_jobs` route at `/user/jobs` (404 unless machines available) +
  `_page_context` keys; `base_user.html` "My Jobs" page_tabs entry; `nav.py`
  `_my_jobs_available`; new `user/my_jobs.html` + `partials/my_jobs_card.html` (machine
  pills, card mode='user'); route-map snapshot regen
- Tests: client-supplied `?user=` ignored in all user fragments; 404 when no machines

### Commit 8 — docs
- `docs/plans/JOB_HISTORY_DASHBOARD.md` (created in Session 1 as the handoff doc): update
  to as-built status — final route table, any contract drift, deferred follow-ups (facet
  chips, By-Project user-mode tab, clickable histogram bars); `src/webapp/README.md`
  jobs/ line update

### Route table (new)

| Endpoint | Rule | Gate |
|---|---|---|
| `jobs.by_user_fragment` / `.wait_times_fragment` / `.job_sizes_fragment` / `.durations_fragment` / `.explore_page` | `/dashboards/user/jobs/<projcode>/{by-user,wait-times,job-sizes,durations,explore}` | `@require_project_access` |
| `jobs.*_machine_fragment` ×5 + `.explore_machine_page` | `/dashboards/user/jobs/machine/<machine>/{jobs,by-user,wait-times,job-sizes,durations,explore}` | `@require_permission(VIEW_ALL_JOB_DATA)` |
| `jobs.*_user_fragment` ×4 + `.explore_user_page` | `/dashboards/user/jobs/user/<machine>/{jobs,wait-times,job-sizes,durations,explore}` | `@login_required`, user pinned |
| `status_dashboard.job_history` | `/status/job-history` | `VIEW_ALL_JOB_DATA` |
| `user_dashboard.my_jobs` | `/user/jobs` | login; 404 if unavailable |

## Verification

1. Rebuild against the plugin branch tip:
   `HPC_USAGE_QUERIES_REF=<sha> docker compose build webdev` (+ hash-keyed conda env via
   `HPC_USAGE_QUERIES_REF=<sha> source etc/config_env.sh`; `make print-env-hash` to confirm)
2. `pytest tests/unit/test_webapp_jobs.py tests/unit/test_webapp_jobs_cache.py
   tests/unit/test_webapp_jobs_charts.py tests/unit/test_webapp_disk_scans.py
   tests/unit/test_rbac.py tests/unit/test_route_map_parity.py`, then full `pytest` —
   run by Claude directly (authorized for this work; mysql-test container on :3307)
3. Playwright smoke on webdev (:5050): HPC resource-details → Job History card (5 tabs lazy-load,
   pie wedge expands user row, pills re-fetch, Open Full View lands filtered); explorer
   `?scope=` re-roots; `/status/job-history` per-machine pills + hidden for non-operators
   (Quick Login); `/user/jobs` shows own jobs only, `?user=other` ignored; Admin →
   Configuration shows the jobs cache buckets; `pg_stat_activity` shows tagging + timeout
4. `grep -rn has_gpus src/ | grep -v webapp/jobs` → only the unrelated system_status/charts/
   status-template sites remain
