# Handoff: charts sweep

**Status:** built on branch `charts-sweep` (26 local commits on `97b8d876`, not pushed, no PR yet);
ledger entry 11 is the as-built record and `JOBS_USER_SUNBURST_HANDOFF.md` the follow-on. Written 2026-10-05 from a planning session (unplanned-city sweep,
wire-dashboard-feature and frontend-design lenses). Base: `origin/staging` at `97b8d876`
(#733 merged). Ben picked on 2026-10-05: all of items 0 to 21, in one PR.

Every `file:line` below comes from three read-only inventories run against that commit, plus a
by-eye pass over ten chart pages. **Re-verify each one before you edit it**; the counts are
leads, not verdicts (sweep skill §3). Items marked *verified* were re-read by hand.

## Context

Ben asked for a sweep of the charts for drift, and for new patterns or improvements that
should be backported to the crusty parts, if any.

**What is already shared, and stays shared:**
- `charts/base.py`: one `BaseChart` lifecycle and `chart_view`, the cache binder.
- `charts/layout.py` + `theme.py`: the two render axes. All 21 call sites forward both.
- `charts/links.py`: one drill scheme (`#sam/<action>/...`), handled by one JS file
  (`static/js/svg-chart-links.js`). No per-chart JS or onclick-style drill is left.
- The fingerprint snapshot: all 17 charts in all six states (227 keys). No undeclared delta
  was found in the commits that could be checked.
- `draw_table_legend`, `label_wedges`, `BaseChart.tooltip`, `facility_palette`: the newest
  pieces, all from the sunburst work (#695 to #731).

**What has drifted:**
- **Three bugs** in the wiring around charts (item 1 to 3).
- **Inside the package:** five ways to build a legend, about seven "top N + Other" folds with
  six spellings of the remainder, four color-assignment rules, font sizes and line widths
  hard-coded in three families, and a few dozen dead aliases and parameters.
- **Around the package:** seven loading placeholders, about ten hand-written pill groups, five
  time-window controls, chart error handling done four ways, chart sizing CSS in six selector
  families with three pie width caps.
- **Prose:** chart counts (16, 17), "eleven key functions", "9 of 18 call sites" (it is 5 of
  21), a lifecycle docstring naming steps that do not exist, three stale Status lines.

**New patterns the older charts lack** (the backport list):
- Hover titles on every mark (`BaseChart.tooltip`): sunbursts only. `CHART_HOVER_LAYER.md`
  stages 2 and 3 are open.
- A table legend that carries the figures: pies, sunbursts, Pace and the status area chart
  have it. Usage trend, disk usage and the jobs timeline list names only.
- `<figure aria-label>` around the SVG: 5 hosts. About 15 charts are unnamed divs.
- Expand to a fullscreen modal, `intersect once` loading, `data-drill-scope`, help-icon
  captions, `aria-pressed` on a pill: allocations page only.

**The crusty parts**, oldest idiom first: the status history pages (`dualpanel.py`,
`queue_history.html`, `nodetype_history.html`), then user resource details, then the status
user/project chart.

**Design stance: one chart frame.** The filters sweep ended with two tiers built from shared
atoms. Charts get the same treatment, as one frame every host builds from:
- a `<figure>` with an accessible name;
- controls above it from one pill macro;
- one loading placeholder and one error state;
- a legend that names each band **with its figure**;
- a hover title on every mark;
- one muted caption line below.

The distinctive element is the sunburst with its table legend. Everything else gets quieter
and uniform. No palette change, no new chart types.

## Verified in the planning session

- **Pace ignores the facility filter.** The loader at
  `allocations/partials/_resource_charts.html:58-60` omits `facilities=`. Its siblings pass
  it (`:43-45` used sunburst; `projects.html:110-111` calendar). `htmx_pace_chart` reads the
  scope from the request (`allocations/blueprint.py:735`), so a filtered page shows every
  facility in Pace. **Seen live:** on `/allocations/projects?facilities=WNA&tab=derecho` the
  table lists WNA only while Pace draws UTAM0017, CESM0002, UBAY0005, NCGD0066 and UAZN0050
  beside the WYOM projects.
- **A dead expand button.** `jobs_usage_panel.html:100-105` draws `expand_button` whenever
  `facility_toggle` is on, which is machine mode (`jobs/routes.py:964`).
  `jobs_explore_page.html` (includes at `:92-93`) has no `#chartExpandModal`. Only
  `status/job_history_page.html:8` and `allocations/projects.html:130` include it. **Seen
  live:** the explorer page has no `#chartExpandModal` in its DOM, and Ben confirmed on
  prod (2026-10-05) that the sunburst does not expand on the exploration page. The local
  job-history Postgres fails By Project with `DiskFull ... could not resize shared memory
  segment`, so smoke the fix on samuel-dev.
- **A login-only request from a public page.** `status/queue_history.html:118-131` loads
  `htmx_user_proj_chart`, which is `@login_required` (`status/blueprint.py:589-591`), from
  `queue_history`, which is public (`:405`). `system_user_proj_chart_card.html:147` guards
  the same card on login. **Seen live:** signed out, the page is a 200 carrying the loader,
  and the loader's request answers 401. htmx does not swap a 401, so the spinner stays.
  `/status/derecho` signed out has no loader, as intended.
- **Mixed tick formats on one axis.** `fmt.mpl_number_formatter` (`sam/fmt.py:514-526`)
  formats each tick alone, so an axis crossing 100,000 reads `350K ... 150K, 100,000,
  50,000`. Seen on the resource-details usage chart.

## Decisions already made

Ben's picks from the ranked report (2026-10-05):

- **All of items 0 to 21, in one PR.** The visible backports (17 to 21) ride with the rest.
- **Ticks: one unit per axis.** When any tick is above 100,000 the whole axis is compact
  (`50K, 100K, 150K`). An axis that stays below keeps exact numbers with commas.
- **The remainder is `N other` wherever the count is known**, and `Others` only where it is
  not. Six spellings exist today: `Others`, `Other`, `N other`, `+N`,
  `N other <panel> projects`, `Other <facility> projects`. The two sunburst phrasings that
  name their parent may keep the parent, in the same `N other ...` shape.
- **Hover titles go to pies, histograms and stacked charts** (`CHART_HOVER_LAYER.md` stages 2
  and 3). Stage 4, the styled tooltip, stays out.
- **A hover that names a project gives its title beside the code, where available**
  (`SCSG0001 · CSG systems project · 1.2M · 3.4%`). This covers the sunbursts that already
  have hovers as well as the charts gaining them.

Standing rules:

- One PR against `staging`, one commit per item, each green on its own
  (`pytest tests/unit/gates tests/unit/charts tests/unit/webapp` plus its domain).
- **Fingerprints are the proof.** A commit meant to change nothing leaves
  `chart_fingerprints.json` untouched. A commit that changes looks regenerates it **in that
  commit** and says which keys moved and why. A desktop-light delta in a mobile or dark
  commit is a bug (CLAUDE.md, Charts).
- The fingerprint does not see path geometry, strokes, opacity or `<title>` text. A
  refactor of drawing code also needs the item 0 contact sheet compared before and after.
- Selected-pill styling stays Unity (selected white, unselected solid blue).
- No palette change. Brand-drift tokens stay on the ledger: fixing them moves every
  fingerprint.
- Work locally. Push as a draft only when Ben wants remote CI.
- An item that turns out larger than its size here stops and is reported.

## Already done (planning session)

- Three inventories (package internals, page wiring, tests and plans). Their findings are
  folded into the items below.
- `ui_snapshots.py` captures of ten chart pages, desktop and mobile, both themes. They were
  written to a session scratchpad and are gone; retake them (item 0).
- Nothing is built.

## Plan

**Setup:**
```bash
git switch -c charts-sweep origin/staging      # in devel/; the untracked handoff docs carry over
docker ps | grep samuel-dev                    # usually under Ben's compose --watch already
docker exec samuel-cache redis-cli -n 0 FLUSHDB
```
samuel-dev is often under `compose up --watch`: keep a name and its import in one write
(wire-dashboard-feature §12.1). Chart caches key on input data, not on rendering code, so
flush Redis after every drawing change or the old SVG is served for 600 s.

### Progress

> **Build notes (2026-10-05).** The reviewed plan with the corrections to this handoff is
> `~/.claude/plans/please-review-docs-plans-charts-sweep-ha-ancient-thacker.md`. Order of
> work differs from the list below: bugs, then the safety net (13 to 15), then 5, 4, 6, then
> 8 to 12, 16, 17 (four commits), 18 to 21, and 7 last. Scratch captures live in the session
> scratchpad (`sheet-ref` is the contact sheet after item 4; `ui-before` the page shots).

Sizes: S is under an hour, M a few hours, L most of a day.

**Tooling**

- [x] `6f6e7766` **0. A way to look at every chart.** (M) Nothing renders all 17 charts in six states
  for review: `/dev/gallery` has no chart section and `ui_snapshots.py`'s default pages
  reach charts only by accident.
  - `tests/unit/charts/chart_samples.py:302` `CASES` holds 51 fixed payloads;
    `test_chart_fingerprints.py` `_render_all` already renders every state and keeps only
    fingerprints.
  - Add `scripts/chart_sheet.py`: render `CASES` to `<out>/<case>__<layout>-<theme>.svg`
    plus one `index.html` contact sheet. Before and after folders compare by eye, and
    `ui_snapshots.py --compare-pixels` works on rasterized copies if wanted.
  - Add the chart pages to a named page set in `ui_snapshots.py` (`--pages charts`):
    `/allocations/projects`, `?view=calendar`, `/status/derecho`,
    `/status/queue-history/derecho/main`, both resource-details variants, the jobs explorer,
    `/user/jobs`, `/user/data`, `/admin/facilities`.
  - Fix `utils/profiling/profile_allocations.py:69-71`, which imports two pie generators
    retired in `7fb6c5f9`.

**Bugs** (regression test first; behavior changes)

- [x] `6985d65a` **1. Pace honors the facility filter.** (S) Pass `facilities=selected_facilities or []`
  in the loader, as its siblings do. Test: `/allocations/projects?facilities=WNA` renders a
  Pace loader URL carrying it.
- [x] `ce282db3` **2. The expand modal exists wherever its opener does.** (S) Include
  `fragments/chart_expand_modal.html` once, in the base the hosts share, and pin
  `jobs_usage_panel.html` in `HTMX_FRAGMENT_SHELL_DEPS`
  (`tests/unit/gates/test_modal_shell_contract.py`). Ledger entry 9 already lists the
  chart-expand shell as hand-rolled; moving it onto `modal_scaffold` is optional here.
- [x] `aea61e30` **3. Queue history does not ask an anonymous visitor for a login-only chart.** (S)
  Guard the loader as `system_user_proj_chart_card.html:147` does. Test: an anonymous GET
  of the page has no `user-proj-chart` `hx-get`.
- [x] `b2e9c169` **4. One unit per axis.** (M) Visible; moves fingerprints wherever ticks cross 100,000.
  - Replace the per-tick `FuncFormatter` with a `ticker.Formatter` that decides once in
    `format_ticks` from the largest tick.
  - `DistributionHistogram` sets a y formatter only for `metric == 'files'`
    (`histogram.py:241-242`); check its bytes axis reads sensibly.
- [x] `7c8a0f3f` **5. Small wrong-text leads.** (S) Verify each, then fix:
  - `resource_details_disk.html:283` says "top 10 users + Others"; the route asks for
    `top_n=15` (`user/blueprint.py:1058, 1068`).
  - Partition history reuses the node-type chart, so its empty state says "node type"
    (`status/blueprint.py:386`).
  - `DistributionHistogram.is_empty` keys on labels, so all-zero data draws empty axes
    (`histogram.py:199-202`); `JobsHistogram` checks values.
  - The used-window caption is written twice and differs: `used_sunburst.html:28-32` and
    `allocations/blueprint.py:863-864`.

**Dead code and stale prose** (no output change; fingerprints untouched)

- [x] `59b7152d` **6. Delete what nothing reaches.** (M) Confirm each with a grep over `src/` and
  `tests/`, and the legend path with coverage.
  - `pie.py:134-152`: the `ax.legend` fallback. Every pie layout is `legend_placement='right'`,
    so `draw_table_legend` returns first.
  - `pace.py:41` `_PACE_TODAY_LINE_COLOR` and its `UNITY_NCAR_NAVY` import.
  - Facade aliases nobody imports (`charts/__init__.py:191-199`): 7 of the 11 cache-key
    aliases, `_autopct_color_for`, `_shade_family`, `_JOBS_METRIC_LABELS`. Eight more live
    only because `test_chart_module_boundaries.py:143-150` lists them.
  - Parameters no caller passes: `PaceChart(window_days, top_n)`, `JobsUsagePie(unknown_label=)`;
    `PACE_WINDOW_DAYS` in `__all__`.
  - `generate_jobs_user_pie_chart` (`__init__.py:166-185`) has no `src` caller.
    `test_chart_cache_registry.py` pins "17 caches for 18 generators"; update it.
  - `JobsTimeseriesChart`: `_bar_kwargs` (`stacked.py:510-511`) respells a default;
    `prepare` computes `jobs_timeseries_series` and discards it (`:494`, recomputed `:499`).
  - Restated defaults: `legend_anchor` (`pie.py:64`, `stacked.py:68`, `pace.py:156`),
    `legend_fontsize` (`stacked.py:67`, `dualpanel.py:34`), `title_fontsize=12`
    (`stacked.py:184`, no legend title exists).
  - Templates: the `{% if chart_svg %}` else branch (`queue_history.html:89-93`), the
    always-true guard (`facility_card.html:43`), the commented title that still ships
    (`user_proj_chart.html:117-120`), `<center>` (`nodetype_history.html:134-140`), unused
    `has_data` in the disk usage fragment, the unused `loading_spinner` macro.
  - CSS: `allocations.css:31-44` (only Pace uses `.chart-container` on that page).
- [x] `7359ddbc` **7. Prose says what is true.** (S)
  - Counts: `__init__.py:121` and `base.py:339` (16 charts), `base.py:453` ("eleven" key
    functions; there are 16), `theme-toggle.js:18`, `test_chart_fingerprints.py:274`
    ("fifteen"), `test_layout_transport.py:12, 189` ("four pies"),
    `test_chart_module_boundaries.py:75` ("twelve modules").
  - CLAUDE.md Charts: "9 of 18 call sites" is 5 of 21; the gate it names is
    `test_renderers_forward_the_axis_they_are_given`; "several charts hold ndarrays" is
    Pace only.
  - `base.py:21, 181` (lifecycle names `to_svg` and `autofmt_xdate`), `dualpanel.py:6-9, 70`,
    `pace.py:13-16, 367-369`, `layout.py:73`, `links.py:19-20, 124-131`.
  - "No chart SVG in cached routes": `extensions.py:58-66` and
    `test_theme_transport.py:190-201`. `/allocations/projects` is cached and inlines
    sunbursts, so the theme half of that key is load-bearing.
  - `layout-axis.js:10, 73-74`; `status/blueprint.py:560-563`; `dashboard.css:1787`.
  - `docs/plans/implemented/CHART_ARCHITECTURE.md`: Status says "PROPOSED"; the family
    table lists retired pies and no sunbursts; the lifecycle lists hooks that do not exist.
  - Status lines: `ALLOCATIONS_SUNBURST.md:3`, `PACE_AFTER_BURN_HANDOFF.md:3`; add one to
    `CHART_HOVER_LAYER.md` and `PANEL_SUNBURST.md`.

**Consolidate inside the package** (fingerprints untouched unless the item says otherwise)

- [x] `e8feec95` **8. One `decorate` in the stacked family.** (S) Four copies at
  `stacked.py:147, 355, 432, 526`; `UserProjAreaChart`'s is the base verbatim
  (`dup-functions`, 42 nodes; ledger entry 4 lists it open). `stacked.py` is 549 lines
  against the gate's 550, so this also buys room.
- [x] `839f3a94` **9. Family-level homes for per-chart copies.** (M)
  - `DiskEntityPie.build` and `UserUsagePie.build` (`pie.py:192-215, 246-266`) are near
    identical; `JobsUsagePie.build` (`:322-338`) is a variant. One `_CumulativePie.build`
    with hooks.
  - `NodetypeHistoryChart.cache_key` and `QueueHistoryChart.cache_key` are identical
    (`dualpanel.py:112-118, 184-187`), each importing `content_hash` lazily.
  - `UsageTrendChart` and `UsageTrendStackedChart` share `bar_url` and `ylabel`
    (`stacked.py:251-259, 289-295`).
  - `PanelSunburst.is_empty` (`sunburst.py:387`) copies `pie.py:103`.
  - Ring scaffolding and the hover text join are written twice or three times
    (`sunburst.py:147, 181, 391`; `draw` at `:149-198` and `:393-450`). Give `PieChart` the
    `tooltip_text` hook `CHART_HOVER_LAYER.md` already plans.
- [x] `1639c95e` **10. Two legends, not five.** (M)
  - Keep: the table legend (`draw_table_legend`) and, for line charts, an artist legend
    through `legend_kwargs`.
  - Pace's hand-rolled swatch zip (`pace.py:361-373`) becomes `link_legend(ordered=True)`,
    whose only production caller today is the stacked family.
  - `dualpanel.panel_legend` (`:78-86`) ignores `legend_kwargs` and, on desktop,
    `layout.legend_fontsize`.
  - `legend_cells` has two signatures: `(label, value)` on pies, `(band)` on stacked.
  - Capping: stacked caps rows, pies cap data, Pace clamps `top_n`; sunbursts and dual-panel
    do not cap although mobile asks for 6.
- [x] `f75abfda` **11. Sizes and inks come from `Layout` and `Theme`.** (M) Mobile and dark
  fingerprints move; desktop-light must not.
  - Histogram axis labels take no size (`histogram.py:152`): 11pt on a phone where the
    profile asks for 9. Stacked and Pace use `label_kw`; dual-panel passes
    `layout.base_fontsize` by hand.
  - Hard-coded: dual-panel line widths 3 and 2 in ten `plot` calls (`:142-215`); annotation
    size 8 (`pace.py:332, 338`); sunburst sizes (`sunburst.py:273, 347-349`).
  - `'#fff'` and `theme.name == 'light'` at `sunburst.py:252`: use a theme role and
    `Theme.is_dark`.
  - Pace bakes alpha 0.85 into "Other" (`pace.py:58`); the stacked family uses
    `theme.area_alpha`.
  - Muted text by alpha, three values: `base.py:248`, `sunburst.py:197, 449`.
  - The same blue spelled two ways: `stacked.py:242`, `histogram.py:321`.
- [x] `84d7a235` **12. One top-N fold and one remainder label.** (M) Visible where the label changes
  (`N other` where the count is known; see Decisions).
  - Seven folds: `pie.trim_cumulative` / `split`, `histogram.bucket_segments`,
    `jobs_metrics.jobs_bucket_segments` and `jobs_timeseries_series`, Pace `top_n`,
    `PanelSunburst._fold`, `StackedSeriesChart.legend_entries`,
    `jobs/routes.py:_facility_rings`.
  - Put the fold in `series.py` (no matplotlib; the module-boundary gate holds).
    `jobs_timeseries_series` uses a literal `'Others'` instead of `series.OTHERS`.

**Safety net**

- [x] `80029be6` **13. The axis-forwarding gate covers every caller.** (S)
  `test_renderers_forward_the_axis_they_are_given` (`test_layout_transport.py:101, 133`)
  scans `jobs.routes` and `disk_scans.routes` only: 7 of 21 call sites. Its check is
  textual, so one forwarding call satisfies a renderer with two. Walk the AST of all six
  calling modules and check each `generate_*` call.
- [x] `46dd6853` **14. Route smokes where there are none.** (S) Only a route-map entry pins:
  `/user/resource-details/usage-chart` (both usage trends), the user/project chart routes
  (`status/blueprint.py:580-620`), the jobs `/timeline` panel, and the fair-share SVG on the
  facilities card. Assert an `<svg` and one drill href each.
- [x] `b2be5892` **15. Small pins.** (S)
  - `PanelSunburst` declares its own profile (`sunburst.py:339`) and is missing from
    `LAYOUT_OWNERS`; its 7in mobile figure breaks the phone-size rule
    (`test_chart_layout_axis.py:146`). It is hidden below `md` by design: exempt it by name.
  - `Theme.LIGHT.surface` and `shade_toward` against light `--surface-card`
    (`test_css_tokens.py:269` pins dark only).
  - `facility_slots` has no direct test and four separately spelled callers' queries.
- [x] `bb980419` **16. Chart exceptions have one rule.** (M) Behavior change.
  - Today: the distribution histogram is rendered inside a `try`
    (`disk_scans/routes.py:670-683`); the entity pie beside it is not (`:608`); jobs wraps
    the service call but not the chart (`jobs/routes.py:969, 1169, 1288`); the expanded
    panel wraps nothing (`:2033`); nor do the ten dashboard and admin sites.
  - htmx does not swap a 500, so a chart that raises leaves its spinner up forever.
  - `BaseChart` must keep not swallowing (CLAUDE.md). Add one caller-side helper that logs
    and renders the shared error state, and use it at all 21 sites.
    `CHART_ARCHITECTURE.md:586` names this as the follow-up.
  - Callers also decide emptiness two ways: five return `None` before calling the chart
    (`jobs/routes.py:989, 1213, 1320`; `disk_scans/routes.py:607, 672`), one passes an empty
    dict to get the chart's own empty state (`user/blueprint.py:956`). Pick the second.

**Backport the new patterns** (visible; each commit declares its fingerprint keys)

- [x] `49dfb5c1 1f7b4a33 f9b45622 59bc284d` **17. Hover titles everywhere, with project titles.** (L) Picked.
  - **Rollout.** `CHART_HOVER_LAYER.md` stages 2 and 3: single-ring pies (`pie.py:106`), then
    histograms and stacked charts. Marks only, never legend entries (`base.py:389`).
    `test_chart_tooltips.py:63` covers three sunbursts; extend it to every chart that draws
    marks.
  - **Project titles.** Wherever a mark is a project, its hover reads code, then title, then
    the figures. The charts that name projects: `PanelSunburst` (the rim, which relies on
    hover for its names), `JobsFacilitySunburst` (outer ring), `JobsUsagePie` by project,
    `PaceChart`, `UserProjAreaChart` and `JobsTimeseriesChart` when grouped by project, and
    any histogram segment keyed on a project.
  - **Charts stay query-free.** A chart takes an optional `titles` mapping
    (`{projcode: title}`); the route fetches it with `sam.queries.projects.project_titles`
    (`projects.py:115`), which is one `IN` query. Job-history rows come from the plugin and
    carry codes only, so those routes need the lookup too. A code with no title (unknown to
    SAM, or the mapping not passed) keeps today's text: that is "where available".
  - **One place builds the text.** Fold the three copies of the join (`sunburst.py:147, 181,
    391`) into the `tooltip_text` hook from item 9, and add the title there. Cap a long title
    (about 80 characters, with an ellipsis).
  - **Cache key.** `chart_view` keys on the generator's arguments, and
    `test_chart_cache_key_signatures.py` requires the key to accept each one. Hash the
    `titles` mapping into the key so a retitled project does not serve the old hover.
  - **Costs to measure.** Query counts move by one on pinned routes (`allocations_pace_route`,
    `sunburst_expanded` x2 in `tests/perf/baselines.json`): update the pins in the same
    commit. `PanelSunburst` draws every project and was 248 KB (`PANEL_SUNBURST.md:41`);
    record its size with titles.
  - **Who may see a title.** Check each surface before adding titles: the viewer should
    already be able to open that project (the Allocations and admin pages are behind
    `VIEW_PROJECTS`). The status user/project chart shows codes to any signed-in user; ask
    Ben before giving that one titles.
  - Fingerprints: `<title>` text is not in the fingerprint, only the `<g>` count. The new
    tooltip tests are the pin for the text.
- [x] `de26cdd2` **18. Legends carry the figures.** (M) `table_legend=True` with a total per band for
  `UsageTrendStackedChart`, `DiskUsageAreaChart` and `JobsTimeseriesChart`. The status area
  chart is the model (`stacked.py:391, 427`). Check the legend still fits at tablet width.
- [x] `b214621d` **19. One chart frame in the templates.** (L) New macros in
  `dashboards/fragments/chart_bits.html`; `--element` shots before and after per host.
  - `chart_figure(label)`: `<figure aria-label>` around the SVG. Today on 5 hosts
    (`_resource_charts.html:23, 30, 41`; `facility_card.html:44`; `chart_expanded.html:19`).
  - `chart_loading(text)`: one placeholder with `role="status"`. Seven variants today, four
    spacings; the allocations variant reserves height (`components.css:603`), which stops
    the page jumping. Use that.
  - `chart_pills(...)`: about ten hand-written groups, two re-fetch styles (URL-baked;
    hidden form plus `hx-include`). Only the calendar sets `aria-pressed`. The disk usage
    chart uses `nav-tabs` as a metric selector (`disk_usage_chart.html:16-38`).
  - Captions: the "Charges are hours weighted" note is written three times
    (`jobs_usage_panel.html:84-89`, `jobs_histogram.html:165-170`,
    `jobs_timeline.html:109-114`). "Reload the page to reset all selections."
    (`usage_chart.html:22-28`) tells the reader to work around the page.
  - Time windows are five controls: `time_range_picker`, a hand-written `?hours=` strip that
    repeats its presets (`system_user_proj_chart_card.html:154-160`), `drp`, jobs day
    pills, used-sunburst day pills. `window_pills` has no chart caller. Fold the duplicate
    strip; leave the full-page pickers.
  - Row-drill triggers split: allocations uses `show.bs.collapse from:closest tr`,
    everything else `shown.bs.collapse from:closest tr.collapse`.
- [x] `4d8889e3` **20. Chart CSS in one file.** (M) `charts.css`, as `allocations.css` and `filters.css`
  did.
  - Sizing lives in six selector families (`dashboard.css:1786-1883`,
    `components.css:565-608`, `allocations.css:31-44`), with three pie caps: 720px, 880px,
    44rem.
  - The tablet gutter list omits `.sunburst-chart` and `.fair-share-chart`.
  - Pace nests `.chart-container` inside `.chart-container` and gets the negative gutter
    twice below 1200px. Measured at 1024px: the SVG spans 20 to 989 while its `.pace-chart`
    box spans 36 to 973, so it bleeds 16px past its own container on each side. The page
    does not scroll sideways.
  - `rolling_rate_htmx.html:110-171` hard-codes two `rgba()` colors: a dark-mode lead.
- [x] `f36ef483` **21. The status history charts catch up.** (M) The oldest idiom. After items 10, 11
  and 19 most of it is done; what is left is the page: the `<h6>` title inside the
  container, and a full-page reload to change the window where every newer chart swaps a
  fragment.

## Ledger only, do not build

- Palette single-sourcing and the brand-drift tokens (`CHART_ARCHITECTURE.md:784`; ledger
  entry 5): every fingerprint moves.
- The `auto` theme (`DARK_MODE.md:293`), a styled tooltip (`CHART_HOVER_LAYER.md:115`),
  per-surface figure tuning (`MOBILE_CHARTS.md:287`).
- The expanded chart reuses the viewport's layout and is stretched by CSS; a dedicated
  large layout is a design change. Measure first.
- "Others" is the largest band on the status area chart at top 15 (99 of about 400 running
  jobs on Derecho). A data-design question, not drift.
- JS duplication: the param-stripping `configRequest` code in `layout-axis.js:75-84` and
  `nav-view-persistence.js:350-358`; two cookie writers, only one setting `Secure`; the
  breakpoint strings in four files.
- Facility ordering written three times (`sunburst.py:320-322`, `jobs/routes.py:858-860`,
  `allocations/blueprint.py:359`, the last by id rather than slot).
- The #707 review items already on ledger entry 1: the run-out tick's color token, Pace's
  zero projection for a new project, calendar month figures living only in hover titles.
- `CacheBase.bytes_used` (ledger entry 4); matplotlib's own ids repeating when one cached
  SVG appears twice on a page.

## Verification

- Per commit: `pytest tests/unit/gates tests/unit/charts tests/unit/webapp` plus the domain
  directory. Read pytest's return code, not a `| tail`.
- Before the PR: full `pytest` on MySQL, the Postgres run, `pytest -m perf -n 0`, and `e2e/`
  against samuel-dev.
- Gates: chart fingerprints, chart module boundaries (no matplotlib in `links.py`,
  `series.py`, `jobs_metrics.py`; 550-line cap per module), chart fonts, cache registry
  (`chart_view` order and names are Redis key prefixes: do not reorder), CSS tokens, CSS
  dead, template CSP lint, modal shell contract, docs.
- **No-change commits (6 to 15):** `chart_fingerprints.json` is untouched, and the item 0
  contact sheet matches before and after for any commit that edits drawing code.
- **Visible commits (4, 11, 12, 17 to 21):**
  `CHART_FINGERPRINT_REGEN=1 pytest tests/unit/charts/test_chart_fingerprints.py` in the same
  commit, with the moved keys named. Then the contact sheet and `ui_snapshots.py` over the
  chart pages in 3 layouts x 2 themes, reviewed by eye. Measure contrast in the page for
  any new ink.
- Template moves onto macros (19): `ui_snapshots.py --element` on both sides and
  `--compare-pixels`.
- Hand smoke checks:
  - Filter `/allocations/projects` to one facility; Pace shows only its projects.
  - On the machine jobs explorer, By Project, By facility: the expand button opens the
    panel sunburst.
  - Hover a thin rim wedge on the expanded panel sunburst: the title reads code, project
    title, then the figures.
  - Signed out, `/status/queue-history/derecho/main` issues no 401.
  - A usage chart whose ticks cross 100,000 reads in one unit.
- After a deploy with a visible change: `sam-admin cache --refresh --category chart`.

## Close-out

- Ledger entry 11 in `docs/plans/UNPLANNED_CITY_LEDGER.md`: mode and area (`py` +
  `templates`, charts), the base commit, what was done with the PR, anything tried and
  dropped with numbers, a metrics row from `scripts/sweep_inventory.py`, and the "Ledger
  only" list above moved onto the entry's open list.
- Tick the chart items other entries carry: the stacked `decorate` (entry 4), the
  chart-expand shell (entry 9), the stale Status lines (untriaged, docs).
- Update `CHART_ARCHITECTURE.md` (family table, lifecycle, file layout) and CLAUDE.md's
  Charts section for anything that moved; the docs gate holds CLAUDE.md to its budget.
- Add the chart frame to `wire-dashboard-feature` §1 (its budget is 275 lines; it is at 266).
- Growth rule: this sweep's new class is **a shared opener whose shell each page must
  remember to include** (item 2). The modal-shell gate can catch it; pin it there. If
  another class turns up, add its heuristic to the sweep skill or a detector to
  `scripts/sweep_inventory.py`.
