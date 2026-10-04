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
- **js:** `number-preview.js` and `path-preview.js` are near twins (`primeAll`, `updatePreview`,
  and a jscpd clone). `writeCookie` is defined in both `layout-axis.js` and `theme-toggle.js`.
- **js:** `htmx:afterSettle` has 10 listeners across 5 files, and `htmx:afterSwap` has 5 across 4.
  One dispatcher in `htmx-config.js` might serve them all.
- **templates:** 405 inline `style=""` attributes; the project trees (`shared/project_tree.html`,
  the admin allocation tree) carry the most.

## Propagation candidates

Shared pieces a window introduced that an older surface could adopt. The canonical example is the
sunburst, which started on the allocations page and then moved to job history.

- [ ] (none recorded yet; the next window sweep fills this from its new macros, chart families and
  helpers)
