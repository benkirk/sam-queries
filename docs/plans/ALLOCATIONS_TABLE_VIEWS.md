# Allocations dashboard: an at-date table and a calendar view

**Status: in progress (2026-10-03)** · branch `allocations-table-views` (from
`origin/staging` after #706 merged) · one PR, one commit per stage below.

## Context

The `/allocations/projects` resource tabs pair two charts with one tree table (facility → type →
lazy project drill). Since #706 the Used ring is a trailing-window rate, and the table no longer
reads coherently: "Used"/"Use share" are since-allocation-start and sit next to a windowed ring.
Worse, for a **past** view-at date the table and drill count charges *after* that date
(`allocations.py:1161`, `end_date = alloc.end_date or check_date`), so "as of" is not true.

Ben's direction (2026-10-03): two table views under each resource tab.
1. **At-date table**: the allocations intersected by the view-at date, coherent: start, end,
   allocated, used, remaining, all as of that date.
2. **Calendar**: wide, horizontally panning; months as columns (past and future), project codes as
   rows, each allocation a date-aligned bar holding a tight usage meter (past = final use,
   current = use so far, future = empty). Bar widths follow real dates, not a fixed month width.

Decisions taken: one meter per allocation now, monthly burn strip later; calendar rows nest in the
same facility → type tree, project rows lazily per type; a persisted **Table | Calendar** switch
under each resource tab (not a new route/top-level tab); at-date columns replace Avg + Use share.

Data facts: ~1,100 active Derecho projects, ~1,500 root allocations in a ±12-month span.
`get_allocation_usage_rows()` (`allocations.py:866`, cached as `cached_allocation_usage_rows`)
already returns one row per root allocation overlapping a window with `total_used` capped at
`as_of`; it is the calendar's data source as-is. No per-month per-allocation charge query exists
(READ_MODEL.md:113 puts time series out of scope), hence the burn strip is deferred.

## Delivery: one PR, one commit per stage

Commits in order, each green on its own; the PR is pushed as a **draft**, base `staging`,
once remote CI is wanted. Commit 0 is this doc.

### Commit 1: refactor for commonality (no intended visual change)

- Split the per-resource pane of `templates/dashboards/allocations/projects.html` into partials
  under `.../allocations/partials/`: `_resource_charts.html` (Share/Pace pills + rings + pace),
  `_alloc_tree_table.html` (today's table). `projects.html` keeps the tabs, filter form, modals.
- A `tree_label_cell(label, depth, count=None)` macro (in `fragments/table_bits.html`) for the
  chevron + name + `group_count` cell, used by both views' facility/type rows.
- Blueprint helpers in `src/webapp/dashboards/allocations/blueprint.py`:
  `_facility_index()` for the `[(id, name, is_active)]` list built three times today (index,
  `htmx_used_sunburst`, `build_facility_trees` callers); rename `_chart_fragment_scope()` →
  `_fragment_scope()` (it will serve a non-chart fragment too).
- Gates: route-map parity unchanged, chart fingerprints unchanged, e2e `test_allocations_tree.py`.

### Commit 2: the at-date table

- **As-of fix** in `get_allocation_summary_with_usage` (`allocations.py:1161`): charge window end
  = `min(alloc.end_date or check_date, end of check_date day)`, matching
  `get_allocation_usage_rows`. A no-op at today (no future charges), corrects past view-at dates in
  the table, drill, xlsx export and `sam-search allocations --active-at`. Read model untouched
  (it already only serves `as_of == today`). Model-layer test with factories: a past allocation
  with charges after the view-at date excludes them.
- **Columns** (HPC/DAV): Facility / type · Count · Allocated · Annual rate · Rate share · Used ·
  Remaining · % used. Storage: Volume / Volume share, Used = occupancy, same tail.
  `build_facility_trees()` adds `remaining = total_amount − used` and `expected` =
  Σ amount × elapsed-fraction(view-at) per row (from `per_project_usage` start/end/amount), so the
  % used meter carries an **amount-weighted elapsed tick**. Drop `used_share` and `avg`.
- New macro `alloc_meter(pct, elapsed_pct=None, slot=None)` in `fragments/table_bits.html`:
  thin, facility-hued (`data-fs` → `--fs-color`, as `share_bar` does), elapsed tick as a CSS class
  (move the inline `rgba(var(--text-primary-rgb),0.55)` style from `project_table.html` into
  `components.css`), over-100% state. Tokens only (`test_css_tokens`).
- Drill (`partials/project_table.html`): unchanged layout; its numbers become as-of via the fix.
- Tfoot: Count, Allocated, Annual rate, Used, Remaining, % used totals.
- Ring caption stays "Charges in the year to …"; table header tooltips say "as of <date>".

### Commit 3: the calendar view

- **Switch**: pills `Table | Calendar` above the table (`role="tablist"`, id
  `alloc-table-view-{slug}`), persisted by `nav-view-persistence.js` and mirrored across resource
  panes by `mirrorResourcePane` in `dashboard-init.js` like Share/Pace.
- **Routes** (both `@login_required` + `@require_permission_any_facility(VIEW_PROJECTS)`, scope
  via `_fragment_scope()`, `facilities=` forwarded in every URL they emit):
  - `/htmx/calendar/<resource_name>`: loads on first show of the Calendar pill
    (`hx-trigger="shown.bs.tab from:#… once"`); renders the month header, view-at line, and the
    facility → type skeleton with counts of projects in the window.
  - `/htmx/calendar/<resource_name>/rows?facility=&allocation_type=`: one type group's project
    rows, lazily on `show.bs.collapse … once` (same pattern as `projects_fragment`).
  - Both call `cached_allocation_usage_rows(resource, window_start, window_end, as_of=active_at)`
    with the same args, so the skeleton warms the cache for every group, then
    `filter_rows_by_facility`. Window = first of month 12 months before view-at → end of month 12
    after (module constants; span pills are a later add, `data-chart-persist-keys` ready).
- **Layout helper** `src/webapp/dashboards/allocations/calendar.py` (pure Python, no Flask):
  `calendar_months(window_start, window_end)` → `[{label, left_pct, width_pct}]` (widths ∝ days);
  `calendar_rows(rows, window, active_at, slots)` → `[{projcode, facility, type, bars: [{left_pct,
  width_pct, clipped_left, clipped_right, pct_used, elapsed_pct, state: past|current|future,
  title}]}]`, one row per projcode (renewals side by side), sorted by projcode. Open-ended bars
  run to the window edge; bars outside the window are dropped.
- **Markup/CSS** (new block in `components.css`, tokens only): not a `<table>` of month cells;
  each row is a grid of a sticky projcode cell (copy `.sticky-col` from `admin.css:73-90`) and a
  `position:relative` track of width `calc(var(--cal-days) * var(--cal-day-px))`, bars absolutely
  positioned by `%`. `--cal-day-px` set per breakpoint in CSS, so mobile/tablet need no server
  layout axis. Month gridlines + the view-at line drawn once as an overlay behind the rows. Bar =
  tinted facility body + `alloc_meter`-style fill; ended bars muted, future bars outlined.
  Projcode via `project_link(..., stop_propagation=True)`; bars carry a `title` tooltip.
- **JS** (static file, CSP-safe): on fragment swap, scroll the calendar so the view-at line sits
  near the left third. Nothing else.
- Storage resources: spans without fills in v1 (`get_allocation_usage_rows` does not substitute
  occupancy); noted as follow-up.

### Later (not in this PR)
- Monthly burn strip: needs per-allocation monthly charges (comp/dav summaries by `activity_date`,
  subtree logic from `batch_get_subtree_charges`), ideally a rollup companion to the read model.
- Calendar span pills; storage occupancy fills; facility/type aggregate bands on group rows.

## Critical files

- `src/webapp/dashboards/allocations/blueprint.py` (projects(), build_facility_trees, fragments)
- `src/webapp/dashboards/allocations/calendar.py` (new)
- `src/sam/queries/allocations.py` (as-of fix, ~line 1161)
- `src/webapp/templates/dashboards/allocations/projects.html` + new partials
- `src/webapp/templates/dashboards/fragments/table_bits.html` (`tree_label_cell`, `alloc_meter`)
- `src/webapp/static/css/components.css`, `src/webapp/static/js/dashboard-init.js`, new calendar JS

Reuse: `share_bar`/`data-fs` coloring, `facility_slots`, `collapse_toggle`, `project_link`,
`filter_rows_by_facility`, `cached_allocation_usage_rows`, `nav-view-persistence.js`.

## Verification

- Unit: `calendar.py` geometry (month widths sum to 100%, clipping, open-ended, future start,
  over-use, renewals on one row); `build_facility_trees` remaining/expected; as-of fix (factories).
- Route tests mocking the cached query in the blueprint (pattern:
  `tests/unit/webapp/test_allocations_used_sunburst.py`): render, facility clamp for a scoped
  user, `facilities=` carried into emitted URLs, bad `active_at` falls back silently.
- Gates: `ROUTE_MAP_REGEN=1 pytest tests/unit/gates/test_route_map_parity.py` (commit 3),
  `test_css_tokens`, `test_template_csp_lint`, `test_collapse_trigger_rows`, `test_static_assets`,
  `test_modal_shell_contract`, `test_docs`; chart fingerprints must not move.
- Full suite (`source etc/config_env.sh && pytest`; `test_reconcile_quotas` is the known flake);
  `pytest -m perf -n 0` for the new calendar route's query count.
- e2e: extend `e2e/test_allocations_tree.py` (switch persists, a type group loads rows).
- Browser smoke on samuel-dev :5050 after `FLUSHDB` on samuel-cache: Derecho tab, both views,
  a past view-at date, a facility-scoped filter, 3 layouts × 2 themes.
