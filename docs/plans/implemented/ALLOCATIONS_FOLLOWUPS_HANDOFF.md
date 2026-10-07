# Handoff: allocations follow-ups after #707 and #711

**Written 2026-10-03, about 20:30Z, for a clean session.** Untracked on purpose.

**Resume prompt:**

> Read `docs/plans/implemented/ALLOCATIONS_FOLLOWUPS_HANDOFF.md`. Ask me which item to start: the dev
> click-through (needs me at the laptop for 2FA), the Burn query grouping, or the tidy-up.

## Where things stand

| | |
|---|---|
| #707 at-date table, calendar + Burn, Pace on burn data | merged to staging 19:38Z (`68a0edbf`), includes four review-fix commits |
| #711 one builder for the batch charge sums | merged to staging 19:50Z (`a16f9c7b`) |
| samuel-dev | serving `sha-a16f9c7`, healthy, no errors in the pod log since the roll |
| prod | **not promoted.** Ben decides; see item 1 |
| local worktree | on branch `charge-sum-builder` (merged; safe to delete). Branch new work from `origin/staging` |

What was verified on dev for #711: the hourly `refresh_allocation_state` task ran on the new
image at 20:07Z with the same 1,493 projects and 4,725 rows in 3.9 s (3.8 s before). fstree
output had identical structure; 174 of 77,526 values moved, all usage and balance figures,
consistent with the hour's new charges.

What was **not** verified anywhere but CI: the calendar, Burn and Pace pages on a real
deployment. They are session-only.

## 1. Before prod: look at the new pages on dev

The only thing between staging and prod. Both unknowns are about speed, not wrong numbers:
a cold Pace or Burn load on dev (Postgres), and the same on prod (MySQL), where the old Pace
took 9-11 s cold (#691) and the new one does more work (25-month rows plus the burn query).

1. Ben at the laptop: `python scripts/dev_capture_session.py` (headed browser, one login with
   2FA; the login path is rate limited, so log in once). Export the
   `SAM_E2E_STORAGE_STATE` path it prints. The cookie is a credential: scratchpad or env only.
2. Make the next request cold: the `dev_cache_refresh` recipe in the `Makefile`
   (`SAM_API_USER=cacheref`, `SAM_CACHE_REFRESH_API_PASS_DEV`,
   `SAM_API_BASE=https://samuel-dev.k8s.ucar.edu`), with `--category usage` and then `chart`.
3. Click through `/allocations/` on Derecho and Casper: Table, Calendar, Calendar -> Burn,
   Pace with all three sorts; open a facility and a type group in each. Light and dark, and
   one pass at phone width. The fragment routes, if driving them directly
   (`src/webapp/dashboards/allocations/blueprint.py:718-883`):
   `/allocations/htmx/calendar/<resource>?active_at=YYYY-MM-DD[&mode=burn]`,
   `.../calendar/<resource>/rows?facility=&allocation_type=&mode=`,
   `/allocations/htmx/pace-chart/<resource>?sort_by=size|past|future`.
   `scripts/dev_session_load.py --list` has no calendar or Pace target yet.
4. Read each request's cost from the pod log:
   `kubectl --context nwc1 -n sam-queries-dev logs deploy/samuel-dev --since=10m | grep 'webapp.run.*allocations'`.
   The line reads `(total ms cpu=… sam=…ms/Nq …) rid=…`. Record cold and warm numbers here.
5. What to look at: a facility strip's current-month cell; a run-out tick and its tooltip;
   a renewed project's Recent Burn legend number (it was double before the fix); the
   committed line's label when off scale.

**Driving it with the Playwright MCP (Ben's ask, 2026-10-03).** Claude opens the MCP browser at
`https://samuel-dev.k8s.ucar.edu/auth/login`; Ben completes Entra login and 2FA in that window,
once (the login path is rate limited). Claude then:

1. Runs the cold cache refresh above.
2. Drives the click-through in step 3 with snapshots and screenshots.
3. Reads the console, and greps the pod log for each request's cost.

`dev_capture_session.py` remains the fallback when a reusable storage state is needed for
`dev_session_load.py`. #712 reaches dev only after it merges to staging, or after Ben dispatches a
deploy of its branch (Ben owns deploy mechanics). Until then the smoke covers #707 and #711.

### Result, 2026-10-04 01:20-01:28Z (dev on `sha-b883b30`, includes #712)

Playwright MCP session; Ben did the 2FA. Caches were empty after the roll. Screenshots are in
`.playwright-mcp/smoke-*.png` (untracked). Everything rendered, the dev pod log had no errors and
the browser console had none. Light, dark and phone width (390 px, no horizontal page scroll) all held.

| request (dev, Postgres) | cold | warm |
|---|---|---|
| `/allocations/projects` (read model served) | 1.48 s, 18 q | 0.12-0.86 s |
| Derecho calendar, Used | 0.46 s, 20 q | 0.05 s |
| Derecho calendar, Burn | 0.72 s, 14 q | n/a |
| Derecho calendar rows (one group) | 0.05 s | n/a |
| Derecho Pace (after the calendar warmed its entries) | 0.41 s, sam 2 ms | 0.32-0.61 s (render only) |
| Casper Pace, opened before its calendar | 1.65 s, 33 q | 0.42 s |

Checked: run-out tick and tooltip ("runs out about 2026-11-28" on UCLA0075); a renewed project's
Recent Burn legend (CESM0002 522M/yr, not doubled); the committed label off scale
("committed 10.2B/yr" with an arrow); pill and sort state mirrored across Resource tabs.

**Found: the current month is overstated.** `burn_through()` stops the even share at today's
midnight ("a day's charges land the day after"), but the burn query counts through the end of the
as-of day, and dev has same-day charges: 555 rows and 6.5M on 2026-10-03 itself. So every
current-month cell divides about three days of charges by two days of even share. It shows as:

- a spike at "today" on every Pace chart (Derecho about 4B/yr against about 2.5B; Casper about
  21M against 8-12M);
- the calendar's October cells (UCLA0075 reads x7.0, where 38,773 of its 38,775 October
  charges are on Oct 3).

**Decided (Ben, 2026-10-04): ignore today on this page**; it accumulates hourly and is complete
only at midnight. Job history's charts keep today. Draft PR #716: the burn charges end at the
last complete day. Earlier candidates, for the record: make the two ends agree, either by ending the burn query at `through`
(today's charges wait for tomorrow) or by moving `through` to the end of the as-of day (today
counts as a partial day). The second matches what the data now does.

**Design question:** every pill and toggle (Share/Pace, Table/Calendar, Used/Burn, the Pace
sorts) draws the active choice outlined and the inactive ones filled, in both themes. It is
consistent with the folder-tab Resource tabs, but beside filled primary buttons the filled one
reads as selected.

**Re-smoke after #716, 2026-10-04 01:50Z (dev on `sha-990e67f`, caches cleared):** fixed.

- The caption reads "Charges through 2026-10-02, the last complete day".
- UCLA0075's October cell reads "2 charged (x0.0 even pace)", where it read x7.0. Its run-out moved
  from 2026-11-28 to 2026-12-01.
- The Pace spike at "today" is gone. What remains is real data:

| resource | Oct 1-2 daily mean vs September | Pace at "today" vs September level |
|---|---|---|
| Derecho | 1.01x | about 2.8B vs 2.6B |
| Casper | 1.32x | about 16M vs 12M |

Cold loads took 1.5-2.2 s; Casper's calendar and Pace computed the same entries in parallel. The
log had no errors.

After a promotion: refresh `usage` and `chart` on prod (narrowest categories), then compare
the `watch-prod` tick with the recorded cold figures (fstree about 3.3 s of DB time, Pace
9-11 s).

## 2. Next code change: date-group the burn query's subtree anchors

`get_allocation_burn` (`src/sam/queries/allocations.py:944`) calls `anchor_sums(...,
by_month=True)` once per path and activity type. Its subtree anchors carry mixed dates, so
they ride in the anchors table, which is the shape that was slow on MySQL:

| local, burn query | subtree comp statement | all anchor statements |
|---|---|---|
| Derecho, MySQL | 203 ms (24 anchors) | 393 ms |
| Casper, MySQL | 165 ms (24 anchors) | 326 ms |
| Derecho, Postgres | 80 ms | 140 ms |

`batch_charges` (`src/sam/accounting/calculator.py:191`) already avoids this by sending subtree
anchors one date range per statement, so `anchor_sums` passes the range as constants
(`calculator.py:170`). Do the same for the burn's subtree path.

- **Measure first.** The burn clamps each anchor to the window
  (`lo = max(start, window_start)`, `hi = min(end, as_of_end, window_end)`), so count the
  distinct ranges before assuming few. The gain is unmeasured; do not promise one.
- Do **not** group the account path: that was tried for totals and was slower (item 4).
- Tests: `tests/unit/queries/test_allocation_burn.py`, including the parity test.
- Perf baselines will rise by the extra statements; re-pin by hand:
  `allocations_calendar_burn_route` 56 (measured 46), `allocations_calendar_burn_rows_route`
  57 (47), `allocations_pace_route` 55 (45).

**Result, 2026-10-03: tried, not a win, not shipped.** The burn's subtree anchors fall into few
ranges: 24 anchors in 6 ranges on Derecho and Casper, 21-22 in 5 on the GPU resources. Grouping
them as `batch_charges` does (a `charge_groups` helper shared by both) gave, local, 7 repeats:

| | before | after |
|---|---|---|
| Derecho subtree comp, MySQL | 207 ms, 1 statement | 215 ms, 6 statements |
| Derecho subtree comp, Postgres | 82 ms | 83 ms |
| Derecho burn wall, MySQL (min/median) | 575 / 590 ms | 571 / 581 ms |
| Casper burn wall, MySQL | 518 / 646 ms | 513 / 650 ms |
| Derecho burn wall, Postgres | 246 / 311 ms | 252 / 323 ms |

Likely reason, not verified: the burn's ranges span 13-25 months, so constant dates do not narrow
the date-index scan the way the dashboards' short ranges do. Leave the burn's subtree path as is.

## 3. Tidy-up

**Rolling usage.** `src/sam/queries/rolling_usage.py` has two more hand-built `VALUES`
builders: `_query_window_charges` (line 36) and `_query_window_subtree_charges` (line 99).
They hardcode `comp_charge_summary` + `dav_charge_summary` instead of routing by
`activity_type`, and have no probe or fallback. Callers: `rolling_usage.py:383-392` and
`fstree_access.py:677-682`. Fold them into `anchor_sums` (the window is the anchor's dates; a
shared window becomes constants).
- fstree is a legacy API: its output must not change. Routing by `activity_type` could change
  a number for a resource with history in both tables (see the comment at the top of
  `calculator.py`), so run the parity capture below before and after.
- Gate: `tests/unit/gates/test_no_fstring_sql.py` pins `sam/queries/rolling_usage.py: 4`.

**Housekeeping PR.**
- One `_usage_anchor(...)` helper in `queries/allocations.py` for the info dict and the
  subtree/account routing that `get_allocation_usage_rows`, `get_allocation_burn` and
  `get_allocation_summary_with_usage` each spell out.
- Usage cache info as the per-bucket list scans and jobs use: drop `burn_cache_info()` and the
  positional `_CACHE.info()[0]` / `[1]` in `sam/queries/usage_cache.py`. Check the JSON
  endpoint in the allocations blueprint and `sam-admin cache` for readers of the single-dict
  shape first.
- Move `docs/plans/ALLOCATIONS_TABLE_VIEWS.md` to `docs/plans/implemented/` and update the
  code comments that cite it.
- **Ben's call:** Pace treats only DISK as occupancy; the calendar treats DISK and ARCHIVE as
  storage (`_STORAGE_RESOURCE_TYPES` in the blueprint). Align Pace, or leave it.

## 4. Settled; do not redo

- **Leave fstree alone.** On dev (and by `helm/values.yaml:185`, prod) it is served from the
  read model: 5-8 queries, about 120 ms of DB time, `rm=served` in the log line.
- **Account anchors one date range per statement: slower.** Hundreds of distinct ranges (779
  for fstree on Derecho). MySQL: fstree anchor time 327 -> 456 ms, summary 1.98 -> 2.44 s.
  Postgres: summary 0.57 -> 1.11 s.
- **Resolve subtrees to accounts, then sum by account: not a win.** MySQL summary subtree sums
  1.44 -> 1.70 s; Postgres 465 -> 264 ms.
- **Constant dates on the subtree path are required on MySQL**: 0.5 ms vs 104 ms for one
  dashboard statement. Record: the as-built section of
  `docs/plans/implemented/ALLOC-DASHBOARD-PROFILING.md`.

## Measurement kit

Saved untracked in `utils/profiling/charge_sums/`:

| script | use |
|---|---|
| `capture.py <db-url> <out.json>` | 30 scenarios to JSON, with time and statement counts |
| `compare.py a.json b.json` | order-insensitive, float-tolerant diff of two captures |
| `timeit.py <db-url> <label> [repeats]` | min and median over repeats for nine cases |
| `stmts.py <db-url> dash\|fstree\|summary\|burn:<resource>` | per-statement time for the anchor queries |
| `twostep.py <db-url>` | the subtree-to-accounts experiment (reference only) |

- URLs: MySQL test container `mysql+pymysql://root:root@127.0.0.1:3307/sam`, Postgres
  `postgresql+psycopg2://sam_test:sam_test@127.0.0.1:5434/sam`. Run with
  `READ_MODEL_ENABLED=0` after `source etc/config_env.sh`.
- To run old code beside new: `git archive <rev> src | tar -x -C <dir>`, then
  `PYTHONPATH=<dir>/src python3 …`.
- An exact diff is the wrong tool: MySQL `GROUP BY` row order moves between runs, and
  Postgres float sums differ at 1e-12, both on unchanged code. Use `compare.py`.
- Time with repeats. A single pass misled twice in this work.

## Dev access facts

- No 2FA needed from Ben's laptop: HTTPS `/api/v1/health/ready`, kubectl in
  `sam-queries-dev`, and the API key.
- API key that works: user `cacheref` with `SAM_CACHE_REFRESH_API_PASS_DEV` (fstree 200, cache
  refresh). `SAM_NEW_API_USER` / `SAM_NEW_API_PASS` return 401 on dev; `collector` returns 403
  on fstree.
- A staging merge reaches dev in about 4 minutes (pin, then roll). The hourly
  `refresh_allocation_state` run lands in the `:07` task job.
- zsh does not word-split: `K="kubectl --context …"; $K get …` fails with "command not
  found". Spell the command out in background loops.

## Other gaps noted

- `tests/unit/queries/test_contract_audit.py` fails under xdist when run on its own (workers
  insert the same fixed `nsf_program_name`); passes serially and in the full suite.
- The perf tier has no way to print measured counts, so baselines were not re-pinned after
  #711 (counts only fell).
- From the #707 review: month figures on the calendar live only in hover tooltips; the
  run-out tick shares the >= 2x color token; a new project with a few days of zero charges
  projects zero on Pace.
- `get_allocation_summary_with_usage(projcode='TOTAL')` issued 1,433 statements in the
  capture. Not checked whether any real caller uses that shape.
- Three different leaf-vs-subtree rules: `queries/allocations.py` (`is_tree_valid and not
  is_leaf()`), `queries/dashboard.py` (`is_leaf()` only), `queries/fstree_access.py`
  (coordinates). Left alone.

## Checklist

- [x] 1. Dev click-through (Playwright MCP, 2026-10-04): timings and two findings recorded in item 1
- [ ] Promotion to prod (Ben), cache refresh, prod timings compared
- [x] 2. Burn subtree anchors grouped by date range, measured: not a win, not shipped (see item 2)
- [x] 3a. Rolling usage folded into the shared builder: draft PR #712
- [x] 3b. Housekeeping, including ARCHIVE (Ben: align Pace): same PR #712, one commit per piece
- [ ] Review and merge #712; then the dev click-through also covers the rolling-window figures
- [x] Shared rows' meter shows only the project's own use (Ben, 2026-10-04: log it). Done in
  `RESOURCE_DETAILS_SWEEP_HANDOFF.md` round B: `alloc_meter(..., pool_pct=)` draws the pool as a
  lighter layer under the own share on every shared row. Since sweep
  round 4, `allocation_cells` (`shared/project_tree.html`, the inheriting branch) draws
  `usage_meter(self_percent_used)`, single-tone, on the project card, the Allocations project
  list and the edit page's tree; the pool's use is only in the tooltip and the "from <root>"
  cell. Only Resource Details still draws the two-tone "this project / rest of tree" bar
  (`shared/usage_bar.html` `render_usage_bar`). Idea: a second segment in `alloc_meter`
  (`fragments/table_bits.html`), fed `percent_used` on inheriting rows only; the data is
  already on every row (`self_*` in `sam/queries/dashboard.py`). Example on the local dev DB:
  P93300042 on Casper, CESM0002's pool #24474, 28.9% own + 32.9% rest = 61.8%
  (`/user/resource-details/P93300042?resource=Casper`); runner-up P93300313 (14.5% + 47.3%).
- [x] Burn is hard to find (Ben, 2026-10-04, samuel-dev `/allocations/projects`: "I don't see any
  UX element to select it"). It is wired and on dev (`cirrus-dev` pins `sha-6ebb79b`, staging
  #726): a resource tab, the **Calendar** pill, then **Used | Burn** in the calendar's toolbar
  (`partials/calendar.html` `.cal-toolbar`, HPC/DAV only). Checked locally, two reasons it hides:
  - Every in-page toggle draws the *selected* option white and the others solid blue (Share |
    Pace, Table | Calendar, Used | Burn, the 30D-1YR window pills), like the page tabs. On a
    two-way toggle the solid one reads as "on": with Used selected, BURN looks lit while the
    calendar shows no shading.
  - The page lands on the first resource alphabetically, Campaign_Store (DISK), which has no
    Burn; Table | Calendar sits below both charts (y = 1344 px at 1440 wide).
  Ben, 2026-10-04: the toggle shading is the Unity style and stays; the page opens on Derecho
  (#732).

**#712 notes (2026-10-03).** Ben asked for one PR with a series of commits. The rolling window now
starts at midnight: the parity capture showed a mid-day bound counted the first day on MySQL for
some query shapes and never on Postgres. fstree was identical on both backends; dashboard
rolling figures moved up only (MySQL 33 values, Postgres 420). `/allocations/cache/status` now
returns a list. Cheyenne allocations past its 2023-12-31 decommission are a data artifact (Ben).
`utils/profiling/charge_sums/rolling.py` (untracked) captures rolling usage plus fstree.
