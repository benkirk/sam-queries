# Handoff: the Allocations project list onto the house table vocabulary

**Status:** unbuilt; written 2026-10-04 at the end of sweeps 5–7.

## Context
Templates sweeps 5–7 (#726 → #727 → #728, stacked; ledger entries 5–7 in
`docs/plans/UNPLANNED_CITY_LEDGER.md`) moved every allocation table to one vocabulary:
- `col-num` columns;
- `alloc_meter` for % used, with the elapsed tick on HPC/DAV only;
- the shared `allocation_cells` (`dashboards/shared/project_tree.html`) for
  Allocated / Used / Remaining, including the shared-pool hybrid: "from <owner>", own use, and
  the pool's remaining muted;
- one desktop bar width (`--bar-share-w` / `--bar-meter-w`, 8rem at ≥ 1200px, in
  `components.css`).

One allocation table never moved: the project list that loads under a type row on
`/allocations/projects` (Allocations → a resource tab → expand a facility → expand a type).
It is ledger entry 7's open item.

**What it is today** (`src/webapp/templates/dashboards/allocations/partials/project_table.html`,
126 lines):
- `table table-sm table-striped`.
- Ten headers: Project, Title, Total Amount, Annual Rate / Data Volume, Usage %, Remaining,
  Start, End, Dur (days), Left (days).
- The last six headers sit over one `colspan="6"` cell holding everything else:
  - a start/end date line;
  - a `render_usage_bar` (Bootstrap progress) with an inline `elapsed-tick`;
  - "N used" in orange, the shared "(N yours)" note, and "N remaining" in green.
- Each of those six headers sorts through `data-sort-attr` against `data-sort-*` attributes on
  that one cell (`static/js/sortable_table.js`).
- The header row's `<br>` line breaks, inline `min-width:280px; padding`, `text-warning` and
  `text-success` are the old idiom.

**Route:** `allocations_dashboard.projects_fragment` (`src/webapp/dashboards/allocations/blueprint.py`
around L1000–1050).
- Data comes from `cached_allocation_usage(...)`. Each row is a dict built in
  `src/sam/queries/allocations.py` (around L700–730 and L1280–1312), with keys `projcode`,
  `total_amount`, `total_used`, `percent_used`, `annualized_rate`, `start_date`, `end_date`,
  `duration_days`, `is_open_ended`, plus, for a single shared pool, `is_inheriting`,
  `self_used`, `self_percent_used` and `root_projcode`.
- The route adds `title` with **one `find_project_by_code` query per row** (an N+1) and sorts
  by `total_used`.

## Goal
Real columns, every one sortable by its own cell, using the house vocabulary, so this list
reads like the tree above it and like Manage Project / the project card. Hold the same visual
contract as sweeps 5–7: aesthetic changes are fine, declared in the commit, with before/after
screenshots.

## Plan (one PR, a commit per step)
1. **Branch** from `sweep-cards-2026-10` (#728) if it hasn't merged; otherwise from
   `origin/staging`. Any stacked PR targets its parent branch and gets retargeted on merge.
2. **Columns** (in `project_table.html`):
   - Project (`project_link`) | Title (`cell-truncate`, full title in `title=`) | % used
     (`alloc_meter`) | Allocated | Annual rate (or Volume for DISK/ARCHIVE) | Used | Remaining |
     Start | End | Left.
   - Everything except Title is `col-shrink` / `col-num`; header labels sit on one line with no
     `<br>`.
   - Drop "Dur (days)" unless the data says it's worth a column. Keep it as the End cell's
     `title=` instead.
   - Each `<td>` carries its own `data-sort-value`, and the `data-sort-attr` indirection goes.
     Check `sortable_table.js` still handles numeric, date and text sorts from cell values
     (it does: `extractKey` falls back to the cell's `data-sort-value`).
3. **Reuse `allocation_cells`.** The row dict uses different key names (`total_amount` /
   `total_used`), so add one small adapter. Normalize in the route (preferred; one place) into
   the resource-dict shape `allocation_cells` reads:
   - `allocated`, `used`, `remaining`, `percent_used`;
   - `elapsed_pct`, computed from start/end/active_at the way the template computes it now;
   - `resource_type`, `is_inheriting`, `self_used`, `self_percent_used`, `root_projcode`;
   - leave `allocation_id` out.

   Then a shared pool reads "from <root>" as everywhere else. Decide whether the template's
   inline elapsed / bar_state math moves into the route too (it should; 25 lines of Jinja
   arithmetic).
4. **States:** expired / open-ended / no-dates as a `state_tag` in the End or Left cell. An
   expired row is `row-inactive`. Keep "No usage data" (`total_used` None) as a muted dash
   across the usage cells.
5. **Fix the title N+1:** one query for all titles (`Project.projcode.in_(codes)`), or fold the
   title into `cached_allocation_usage`. Measure the query count before and after; this route
   should have a perf-tier entry. Check `tests/perf/` and `baselines.json` for a route
   query-count test, and add one if missing (`feedback_perf_tier_for_query_count_changes`).
6. **Delete what this frees:** if `project_table.html` was the last list user of
   `render_usage_bar` / `.elapsed-tick`, note that Resource Details and `usage_modal.html` still
   use them, so they stay. Run `test_css_dead.py` and delete only what it flags.
7. **Ledger:** tick entry 7's project-list open item, update the metrics row (whole-tree
   `scripts/sweep_inventory.py`), and note Resource Details as the last `render_usage_bar`
   holdout.

## Tools and traps (learned in sweeps 5–7)
- **Servers:**
  - Base: `scripts/dev_server_alt.sh ../sweep5-base 5053` (re-point that worktree at the new
    base, or make a fresh one).
  - Branch: `scripts/dev_server_alt.sh . 5052`.
  - After a CSS edit, `touch src/webapp/utils/static_assets.py`: the `?v=` hash is memoized
    per process.
- **Screenshots:** `scripts/ui_snapshots.py --base-url http://localhost:5053 --out before --page
  '/allocations/projects?tab=derecho'`, and the same against 5052. The project list is lazy;
  open a type row with Playwright (the MCP browser) to capture it.
- **Check pytest's exit code before committing.** Piping into `tail` hides failures. Use
  `pytest … > out.txt; echo rc=$?`.
- **Known flakes:** `tests/unit/models/test_reconcile_quotas.py` (about 1 run in 2), plus one
  unidentified gates/webapp failure that passes on rerun.
- **Gates to run:**
  - `tests/unit/gates/` (CSS tokens, css-dead, template detectors: bs4-classes and
    row-buttons, docs, action-cell nowrap, CSP lint);
  - `tests/unit/webapp/test_allocations_performance.py`;
  - `tests/integration` for the allocations routes;
  - the perf tier `pytest -m perf -n 0` if query counts move.
- **Phone width:** wrap the table in `table-responsive`. Never put a `visually-hidden` span in
  a `<th>` (`position: absolute` escapes the wrapper and widens the page); use `aria-label`.
  Check `document.documentElement.scrollWidth` is 375 at 390px.
- **Comment budget** (CLAUDE.md §12), American spelling, no changelog phrasing, no skip-ci
  tokens in commit messages.

## Verification
- Before/after screenshots of the project list at desktop light/dark and 390px; row heights
  measured with `getBoundingClientRect`.
- Every header sorts; click each and check the order, the date columns especially.
- A shared-pool project shows "from <root>" (find one under a type with inheriting
  allocations).
- Route query count is flat in the number of rows (the N+1 is fixed); perf tier green.
- Full MySQL suite with rc = 0; postgres-test for `tests/unit/webapp` and allocations
  integration.

## Prompt for a new session
> Read `docs/plans/implemented/ALLOCATIONS_PROJECT_LIST_HANDOFF.md` and do it with the unplanned-city-sweep
> and wire-dashboard-feature skills. Check whether #728 (`sweep-cards-2026-10`) has merged;
> branch from it if not, else from origin/staging, and open a draft PR against the right base.
> One commit per plan step, re-check every file:line before editing, and prove the visual
> change with before/after `ui_snapshots.py` plus Playwright on the lazy project list, at
> desktop and 390px in both themes. Measure the projects_fragment query count before and after
> the title fix and add or update the perf-tier entry. Finish with the ledger update and send me
> before/after screenshots.
