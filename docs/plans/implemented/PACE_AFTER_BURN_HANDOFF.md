# Handoff: Pace after Burn (lazy load, run-out marker, Pace on burn data)

**Status: built** (#707, #710 and #716 merged 2026-10-03 and 2026-10-04; #709, the lazy-load PR,
was closed unmerged, and the `intersect once` loading it proposed is in the tree). Written as:
planned, partly started (2026-10-03). Untracked resume note: on approval this file is
saved as `docs/plans/implemented/PACE_AFTER_BURN_HANDOFF.md` in the `devel` worktree. When Track 3 starts,
fold its decisions into `docs/plans/implemented/ALLOCATIONS_TABLE_VIEWS.md` (a "Pace" section) on that branch.

## Progress

- [x] **Track 1** (new PR, base `staging`): lazy-load the per-resource Pace and Used-ring charts -> #709 (draft)
- [x] **Track 2** (on `calendar-burn`, PR #708): finish the run-out marker; move burn math into `burn.py` -> 86d1cdde
- [x] **Track 3** (new PR stacked on `calendar-burn`): Pace reads the burn data -> #710 (draft, branch `pace-burn`)
  - [x] 3.1 shared fetch with the calendar
  - [x] 3.2 monthly past side
  - [x] 3.3 projected area + **committed** line
  - [x] 3.4 drop `window_used`
  - [x] 3.5 perf baseline + docs

## Where things stand

- **PR #707** `allocations-table-views` -> `staging`: open, CI green, not yet reviewed (at-date
  table + calendar).
- **PR #708** `calendar-burn` -> base `allocations-table-views`, **draft**. Five commits: plan doc,
  `get_allocation_burn` + `cached_allocation_burn` (own `allocation_burn` bucket, 12 h TTL), burn
  geometry, Used | Burn UI, perf baselines. Full suite and perf tier green locally. Remote CI
  probably will not run until it is retargeted to `staging` after #707 merges:
  `git rebase --onto origin/staging f3532e14 calendar-burn`, then `gh pr edit 708 --base staging`.
  GitHub may close the child PR when #707's branch is deleted; see memory
  `reference_stacked_pr_reopen` for the reopen order.
- **Uncommitted on `calendar-burn`** (untested, `src/webapp/dashboards/allocations/calendar.py`):
  `RUNOUT_LOOKBACK_DAYS = 90`, `RUNOUT_MARGIN_DAYS = 30` (`calendar.py:18-19`); `runs_out(row,
  months_charges, through)` (`:127-144`); `calendar_rows` sets `bar['runs_out']` and
  `bar['runs_out_left']` (`:191-206`). No template, CSS, test or doc work yet. Track 2 picks it up.
- Not measured yet: cold burn timing on samuel-dev Postgres (needs this branch deployed there;
  Ben owns deploy mechanics).

## Context

#708 added Burn mode to the allocations calendar: each month of an allocation's bar is shaded by
its charges against an even pace (`get_allocation_burn` returns `{allocation_id: {yyyymm:
charges}}`). Ben then asked about a "future risk" view (a project that ran cold and ramps up) and
to examine the Pace chart for gaps, duplication and refactoring the burn data makes possible.

### Future-risk analysis (local MySQL snapshot, as of 2026-10-03, Derecho; Casper in parentheses)

1,054 current allocations with >= 30 days left (1,156):

| Signal | Projects | Reading |
|---|---|---|
| Catch-up rate >= 1.25x (unspent / even share of time left) | 715, 68% (844) | Noise, and Pace already draws it |
| Last ~90 days >= 1.25x even pace | 124 (84) | |
| Cold before (< 0.75x), hot now, balance large | 40, 4% (28) | Real but small: 1.9% of remaining balance, ~+15M over 90 days |
| At the 90-day rate, runs out >= 30 days before its end | 89, 8% (54) | The actionable signal: a date |

**Decided:** no "future risk" toggle, no projected calendar cells, no facility demand forecast.
Instead a run-out marker on Burn bars (Track 2), and a projected future on Pace (Track 3).
Look-back is **90 days** (Ben).

### Pace gaps (same snapshot)

| # | Gap | Evidence |
|---|---|---|
| G1 | **Eager load**: `_resource_charts.html:38` (Used ring) and `:53` (Pace) use `hx-trigger="load"`, so every resource tab fetches both while hidden | dev log: ~10 pace-chart requests per page view; #691 measured 9-11 s each cold on prod |
| G2 | **Future side is the committed ceiling, not a forecast**: height = unspent / days left (`charts/pace.py:95-98`) | Derecho 10.3B/yr vs 2.0B/yr actually burned (5.1x); Casper 6.4x |
| G3 | **Past side is a flat average** over the 180-day window (`pace.py:83-93`) | Derecho flat 2.27B/yr; monthly actuals 165M, 207M, 206M, 219M, 206M, 211M (Apr-Sep) |
| G4 | **Duplicated fetch**: Pace caches ±180-day usage rows (`blueprint.py:710` route), the calendar caches 12+12-month rows; `window_used` and the `('window', id)` anchors (`sam/queries/allocations.py:914`, `:937`) exist only for Pace | grep: Pace is the only consumer |
| G5 | **ymax clamp hack** (`pace.py:345-351`) exists for near-expiry spikes of unspent/1 day | |
| G6 | `sort_by='future'` is documented as "the risk signal" (`pace.py:145-148`) but ranks the ceiling, so mostly idle projects | |

**Decided (Ben, 2026-10-03):** Pace's future side = **projected demand as the stacked area, plus
the committed total as a dashed line**. The gap is latent demand ("future risk" at machine scale).
Name it **committed**, never "required": it is what allocations promise to deliver by their end
dates, a commitment we made but cannot fill, and overcommitted because many projects never use
their allocation. Use that word in the label, legend, footnote, docstrings and docs.

## Track 1: lazy-load the per-resource charts (small PR, base `staging`)

Independent of #707/#708 and the biggest cost win. Branch from `origin/staging`.
- `src/webapp/templates/dashboards/allocations/partials/_resource_charts.html`: the Used-ring
  loader (`:38`) and the Pace loader (`:53`) go from `hx-trigger="load"` to `"intersect once"`.
  The calendar already uses this idiom (`projects.html`, Calendar pane): a hidden tab pane never
  intersects, so only the visible resource fetches; a tab or pill reveal fires the rest.
- Check: `nav-view-persistence.js` injects saved `sort_by` / `days` on `htmx:configRequest`
  (trigger-agnostic); `mirrorResourcePane` in `dashboard-init.js`; the e2e allocations tests
  (`e2e/test_allocations_tree.py`) still find the charts after a tab click.
- Verify in the dev log: one page view requests pace/ring only for the active tab.

## Track 2: finish the run-out marker (on `calendar-burn`, PR #708)

- New pure module `src/webapp/dashboards/allocations/burn.py` (no Flask, no matplotlib): move
  `BURN_EDGES`, `burn_class`, `burn_key`, `burn_through`, `_month_shares`, `_burnable` and the
  uncommitted `runs_out` out of `calendar.py`; add `recent_rate(row, cells, through, days=90)`
  (sum of month cells overlapping the look-back, a partial month pro rata, over the days in it),
  which `runs_out` then calls. `calendar.py` keeps geometry and imports these. Update imports in
  `blueprint.py` and the tests.
- `runs_out` rule: only for a burnable, current allocation (`start < through <= end`), with
  unspent > 0 and recent daily rate > 0; date = through + unspent / daily; returned only when
  `<= end - 30 days`.
- UI (Burn mode): in `calendar_rows.html`, `<span class="burn-runout" style="left: X%"
  title="At its last-90-day rate, runs out about {{ date | fmt_date }}">` inside the bar, and the
  same clause in the bar's title. Key item in `calendar.html`: a tick swatch, "runs out early at
  its 90-day rate". CSS in `components.css` near the `.burn-*` rules: absolute, full bar height,
  2px, `var(--data-burn-4)`, 1px `--surface-card` halo. Tokens only.
- Tests in `tests/unit/webapp/test_allocations_calendar.py`: exact date on a 365-day allocation;
  pro rata partial month; none within 30 days of the end, at zero recent rate, for open-ended,
  ended or future allocations, or when unspent <= 0; position in % of the bar; route renders
  `burn-runout` in Burn mode only.
- Docs: a "Future risk (decided 2026-10-03)" item in the Burn section of
  `docs/plans/implemented/ALLOCATIONS_TABLE_VIEWS.md` with the analysis table above. Expect ~89 marks on
  Derecho as of 2026-10-03 (spot-check UHWM0061: 8.5x recent, ~27 days to run out, 120 left).
- Push to `calendar-burn`; add the commit to #708's body.

## Track 3: Pace reads the burn data (new PR stacked on `calendar-burn`)

1. **Shared fetch.** `htmx_pace_chart` (`blueprint.py:710`), non-disk path: read
   `cached_allocation_usage_rows` over `calendar_window(active_at)` and `cached_allocation_burn`,
   the calendar's exact cache entries, via `_calendar_rows` (`:821`, filters by facility) and
   `_calendar_burn` (`:840`). The x-range stays ±`PACE_WINDOW_DAYS` (180). Disk keeps its
   occupancy path. Visiting either view warms both.
2. **Monthly past.** `pace_bands` (`pace.py:60`) past region = per-allocation monthly rate (cell
   charges / cell days, using `burn.py`'s month spans) instead of one flat average; stacked, it is
   the resource's actual monthly charge rate.
3. **Projected area + committed line.**
   - Area per allocation: current -> `recent_rate` until `runs_out` (or its end date), then 0; not
     yet started -> its project's recent ratio (recent charges / recent even share on the
     project's latest started allocation) x its own even rate, or even rate if the project has no
     history; ended -> nothing.
   - Dashed line, **committed**: summed unspent / days left (today's future side), over the stack.
   - y-axis fits the area and the past; an off-scale committed line is clipped at the top with its
     value labeled at today ("committed 10.3B/yr"). Remove the G5 clamp.
   - Sorts: `size` unchanged; `past` = recent actual rate; `future` = projected rate at today.
     Legend numbers follow the sort. Rewrite the docstrings and comments in `pace.py` to say
     "committed" where they say "required rate to completion".
   - Footnote (`pace_chart.html`): the area is projected use at each allocation's last-90-day
     rate; the dashed line is what allocations commit to deliver by their end dates, more than
     will be used.
   - Regenerate chart fingerprints in this same commit:
     `CHART_FINGERPRINT_REGEN=1 pytest tests/unit/charts/test_chart_fingerprints.py`.
4. **Drop `window_used`.** Remove it and the `('window', id)` anchors from
   `get_allocation_usage_rows` (`sam/queries/allocations.py:869`; fewer anchors on every rows
   query); update `pace_key_fields`, `tests/unit/queries/test_allocation_usage_rows.py`,
   `tests/unit/charts/test_pace_window.py`.
5. **Perf + docs.** A perf-tier baseline for the Pace route (`tests/perf/test_route_query_counts.py`
   + `baselines.json`: set 0, run, pin measured + ~10); a "Pace" section in
   `ALLOCATIONS_TABLE_VIEWS.md` with the gaps table, decisions and measurements. After merge,
   update memory `project_pace_chart_window`.

Reuse: `_calendar_rows`, `_calendar_burn`, `calendar_window`, `cached_*` in
`sam/queries/usage_cache.py`, `fmt.number` for the line label, `BaseChart.apply_date_axis`,
`theme.accent` styling for the dashed line.

## Verification

- Numbers (local MySQL, Derecho, as of 2026-10-03): past steps equal the monthly actuals x 12
  (Apr 165M ... Sep 211M per month); projected area at today ~2.0B/yr; committed line ~10.3B/yr.
- Unit: `burn.py` (pro rata recent rate, run-out date, renewal inherits ratio, no-history even
  rate); pace bands (monthly past, projection drops to 0 at run-out, ended allocations absent);
  rows query without `window_used`. CI runs MySQL and Postgres.
- Gates: chart fingerprints (regen only in the commit that changes the visual), chart module
  boundaries (`burn.py` imports no matplotlib), css tokens, CSP lint, docs, route map; full suite
  (`source etc/config_env.sh && pytest`); `pytest -m perf -n 0`.
- Timing: Pace after the calendar is warm should hit cache for both data sets; cold-cold on local
  MySQL vs today's ±180-day path.
- Browser smoke on :5050 (`docker exec samuel-cache redis-cli -n 0 FLUSHDB`; restart samuel-dev
  for CSS `?v=`): Derecho and Casper, light and dark, desktop and 390 px; the dashed line and its
  label; sort pills; the calendar's Burn view and run-out marks agree.

## How to resume

1. `cd /Users/benkirk/codes/project_samuel/devel && source etc/config_env.sh`; `git fetch origin`;
   check whether #707 and #708 have merged and restack if so.
2. Read this file, then `docs/plans/implemented/ALLOCATIONS_TABLE_VIEWS.md` (Burn section),
   `src/webapp/dashboards/allocations/calendar.py` and `src/webapp/dashboards/charts/pace.py`.
3. Track 1 first (its own branch from `origin/staging`), then Track 2 on `calendar-burn`
   (`git diff` shows the uncommitted `runs_out` work), then Track 3 on a branch from `calendar-burn`.
4. Commit per step; push drafts when remote CI is wanted.
