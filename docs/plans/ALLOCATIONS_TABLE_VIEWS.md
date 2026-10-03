# Allocations dashboard: an at-date table and a calendar view

**Status: implemented, unreviewed (2026-10-03)** · branch `allocations-table-views` (from
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

### Commit 3: the calendar view (as built)

- **Switch**: pills `Table | Calendar` under the charts (`role="tablist"`, id
  `alloc-table-view-{slug}`, class `alloc-view-pills`), persisted by `nav-view-persistence.js`;
  `mirrorResourcePane` in `dashboard-init.js` now mirrors every `.alloc-view-pills` group, so
  the view follows the resource tab like Share/Pace.
- **Routes** (both `@login_required` + `@require_permission_any_facility(VIEW_PROJECTS)`, scope
  via `_fragment_scope()`):
  - `/htmx/calendar/<resource_name>` (`hx-trigger="intersect once"`: loads once the Calendar
    pane is both shown and scrolled into view): month axis, view-at line, facility -> type
    skeleton (no counts: the Table view carries them). Forwards `facilities=` into every rows URL.
  - `/htmx/calendar/<resource_name>/rows?facility=&allocation_type=`: one group's project rows,
    on `show.bs.collapse ... once`; 403 for a facility outside the user's scope.
  - Both read `cached_allocation_usage_rows(window, as_of=active_at)` with the same args (one
    cache entry per resource and date), then `filter_rows_by_facility`. Window =
    `calendar_window()`: first of the month 12 months back to the end of the month 12 ahead.
- **Geometry** `src/webapp/dashboards/allocations/calendar.py` (pure): `calendar_window`,
  `calendar_months` (`{label, year, left, width}`, widths follow days), `calendar_rows` (one row
  per projcode, renewals side by side, overlaps stacked into lanes; each bar has `left, width,
  clip_left, clip_right, state` past/current/future, `pct_used, fill, over`), `calendar_groups`.
- **The fill** runs along the whole allocation: it ends at the date an even burn would have
  reached at its % used, so it lines up with the view-at line (short of it = under pace). A
  window edge cuts bar and fill alike; a cut end fades (CSS mask). A multi-year allocation that
  started before the window can therefore show little or no visible fill; the % label carries it.
- **Markup**: a two-column table (sticky label, a track `--cal-days x --cal-day-px` wide); a type
  group's rows arrive as a nested `.cal-rows` table of the same fixed widths, so one `.cal-scroll`
  scroller holds everything. Gridlines and the view-at line are one overlay. `--cal-day-px` is
  3 / 2.5 / 1.5 px by breakpoint (phones also drop the axis year and tighten the indent); below lg, `cal-table` opts out of dashboard.css's
  `display: block` rule for bare tables (it would give each row table its own scroller).
- **JS** (`dashboard-init.js`, `focusCalendars`): once visible, scroll so the view-at line sits a
  third of the way across the track.
- **Contrast**: bar body is the facility hue mixed 30% (light) / 40% (dark) into the card,
  ended bars at 0.7 opacity: body vs card at least 1.4:1, fill vs body at least 2.3:1.
- **Storage**: spans without fills (`get_allocation_usage_rows` sums charges; no occupancy).
- **Perf tier**: not extended; the sibling chart fragments (Pace, Used ring) have no baselines either.

### Later (not in this PR)
- Calendar span pills; storage occupancy fills.

## Burn: the monthly burn strip (follow-on PR, branch `calendar-burn`)

**Status: in progress (2026-10-03)** · stacked on #707 (base `allocations-table-views`; rebase
`--onto origin/staging` and retarget once #707 merges) · one commit per stage below.

Ben wants to see *when* the use happened: each month of a bar shaded by that month's charges
against an even-pace share, and the same for the facility and type rows, whose tracks are empty
in the Used view. The default calendar must stay as fast as it is.

Decisions (2026-10-03): a **Used | Burn** toggle on the calendar, burn data fetched only when
chosen; group rows show an aggregate strip in Burn mode. **No DDL**: read-only queries over the
existing charge summaries plus the usage cache. A READ_MODEL.md rollup companion would be DDL,
so it stays a last resort needing Ben's go-ahead.

Data facts (local snapshot, Derecho, 25-month window): only 222 of 1,525 root allocations start
on the 1st, so month buckets must be per allocation (a mid-month renewal splits its month), not
per account. Per allocation x month is ~20k cells. No (account_id, activity_date) index exists;
the shape matches `get_allocation_usage_rows`, whose ±180-day Pace window costs 9-11 s cold.

1. **Query** `get_allocation_burn()` (`sam/queries/allocations.py`): the usage-rows allocation set
   and subtree/account routing; per-anchor dates `[max(start, window start), min(end, end of the
   as-of day, window end)]` in a VALUES table (UNION ALL where VALUES is unsupported) with
   `sqlcompat.month_key()` in the GROUP BY; charge model per `get_charge_models_for_activity`,
   adjustments into the same cells. Returns `{allocation_id: {yyyymm: charges}}`. New code beside
   the batch methods, which feed every usage figure and stay untouched. Usage rows gain
   `allocation_id` so the calendar can join the two.
   - **Cache**: its own bucket, `allocation_burn` (`ALLOCATION_BURN_CACHE_TTL` 12 h, size 50), not
     usage's 1 h: an entry is keyed on its as-of day and only that day's month still moves (Ben,
     2026-10-03). Purged with the `usage` category; a second row on the Admin Caching card.
   - **Measured** (local MySQL snapshot, as of 2026-10-03, fresh process): Derecho 0.68 s, 12
     statements, 952 allocations, 5,303 cells, 79 KiB pickled; Casper 0.57 s. Usage rows on the
     same window: 0.87 s. Pressing Burn on the local dev server (:5050): 1.3 s cold, 30-60 ms warm.
2. **Geometry** (`calendar.py`): `burn_cells` (one cell per month the bar covers, in % of the bar;
   ratio = charges / (amount x cell days / allocation days)), 5 classes at `<0.25, <0.75, <1.25,
   <2, >=2` (`BURN_EDGES`, which also builds the key's labels), and `group_burn` / 
   `calendar_group_burn` (summed charges over summed even shares per window month). Shading stops
   at `burn_through()`: the end of the as-of day, or **today's midnight** if sooner, because a
   day's charges land the next day and would otherwise read every current month low (a third low
   on the 3rd). No cells for open-ended, zero-amount, id-less or future allocations.
3. **UI**: `mode=burn` on `htmx_calendar` and its rows route (HPC/DAV only; anything else reads as
   `used`), persisted via `data-chart-persist-keys="mode"` on the loader and the fragment, and
   forwarded into every rows URL. A toolbar holds the `Used | Burn` pills and, in Burn mode, the
   key. Burn cells replace the fill and % label; group rows carry a strip. The toggle re-renders
   the fragment, so `dashboard-init.js` carries the open type groups (`data-no-persist`) and the
   horizontal scroll across the swap.
   - **Colors (changed from the handoff)**: burn ratio is a polarity around even pace, so a
     diverging scale (`--data-burn-0..4`: blue under, gray on pace, red over), not facility tints
     with a `--danger-color` cap (a status color is reserved). In Burn mode a bar's unshaded rest
     is an empty frame: the UNIV tint there read as the 1.25-2x step. Adjacent steps, OKLab dE x100,
     light >= 16.8 normal / >= 14.5 under protan, deutan and tritan; dark >= 15.6 / >= 11.2, the
     dark midpoint 1.6:1 on the facility rows' `--surface-secondary`.
4. **Perf**: route query-count baselines; cold timing on samuel-dev Postgres. Over ~3 s cold on
   dev means prewarming today's entry (cache-only).
5. **Future risk (decided 2026-10-03)**: a run-out tick on Burn bars, no "future risk" mode.
   Ben asked about projects that ran cold and are ramping up. Local snapshot, as of 2026-10-03,
   current allocations with >= 30 days left, Derecho (Casper in parentheses), 1,054 (1,156):

   | Signal | Projects | Reading |
   |---|---|---|
   | Catch-up rate >= 1.25x (unspent / even share of time left) | 715, 68% (844) | Noise, and Pace already draws it |
   | Last ~90 days >= 1.25x even pace | 124 (84) | |
   | Cold before (< 0.75x), hot now, balance large | 40, 4% (28) | Real but small: 1.9% of remaining balance, ~+15M over 90 days |
   | At the 90-day rate, runs out >= 30 days before its end | 89, 8% (54) | The actionable signal: a date |

   So no toggle, no projected calendar cells, no facility demand forecast; the projected future
   goes on the Pace chart instead (the follow-on PR stacked on this one). The burn
   math moved to `allocations/burn.py` (no Flask, no matplotlib; gated in
   `test_chart_module_boundaries.py`) so Pace can share it. `recent_rate` = charges per day over
   the last 90 days (`RUNOUT_LOOKBACK_DAYS`; from the start date if later), a month cell partly
   inside counting pro rata. `runs_out` = through + unspent / recent rate, only for a current
   burnable allocation with a balance and a rate, and only when it lands >= 30 days
   (`RUNOUT_MARGIN_DAYS`) before the end date; the day counts are compared before building the
   date, because a trickle of charges overflows `datetime`. The bar gets a 2px `--data-burn-4`
   tick with a `--surface-card` halo inside an 8px hover target, the date in its title and the
   bar's, and a key item.
   - **Measured** (local MySQL, as of 2026-10-03): 87 Derecho / 55 Casper run-outs (the analysis
     counted 89 / 54); 80 / 51 draw, the rest fall past the window's end. UHWM0061: 8.9x even
     pace over 90 days, 120 days left, runs out about 2026-10-29.

## Critical files

- `src/webapp/dashboards/allocations/blueprint.py` (projects(), build_facility_trees, fragments)
- `src/webapp/dashboards/allocations/calendar.py` (new), `burn.py` (burn math, run-out)
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
