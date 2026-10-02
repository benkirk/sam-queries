# Allocations dashboard: two-ring charts and one-table trees

**Status: sketch, not started (2026-10-02).** Depends on the admin-table-polish PR
(branch `admin-table-polish`): `FairShareSunburst`, `table_bits.share_bar`,
`.tree-cell`, `charts/theme.py` `facility_slots` / `FAIR_SHARE_LIGHT/DARK`,
`links.FACILITY_ROW`. Start from `origin/staging` after it merges.

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
   table view; no hover layer yet (see the chart-framework follow-on).
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

- [ ] 1. `TwoRingPie` base; `FairShareSunburst` on it with unchanged fingerprints.
- [ ] 2. `generate_allocation_sunburst` + samples + fingerprint additions.
- [ ] 3. Allocated / Used sunbursts per Resource tab (replace facility pies).
- [ ] 4. Summary table -> tree (Bootstrap collapse, share bars, drill attrs).
- [ ] 5. Remove per-facility type pies; keep Pace as an expansion row.
- [ ] 6. Tests (conventions-style render test for the tree, drill e2e), snapshots,
      browser pass 3 layouts x 2 themes via `scripts/ui_snapshots.py`.
