# The By User sunburst (job history)

**Status: built** (2026-10-05, plugin hpc-usage-queries #123 + this PR). Follow-on to the
charts sweep (#735): Job History → By User, machine mode, gets the "By facility" switch and
the fullscreen three-ring expand that By Project has, with usernames on the rim.

Ben, on the project version: "the way it shows the triple ring with project codes is
awesome. the concept could apply to usernames as well".

## Decisions (Ben, 2026-10-05)

- Users group by the **facility of the projects they ran under**; the middle ring is the
  **panel**, as on By Project, so the two charts read the same.
- A user who charged under two facilities (or two panels) is a wedge in each. **No marker**
  for "also elsewhere": the repeat is the information.
- **Machine mode only.** In project mode the natural inner ring is the sub-project, a
  different chart.
- Rim wedges open the **user modal only for viewers with `VIEW_USERS`** (the By User
  table's own rule); everyone keeps the hovers.

## The rollup

A user has no facility of their own, so the grouping needs usage per (user, project).
`JobQueries.jobs_usage_by_pair(('user', 'account'), ...)` (hpc-usage-queries #123) is
`jobs_usage_by` grouped on two FK columns: same filters, the same always-applied `account`
scope, the same `daily_summary` fast path (both dimensions and every active filter are
rollup keys over a bounded, covered window) and the same `jobs` scan otherwise. Rows are
`{'user', 'account', <five metrics>}`, `totals` over every row, no `limit`: a pair row is not
a whole entity, so SAM folds. Measured on the read replica, Derecho machine-wide: summary
path 30 days ~330 ms (1,284 pairs), 365 days ~390 ms (3,710 pairs, 2,565 users); scan path
over a 411k-job month ~1.4 s warm against ~1.3 s for one dimension.

SAM's `service.jobs_usage_by_user_account` caches it as its own `usage_by_user_account`
family in the `jobs` category (`sam-admin cache --refresh --category jobs`).

## As built

- **One call, both views.** With the switch on, `_render_usage_panel` fetches the pairs
  once: `_facility_user_rings` groups them by the project's facility (sharing
  `_facility_ring_rows` with the project grouping, so a user under two projects of one
  facility sums into one wedge) and `_users_from_pairs` folds them per user, ranked by the
  viewed metric with the pairs' own totals, for the table. No second rollup runs.
- **Two rings.** `JobsFacilitySunburst(row_attr, noun)`, the `JobsUsagePie` pattern: the
  By User tab draws over `data-job-user` rows with "N other <facility> users" hovers; only
  the table's top 25 drill. The switch's title and the expand button's title read the
  entity's plural from `_USAGE_ENTITIES['noun']`.
- **Three rings.** `/dashboards/user/jobs/machine/<machine>/by-user/expanded`
  (`VIEW_ALL_JOB_DATA`, like its sibling): pairs → `project_panels` → `panel_rows_grouped`
  → `UserPanelSunburst` (`rim_link = USER_MODAL`, `rim_noun = 'users'`, cache
  `user_panel_sunburst`, generator 11). `PanelSunburst(rim_links=False)` draws the rim
  without links; the flag joins the cache key. The modal caption takes `rim` and
  `rim_links` so it says "click a user to open it" only when a click does something.
- `panel_rows_grouped(entries, slots)` takes `(facility_id, facility, panel, name, value)`
  entries and sums a recurring name; `panel_rows` is the project-code wrapper. The leaf
  key is `rim`.
- The chart package pins the Agg backend at import (`charts/theme.py`): under the macosx
  backend a Retina display doubles the figure dpi, which moves every label-fit threshold,
  and the new mobile sample rendered 46 labels serially and 45 under xdist.

## Verification

- Plugin: 16 new tests (signature parity, row contract, fold to the single dimension,
  routing, fast/scan equivalence on a fixture with one user under two accounts).
- SAM: `test_jobs_facility_rings.py` (groupings, the fold, the switch, the expanded route's
  mode, permission and link gate), `test_panel_sunburst.py` (grouped rows, gated links),
  the registry at 18, fingerprints with 19 new keys and none changed, route map.
- Browser pass on samuel-dev after the SAM image builds against the plugin's `main`.
