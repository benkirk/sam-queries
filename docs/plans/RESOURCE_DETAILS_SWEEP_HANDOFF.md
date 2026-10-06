# Handoff: Resource Details, one shared-usage grammar, the jobs and storage drilldowns

**Status:** round A in review (ledger entry 13); round B in review, stacked on A (entry 14);
round C unbuilt. Written 2026-10-06 after
sweep 12 (#737, the usage kernel). Three rounds, three PRs, each green alone. Round C can be
dropped if time runs out.

## Context

Templates sweeps 5–9 (#726; ledger entries 5–9 in `docs/plans/UNPLANNED_CITY_LEDGER.md`) brought
the admin cards, the project trees, the project and user cards, the Allocations project list and
the modals onto the house vocabulary, and deliberately left **Resource Details** for a round of
its own (entries 5, 6, 8; `SWEEP_FOLLOWUPS_HANDOFF.md` "do not start"). Sweep 12 then made
allocation usage one computation (`build_user_projects_resources_batched` over
`accounting/calculator.py`), so the data under these pages is settled.

This is a visual consistency and consolidation pass with no behavior change except the bug
fixes named below. Three censuses were run on 2026-10-06 (Resource Details; every renderer of a
shared allocation; the jobs and storage drilldowns). Their findings are folded into the rounds.

**Decisions (Ben, 2026-10-06):** the two-tone meter lives in `alloc_meter` and every surface
adopts it; three rounds, three PRs, one handoff.

## Setup for the fresh session

- **Base:** `origin/staging` once #737 has merged (round B edits the kernel's rows). Until then
  branch from `sweep-usage-2026-10` and retarget.
- **Skills, in order:** `unplanned-city-sweep` (area mode `templates`; ledger entries 13, 14, 15),
  `wire-dashboard-feature` (§1 reuse list, §7 columns and action cells, §12 smoke and gates),
  and `frontend-design:frontend-design` for round B's meter only, inside the brand stance of
  ledger entry 5 (NCAR blue and aqua, tinted surfaces, the `--status-*` tokens, no new hues).
- **Contract:** aesthetic, as sweeps 5–9: each commit declares its visual change; a bug fix says
  "bug" in its subject.
- **Proof rig:** base served from the `staging` worktree by `scripts/dev_server_alt.sh
  /Users/benkirk/codes/project_samuel/staging 5053`, the branch on 5052.
  `scripts/ui_snapshots.py` before and after in the six states; `--styles` + `--compare` where
  nothing may change; `--element SEL` + `--compare-pixels` where markup moves onto a macro;
  `--headers --layout desktop --layout mobile` for header widths; `getBoundingClientRect` for
  heights; WCAG ratio computed in the page, alpha blended. Jobs and storage pages need the
  plugin DB: samuel-dev (`watch-dev` skill) or the local `hpc-usage-postgres` container.
- **Caches lie:** `docker exec samuel-cache redis-cli -n <2 + port % 14> FLUSHDB`, and
  `touch src/webapp/utils/static_assets.py` after a CSS or JS edit.
- **Usage parity (round B.2 only):** `utils/profiling/usage_sweep/capture.py` + `compare.py`
  (untracked; see ledger entry 12). Its project set already holds the shared and disk holders.

## Decisions already made (do not relitigate)

- The shared-row text stays the hybrid of entry 6: Allocated says "from <root>", Used is the
  project's own, Remaining is the pool's, muted. Round B adds the pool tone to the meter under
  that text; it does not change the words.
- The owner of a pool is the **root** project (`root_projcode`), never the immediate parent
  (`admin/fragments/edit_allocation_form_htmx.html` names the parent; leave that modal alone).
- Disk keeps its own date-picker card; `usage_tab` is server-side; exactly one chart request per
  page load (`docs/plans/implemented/RESOURCE_DETAILS_USAGE_TABS.md`).
- The lazy-fragment lever for the compute page's first paint
  (`docs/plans/implemented/READ_MODEL.md` § "companion grains EAGERLY") is a performance change
  and is **not in this pass**. Record it as a follow-on.
- The day and user subtrees' nested fixed-layout tables (`usage-table-4` / `-5`, `.ucol-*` in
  `components.css`) are deliberate. Leave them.
- `SWEEP_FOLLOWUPS_HANDOFF.md` item 3 (the list sorted on the pool's `used`) is fixed in code:
  `_shown_used` in `src/webapp/dashboards/allocations/blueprint.py`. Tick the doc in round B.

## Round A: Resource Details, compute and disk (one PR)

Files: `src/webapp/templates/dashboards/user/resource_details.html`,
`resource_details_disk.html`, `partials/day_subtree.html`, `partials/user_subtree.html`,
`_resource_details_macros.html`, `user/fragments/rolling_rate_htmx.html`,
`src/webapp/dashboards/user/blueprint.py` (`resource_details` ~:463–702, `_build_node` ~:582,
`_render_disk_resource_details` ~:1132), `src/webapp/static/css/dashboard.css` (`.tree-list`
~:1610–1641, `li.tree-node-current` ~:1666, `.progress*` ~:869–910),
`src/webapp/static/css/components.css` (`.tree-list` duplicate ~:142, `.ucol-*` ~:221).

1. **Scope picker onto `project_tree_rows`** (`dashboards/shared/project_tree.html:98`). The
   picker is a recursive `<ul class="tree-list">` of `<li>`s (`resource_details.html:363–412`,
   `resource_details_disk.html:15–65`) over `_build_node` dicts (`projcode`, `title`,
   `direct_charges`, `subtree_charges`, `children`; the disk variant has `account_id`,
   `fileset_paths`, `current_bytes`). The macro needs: a row-href mode (the link is
   `resource_details?scope=<node>&usage_tab=…` carrying `data-tab-param-link`), a trailing
   `col-num` value cell (charges, or `fmt_size` bytes; the disk tree also shows fileset paths),
   a muted non-clickable state for nodes with no usage, and the scroll and font wrapper
   (`:424`, `:253`) moved into CSS. `active_only` already defaults false, which these dicts need
   (they carry no `active`). Then delete `.tree-list` (both CSS blocks; zero users remain), which
   also removes the inherited-bold bug: `font-weight: 700` on the whole current `<li>`.
   Proof: `--element` pixels on the tree, three layouts.
2. **Summary row onto `allocation_cells`** (`project_tree.html:38–46`) plus `allocation_actions`
   for the `btn-row` edit. `Project.get_detailed_allocation_usage` reshapes the builder's rows
   but does not pass `elapsed_pct` / `bar_state`; pass them through, because a missing
   `elapsed_pct` is Jinja `Undefined`, and `Undefined is not none` is true. Shared rows read as
   the hybrid until round B lands the meter. The disk capacity card needs a resource-shaped dict;
   the Filesets "Share" column becomes `table_bits.share_bar`, not a meter. This retires
   `dashboards/shared/usage_bar.html` (`render_usage_bar`) and the `.progress` block with its
   three `!important`s; grep first — the status filesystem table was the other holdout (entry 8).
3. **Card headers**: 13 raw `data-bs-toggle="collapse"` headers (7 compute, 6 disk) onto
   `collapse.collapse_toggle` with `.accordion-chevron`. Today's `fa-chevron-down float-end
   transition-smooth` never rotates: `.transition-smooth` has no transform rule. The orphan
   `user/partials/collapsible_card.html` has the same chevron and no callers; delete it. Rows
   keep `data-bs-target` on the `<tr>`: `static/js/svg-chart-links.js` (`openDayRow`,
   `openUserRow`) reads it, so copy `partials/jobs_usage_panel.html`'s shape (target on the row,
   toggle on a chevron button).
4. **Column roles**: 24 `td` / `th.text-end` onto `.col-num` / `.col-shrink` / `.cell-truncate`;
   one username rendering (`user_rows.user_link`; the user table prints bare `<code>`);
   `{{ day.user_count | fmt_number }}`; the `btn  btn-link` double space in
   `_resource_details_macros.html`.
5. **Rolling rate fragment**: 16 inline styles onto classes; two `btn-outline-*` onto `btn-row`;
   its hand-built pool bar becomes `alloc_meter` (pool tone in round B).
6. **Inline styles**: the remaining 5 + 4 on the two pages onto CSS; the three dynamic ones are in
   the deleted bar.
7. **`page_header`** on both pages if either still hand-writes its heading.

Gates: `tests/unit/webapp/test_resource_details_partials.py`,
`tests/unit/webapp/test_chart_route_smokes.py`, `tests/unit/gates/test_collapse_trigger_rows.py`
(names `user_subtree.html`), `tests/unit/gates/test_modal_shell_contract.py`, route-map parity
(no route change expected), `tests/perf/test_resource_details_disk.py`, and the §12 list.

## Round B: one shared-usage grammar (one PR; bug fixes named)

Eight surfaces draw a shared allocation six ways today. Six already go through
`allocation_cells` (own-share meter, "from <root>", muted pool Remaining); Resource Details draws
a two-tone pool bar with "(N yours)"; the rolling rate and the CLI show pool figures.

Files: `dashboards/fragments/table_bits.html` (`alloc_meter`, ~:43),
`static/css/components.css` (`.share-bar` family, ~:510–551),
`dashboards/shared/project_tree.html` (`allocation_cells`, ~:38–46),
`dashboards/user/partials/project_card.html` (header badge ~:312; 30d/90d lines ~:182),
`dashboards/fragments/glossary.html` (`g_shared_pool`, `g_roots_only`),
`src/sam/queries/dashboard.py` (`_apply_disk_capacity_overrides`, the builder's `self_used`),
`src/sam/queries/allocations.py` (the all-disk branch of `get_allocation_summary_with_usage`),
`src/cli/project/display.py` (~:162–187), `src/cli/allocations/display.py` (~:112–126).

1. **`alloc_meter(pct, elapsed_pct=None, slot=None, tint=False, title='', pool_pct=None)`.** A
   second background layer sized by `--pool` in the `.share-tint` `color-mix` tone, the own share
   solid on top; `.meter-over` keys on `pool_pct` when given; the `%` label stays the own share;
   the hover title names both ("you N% · <root>'s pool M%"). `allocation_cells` passes
   `percent_used` as `pool_pct` on inheriting rows and restores the elapsed tick they drop today.
   Design pass with `frontend-design` on P93300042 / Casper (28.9% own + 32.9% others), both
   themes; measure the lighter tone's contrast against the track. Surfaces that change: user
   card, admin card, expirations cards, project details modal, Manage Project tree, Allocations
   project list, Resource Details summary, rolling rate, `/dev/gallery`.
2. **Bug: shared DISK rows mix units.** The disk override replaces `used`, `remaining` and
   `percent_used` with the project's own subtree TiB but leaves `self_used` / `self_percent_used`
   as cumulative TiB-years, so the shared branch shows a TiB-year share next to a TiB pool, and
   "the pool has used N" names the project's own capacity. The summaries path forces the two
   equal instead. Decide the pool figure for disk (the root project's subtree capacity through
   `bulk_get_subtree_disk_capacity`) and make `self_*` the own capacity. Run the usage capture
   before and after on both backends: only shared DISK rows may move.
3. **Bug: the card's header badge is the pool %** while its rows show the own %: a 95% danger
   badge can sit over a row reading 3%. Badge = max own %, with the pool % in its title. Label
   the card's 30d / 90d lines as pool burn.
4. **Overdrawn pool** on a shared row: negative pool Remaining reads muted grey in the trees and
   `text-success` on Resource Details. One rule: `text-danger`, and `.meter-over` from the pool.
5. **Allocated sort key** on shared rows sorts the pool amount while the cell says "from X".
   Either keep it and say so in the cell's title, or sort on 0. Pick one; put it in the glossary.
6. **Glossary**: `g_shared_pool` is unused and describes the tones backwards; wire it to the
   meter's help icon and rewrite it. `g_roots_only` says Remaining is the project's own; it is
   the pool's. Resource Details' summary gets a help icon.
7. **CLI**: `sam-search project` prints the pool `% Used` with "(N yours)" and no owner. Align
   with the web: own % and "from <root>". `sam-search allocations` keeps its group semantics.
   The JSON envelope is unchanged.
8. Tick `SWEEP_FOLLOWUPS_HANDOFF.md` item 3; close `ALLOCATIONS_FOLLOWUPS_HANDOFF.md`'s
   "second segment in `alloc_meter`" item.

Gates: `tests/unit/webapp/test_admin_project_card.py::TestSharedRowHelpTerm`,
`tests/unit/webapp/test_allocations_performance.py::TestProjectListStates`,
`tests/unit/models/test_shared_allocation_usage.py`, the perf tier (counts unchanged),
`ui_snapshots.py --recipes` for the card and modal heights.

## Round C: the jobs explorer, disk scans, shared drill macros (one PR)

Files, all under `src/webapp/templates/dashboards/user/partials/` unless pathed:
`jobs_fragment.html`, `jobs_histogram.html`, `jobs_timeline.html`, `jobs_usage_panel.html`,
`_jobs_filters.html`, `_jobs_macros.html`, `../jobs_explore_page.html`,
`disk_scans_directories.html`, `disk_scans_distribution.html`, `disk_scans_entities.html`,
`_disk_scans_dir_filters.html`, `../disk_scans_directories_page.html`,
`dashboards/fragments/collapse.html`, `dashboards/fragments/sort_link.html`,
`src/webapp/disk_scans/routes.py` (the sort whitelist, ~:76 and ~:135).

0. **Relocate first (Ben, 2026-10-06: "if it makes sense to relocate some partials that's
   valid").** The `jobs_*` / `_jobs_*` and `disk_scans_*` / `_disk_scans_*` partials sit under
   `dashboards/user/partials/` because the user pages used them first; they also serve the
   status pages (`status/partials/job_history.html`, `status/partials/filesystem_scans.html`)
   and the explorer pages, and their routes live in `src/webapp/jobs/` and
   `src/webapp/disk_scans/`. Rule: a partial lives with its blueprint. `git mv` them to
   `dashboards/jobs/` and `dashboards/disk_scans/` in one content-free commit, update every
   `render_template` / `{% include %}` / `{% import %}` (about 25 sites in 8 files; `jobs/routes.py`
   and `disk_scans/routes.py` hold 15 of them) and the gates that name paths
   (`HTMX_FRAGMENT_SHELL_DEPS` in `tests/unit/gates/test_modal_shell_contract.py`,
   `tests/unit/gates/test_collapse_trigger_rows.py`, `tests/unit/gates/test_chart_frame.py`,
   `scripts/ui_snapshots.py`), and prove it with `ui_snapshots.py --styles --compare` (zero
   differ). Leave the Resource Details partials (`day_subtree`, `user_subtree`,
   `_resource_details_macros`) where they are: their blueprint is `dashboards/user/`.
1. **Lift first, then adopt.** `lazy_drill_row(rid, url, colspan, loading)` + `drill_toggle` in
   `fragments/collapse.html`; sites: `jobs_usage_panel.html` (~:128, ~:162),
   `disk_scans_entities.html` (~:104, ~:119), `_resource_details_macros.html` (`jobs_button`,
   `jobs_collapse_row`), the histogram's and timeline's `owner_jobs_drill`, the distribution's
   `dir_drill`. An **owner-tier table** macro: `jobs_histogram.html` ~:243–320,
   `jobs_timeline.html` ~:169–240 and `disk_scans_distribution.html` ~:211–255 are the same
   table (#, owner, stats, share of band); only the histogram adds modal buttons. `sort_link`
   gains `extra_qs=` and `fixed_dir=` so `jobs_fragment.html` (~:63–83) and
   `disk_scans_directories.html` (~:45–61) drop their copies and get the `&nbsp;` icon join.
2. **Jobs**: column roles on every table (none today); `name` onto `.cell-truncate` instead of an
   inline `max-width`; 15 inline styles onto classes (10 are filter widths shared with the disk
   filters: one set of width utilities); `exit_status` through `exit_status_badge`, which has no
   callers, or delete it with `mode_badge`; `href="/dashboards/admin/configuration"` onto
   `url_for`; `page_header` on the explorer page; the inner `table-responsive` in a spanning cell
   (`jobs_histogram.html` ~:242, `jobs_timeline.html` ~:168) never scrolls on a phone, so the
   owner-tier macro renders rows of the outer table (entry 8's item).
3. **Disk scans**: the sort arrow always points down, even for `path`; the Last Access header is
   left-aligned over right-aligned cells; the path cell is `text-nowrap` without `.cell-truncate`,
   so long paths widen the table; the date prints `a[:10]` instead of `fmt_date`. Column roles and
   `page_header`. The hand-whitelisted directory sort is a route concern: if `sort_link` grows
   `fixed_dir`, two lines (`read_sort(...)['sort_by'] or 'size'`); else leave it and record why.
4. Resource Details' subtrees: nothing beyond round A's `user_count` fix.

Gates: `tests/unit/webapp/jobs/` (its `test_jobs_fragment.py` pins the sortable markup),
`tests/unit/webapp/disk_scans/`, the chart smokes, `test_collapse_trigger_rows`, route-map
parity. Browser proof on samuel-dev in the six states; e2e has no page classes for these pages.

## Close-out, per round

A ledger entry (13, 14, 15): mode area `templates`, end commit, done with the PR, tried and
dropped with numbers, open items, and a metrics row from `scripts/sweep_inventory.py --area
templates` (inline styles: 210 in 78 templates at writing). Round B records its deviations from
Unity and the brand guide. Any `lazy_drill_row` or owner-tier site left unconverted goes on the
ledger's Propagation list.

## Open calls for Ben (settled 2026-10-06)

- B.5: Allocated sort on shared rows keeps the pool amount; the glossary says so.
- A.1: the disk scope tree's fileset paths ride a copy button (every abbreviated path gets one).
- C.3: `sort_link` learns `fixed_dir`; `dir_sort_link` goes.

## Follow-ons, not in this pass

- The compute page's eager companion grains (READ_MODEL.md): lazy fragments for the per-day,
  per-user, monthly-count and tree-rollup aggregates; `get_resource_detail_data` runs again per
  chart fragment and `resolve_scope_projcodes` runs twice per page.
- Two `metric_pills` macros with one name (`_resource_details_macros.html`,
  `_jobs_macros.html`), both wrapping `chart_pills`.
- The usage card's By User pane and the Job History card's By User tab do the same job on one
  page from two sources (SAM charges, plugin data).
