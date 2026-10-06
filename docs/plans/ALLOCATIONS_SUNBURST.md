# Allocations dashboard: two-ring charts and one-table trees

**Status: implemented and merged** (#697 on 2026-10-02, the legend follow-up #700 the same
day) · branch `allocations-sunburst` (from `origin/staging` after #696 merged).

## Why

`/allocations/projects` (`dashboards/allocations/blueprint.py:372` `projects()`,
template `templates/dashboards/allocations/projects.html`) shows, per Resource tab:

- a **facility pie** for allocated annual rate and another for usage
  (`generate_facility_pie_chart_matplotlib`, `blueprint.py` ~l.500 and ~l.580), colored
  `UNITY_PALETTE_10` **by rank**, so a facility changes color between pies and tabs;
- a bordered `summary-table` (`projects.html:203`) whose facility rows expand into
  allocation-type rows via custom JS (`data-action="alloc-toggle-facility"`,
  `static/js/dashboard-init.js:21`), with a per-facility subtotal row;
- inside each expanded facility, a pills strip (Allocated / Usage / Pace,
  `projects.html:~300`) holding **separate allocation-type pies** per facility.

That is the facility -> allocation type hierarchy split across ~2 x (1 + N) pies and
a table that reads differently at each level: the same problem the admin Facilities
card had. Lessons from that work apply directly.

## Lessons to carry over (from ADMIN_TABLE_POLISH.md)

1. **One chart per hierarchy, not one per level.** Inner ring = facilities, outer =
   their allocation types, each a `shade_family` shade of the facility's hue.
2. **Color follows the entity, never rank.** `facility_slots()` gives a facility one hue
   on every page (admin Facilities, Resources fair share, and here).
3. **Validate the palette with the dataviz script** (`validate_palette.js`, light on
   `#ffffff`, dark on `#1b2733`); the fair-share sets already pass.
4. **One table, one column set**: tree rows (`.tree-cell` + `--depth`, guide lines),
   `share_bar` beside numbers in the facility family, `.col-num` / `.cell-truncate`
   roles, `.row-actions`; no bordered table, no per-level header.
5. **Chart <-> table drill**: wedges and legend entries carry `RowDrill` sentinels;
   `svg-chart-links.js` opens the row (zero JS when rows use Bootstrap collapse).
6. Direct labels only on big wedges; outer labels dropped on mobile; the table is the
   table view; no hover layer yet (`CHART_HOVER_LAYER.md`).
7. Fingerprint regen must only ADD entries; bind new `chart_view`s LAST.

## Proposed shape (per Resource tab)

- **Two sunbursts side by side**: Allocated (annualized rate; data volume for storage)
  and Used, same facility order and hues, so the reader compares rings directly. Stack
  on mobile. They replace the two facility pies AND every per-facility type pie in the
  expansion (the Pace pill stays, see below).
- **Generalize the chart**: extract a `TwoRingPie(PieChart)` base taking absolute nested
  values `[{id, facility, slot, value, types: [{name, value}]}]` (inner = sum of types
  unless given); `FairShareSunburst` becomes the percent adapter over it, so its
  fingerprints must stay byte-identical (a refactor check). New binding
  `generate_allocation_sunburst`, appended last.
- **The summary table becomes a tree**: facility rows (depth 0) and type rows (depth 1)
  in one column set: Facility / type, Total amount, Annual rate, Count, Avg, Share of
  rate (bar, facility hue; types tinted), Share used (bar) if usage is available.
  Subtotal rows go: the facility row is the subtotal. Footer total stays.
- **Expansion mechanics**: move facility rows to Bootstrap collapse (one `tbody.collapse`
  per facility, toggle on cells, inert `data-bs-target` + `data-facility-id` on the
  `<tr>`), so `links.FACILITY_ROW` drills with no JS change. The type -> details level
  (`alloc-toggle-details`) can stay custom for now; note it if converted.
- **Pace**: keep the per-facility Pace chart, as an expansion row after the facility's
  types (htmx-loaded as today), without the Allocated/Usage pills.
- **Drill scoping**: `openEntityRow` searches within the clicked chart's `.tab-pane`,
  which is exactly the Resource tab, so identical facility ids across tabs never
  cross-fire.

## Traps and checks

- **Caching**: `projects()` and `projects_fragment()` are `@cache.cached` with
  `user_aware_cache_key` (stale up to 300 s; no invalidation). Chart SVGs are separately
  cached by content hash. Layout/theme must be forwarded to every chart call
  (`read_layout()` / `read_theme()` already are).
- **Facility scope**: scoped users see a subset of facilities (`feedback_allocations_dashboard_shape`);
  `facility_slots` must be computed over ALL active facilities, not the visible subset,
  or a scoped user's colors shift.
- **Storage resources** (`is_storage`): no annual rate; use data volume and say so in the
  chart title/legend.
- Fix in passing: `projects.html:392` formats with `"{:,.0f}".format(...)`; use
  `fmt_number` (CLAUDE.md § Display Formatting).
- Existing tests: `tests/unit/charts` (registry pins, samples, fingerprints),
  allocations route tests under `tests/unit/webapp/` (grep `allocations/projects`),
  `e2e/test_console_sweep.py` covers the page in both themes; extend
  `e2e/test_admin_table_density.py`'s drill test pattern to this page.
- Perf: `tests/perf/baselines.json` pins query counts for these routes; the change
  should be render-only. Run `pytest -m perf -n 0` if any query moves.

## Progress

- [x] 1. `TwoRingPie` base; `FairShareSunburst` on it. SVG bytes compared before/after
      (12 renders: 3 layouts x 2 themes x 2 data sets): identical.
- [x] 2. `generate_allocation_sunburst` (`AllocationSunburst`, `center` in the key),
      samples, +7 fingerprint entries only.
- [x] 3. Allocated / Used sunbursts per Resource tab, behind **Share | Pace** pills.
- [x] 4. Summary table -> tree (Bootstrap collapse, share bars, drill attrs).
- [x] 5. Per-facility type pies removed; per-facility Pace is a lazy expansion row.
- [x] 6. Tests: `tests/unit/webapp/test_allocations_tree.py`,
      `e2e/test_allocations_tree.py`; the dark sweep now expands this table too.
- [x] 7. `FacilityPie` / `AllocationTypePie` retired (-14 fingerprint entries only).

## As built (where it differs from the sketch)

- **Charts live inside each Resource pane**, behind Share | Pace pills (Ben's call).
  A pill pane is itself a `.tab-pane`, so `svg-chart-links.js` resolves a row drill
  in the nearest `[data-drill-scope]` first; the Resource pane carries it.
- **Expansion follows the tab** (Ben, 2026-10-02): on a Resource tab switch,
  `dashboard-init.js` mirrors which facilities are open and the Share/Pace pill from
  the pane just left. Facility level only: type rows hold lazily fetched project
  tables (`data-no-persist`), and mirroring them would fire fetches.
- **Columns**: Count / Total amount / Annual rate / Avg / Rate share / Used / Use
  share; storage drops the rate column and charts volume (`total_amount`). Shares are
  of the parent row. The facility row is the subtotal.
- **Lazy loads**: a type's projects and a facility's Pace load on first expand
  (`hx-trigger="show.bs.collapse from:closest tr once"`), where every per-facility
  Pace used to fetch at page load.
- **Found on the way**: storage pies plotted `annualized_rate` under a "Data Volume"
  title; share-bar numbers of different widths pushed their bars off one x (fixed in
  `table_bits.share_bar` with a tabular `.share-num` slot, which also aligns the
  admin Facilities and Resources trees).
- **Roots only** still filters only the per-type project lists; the summaries, both
  sunbursts and Pace are always root-only (a child draws on its parent's amount, so
  including it would double-count).
- `pie.py` hit the chart-module 550-line cap at step 1; over-budget comments were
  compressed, and step 7 brought it to 478.
- **Expanded view**: each sunburst opens a fullscreen facility / panel / project
  chart; see `PANEL_SUNBURST.md`.
