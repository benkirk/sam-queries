# Unplanned-city ledger

The running record of `unplanned-city-sweep` passes (skill: `.claude/skills/unplanned-city-sweep/`).
Each sweep adds an entry. The next window sweep starts from the newest entry's end commit, and the
metrics table shows whether the city is getting more legible or less.

**Entry shape:** date, mode, range or area, end commit, then done (with PR), tried and dropped
(with the measurement), and open. Open items stay on the list until a sweep does them or Ben drops
them. Tick an item when its fix merges.

## Metrics

From `scripts/sweep_inventory.py`, whole tree, run at the end commit.

| date | end commit | private imports (helpers / sites / package-private) | dup-function groups (extra copies) | py-dup-names (names / definitions) | dead CSS classes (dynamic stem) | CSS lines / `!important` / repeated blocks | inline styles (templates) | JS shared names / shared events |
|---|---|---|---|---|---|---|---|---|
| 2026-10-03 | `79f1a147` | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,789 / 70 / 14 | 405 (106) | 5 / 4 |
| 2026-10-03 | `5254c65b` + js sweep | 70 / 88 / 33 | 9 (10) | — | 38 (17) | 4,817 / 70 / 14 | 404 (105) | 5 / 4 |
| 2026-10-04 | `0515333f` + css sweep | 70 / 88 / 33 | 9 (10) | — | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | 5 / 4 |
| 2026-10-04 | `75f89900` (base, py) | 70 / 88 / 33 | 9 (10) | 17 / 59 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | 5 / 4 |
| 2026-10-04 | `75f89900` + py sweep | 61 / 70 / 33 | 3 (4) | 14 / 46 | 15 (15), 2 kept | 4,631 / 59 / 10 | 404 (105) | 5 / 4 |

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

- [ ] PROJECT CARDS / PROJECT TREE (`dashboard.css`, about 240 lines) are user-dashboard
  specific and could move to their own file. Not crowding anything today.

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

## Untriaged: first whole-tree inventory, 2026-10-03

Surfaced by the first run of `scripts/sweep_inventory.py`. Each item belongs to an area sweep;
nothing here has been read for intent yet.

- **py:** the first inventory's py items were read and closed by sweep 4; its leftovers are on
  that entry's open list.
- **docs:** `plans-stale --gh` on 2026-10-03 found no retirement candidates among 22 top-level
  plans. `ADMIN_TABLE_POLISH.md` and `ALLOCATIONS_SUNBURST.md` still say "implemented, in
  review" although their PRs have merged; they pass the 14-day idle bar on 2026-10-17.
- **templates:** 405 inline `style=""` attributes; the project trees (`shared/project_tree.html`,
  the admin allocation tree) carry the most.

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
