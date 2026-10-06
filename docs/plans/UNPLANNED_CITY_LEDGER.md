# Unplanned-city ledger

The running record of `unplanned-city-sweep` passes (skill: `.claude/skills/unplanned-city-sweep/`).
Each sweep adds an entry. The next window sweep starts from the newest entry's end commit, and the
metrics table shows whether the city is getting more legible or less.

**Entry shape:** date, mode, range or area, end commit, then done (with PR), tried and dropped
(with the measurement), and open. Open items stay on the list until a sweep does them or Ben drops
them. Tick an item when its fix merges.

## Metrics

From `scripts/sweep_inventory.py`, whole tree, run at the end commit.

| date | end commit | private imports (helpers / sites / package-private) | dup-function groups (extra copies) | py-dup-names (names / definitions) | dead CSS classes (dynamic stem) | CSS lines / `!important` / repeated blocks | inline styles (templates) | bs4-classes (uses / classes) | row-buttons | JS shared names / shared events | modal alerts (templates) | helper-bypass (sites) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-03 | `79f1a147` | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,789 / 70 / 14 | 405 (106) | — | — | 5 / 4 |
| 2026-10-03 | `5254c65b` + js sweep | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,817 / 70 / 14 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `0515333f` + css sweep | 70 / 88 / 33 | 9 (10) | — | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `75f89900` (base, py) | 70 / 88 / 33 | 9 (10) | 17 / 59 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `75f89900` + py sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | — | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` (base, templates) | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | 20 / 3 | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` + templates sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,621 / 57 / 10 | 323 (95) | 0 / 0 | — | 5 / 4 |
| 2026-10-04 | `21e6bae5` + sweep 5 + trees round | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,615 / 57 / 10 | 263 (94) | 0 / 0 | 0 (from 22) | 5 / 4 |
| 2026-10-04 | `21e6bae5` + sweeps 5–7 | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,603 / 54 / 10 | 257 (93) | 0 / 0 | 0 | 5 / 4 |
| 2026-10-04 | `a75b9db7` + project list + headers | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,617 / 54 / 10 | 255 (92) | 0 / 0 | 0 | 5 / 4 | 52 (23) |
| 2026-10-04 | `ede454cf` + modal sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,699 / 50 / 10 | 216 (80) | 0 / 0 | 0 | 5 / 4 | 20 (14) |
| 2026-10-04 | `36e69595` (base, filters) | 61 / 70 / 33 | 3 (4) | 14 / 46 | 16 (16), 2 kept | 4,714 / 50 / 10 | 216 (80) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 39 |
| 2026-10-05 | `36e69595` + filters sweep | 52 / 61 / 24 | 3 (4) | 14 / 46 | 16 (16), 2 kept | 4,810 / 50 / 10 | 210 (78) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |
| 2026-10-05 | `97b8d876` + charts sweep | 50 / 59 / 24 | 2 (3) | 14 / 46 | 16 (16), 2 kept | 4,754 / 50 / 9 | 210 (78) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |
| 2026-10-05 | `2122755e` + sweep 12 (src from the air) | 50 / 59 / 24 | 2 (3) | 14 / 46 | 16 (16), 2 kept | 4,754 / 50 / 9 | 210 (78) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |
| 2026-10-06 | `b682c918` + Resource Details round A | 50 / 59 / 24 | 2 (3) | 14 / 46 | 20 (20), 2 kept | 4,736 / 50 / 8 | 183 (74) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |
| 2026-10-06 | `b682c918` + round B (shared-usage grammar) | 50 / 59 / 24 | 2 (3) | 14 / 46 | 20 (20), 2 kept | 4,750 / 50 / 8 | 183 (74) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |
| 2026-10-06 | `b682c918` + round C (jobs, disk scans, drill macros) | 50 / 59 / 24 | 2 (3) | 14 / 46 | 20 (20), 2 kept | 4,763 / 50 / 8 | 163 (68) | 0 / 0 | 0 | 5 / 4 | 33 (23) | 6 |

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

- [x] Three leaf-versus-subtree rules (sweep 12: `Project.sums_as_subtree()`; there were four, and
  they agreed on every row of both snapshots): `src/sam/queries/allocations.py` (valid tree and not a leaf),
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
- [x] (sweep 12: a session proxy + `test_layer_imports.py`) `sam` still imports `webapp.extensions` in `sam/schemas/__init__.py` and `sam/base.py`
  (this sweep removed the `normalize_end_date` edge). The rule is not gated.
- [ ] jscpd clones not read (sweep 12 read the first: ~60-line loops differing in four places,
  a merge decision recorded at `adjustment.py:59`; leave): `sam/xras/handlers/adjustment.py` / `supplement.py`,
  `sam/summaries/archive_summaries.py` / `disk_summaries.py`, the `cli/*/display.py` pairs.
- [ ] dup-functions left: `active_account_users` on User and Project. (The duplicate `decorate`
  in `charts/stacked.py` went in sweep 11.)
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
- [x] Bootstrap 4 classes (+14 / -14): 20 uses down to 0, held there by `test_template_detectors.py`.
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
- [ ] `xras_request_detail.html`: 9 small outline buttons remain beside its 6 `btn-row`s. The XRAS
  cards are only half adopted (nested tables, `width:99%`).
- [x] The saturated `modal_scaffold` headers (entry 9: quiet accent headers) and the `text-bg-*` toasts (the alert recipe, sweep follow-ups). This is an app-wide
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
- [ ] 14 `class="btn  btn-…"` double-space leftovers in 10 templates: an old bulk `btn-sm`
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
  means reworking its data-sort attributes. Done in entry 8. Resource Details
  (`resource_details.html`, `resource_details_disk.html`) is the last user of `render_usage_bar`.

- [ ] On a phone, the project card's resource table scrolls sideways inside its frame. The
  meter column could hide below md and show only the percentage.
- [ ] The user card's group branch switch is still the solid-blue toggle bar, like the page
  tabs and pills that sweep 5 kept.
- [ ] `contract_card.html` uses `stat-item-block` with stacked labels and could opt into
  `.stat-item-inline`, as the transaction / adjustment / XRAS detail modals do (entry 9).

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
- [x] Header wrapping (Ben: "Annual rate" and "Days left" set their columns' widths; then
  "sweep this for consistency"):
  - A multi-word `.col-num` / `.col-shrink` header wraps at its spaces, app-wide, so a column
    sizes to its figures. Where the content is wider (share bars, meters) nothing changes.
  - Measured column width before -> after, at 1440px:
    - the tree's Annual rate: 115 -> 76;
    - Default amount: 146 -> 80; Wallclock limit: 146 -> 105;
    - Processed by: 145 -> 100; Adjusted by: 136 -> 95;
    - Used / Capacity (TiB): 93 -> 63 / 130 -> 90.
  - Wrapping exposed sort icons orphaned on a second line (Font Awesome's `inline-block` is a
    break point) and a bare `#` leading a line. `sort_link` / `sort_header` and the `::after`
    arrows now join the icon with `&nbsp;`, and "Request #" / "Contract #" bind the `#`.
  - Orphan check across 34 pages at two widths: 0 new.

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
  raw `.progress` holdouts. The usage modal was an orphan and is deleted (entry 9).
- [ ] The project list is still a nested table in a spanning cell, not rows of the tree table
  (wire-dashboard §7). Its column set differs from the tree's, so folding it in would mean the
  tree growing columns.
- [ ] On a phone, the list's own `table-responsive` never scrolls. It sits in an auto-layout
  cell, so the tree's wrapper scrolls the whole tree (1055px).
- [ ] Headers outside the column roles (no `.col-num` / `.col-shrink`) keep Bootstrap's
  wrapping and no `&nbsp;` icon join. jobs, disk scans and the members table carry their own
  sort markup; the orphan check found nothing there today.

## 9. 2026-10-04: area sweep, `templates` round 5 (the modals)

**Mode:** area, from `docs/plans/MODAL_SWEEP_HANDOFF.md`. **Stacked on:** sweep 8's branch
(`alloc-project-list-2026-10`, #729; end commit `ede454cf`). **PR:** #730, merged with the
stack as #726.
**Contract:** aesthetic; each commit declares its change with dialog heights from
`ui_snapshots.py --recipes scripts/ui_snapshots_modals.json` (42 modals, 1440px and 390px, both
themes). Ben's calls: quiet accent headers everywhere; one PR; the three bugs first.

**Done, in this round's PR**, one commit each:

- [x] Bugs, each with a regression test:
  - Create Resource's two pickers reach the row. The schema dropped them; the ORM always
    accepted them.
  - Detach / re-link / propagate errors render inline. They were 400s that htmx never swaps.
  - The orphan panel-session edit is deleted.
- [x] `ui_snapshots.py --modal` / `--step` / `--recipes`: shoots the open dialog and prints
  `height` / `natural`, the latter being what a fullscreen phone dialog needs unclipped.
  It retries a suspended navigation and grows the window for a tall dialog.
- [x] Shared chrome:
  - one `--surface-accent` header (10.41:1 light, 8.81:1 dark);
  - danger ink for a destructive confirm only;
  - square corners;
  - footer buttons sized to the dialog;
  - the ten lazy hand-rolled shells on `modal_scaffold`.
- [x] Shared pieces:
  - `form_fields` `tip=` / `tip_rich=`;
  - `select_field` option data, `groups=`, `attrs=`, `help_html=`;
  - `date_field` `attrs=`;
  - `htmx_form` `submit_id=`;
  - `modals.modal_title` (the identity in the header);
  - `.modal-facts` (facts as one quiet panel);
  - ten glossary terms;
  - `email_preview.delivery_banner`.
- [x] Per-modal passes: edit allocation; exchange and allocate down; renew / extend / align;
  add allocation and notify; the tree and linked elements; the admin CRUD family;
  invitations; add member; create adjustment; the detail modals; queue cleanup and bulk
  deactivate; the XRAS merge and action forms.
- [x] Submit buttons say the bare verb when the header names the thing (Ben, mid-sweep).
- [x] The orphan usage modal is deleted (Ben). It replaced step 19.
- [x] `modal-alerts` detector plus an equality ratchet (`test_template_detectors.py`, 33: 20 in
  the form and shell templates, 13 in the XRAS fragments that swap into the details modal).

**Dialog natural height**, before -> after (light; 35 modals with both captures). Total:
23,143 -> 20,698 at 1440px (-11%) and 29,968 -> 24,455 at 390px (-18%).

| modal | 1440px | 390px |
|---|---|---|
| edit allocation, shared | 764 -> 637 | 1078 -> 798 |
| edit allocation, carve-out child | 788 -> 597 | 1054 -> 731 |
| edit allocation, pool root | 790 -> 627 | 1056 -> 812 |
| edit allocation, carve-out root | 690 -> 589 | 908 -> 723 |
| user edit allocation | 585 -> 499 | 733 -> 612 |
| exchange | 551 -> 427 | 593 -> 454 |
| allocate down | 675 -> 583 | 757 -> 658 |
| add allocations | 1145 -> 1015 | 1514 -> 1075 |
| renew | 900 -> 843 | 1258 -> 1001 |
| extend | 897 -> 877 | 1204 -> 926 |
| align | 596 -> 511 | 806 -> 655 |
| notify | 1963 -> 1996 | 2360 -> 2332 |
| invite | 800 -> 729 | 1126 -> 929 |
| new event | 1178 -> 1003 | 1584 -> 1185 |
| add member | 493 -> 500 | 713 -> 631 |
| create adjustment | 683 -> 651 | 793 -> 705 |
| transaction / adjustment details | 679 -> 639 / 539 -> 499 | unchanged |
| fair-share override | 428 -> 279 | 581 -> 303 |
| resource edit | 524 -> 405 | 718 -> 494 |
| machine / queue edit | 545 -> 451 / 607 -> 513 | 739 -> 513 / 763 -> 537 |
| facility / panel / allocation type edit | 475 -> 381 / 389 -> 295 / 475 -> 381 | 607 -> 381 / 521 -> 295 / 607 -> 381 |
| resource create | 655 -> 733 | 827 -> 811 |

**Deviations from the handoff:**
- The danger header is danger ink plus a rule, not a fill: white on vermilion measured 3.84:1.
- Notify keeps its own delivery banner, now the shared macro: with a Skip first row, the pane
  shows a note and no banner.
- Bulk deactivate stays hand-rolled: its Back button re-fetches step 1, which `htmx_form`'s
  Cancel cannot express.
- Step 6 had one more shell (`allocations/adjustments.html`).
- Step 15 measured and did not fold: only 11-15 lines per create/edit pair are shared, so a
  `mode=` template is no shorter.
- Step 19 became a deletion.
- Institution and organization keep their read-only pair (an id plus a type or parent, not a
  name).
- Resource create grows: its fields stack at the default width.
- The add-member commit message gives 484 / 653; the true heights are 508 / 701.

**Tried and dropped:**
- Bare `tip=` icons after an inline-block label: a wrapped label orphaned the icon. Fixed with
  `.form-label-tip` and `&nbsp;`.
- A term inside "emailed a link": tests pin the phrase. A help icon follows it instead.

**Open from this round:**

- [ ] Resource edit cannot set the primary sysadmin or responsible organization
  (`EditResourceForm`, `Resource.update`).
- [x] `htmx_panels_for_facility` admits `CREATE_FACILITIES` as well as `CREATE_PROJECTS`, so
  New Allocation Type's Panel cascade fills for its own creators (sweep follow-ups).
- [ ] The hand-rolled detail shells (project, user, contract, audit, chart expand, outage)
  could share a static-body scaffold.
- [ ] The XRAS forms' own footers keep the page-size buttons (they are not `.modal-footer`).
- [ ] Extend's resource table scrolls sideways at 390px now that its dates no longer wrap.
- [x] The toasts take the alert recipe instead of `text-bg-*` fills (sweep follow-ups).
- [x] `test_ticket_card::test_a_request_without_tickets_renders_no_ticket_row` reads only its
  own row, so another worker's ticketed request cannot reach it (sweep follow-ups).
- [ ] `test_db_browser_killswitch` fails under xdist on some postgres-test runs.

## 10. 2026-10-04: area sweep, `templates` + `py` (filters and facets)

**Mode:** area, from `docs/plans/FILTERS_SWEEP_HANDOFF.md`. **Base:** `36e69595` (staging).
**Branch:** `filters-sweep`. **PR:** #733 (draft).
**Contract:** mixed, declared per commit. Items 7 and 8 must look identical; items 3b, 6 and 11
are deliberate visual changes; items 1 and 9 change behavior to fix a bug.

**Done**, one commit each:

- [x] 1. `webapp/utils/facets.py`: `Facet` / `FacetSet`, one in-memory facet engine for account
  requests and the three XRAS worklist cards.
  - Fixes Pending Users: an Identity selection reached none of the other four strips, and a
    Request selection reached no strip.
  - A selected value always renders, so Readiness and Blocker chips can be deselected at zero.
- [x] 2. `read_multi` replaces nine `getlist` comprehensions.
- [x] 3. `facet_fields` / `facet_form` / `facet_grid_row`: about 20 hand-written hidden selects
  go. Gate: `tests/unit/gates/test_facet_form_contract.py` (every chip resolves to a control;
  every `FacetSet` dimension has one).
- [x] 3b. Chips are multi-select toggles (`aria-pressed`). A single-value target (jobs explorer,
  mnemonic Show, Last seen Source) still replaces.
- [x] 4. One `sort_rows` in `utils/htmx.py`; mnemonic codes reads its sort through `read_sort`.
- [x] 6. In-card atoms: `facet_clear_all`, `filter_search` (seven boxes, five triggers, now one),
  `active_switch` (seven cards).
- [x] 7. One pill markup (`btn-outline-secondary` + `active`); the second CSS rule goes. Gate:
  `test_pill_markup.py`.
- [x] 8. The Allocations and Expirations panels on `filter_panel_shell`, which gains a bare mode
  for a form its page's script submits.
- [x] 9. `webapp/utils/windows.py`: `read_days`, `read_log_window`, `read_chart_window`.
  The audit default window loses its sub-second upper bound.
- [x] 10. Eleven flag, sort and page reads move onto the shared helpers; first direct tests of
  `read_flag`, `read_page`, `read_sort`, `build_facet_strip`, `parse_window`.
- [x] 11. (Added by Ben at kickoff: ordering and packing of the older panels.)
  `multiselect_filter` is a one-line dropdown checklist; the XRAS action log panel drops the two
  lists its chips already cover; `filter_apply()` is the one primary action; the house field
  order is written into `filter_panel.html`.

- [x] Growth rule, from the friction this sweep met:
  - `helper-bypass` detector (`sweep_inventory.py`, fixture test) and a "Bypassed helper"
    heuristic in the sweep skill: request reads that hand-roll a shared reader. 39 sites at
    the base, 6 now.
  - `ui_snapshots.py --compare` counts a custom property that differs alone without failing
    (`--strict` fails it) and takes `--px-tolerance`; `--element SEL` + `--compare-pixels`
    prove a restructure, where every element path moves. Replayed on item 7's captures:
    36 of 42 "differ" -> 6, all one live clock label.
  - `wire-dashboard-feature`: the filter tiers and macros, and the `compose --watch` trap (a
    module saved one edit before its import exits samuel-dev). Its length budget goes
    250 -> 275.

**Panel height**, before -> after (px), desktop / tablet / phone:

| panel | 1440 | 1024 | 390 |
|---|---|---|---|
| Transactions / Adjustments | 279 -> 203 | 469 -> 289 | 996 -> 788 |
| Allocations | 279 -> 175 | 349 -> 245 | 743 -> 535 |
| Expirations | 279 -> 175 | 349 -> 245 | 749 -> 573 |
| XRAS action log | 305 -> 203 | 521 -> 203 | 962 -> 530 |

**Deviations from the handoff:**
- Item 5 stopped at the strips (`build_facet_strip` in the action-log route). The `LogSpec`
  retrofit is larger than "add a `LogSpec`": `facet_counts` groups on the raw column, so the
  `Adjust` / `Adjustment` aliases would split; `_apply_action_filters` returns a query, not
  terms; and `summarize_xras_actions` also feeds the CLI. `src/querykit/README.md` lists it as
  deferred growth.
- Last seen's Source stays one value. Choosing a source re-derives every row from that
  source's sightings (`review()`), so it is a lens, not a filter. The buckets are multi-select.
- Mnemonic Show stays one value: its values overlap and `all` is a sentinel.
- Item 7's `--compare` is not literally zero: it reports the selected pill's own `--bs-btn-*`
  custom properties and sub-pixel noise on live status charts. No rendered property differs.
- Item 8's Expirations panel is not identical: its title gains the shell's collapse chevron.
- Item 9's audit bug is latent. Both pages always send their form's explicit dates.
- The handoff paths `xras/...`, `jobs/routes.py` and `disk_scans/routes.py` were wrong
  (`allocations/xras/...`, `src/webapp/jobs`, `src/webapp/disk_scans`).

**Tried and dropped:**
- A solid-blue icon cell on the search box (the default `.input-group-text`): too loud beside
  quiet chips. It takes the input's own surface; contrast 4.0:1 light, 7.3:1 dark.
- Text-tertiary for that icon: 2.86:1.
- Folding the Expirations hints into longer labels: "(abandoned users)" wrapped Export CSV to a
  second row at 1440px. "(abandoned)" fits.

**Open from this round:**

- [ ] XRAS action log as querykit's third caller (see Deviations).
- [ ] `observed_task_names` (`system_status/queries/task_runs.py`) has no caller outside its
  tests since the tasks page stopped pre-filling its hidden select.
- [ ] `get_observed_action_types` likewise, since the action-log panel dropped its list.
- [ ] `/admin/organizations?tab=institutions` leaves the pane on "Loading institutions…": its
  trigger is `shown.bs.tab ... once`, which never fires for a tab rendered active.
- [x] The Notifications and Scheduled tasks Filter cards are gone: the search and the window
  are rows of the log's chip grid (`search_box.filter_window`), and the summary is a one-row
  `.stat-strip`. The log starts about 200px higher.
- [ ] `tests/unit/queries/test_contract_audit.py` deadlocks on an `nsf_program` insert between
  xdist workers (passes with `-n 0`).
- [ ] A `ViewState`-like registry for the jobs explorer's five overlapping key lists (model:
  `db_browser/params.py`); one macro for the notifications and tasks filter cards; a shared
  uppercase-label CSS block. Ledger-only by Ben's call in the handoff.
- [ ] The disk-scan directory sort is still hand-whitelisted (`read_sort` is not a drop-in).
- [ ] The six `helper-bypass` leads left: `include_adjustments` (`api/v1/allocations.py`,
  `api/v1/projects.py`), `strict` (`api/v1/health.py`), `sent` (`register/blueprint.py`), and
  two `int(v) for v in form.getlist('resource_ids')` in `admin/projects_routes.py`, which is
  a form POST and wants a schema, not `read_multi`.
- [x] Option order in the Facilities and Resources checklists stays alphabetical, matching
  the resource tabs (Ben, 2026-10-05).

## 11. 2026-10-05: area sweep, `py` + `templates` (charts)

**Mode:** area, from `docs/plans/CHARTS_SWEEP_HANDOFF.md` (22 items, all picked by Ben).
**Base:** `97b8d876` (staging). **Branch:** `charts-sweep`. **PR:** #735 (draft).
**Contract:** mixed, declared per commit. Fingerprints are the proof for the package, and
`scripts/chart_sheet.py --compare` (new) is the stronger one for a refactor: all 227 sample
renderings byte for byte, which sees the geometry, strokes, opacity and `<title>` text the
fingerprint cannot.

**Done**, one commit each unless noted:

- [x] 0. Tooling: `scripts/chart_sheet.py` (a byte-stable contact sheet of every chart in six
  states), `ui_snapshots.py --pages charts` (19 chart hosts, each with its own steps), the
  allocations profiler off the retired pie generators.
- [x] 1. Pace honors the facility filter. The fix as written would have given the page-wide
  chart the facility card's DOM id; the card marks itself (`card=1`) instead.
- [x] 2. `#chartExpandModal` is in `dashboards/base.html`. The machine job explorer drew the
  opener with no modal on the page.
- [x] 3. The public queue-history page no longer loads the login-only user/project chart (its
  401 carried `HX-Redirect`, sending an anonymous visitor to the login screen).
- [x] 4. Tick labels in one unit and one precision per axis (`fmt.axis_labels`). The old
  formatter also rounded fractions: ticks at 2.5, 7.5 and 12.5 read `2`, `8`, `12`.
- [x] 5. Four wrong texts: the disk card's "top 10", partition history calling itself a node
  type, an all-zero distribution drawing empty axes, the used-window caption written twice.
- [x] 6. Dead code: the pie legend fallback, every private alias on the charts facade (20),
  `generate_jobs_user_pie_chart`, two unused parameters, dead template branches, the
  `loading_spinner` macro and its CSS.
- [x] 7. Stale prose, done last: counts, the lifecycle docstring, "no chart SVG in a cached
  route", CLAUDE.md, `CHART_ARCHITECTURE.md` (it said PROPOSED), three Status lines.
- [x] 8. The duplicate `decorate` in `stacked.py` (entry 4's open item).
- [x] 9. Family homes: `_CumulativePie.build`, `PieChart.tooltip_text` and `ring`, the usage
  trends' shared base, the dual-panel `cache_key`. The three-ring rim names its entity
  (`rim_link`, `rim_noun`).
- [x] 10. Two legend kinds and one `legend_cells(label, value)` signature.
- [x] 11. Sizes and inks from `Layout` and `Theme`: `label_kw` on the histograms and dual
  panels, `Layout.line_scale`, `Theme.muted_alpha`, Pace's remainder at `area_alpha`.
- [x] 12. `series.fold_top` and `series.other_label`; `Series.is_other`; the three producers
  of the stacked remainder emit their folded `count`.
- [x] 13. The axis-forwarding gate checks every call in all six calling modules.
- [x] 14. Route smokes for four chart routes that had only a route-map pin.
- [x] 15. Pins: `PanelSunburst` in `LAYOUT_OWNERS`, the light surface, `facility_slots`.
- [x] 16. `webapp.utils.charts.draw_chart` at all 21 call sites; a cached page declines to
  cache a chart error.
- [x] 17. Hover titles on pies, histograms, stacked charts, area bands and Pace (four
  commits), with project titles for viewers `hover_titles` admits.
- [x] 18. Table legends with figures on the usage trend, disk usage and the jobs timeline.
- [x] 19. The chart frame: `fragments/chart_bits.html` (`chart_figure`, `chart_loading`,
  `chart_pills`, `chart_error`). Twenty loaders, twenty pill groups, fifteen unnamed charts.
  Gate: `tests/unit/gates/test_chart_frame.py`.
- [x] 20. `charts.css`. Pace's doubled gutter, one pie cap.
- [x] 21. The status history pages change their window in place (`chart_pills` with `select`
  and `push`), with no fragment route.

- [x] Growth rule. This sweep's new class is **a shared opener whose shell each page must
  remember to include** (item 2). The modal-shell gate walks `extends` and `include` but not
  `{% from %}`, so it cannot see which hosts call a macro. `SITE_WIDE_SHELLS` pins the
  structural answer: such a shell is included by the base page and nowhere else. The
  dashboard skill says so beside the modal trap.

**Deviations from the handoff:**
- Order: bugs, then the safety net (13 to 15) ahead of the refactors it guards, then 5, 4, 6,
  8 to 12, 16 to 21, and 7 last, so prose was rewritten once.
- Item 2: the handoff's pin (`jobs_usage_panel.html` in `HTMX_FRAGMENT_SHELL_DEPS`) cannot
  work, for the reason under Growth rule.
- Item 6: `PaceChart(top_n)` stays (a sample passes it; the layout clamps it), and
  `allocations.css:31-44` was not dead (its `width: 100%` stretches Pace); item 20 moved it.
- Item 8: one duplicate `decorate`, not four.
- Item 12: the three-ring rim keeps `+N` on the wedge (a 7pt radial label where `12 other`
  rarely fits); its hover says `N other`. Plugin remainders read `Others`: the count is unknown.
- Item 16: the five places a panel skips its chart when it has no rows stay. Each panel's own
  "no jobs match" message covers the table too.
- Item 17: bar segments hover with their own value, not the band total; area bands and Pace
  got titles although `CHART_HOVER_LAYER.md` had left areas out. That plan records both.
- Item 20: the ring charts stay out of the below-desktop gutter, on purpose (grid columns).
- Item 21: swapping only the chart would have left the statistics card, the breadcrumbs and
  the user/project card on the old window, so the page's main content is swapped instead.

**Tried and dropped:**
- Following `{% from %}` in the modal-shell gate's closure walk: 36 of 54 pinned entries
  change, mostly noise (an imported file is not a called macro), and it surfaced one lead,
  `dashboards/user/accounts.html` (allocate-down and exchange modals), left open below.
- `MaxNLocator(integer=True)` with its default steps on the dual-panel count axes: ticks at
  150, 300, 450. `steps=[1, 2, 5, 10]` keeps AutoLocator's ticks less the halves.
- Naming fs-scan histogram segments after the fold: a uid cached as a string was taken for a
  username. Owners are rekeyed by username before the fold.

**Measured:**
- Hover titles add about 11% to the bar charts (a 365-day, 11-band usage trend: 1.39 MB of
  SVG before, 156 KB of titles; the 120-bar jobs timeline: 405 KB to 454 KB) and 12% to the
  three-ring chart on Derecho (242 KB to 270 KB, 436 project titles).
- Pinned routes with one more query each: Pace 40 of 55, expanded 28 of 36 and 17 of 22.
- Item 19: 32 of 38 before/after page shots pixel-identical; the six are the intended ones.
- Item 20: 104 of 114 computed-style captures identical; the ten are Pace's gutter and one
  color serialized differently.

**Open from this round:**

- [ ] **The By User sunburst** (Ben, 2026-10-05): facility, panel, user, with usernames on the
  rim. Needs a user x account rollup in `hpc-usage-queries` first.
  `docs/plans/JOBS_USER_SUNBURST_HANDOFF.md`.
- [ ] A usage trend over a long window is megabytes of SVG (4,015 rectangles at 365 days by 11
  bands), before any title. Weekly bars past some span, as the jobs timeline does, would fix it.
- [ ] `dashboards/user/accounts.html`: the import-following experiment says it reaches the
  allocate-down and exchange modals without including their shells. Not read for intent.
- [ ] The chart-expand shell is still hand-rolled (entry 9's scaffold item); its ids are
  `...Title` / `...Body`, which `svg-chart-links.js` reads.
- [ ] Count axes outside the dual panels (jobs histogram, timeline) can still tick at halves.
- [ ] `test_account_requests_manage.py::TestEventEnrollmentLedger::
  test_existing_user_invite_without_event_records_nothing` counts every `EventEnrollment` row
  and failed once under xdist; passes alone. (`test_contract_audit.py`, entry 10, also bit.)
- [ ] The local `hpc-usage-postgres` container was down for this sweep, so no jobs chart was
  seen in a browser: By Project expand, the timeline legend and the jobs hovers want a look
  on samuel-dev.
- [ ] Carried from the handoff's "Ledger only" list, not built:
  - Palette single-sourcing and the brand-drift tokens (entry 5): every fingerprint moves.
  - The `auto` theme; a styled tooltip (`CHART_HOVER_LAYER.md` step 4); per-surface figure
    tuning (`MOBILE_CHARTS.md`).
  - The expanded chart reuses the viewport's layout and is stretched by CSS.
  - "Others" is the largest band on the status load chart at top 15: a data-design question.
  - JS: the param-stripping `configRequest` code in `layout-axis.js` and
    `nav-view-persistence.js`; two cookie writers, one setting `Secure`; the breakpoint
    strings in four files.
  - Facility ordering written three times (`sunburst.py`, `jobs/routes.py`,
    `allocations/blueprint.py`, the last by id rather than slot).
  - The #707 review items on entry 1: the run-out tick's color token, Pace's zero projection
    for a new project, calendar month figures living only in hover titles.
  - `CacheBase.bytes_used` (entry 4); matplotlib's own ids repeating when one cached SVG
    appears twice on a page.

## 12. 2026-10-05: area sweep, `src/` from the air (allocation usage, import graph)

**Mode:** area, all of `src/` (117k lines, 30% prose), read for the structural debt a small team
has to carry rather than for detector leads, which sweeps 1–11 had drained. **End commit:**
`2122755e` (`origin/staging`). Three censuses (allocation usage, the XRAS footprint, the route
monoliths and CLI) ranked three findings; Ben picked #1 and #3, deferred XRAS and left #2 open.
`src/` across the branch is +262 / -1,646.

**Parity:** `utils/profiling/usage_sweep/` (untracked): 659 captures over 108 projects (every
non-leaf, a child of each, inheriting and disk holders, charged leaves, SCSG0001) of every live
usage assembly at a fixed as-of date, plus a rule matrix that sums each anchor both ways from the
kernel. Two runs of unchanged code agree exactly on mysql-test and postgres-test; the backends
differ from each other only at the eighth significant digit.

**Done, in this sweep's PR**, one commit each:

- [x] Dead: `sam/queries/examples.py`, the ORM notebook, the `sam_search_cli` shim (-425).
- [x] `sam/queries/__init__.py` re-exports nothing. Its 122 names had one consumer, the dead
  `examples.py`; the facade was the whole reason for the eager-import trap three docstrings, a
  CLAUDE.md DON'T and two tests described (-418).
- [x] The schema packages bind `sqla_session` to a proxy, so `import sam.schemas` loads no Flask;
  `tests/unit/gates/test_layer_imports.py` holds the facade empty and the `sam`-side packages free
  of module-level `webapp` imports.
- [x] `Project.sums_as_subtree()`: the leaf-versus-subtree rule, written 5 times in 3 shapes (8
  copies of the coordinates check), now one method. **The four rules agreed on every row:**
  `is_leaf()` already answers True without coordinates, and the "leaves included" spelling can
  only differ through a deleted or duplicate account, of which both snapshots hold zero.
  Rule matrix: 247 anchors, 146 on parents, 80 with a subtree total that differs from the
  own-account total, 0 disagreements.
- [x] `usage_anchor` / `anchored_charges` in `accounting/calculator.py`; the four hand-built anchor
  dicts and the `Project.batch_get_*_charges` pass-throughs are gone; fstree needs no `Project`.
- [x] One assembly. `Project.get_detailed_allocation_usage` reshapes the dashboard builder's rows
  (its 7 callers gain the read model; `include_adjustments=False` works, it raised before);
  `AllocationWithUsageSchema` sums through the kernel once per allocation per dump (statements
  2,926 -> 712 over the set); the per-account kernel copy (`get_subtree_charges`, five siblings,
  `calculate_charges`) is deleted. **Output change:** the CLI JSON drops the unread `hierarchical`
  key. Perf tier green, `baselines.json` unchanged.

**Tried and dropped:**
- Having the API routes precompute usage and hand it to the schema (plan step 1f): memoizing one
  kernel call per allocation got the 4x without a new context key, and the read model already
  serves those routes when fresh.
- Deleting `sam/queries/xras_remediations.py` (tests-only): XRAS, so it waits with the rest.

**Deferred until `XRAS_SUBMISSION.md` lands (Ben, 2026-10-05).** The XRAS census (22.7k Python
lines, 4.9k template lines; two HTTP clients on purpose; the query modules do three different
jobs): `webapp/dashboards/allocations/xras/remediation.py`'s seven editors each repeat a
`_safe_*` fallback / GET modal / one-click-write triplet (984–1929; about -250);
`xras_api/admin_client.py`'s 14 write calls share a read/write/re-read tail `_act` could
generalize (about -150); the preflight batch duplicated by `manage/xras_remediation.py:257` and
`scheduling/tasks/xras_sweep.py:265` (about -60); five of the six `xras_views` classes and
`queries/xras_remediations.py` have no `src/` caller (about -200); both clients import parsing
helpers from `queries/xras_requests.py`, so the HTTP layer depends on the query layer; about
1,000 lines of over-budget prose in `integration/xras.py`, `xras/extractors.py`,
`queries/xras_actions.py`, `queries/xras_accounts.py`, `api/xras/actions.py`. The
adjustment/supplement handler clone was read: leave.

**Open from this sweep:**

- [ ] **`admin/projects_routes.py` (3,527 lines).** Nine sections with clean ranges (create
  210–689, add/exchange 1017–1537, renew/extend 1538–2313, linked entities 2689–3027,
  directories CRUD 3028–3394, access grid 3395–3527) want to be ~6 modules on the same blueprint.
  Project creation (`:580–660`) is duplicated in `sam/xras/handlers/new.py:170–207` and belongs in
  `sam.manage.projects.create_project()`; `_exchange_candidates:1195`, the renew date proposals
  `:1547–1660` and the contract auto-retire `:2943` are domain rules testable only through Flask.
- [ ] Eight retire/toggle routes on one `management_transaction` + error skeleton
  (`resources_routes.py:160,238,261,386,749`, `contracts_routes.py:694`,
  `projects_routes.py:3268`, `admin/blueprint.py:1262`, which has no guard): extend
  `handle_htmx_soft_delete`.
- [ ] `_window_control_context` exists three times (allocations blueprint, `jobs/routes.py:1473`,
  `disk_scans/routes.py:168`); home: `utils/age_bands.py`. Homes for the other private imports:
  `_available_primary_groups` -> `sam/queries/lookups.py`; `_user_can_access_project` ->
  `utils/project_permissions.py` (`_get_sam_user` repeats a query `current_user.sam_user` has);
  `_RESOURCES_TABS` / `_ORGANIZATIONS_TABS` -> move the page routes into their modules.
- [ ] The six linked-element routes (`projects_routes.py:2833–3006`) check a global
  `require_permission` where their GET is project-scoped. A policy decision, not a tidy-up.
- [ ] CLI: shared option bundles for `cmds/search.py` / `cmds/admin.py` (about -45; `--help`
  text moves); the provisioning-issues table, status-style and timestamp helpers duplicated across
  `cli/*/display.py` (about -30); the mnemonic handlers onto `HtmxFormHandler` (about -40);
  `legacy_dedup_key` once a notification cycle has passed.
- [ ] `READ_MODEL.md:141` now matches the code: resource details' summary card is served by the
  read model through `get_detailed_allocation_usage`.
- [ ] Prose: `src/` is 30.0% doc lines; `DOC_SLIMMING.md` phases 5–8 remain the vehicle.

## 13. 2026-10-06: area sweep, `templates` round 6 (Resource Details, round A)

**Mode:** area, `templates`, from `RESOURCE_DETAILS_SWEEP_HANDOFF.md` round A. **End commit:**
`b682c918` (`origin/staging`, sweep 12 merged). Aesthetic contract as sweeps 5–9: each commit
declares its visual change; bug fixes say "bug". Proof rig: staging served on 5053, the branch on
5052, `ui_snapshots.py` in the six states on CESM0002 (Derecho, Campaign_Store), P93300042/Casper
(shared) and SCSG0001/Derecho; Playwright smoke of every control moved.

**Done, in this sweep's PR**, one commit each:

- [x] The compute and disk scope pickers are `project_tree_rows` tables. The macro gains a picker
  mode (`href`, `link_attrs`, `idle_title`, `value` as node macros); its two older callers render
  byte-identically (HTML diffed against staging). `.tree-list` (two CSS blocks) is deleted, and
  `.tree-node-current` keeps only its `<tr>` form, which drops the whole-row bold (bug).
- [x] The summary rows are `allocation_cells` + `allocation_actions` (`edit_url=` added for the
  user-route edit form). `get_resource_detail_data` stamps `elapsed_pct` / `bar_state`;
  `get_detailed_allocation_usage` is untouched, so the CLI JSON and `/api/v1/users` do not move.
  `shared/usage_bar.html`, `.progress-small`, `.table-col-usage`, `.table-bordered` are deleted.
- [x] Nine card headers on `collapse_toggle` + `.accordion-chevron` (now in components.css). Their
  chevrons never rotated: `.transition-smooth` had no transform (bug). `drill_toggle` added to
  `collapse.html` for a row that also holds a link (By User rows: `user_link` + chevron button,
  `data-bs-target` kept on the `<tr>` for `openUserRow`).
- [x] Column roles: every numeric cell on both pages and the subtrees is `.col-num`; `user_count | fmt_number`.
- [x] Paths are copyable (Ben, 2026-10-06): the disk tree's fileset paths ride an inline
  `copy_button` in the size cell (the name cell truncates); the Filesets Path cell is a link plus
  a copy icon on a new `.cell-truncate .cell-path` (min-width 12rem). The Filesets row stops being
  a `data-action="navigate"` row: a copy button there would also navigate, because the clipboard
  listener on `body` lets the click reach the document-level dispatcher.
- [x] The rolling-rate gauge's 16 inline styles are a `.rate-*` family (geometry on custom
  properties; fill color a state class). Pixel-identical gauge (edge rows aside). Limit actions
  are icon `btn-row`s.
- [x] Bug, pre-existing: on a phone the gauge's bar column resolved to zero width and the
  fixed-layout usage tables split header words letter-wise. `minmax(6rem, 1fr)` + a wrapping
  annotation; the usage tables take a min-width and scroll.

**Handoff corrections (census, 2026-10-06):** `.progress` stays (four status templates use it;
the three `!important`s are on global `.bg-*` utilities); 9 card headers, not 13; 49 `text-end`
cells, not 24; `page_header` was already on both pages; the rolling bar is a rate gauge, not a
pool bar, so it takes neither `alloc_meter` nor round B's pool tone; the disk tree's clickability
and URL differ from compute (no `usage_tab`).

**Tried and dropped:**
- Passing `elapsed_pct` through `get_detailed_allocation_usage` (the handoff's route): it feeds
  the CLI JSON envelope and `/api/v1/users`, so the summary computes it beside its other
  page-only keys instead.
- An `extra=` cell hook on `project_tree_rows` for the copy icon: a trailing icon in a truncating
  cell is the first thing clipped, so the icon lives in the value cell.

**Open from this sweep:**

- [ ] The Filesets card could not render on the local snapshot (no multi-fileset project with
  `disk_activity`); proven by test client + injection. Recheck on samuel-dev.
- [ ] `css-dead`'s dynamic-stem count is 20 (from 16): the four `rate-{{ state }}` classes.
- [ ] The threshold inline form (`threshold_form_htmx.html`) keeps its documented inline styles.

## 14. 2026-10-06: area sweep, `templates` + `py` round 7 (one shared-usage grammar, round B)

**Mode:** area, from `RESOURCE_DETAILS_SWEEP_HANDOFF.md` round B, stacked on entry 13's branch.
**End commit:** `b682c918` (`origin/staging`). One rule for a shared (inheriting) allocation
everywhere it is drawn: the project's own % and Used, "from <root>", the pool's Remaining.

**Done, in this sweep's PR**, one commit each:

- [x] `alloc_meter(..., pool_pct=)`: the pool as a lighter layer of the same hue under the solid
  own share; `.meter-over` (red) follows the pool. `allocation_cells` passes it and restores the
  elapsed tick shared compute rows dropped. A negative Remaining is `text-danger` (bug: muted).
  B.5 (Ben): Allocated keeps sorting by the pool's amount; its title says so.
- [x] Glossary: `g_shared_pool` rewritten (it described the tones backwards and had no caller)
  and carried by `g_usage_bar`, the "% used" help; `g_roots_only` names the pool's Remaining.
- [x] Bug: the card's usage badge read the pool's % over rows showing the project's own; it
  now takes the highest % the rows show and names the fullest shared pool in its title. 30d /
  90d limit lines on a shared row say "pool".
- [x] Bug: shared DISK rows mixed units and owners (own TiB as `used`, TiB-years as
  `self_used`; the summaries path forced them equal; the API schema did both). Pool = the root's
  subtree capacity, self = the project's own, in the builder, the summaries path, the API schema
  and both read-model paths. Found on the way: `bulk_get_subtree_disk_capacity` double-counted a
  repeated pair (the whole-snapshot projection read a 4,096 TiB pool as 8,191).
- [x] `sam-search project`'s shared rows read as on the web; the JSON envelope is unchanged.

**Output changes:** shared DISK rows only. `used` / `remaining` / `percent_used` go from the
project's own TiB to the pool's, and `self_used` from TiB-years to the project's own TiB, on
`/api/v1/projects/<projcode>/allocations` and, because the builder feeds
`Project.get_detailed_allocation_usage()`, on `GET /api/v1/users/<username>/projects` (readable
with an API key) and in `sam-search --format json project`.

**Parity:** sweep 12's 659-capture set moved 0 values on MySQL and Postgres. It holds no shared
disk row (its inheriting set is capped alphabetically at 25), so a new untracked
`utils/profiling/usage_sweep/shared_disk.py` captures all 91 inheriting DISK allocations through
the builder, the summary and the API schema, staging vs branch: only inheriting rows move (58
`used`, 15 `self_used`, 32 summary `total_used`), MySQL and Postgres agree exactly.

**Design and deviations (brand stance of entry 5):** no new hue; the pool tint is the row's own
color mixed 32% toward the track. Measured in-page: solid vs tint 3.4:1 light / 3.2:1 dark (40%
fell to 3.0 / 2.7); tint vs track 1.6:1 / 1.8:1. **Deviation:** the pool layer is below WCAG
1.4.11's 3:1 against the track; it is supplementary, since every shared row also states the
pool's Remaining as text and its title names both figures. In the over state the tint spans the
bar, so only solid vs tint matters (2.3:1, red on red).

**Tried and dropped:**
- A stronger mix for the over state (45-65%): the tint never meets the track there, and a
  stronger mix only lowered solid vs tint (2.0 -> 1.6).
- Keeping the API's `current_used_*` on the project's own subtree: the read-model row carries one
  snapshot date (the pool's), so on and off could not agree; they follow `used`.

**Open from this sweep:**

- [ ] Read-model rows written before deploy carry the old shared-disk figures until the hourly
  `refresh_allocation_state` rewrites them; run it once after the deploy.
- [ ] The rolling-rate fragment keeps its "(N yours)" slice beside the pool's burn: it is a rate
  gauge, not a meter, and its banner already says the rate covers the pool.

## 15. 2026-10-06: area sweep, `templates` round 8 (jobs explorer, disk scans, drill macros; round C)

**Mode:** area, `templates`, from `RESOURCE_DETAILS_SWEEP_HANDOFF.md` round C, stacked on entry
14's branch (`36897959`). **End commit:** `b682c918` (`origin/staging`). Aesthetic contract as
before. Proof rig: staging served on 5053 and the branch on 5052, both with fs-scans on
(`ALT_FS_SCANS_ENABLED=1`, the round's first commit) against the read-only plugin replica. Pages:
both explorers (the jobs pages pinned to a past window, since the machine view lists live jobs),
CESM0002 Derecho Job History and Campaign_Store Filesystem Scans, status Job History / Filesystem Scans.

**Done, in this sweep's PR**, one commit each:

- [x] The jobs and disk-scans partials and both explorer pages moved to `dashboards/jobs/` and
  `dashboards/disk_scans/` (a partial lives with its blueprint; 48 path sites). `--styles
  --compare`: 0 of 42 captures differ (the two live job pages reshot with a pinned window on
  both commits at once).
- [x] `sort_link(..., extra_qs=, fixed_dir=)`. The per-job table's private copy goes (per_page
  rides `extra_qs`). The directory table's `dir_sort_link` goes: `fixed_dir` sends no `sort_dir`
  and points the arrow the facade's way, so Path points up (bug). The route reads the key through
  `read_sort` against `_DIR_SORT_WHITELIST` (kept).
- [x] `collapse.lazy_drill_row` (tr#`<rid>-row`, body #`<rid>-content`, `persist=`) beside
  `drill_toggle`, which the By User / By Project and disk owner/group drills adopt. Pixel-identical
  (`--element` + `--compare-pixels`, six states).
- [x] `collapse.owner_tier`: a band's owners are rows of the outer table in its columns, one
  collapsing `<tbody>` per band (closing a band hides any open drawer), with a trailing Share
  `share_bar` (Ben: band share of the whole, owner share of the band). Histogram, timeline and the
  disk distribution adopt it. On a phone the nested tier never scrolled (bug, entry 8); the outer
  table now scrolls. `tier_num` reads 'charges' as cpu + gpu (no dict carries the key; a plain
  `sum(attribute=)` raised under the Charges pill before it shipped). Owner modal buttons take
  `btn-entity` (20px names). The disk distribution keeps its Owners count as a column (Ben: no
  count badges on row labels).
- [x] Column roles on every jobs and disk-scans table; the job name is the one `.cell-truncate`
  column (10rem floor, `components.css`). One filter-width scale (`.ctl-w-xs..lg`,
  `.ctl-minw-picker`, `filters.css`) for both filter panels: 14 inline widths go; Queue and the
  numeric fallbacks widen 10px.
- [x] Bug: the plugin-off banner's "Check the Database card" link was `/dashboards/admin/configuration`,
  never a route. `url_for`; `jobs_fragment`'s copy of the banner calls `jobs_disabled()`.
- [x] Exit status through `exit_status_badge`; the uncalled `mode_badge` is deleted.
- [x] Bug: the directory table's Last Access header sat left of right-aligned dates. Paths are
  `.cell-truncate .cell-path`; the folder icon is the copy button (Ben: one icon per path), and the
  drill link is titled "Browse into <path>". The atime prints through `fmt_date` (the route
  normalizes the plugin's datetime or ISO string with `parse_wire_date`).
- [x] `page_header` on both explorer pages.

**Handoff corrections:** 18 files moved and 48 path sites, not 25 in 8 files; `mode_badge` had an
import but no call; the owner tiers' columns differ (the timeline adds Charges, the distribution
Data/Files), so the macro takes a column spec rather than one fixed table.

**Tried and dropped:**
- A `group_count` badge on the distribution's band label in place of its Owners column (the
  wire-dashboard §7 group-row rule): Ben, the badge is noise; a count that matters is a column.
- A separate copy icon before the folder icon: two icons for one path.

**Open from this sweep:**

- [ ] **Phone-width wrap sweep (Ben, next after this round):** a chevron or caret plus a name in an
  auto-width cell wraps onto two lines on a phone (the owner tier did until `text-nowrap`); Ben has
  seen carets with project codes do it elsewhere.
- [ ] Bug, pre-existing (staging too): sorting the per-job table by Elapsed desc puts null-elapsed
  jobs first on Postgres; `_visible_cols` then folds the empty columns, Elapsed included, so the
  sort cannot be toggled back.
- [ ] `copy_button`'s title is the copied text, so a hover on the folder says the path, not "Copy";
  discoverability rests on the aria-label and the toast.
- [ ] The distribution's tier persists open across reloads (no `data-no-persist`) while the jobs
  tiers do not; kept as it was.
- [ ] The By User / By Project drawers and the owner drawers render a full per-job table in a
  spanning cell; on a phone that table widens the outer one (it scrolls as a whole).

**Pre-staging review of entries 13-15 (2026-10-06), on this sweep's PR:**

- [x] Bug: disk Resource Details' Capacity Summary, on a scope drawing on another project's pool,
  subtracted the scope's own bytes from the pool's amount (P03010039: Remaining 2,796 TiB while
  NCGD0009's pool is 1,300 TiB over). The row is `allocation_cells` fed by the route:
  `scope_disk_pool` (moved from the blueprint to `sam/queries/disk_usage.py`) names the pool's
  owner, and a scope that is not the owner reads as every shared row does. A scope that owns its
  pool renders as before.
- [x] The dashboard skill's reuse list and `/dev/gallery` name `drill_toggle`, `lazy_drill_row`,
  `owner_tier`, `sort_link`'s two arguments, the `ctl-w-*` scale and `copy_button`.
- [ ] `AllocationWithUsageSchema._disk_caps` is memoized per instance and never cleared (#739
  gave `_sums` a one-dump lifetime). Fold it into the same `dump()` hook once this branch sits on
  staging. No live effect: every caller builds a schema per request.
- [ ] A `?fileset=` view on a project that owns its pool still reads Remaining as the pool's
  amount minus that one fileset's bytes.
- [ ] The card's usage badge is silent when only the pool is nearly spent (own 3%, pool 96%); the
  meter's lighter layer is the one cue. Ben's call; left as entry 14 set it.

## Untriaged: first whole-tree inventory, 2026-10-03

Surfaced by the first run of `scripts/sweep_inventory.py`. Each item belongs to an area sweep;
nothing here has been read for intent yet.

- **py:** the first inventory's py items were read and closed by sweep 4; its leftovers are on
  that entry's open list.
- **docs:** `plans-stale --gh` on 2026-10-03 found no retirement candidates among 22 top-level
  plans. `ADMIN_TABLE_POLISH.md` and `ALLOCATIONS_SUNBURST.md` said "implemented, in review"
  after their PRs merged; sweep 11 corrected both Status lines. They pass the 14-day idle bar
  on 2026-10-17.
- **templates:** sweeps 5–9 took inline `style=""` from 404 to 216 (`sweep_inventory.py --area
  templates`).

## Propagation candidates

Shared pieces a window introduced that an older surface could adopt. The canonical example is the
sunburst, which started on the allocations page and then moved to job history.

- [x] `collapse.drill_toggle` and `clipboard.copy_button` on a `.cell-path` cell (sweep 13):
  the jobs and disk-scans drilldowns (sweep 15).
- [ ] `collapse.lazy_drill_row` / `owner_tier` (sweep 15): `_resource_details_macros.jobs_collapse_row`
  stays a two-level shape (`outer_tr_collapse_id` + an inner `div.collapse`, `data-no-persist`), and
  Resource Details' user and day subtrees load as whole `tr.collapse` rows; neither was converted.
- [ ] The `.ctl-w-*` filter widths (sweep 15): `audit_filters.html`, `allocations/projects.html`
  and `admin/projects.html` still size fields inline, which is why the phone rule keeps its
  `!important`.
- [ ] `sam.dates.start_of_today()`: the `datetime.now().replace(hour=0, ...)` expression still
  appears in `xras/card_routes.py`, `sam/xras/handlers/_allocations.py` and
  `sam/resources/machines.py` (sweep 4).
- [ ] `sam.dates.parse_wire_date`: `webapp/jobs/routes.py:1104` parses its window bounds with an
  inline `fromisoformat` try block (sweep 4).
- [ ] `sam.text.strip_or_none`: `samuel_roles._clean_note` is `strip_or_none(note, _NOTE_MAX)`
  (sweep 4).
