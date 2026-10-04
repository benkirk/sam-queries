# Unplanned-city ledger

The running record of `unplanned-city-sweep` passes (skill: `.claude/skills/unplanned-city-sweep/`).
Each sweep adds an entry. The next window sweep starts from the newest entry's end commit, and the
metrics table shows whether the city is getting more legible or less.

**Entry shape:** date, mode, range or area, end commit, then done (with PR), tried and dropped
(with the measurement), and open. Open items stay on the list until a sweep does them or Ben drops
them. Tick an item when its fix merges.

## Metrics

From `scripts/sweep_inventory.py`, whole tree, run at the end commit.

| date | end commit | private imports (helpers / sites / package-private) | dup-function groups (extra copies) | dead CSS classes (dynamic stem) | CSS lines / `!important` / repeated blocks | inline styles (templates) | JS shared names / shared events |
|---|---|---|---|---|---|---|---|
| 2026-10-03 | `79f1a147` | 70 / 88 / 33 | 9 (10) | 38 (17) | 4,789 / 70 / 14 | 405 (106) | 5 / 4 |
| 2026-10-03 | `5254c65b` + js sweep | 70 / 88 / 33 | 9 (10) | 38 (17) | 4,817 / 70 / 14 | 404 (105) | 5 / 4 |

## 1. 2026-10-03: allocations views, window sweep

**Mode:** window, retroactive. **PRs:** #707 (at-date table, calendar, Burn, Pace on burn data),
#711 (one builder for the batch charge sums). **End commit:** `79f1a147`, the merge of #712.

**Done, in #712**, one commit each:

- [x] Rolling-window charges folded into `batch_charges`, so they gain the probe, the fallback and
  routing by `activity_type`. Parity found that a mid-day window start counted the first day on
  MySQL for some query shapes and never on Postgres. The window now starts at midnight.
- [x] `_usage_anchor()`: one anchor builder for usage rows, burn and the summary.
- [x] The usage cache reports a per-bucket list, like scans and jobs.
- [x] `ALLOCATIONS_TABLE_VIEWS.md` moved to `docs/plans/implemented/`.
- [x] Pace treats ARCHIVE as storage, as the calendar does (Ben's call).

**Tried and dropped:** grouping the burn's subtree anchors by date range. 24 anchors fall into 6
ranges. MySQL went 207 to 215 ms, Postgres 82 to 83 ms, and wall time was flat. The record is
the allocations follow-ups handoff, item 2.

**Open from this window:**

- [ ] Three leaf-versus-subtree rules: `src/sam/queries/allocations.py` (valid tree and not a leaf),
  `src/sam/queries/dashboard.py` (`is_leaf()` only), `src/sam/queries/fstree_access.py`
  (coordinates). This is convention drift; pick one rule.
- [ ] `get_allocation_summary_with_usage(projcode='TOTAL')` issued 1,433 statements in a capture.
  Nobody has checked whether a real caller uses that shape.
- [ ] Private imports touching the window: `sam.queries.allocations._aggregate_usage_to_total`
  (allocations blueprint), `sam.queries.disk_usage._EMPTY_CAP`, `webapp.api.v1.health._ping_engine`
  (config inspector), and `webapp.dashboards.allocations.blueprint._window_control_context`
  (XRAS card routes).
- [ ] From the #707 review: calendar month figures live only in hover tooltips; the run-out tick
  shares the >= 2x color token; a new project with a few zero-charge days projects zero on Pace.

## 2. 2026-10-03: area sweep, `js`

**Mode:** area, `src/webapp/static/js/` (20 files, 3,575 lines). **End commit:** `5254c65b`
(`origin/staging`). Run as a screening test of the skill: report short, most items dropped.
Inventory: 5 shared names, 4 shared events, 3 jscpd clones (0.9% of lines). Every
`registerAction` name has a template user.

**Done, in this sweep's PR**, one commit each:

- [x] Admin card sort ran twice per click. `admin-cards.js` re-bound the Machines and NSF
  Programs headers that `sortable_table.js` already binds, so the column stayed descending.
  Confirmed in a browser on the old and new code. The copy (a jscpd clone) is deleted.
- [x] Date-range presets formatted local dates with `toISOString()` (UTC). At 20:30 Mountain a
  30d click sent `end_date` one day ahead (measured with a pinned browser clock). It now uses
  the local calendar fields.
- [x] `SamCollapseChevron` retired. Its last caller, the Project Directories rows, uses the
  house `.collapse-icon` rule instead. Dead `initLazyLoading()` and a duplicate `afterSwap`
  binding in `sortable_table.js` are also removed.

**Tried and dropped:** `writeCookie` twins (session cookie vs one-year `Secure` cookie, and
`theme-toggle.js` loads alone on the login/register pages); `has()` twins (3 lines); merging
`number-preview`/`path-preview` (shared wiring, no shared logic); one `htmx:afterSettle`
dispatcher (10 unrelated, cheaply guarded listeners; a dispatcher couples 5 files); folding
`show-user-details` into `show-detail-modal` (5 sites, cosmetic); the error-toast twins in
`htmx-config.js`.

**Open from this sweep:**

- [ ] Hardcoded routes in JS: `dashboard-init.js` (`/admin/expirations`, `.../export`,
  `/admin/project/`) and `modals.js` (`/status/htmx/outage/<id>/edit`). The house idiom is a
  `url_for`-rendered `data-*-url` attribute.
- [ ] The "strip overridden params from `detail.path`" clone in `layout-axis.js` and
  `nav-view-persistence.js` could be one helper.

## Untriaged: first whole-tree inventory, 2026-10-03

Surfaced by the first run of `scripts/sweep_inventory.py`. Each item belongs to an area sweep;
nothing here has been read for intent yet.

- **py:** `webapp.utils.project_permissions._is_project_steward` is imported by three modules
  (access control, project members, RBAC). It should be public, or live in RBAC.
- **py:** the chart-cache decorator and wrapper are identical in `src/webapp/caching/chart.py` and
  `src/webapp/caching/redis_chart.py`.
- **py:** `_search_orgs_for_project` (admin projects routes) and `_search_organizations_fk` (admin
  resources routes) have identical bodies.
- **py:** `Machine.is_active`, `Resource.is_commissioned` and `Resource.is_active` share one body.
  So do `active_account_users` on User and Project, `_require` in the notify addressing and template
  stores, `raw` and `_raw` in the integration and notify configs, the two
  `coerce_and_validate_dates` in the resources form schema, and the two `decorate` in the stacked
  chart family.
- **css:** the `.sortable-header` rules are repeated in `admin.css`, `allocations.css` and
  `components.css`.
- **css:** `dashboard.css` is 2,489 lines with 60 `!important`; feature sections could move into
  per-feature files the way `allocations.css` did.
- **css:** 21 dead classes without a dynamic stem, for example `.logout-link`, `.date-filter-form`,
  `.stat-box` and the `.border-status-*` set.
- **templates:** 405 inline `style=""` attributes; the project trees (`shared/project_tree.html`,
  the admin allocation tree) carry the most.

## Propagation candidates

Shared pieces a window introduced that an older surface could adopt. The canonical example is the
sunburst, which started on the allocations page and then moved to job history.

- [ ] (none recorded yet; the next window sweep fills this from its new macros, chart families and
  helpers)
