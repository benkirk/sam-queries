# Unplanned-city ledger

The running record of `unplanned-city-sweep` passes (skill: `.claude/skills/unplanned-city-sweep/`).
Each sweep adds an entry. The next window sweep starts from the newest entry's end commit, and the
metrics table shows whether the city is getting more legible or less.

**Entry shape:** date, mode, range or area, end commit, then done (with PR), tried and dropped
(with the measurement), and open. Open items stay on the list until a sweep does them or Ben drops
them. Tick an item when its fix merges.

## Metrics

From `scripts/sweep_inventory.py`, whole tree, run at the end commit.

| date | end commit | private imports (helpers / sites / package-private) | dup-function groups (extra copies) | py-dup-names (names / definitions) | dead CSS classes (dynamic stem) | CSS lines / `!important` / repeated blocks | inline styles (templates) | bs4-classes (uses / classes) | row-buttons | JS shared names / shared events |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-03 | `79f1a147` | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,789 / 70 / 14 | 405 (106) | — | — | 5 / 4 |
| 2026-10-03 | `5254c65b` + js sweep | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,817 / 70 / 14 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `0515333f` + css sweep | 70 / 88 / 33 | 9 (10) | — | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `75f89900` (base, py) | 70 / 88 / 33 | 9 (10) | 17 / 59 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `75f89900` + py sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` (base, templates) | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | 20 / 3 | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` + templates sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,621 / 57 / 10 | 323 (95) | 0 / 0 | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` + sweep 5 + trees round | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,615 / 57 / 10 | 263 (94) | 0 / 0 | 0 (from 22) | 5 / 4 |
| 2026-10-04 | `21e6bae5` + sweeps 5–7 | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,603 / 54 / 10 | 257 (93) | 0 / 0 | 0 | 5 / 4 |
| 2026-10-04 | `a75b9db7` + project list | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,610 / 54 / 10 | 255 (92) | 0 / 0 | 0 | 5 / 4 |

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
`registerAction` name has a template user. `js-dead`, added by this sweep, reads 1 unreferenced
global before (`initLazyLoading`) and 0 / 0 / 1 after; the 1 is Turnstile's own
`data-action="register"`.

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

## 3. 2026-10-03: area sweep, `css`

**Mode:** area, `src/webapp/static/css/` (9 files, 4,817 lines, 70 `!important`). **End commit:**
`0515333f` (`origin/staging`, the merge of #722; planned stacked on it). Planning record:
`docs/plans/CSS_SWEEP_HANDOFF.md`. Contract: nothing looks different. Proved with
`ui_snapshots.py --styles` on a base server and the branch server: 13 pages (gallery, login,
register, admin resources/organizations/institutions/projects, allocations, status, user
dashboard, job history, `/database`) x 3 layouts x 2 themes, `--compare` reports 0 of 78
captures differ. The same compare between the two servers before any CSS change was also 0.

**Done, in this sweep's PR**, one commit each:

- [x] 21 classes nothing names, deleted (10 `!important`), plus the banners they emptied.
- [x] One `.sortable-header` rule: the `admin.css` and `allocations.css` copies of
  `components.css`'s rule are gone.
- [x] Login and register inherit the page watermark from `dashboard.css`'s `body` rule instead
  of repeating it.
- [x] The filter UI (facet chips, ladder range, filter sidebar, section toggle; 536 lines) moved
  to `css/filters.css`, linked right after `dashboard.css`. `dashboard.css` is 1,916 lines.
- [x] `test_css_dead.py` holds unnamed classes at zero.
- [x] Skill friction, fixed first: css-dead stems count as dynamic only before an interpolation
  (`id="col-{{ i }}"` had made `.col-chevron` look dynamic); `CSS_KEEP` with stale-keep
  reporting; every jscpd pair printed from its JSON report (the console tail lost 8 of 18) with
  the `/* ==== */` banners ignored (10 of 18 were banner noise); `ui_snapshots.py --styles` /
  `--compare`; `scripts/dev_server_alt.sh` for the base server.

**Tried and dropped:** jscpd's remaining 4 clones: the light/dark token block in `variables.css`
(must exist under both the media query and `[data-bs-theme=dark]`), two button variants that
override the same Bootstrap variables with different values, and an admin/status pair that is
mostly a banner. The `!important` left on rules that must beat Bootstrap's own utilities (muted
badges, text colors, tab counters).

**Open from this sweep:**

- [x] PROJECT CARDS / PROJECT TREE (`dashboard.css`, about 240 lines) are user-dashboard
  specific and could move to their own file. Not crowding anything today. Closed by sweep 6:
  mostly shared rules, see its dropped list.

## 4. 2026-10-04: area sweep, `py`

**Mode:** area, `src/` Python, focused on helpers written more than once. **End commit:**
`75f89900` (`origin/staging`, the merge of #723). Planning record:
`docs/plans/PY_HELPERS_SWEEP_HANDOFF.md`. Ben's constraint: no net new code. `src/` across the
branch is +445 / -920; every finding commit shrinks `src/` on its own.

**Parity:** an untracked capture (`utils/profiling/py_sweep/`) of 230 calls, run before and after
on mysql-test and postgres-test, compares values order-insensitively with a float tolerance, plus
the SQL each call emits. It covers `get_allocation_summary` with every scope as None / "TOTAL" /
scalar / list / "", root-only, transactions and adjustments plus counts, usage, both searches
and an env matrix through `NotifyConfig`, `ReadModelConfig` and the scheduling readers, in and
out of a Flask app. Two reruns of unchanged code agree exactly once selectin batches and anchor
order are normalized; both float with unordered rows. The final diff on both backends holds only
the two intended changes below. Perf tier: 64 passed before and after; `baselines.json` unchanged.

**Done, in this sweep's PR**, one commit each (`src/` lines added / removed):

- [x] Skill friction: jscpd `--format` per area (py: 888 files / 321 clones down to 428 / 135),
  and the `py-dup-names` detector with a fixture test.
- [x] Forms (+43 / -110): `_date_range` on `HtmxFormSchema` replaces 13 date post_loads. New
  stdlib-only `sam/dates.py`. `normalize_end_date` stops importing `webapp` and raises a field
  error. **Behavior change:** a malformed end date was a 500 on 11 forms; it is now an inline
  "Invalid end date format.".
- [x] Dates (+51 / -100): `parse_ymd_or` / `start_of_today` at the `active_at` and query-string
  sites. Each site keeps its own fallback.
- [x] Queries (+21 / -86): `apply_scope_filters` replaces the 15 None/TOTAL/list/scalar copies.
  Parity: identical values and SQL.
- [x] Config (+44 / -138): the notify and read-model readers fold into
  `sam.integration._config` (`config_int(minimum=None)` keeps 0 and negatives), and
  `xras_sweep._positive_int` folds into `positive_int_env`. **Behavior change (tests only):** an
  explicit `env={}` no longer falls through to `os.environ` in the five notice/digest readers.
- [x] Status models (+42 / -145): the `staged_name` descriptor replaces 13 getter/setter pairs.
- [x] Caching (+6 / -38): `chart_cached_redis` is deleted; one `chart_cached` serves both backends.
- [x] Search (+18 / -30): `Organization.search_by_pattern` replaces 3 copies, and `limit=` on
  `search_projects_by_code_or_title` replaces 3 `[:10]` slices. The rows and their order match;
  the pickers emit `LIMIT 10` and fewer selectin loads.
- [x] Text (+61 / -83): `sam/text.py strip_or_none` replaces 3 helpers (59 references).
- [x] Names (+108 / -136 including tests): 7 private helpers made public. `to_display_tz` moves to
  `charts/series.py`, which drops the stacked → dualpanel family edge from the boundary gate.
  `_require` becomes one `require_text`, and the `drop_already_notified` docstrings are de-duplicated.
- [x] Wire dates (+55 / -86): `parse_wire_date` replaces `preflight._parse_date` and three inline
  `fromisoformat` try blocks. `modals.py` and `remediation.py` imported the private `_as_date`.

**Tried and dropped:**
- `account_requests` `_normalize` hooks: DateTime fields with no end-of-day rule, and other work
  in the same hook.
- `awards/audit._as_date`: a passthrough, not a parser.
- `_iso` ×3 (one-liner; two copies on the legacy byte-exact path).
- The NSF / USAspending `_parse_date` (vendor formats).
- `_read_model_rows` ×3 (the fstree copy picks the legacy path).
- `get_cache_adapter` ×3 (different default buckets).
- `refresh_cache` ×7 (legacy blueprints).
- Name collisions: `institutions_fragment`, `page_context`, `parse_filters`, `filters`,
  `resolve`, `contracts`, `xras`.
- The ORM `is_active` hybrid repeats (per-model semantics).
- The `get_session` twins (low value).
- `charges._apply_filter` (LIKE semantics, a different shape from the scope filter).

**Open from this sweep:**

- [ ] `caching/buckets.py` reads app config only and raises on a bad int; folding it into
  `integration._config` gains env fallback and error tolerance, a behavior change.
- [ ] `on` vocabulary drift: `READ_MODEL_ENABLED=on`, `MAIL_USE_TLS=on` and `NOTIFY_ENABLED=on`
  read true in the runtime readers but false in `webapp/config.py`'s import-time tuple. Ben's
  call: recorded, not fixed.
- [ ] `webapp/api/v1/status.py` (about L86, 426, 433, 613) builds timezone-aware datetimes with
  `fromisoformat(s.replace('Z', '+00:00'))` against naive-UTC `system_status`; check whether the
  model layer strips the zone.
- [ ] `search_projects_by_code_or_title` has no `ORDER BY`, so which 10 rows the pickers show is
  plan order on both backends. It also filters on `Project.active == active` (§5 drift).
- [ ] `sam` still imports `webapp.extensions` in `sam/schemas/__init__.py` and `sam/base.py`
  (this sweep removed the `normalize_end_date` edge). The rule is not gated.
- [ ] jscpd clones not read: `sam/xras/handlers/adjustment.py` / `supplement.py`,
  `sam/summaries/archive_summaries.py` / `disk_summaries.py`, the `cli/*/display.py` pairs.
- [ ] dup-functions left: `active_account_users` on User and Project, and the two `decorate` in
  `charts/stacked.py`.
- [ ] `CacheBase` has no `bytes_used`, which `chart_cached` reads; both backends define it.

## 5. 2026-10-04: area sweep, `templates` + `css` (aesthetic)

**Mode:** area. Read `src/webapp/templates/` and `static/css/` for UX patterns the recent PRs
introduced that older templates never adopted (#696/#697/#704–#707/#721).

**End commit:** `21e6bae5` (`origin/staging`: the #724 promotion, which carries #725).

**Contract:** unlike sweeps 1–4, this one is not "nothing looks different". The changes are
visual on purpose, and each commit declares its change. `src/` across the branch is +578 / -916.

**Proof:**
- `scripts/ui_snapshots.py`, before and after, reviewed by eye. Before is a base worktree served
  by `dev_server_alt.sh` on :5053; after is the branch on :5052. 13 pages × 3 layouts × 2 themes.
- Contrast is measured in the page (WCAG ratio, both themes); spacing with
  `getBoundingClientRect`.
- Full MySQL suite green, except the known `reconcile_quotas` flake, which passes on rerun.
- The webapp, status and gate tests are also green on postgres-test.

**Brand stance (Ben):** honor the NSF NCAR brand guide without being bound by it.
- Keep: Poppins; NCAR Blue `#0057C2`, Dark Blue `#00357A`, Space `#011837`, Aqua; orange and
  yellow as small accents.
- Unity is built for sparse marketing pages, with solid-fill alerts, solid-blue table heads and
  18px cell padding. SAM's density is much higher.

**Declared deviations from the brand guide and Unity:**
- Alerts are tinted surfaces with a left rule, not Unity's solid fills. This follows the guide's
  own "no accent as a large block" rule.
- The guide has no red or green. Danger is `#c0272d` / `#ff8a7a` and success is `#2b8a4b` /
  `#4cc77f` (`--status-*` tokens).
- Compute utilization bars use one brand fill. Threshold colors stay only where full is bad:
  filesystems and login-node load.
- The Google Calendar embed is CSS-inverted in dark mode, so its event colors are approximate.

**Done, in this sweep's PR**, one commit each (`src/` lines added / removed):

- [x] Skill friction: the `bs4-classes` detector with a fixture test, the status pages in
  `ui_snapshots.py` `DEFAULT_PAGES`, and an "aesthetic sweep" note in the skill.
- [x] Alerts (+32 / -68): tinted surface plus left rule app-wide, with `--status-*` and
  `--alert-tint` tokens. Body text is 9.3–12.2:1 and muted text at least 5.5:1, in both themes.
- [x] Page titles (+80 / -103): one `fragments/page_header.html` macro across 9 pages; the h1
  goes from 48px to 28px. On the status page, title to first content goes from 228px to 161px.
- [x] Reservations (+97 / -94, plus +4 / -2 for phones): one `reservation_table` on Derecho,
  Casper and Events. Rows are 62px, where each slab was 120–150px. A
  `ResourceReservation.is_active` hybrid drives "in progress".
- [x] Calendar (+15): a dark-mode filter on the cross-origin embed.
- [x] Status tables (+97 / -146):
  - the house table vocabulary;
  - counts colored only when they mean something;
  - `<code>` in heading ink instead of Bootstrap pink;
  - outage actions as `btn-row` buttons.
- [x] Utilization (+44 / -93): the brand fill; four tiles become one macro; the node-type cells
  use `alloc_meter`.
- [x] JupyterHub (+77 / -273):
  - the shared `metric_card` / `util_card` macros;
  - no jumbo icons, and the orphan heading is gone;
  - inline styles go from 12 to 0;
  - a shared `.status-dot`.
- [x] Bootstrap 4 classes (+14 / -14): 20 uses down to 0, held there by `test_bs4_classes.py`.
- [x] Audit-log tables (+102 / -109): seven ledgers on `col-shrink` / `col-num` /
  `cell-truncate`; inline styles go from 50 to 1.
- [x] Leftovers (+44 / -42): light and dark badges muted, redundant badge ink dropped, and
  `opacity-50` becomes `row-inactive`.

**Tried and dropped:**
- Wrapping the reservation window on phones. It pushed the System column wider too, so the
  window moves under the name instead.
- Autosquashing that phone fix into the reservations commit. It conflicts with the JupyterHub
  commit's `.status-dot` change, so it stays a separate commit.

**Open from this sweep:**

- [x] The project trees, `shared/project_tree.html` and `admin/.../project_allocation_tree_htmx.html` (sweep 6).
  - Today: 23 inline styles each, a nested table and px colgroups.
  - Target: `tree-cell` + `col-num` + `btn-row` + `alloc_meter`.
  - Ben wants this as a second round, planned with fresh context. Sweep 3's move of the
    project-card CSS rides along.
- [ ] Resource Details (user `resource_details*` and the day/user subtrees): nested tables, 13 raw
  collapse toggles, and `td.text-end` instead of `col-num`.
- [ ] `xras_request_detail.html`: its 15 small outline row buttons should be `btn-row`. The XRAS
  cards are only half adopted (nested tables, `width:99%`).
- [ ] The saturated `modal_scaffold` headers and the `text-bg-*` toasts. This is an app-wide
  design call.
  - Create: `bg-success`.
  - Edit: `bg-warning`, which renders navy.
  - Confirm: `bg-danger`.
- [ ] Brand-drift tokens. `charts/theme.py` mirrors these, so a fix moves the chart fingerprints.
  - `--ncar-light-blue` is really UCAR Aqua.
  - `--info-color` is `#0056C2`, a step off NCAR Blue.
  - `--success-color` and `--warning-color` are Tailwind colors.
- [ ] The solid-blue `page_tabs` strip and `nav-pills`. Ben kept them this round.
- [ ] A one-off failure in `tests/unit/gates tests/unit/webapp` that did not reproduce in two
  reruns.

## 6. 2026-10-04: area sweep, `templates` round 2 (the project trees)

**Mode:** area. This is sweep 5's first open item, planned and run with sweep 5's context still
loaded.

**Stacked on:** sweep 5's branch (`sweep-templates-ux-2026-10`, end commit `21e6bae5`). Retarget
to `staging` once that merges.

**Contract:** aesthetic, like sweep 5; each commit declares its visual change.
`src/` is +244 / -637.

**Shared rows (Ben's call):** two variants were shown side by side, then a hybrid was picked.
- *Muted pool numbers:* the pool's Allocated and Remaining in gray.
- *Label only:* "from NCGD0006" in place of the numbers. Ben's objection: with the label alone,
  a row has no quantitative reference once the owner's row has scrolled off the page.
- *Hybrid (shipped):* Allocated says "from <owner>", Used is the project's own `self_used`, and
  Remaining keeps the pool's remaining, muted.

**Done, in this round's PR**, one commit each (`src/` lines added / removed):

- [x] Project trees (+145 / -526): one `project_tree_rows` recursion for both trees, built on
  `tree-cell` + new `.tree-guides` (guide lines at any depth), `alloc_meter`, `btn-row`,
  `tree_label_cell` and `collapse_toggle`.
  - Fixes the user hierarchy's all-bold, all-yellow bug: children were nested inside the
    current node's `<li>`.
  - The user hierarchy goes from 862px to 738px for 18 nodes.
  - Deleted: the dead allocation branch of `render_project_tree`, the second recursion, and the
    `.alloc-caret` / `.alloc-resource-header` / `.tree-node-inactive` CSS.
  - `.btn-entity` loses Unity's 3px border, which added 6px to every row with an entity link.
- [x] Row actions (+38 / -41): 22 icon-only outline buttons in table cells become `btn-row`.
  A new `row-buttons` detector with a fixture test is held at zero by
  `test_template_detectors.py`, which absorbs `test_bs4_classes.py`. Prompted by Ben: "many
  places where an edit or delete button appears on subsequent rows with a fat border".
- [x] Manage Project header (+19 / -32): one breadcrumb trail, where it had two; the shared
  `page_header`; the toolbar back at `btn-sm`.
- [x] Linked elements (+36 / -38): house table vocabulary, `hidden` instead of an inline
  `display:none`; inline styles go from 17 to 3.
- [x] Phones (+10 / -4): the tree is wrapped in `table-responsive`. A `visually-hidden` label in
  a `<th>` (`position: absolute`) was escaping the scroll wrapper and stretching the page to
  587px. Tree cells keep a projcode-wide minimum.

**Tried and dropped:**
- Moving the "PROJECT CARDS / PROJECT TREE" CSS (sweep 3's open item) into its own file. Most
  of that section is shared rules: `.stat-item` / `.stat-label` (user and configuration cards),
  `.accordion-chevron`, `.sam-fluid-1800`, and `.tree-list`, which Resource Details still uses.
  A file named for project cards would mislabel half of it. Closing the item.

**Open from this round:**

- [ ] Resource Details scope pickers (`resource_details*.html`) still use nested `.tree-list`
  `<li>`s, so they have the same inherited bold and tint under a current node. Moving them to
  `project_tree_rows` would delete `.tree-list`.
- [ ] 26 `class="btn  btn-…"` double-space leftovers in 13 templates: an old bulk `btn-sm`
  removal whose reason isn't recorded. Several are pane-toolbar buttons that the house rule
  says should be `btn-sm`.
- [ ] `.tree-d1` / `.tree-d2` could become `.tree-guides`, which draws the same lines at any
  depth. Prove it with `ui_snapshots.py --styles --compare`.
- [ ] One test failure in `tests/unit/gates tests/unit/webapp` that did not reproduce on rerun,
  seen twice across rounds 1 and 2.

## 7. 2026-10-04: area sweep, `templates` round 3 (project and user cards and modals)

**Mode:** area, run with sweeps 5–6 still in context. **Stacked on:** sweep 6's branch
(`sweep-trees-2026-10`). **Contract:** aesthetic, with each commit declaring its change.
`src/` is +186 / -266. Full MySQL suite: 10,595 passed, 0 failed.

**Ben's picks:**
- The project card and modal show resources in the tree-table columns.
- The user card groups SAM permissions by verb.

**Done, in this round's PR**, one commit each (`src/` lines added / removed):

- [x] Resources (+73 / -158): `render_project_resources`, which serves the user card, the admin
  card and the project modal, uses the Manage Project columns and the shared `allocation_cells`.
  - A shared allocation reads the same everywhere.
  - Rows are about 45px; they were 70–87px across three lines.
  - "361d remaining" appears once per date group, not on every row.
  - `allocation_cells` gains units and shows the elapsed tick only for HPC/DAV.
- [x] Card shell and info (+57 / -57):
  - the expanded header is the accent surface, not a solid blue slab;
  - Manage Project goes into the info heading via a `caller()` slot;
  - org ancestry is inline and directories sit on one line;
  - multi-line labels go in the label column via opt-in `.stat-item-inline`;
  - the modal sorts resources by name like the card.
  - SCSG0001's card goes from 1,587px to 954px; the modal from 1,564px to 905px.
- [x] User card (+56 / -51):
  - the `project-stats-box` panels, with labels beside values in a `fit-content(10rem)`
    label column;
  - `btn-row` edits for GID and shell;
  - permissions as a by-verb table, which fixes the chip row clipped by `.scrollable-list`.
  - `/user/info` goes from 2,001px to 1,726px; the admin user modal from 1,931px to 1,761px.

- [x] Bar widths (Ben: "uniform treatment on desktop that is wider"): every `share_bar` and
  `alloc_meter` column takes `--bar-share-w` / `--bar-meter-w`. Below 1200px those are 3.5rem
  and 5rem; at ≥ 1200px both are 8rem. That covers the Allocations tree, the admin
  facility/resource cards, Manage Project, the project card, and the status node-type and
  JupyterHub tables. It replaces the per-table 7.5rem rule.

**Tried and dropped:** nothing.

**Open from this round:**

- [x] The Allocations project list (`allocations/partials/project_table.html`, loaded under a
  type row) still draws `render_usage_bar` in one `colspan="6"` usage-and-dates cell behind six
  sortable headers (`min-width:280px`). It should become `alloc_meter` plus real columns, which
  means reworking its data-sort attributes. Done in entry 8. Resource Details and the usage
  modal still use `render_usage_bar`.

- [ ] On a phone, the project card's resource table scrolls sideways inside its frame. The
  meter column could hide below md and show only the percentage.
- [ ] The user card's group branch switch is still the solid-blue toggle bar, like the page
  tabs and pills that sweep 5 kept.
- [ ] `group_card.html`, `contract_card.html` and the transaction / adjustment / XRAS detail
  modals use `stat-item-block` with stacked labels. They could opt into `.stat-item-inline`.

## 8. 2026-10-04: area sweep, `templates` round 4 (the Allocations project list)

**Mode:** area, from `docs/plans/ALLOCATIONS_PROJECT_LIST_HANDOFF.md` (entry 7's open item).
**Stacked on:** sweep 7's branch (`sweep-cards-2026-10`, end commit `a75b9db7`). **Contract:**
aesthetic, with each commit declaring its change. `src/` is +123 / -192.

**Done, in this round's PR**, one commit each:

- [x] Adapter and lift (handoff step 3, landed first because the columns read it):
  - `sam.queries.dashboard.allocation_timeline` replaces three copies of the elapsed / bar-state
    rule: two in `dashboard.py` and 25 lines of Jinja in `project_table.html`.
  - `projects_fragment` normalizes rows into the `allocation_cells` shape (`_as_resource_row`)
    and no longer writes titles into the cached usage rows.
  - `ui_snapshots --styles --compare` against the base: 0 of 6 captures differ.
- [x] Columns (step 2):
  - Project | Title | % used | Allocated | Used | Remaining | Annual rate | Start | End |
    Days left, each cell carrying its own `data-sort-value`; the `data-sort-attr` indirection
    is gone.
  - `allocation_cells` gains opt-in `sort=` and `units=`, so a pool member reads "from <root>"
    as everywhere else.
  - Rows are 44px at 1440px (from ~76px) and 38px at 390px (from 69–212px). The page never
    scrolls sideways.
  - `.elapsed-tick` deleted: this list was its only user.
- [x] States (step 4): expired / open-ended / no dates as a `state_tag` in Days left; expired
  rows are `row-inactive`; no usage renders `allocation_cells(None)`.
- [x] Title N+1 (step 5): `sam.queries.projects.project_titles`, one `IN` query.
  - Route queries go from 18 + 2 per row (495 at 239 rows, 837 at 410) to a flat 18.
  - Pinned by `test_allocations_project_table_route`, baseline 27.

**Deviations from the handoff:**
- Annual rate follows Remaining, so `allocation_cells` stays one call.
- DISK/ARCHIVE drop "Data Volume", which repeated Allocated.
- The unit sits in the Allocated header's title, not on 50 rows.
- The usage bar loses its red / orange / green thresholds for the house meter: blue, red only
  past 100%.
- Step 6 had no commit of its own. Its one deletion had to ride with the columns commit to keep
  `test_css_dead` green.

**Tried and dropped:** the unit on every Allocated row. On a 50-row list it was noise and
widened the column.

**Open from this round:**

- [ ] Resource Details (`user/resource_details.html`, `resource_details_disk.html`), the
  allocations usage modal and the status filesystem table are the last `render_usage_bar` /
  raw `.progress` holdouts.
- [ ] The project list is still a nested table in a spanning cell, not rows of the tree table
  (wire-dashboard §7). Its column set differs from the tree's, so folding it in would mean the
  tree growing columns.
- [ ] On a phone, the list's own `table-responsive` never scrolls. It sits in an auto-layout
  cell, so the tree's wrapper scrolls the whole tree (1055px).

## Untriaged: first whole-tree inventory, 2026-10-03

Surfaced by the first run of `scripts/sweep_inventory.py`. Each item belongs to an area sweep;
nothing here has been read for intent yet.

- **py:** the first inventory's py items were read and closed by sweep 4; its leftovers are on
  that entry's open list.
- **docs:** `plans-stale --gh` on 2026-10-03 found no retirement candidates among 22 top-level
  plans. `ADMIN_TABLE_POLISH.md` and `ALLOCATIONS_SUNBURST.md` still say "implemented, in
  review" although their PRs have merged; they pass the 14-day idle bar on 2026-10-17.
- **templates:** sweeps 5–8 took inline `style=""` from 404 to 255.

## Propagation candidates

Shared pieces a window introduced that an older surface could adopt. The canonical example is the
sunburst, which started on the allocations page and then moved to job history.

- [ ] `sam.dates.start_of_today()`: the `datetime.now().replace(hour=0, ...)` expression still
  appears in `xras/card_routes.py`, `sam/xras/handlers/_allocations.py` and
  `sam/resources/machines.py` (sweep 4).
- [ ] `sam.dates.parse_wire_date`: `webapp/jobs/routes.py:1104` parses its window bounds with an
  inline `fromisoformat` try block (sweep 4).
- [ ] `sam.text.strip_or_none`: `samuel_roles._clean_note` is `strip_or_none(note, _NOTE_MAX)`
  (sweep 4).
