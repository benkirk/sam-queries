# Handoff: filters and facets sweep

**Status:** built 2026-10-04 on local branch `filters-sweep` (13 commits on `36e69595`),
not pushed, no PR yet. Full record, deviations and open items:
`docs/plans/UNPLANNED_CITY_LEDGER.md` entry 10. Item 5 stopped at the strips; an item 11
(ordering and packing of the older navy panels) was added at kickoff. Verified: full
`pytest` on MySQL (10,788 passed) and Postgres (10,748), `-m perf` (65), `e2e/` against
samuel-dev (132 passed).

Written 2026-10-04 from a planning session (unplanned-city sweep, wire-dashboard-feature
and frontend-design lenses). Base at writing: `origin/staging` at `5144e70a`.

Every `file:line` below comes from two read-only inventories run against that commit.
**Re-verify each one before you edit it**; the counts are leads, not verdicts (sweep skill §3).

## Context

Ben asked two questions about the filter/facet UI. Does it warrant an object-oriented
redesign to extract common bits? And should filter styling be made consistent across the
project, with https://ncar.github.io/unity/events/ as inspiration but not a constraint?

**What is already shared, and stays shared:**
- `facet_chips.facet_row`, with 13 real callers.
- `filter_panel.filter_panel_shell`, with 6 callers: `audit_filters`, `xras_filters`,
  institutions, the jobs explorer, disk large-dirs, and the db browser (plain GET via
  `action=` / `clear_url=`).
- `ladder_range` / `age_band_range`, and `form_fields.multiselect_filter`.
- `querykit.faceted` for SQL self-exclusion counts (2 callers: notifications, task runs).
- `webapp/utils/faceted_log.py`: `parse_window` and `build_facet_strip`.
- `webapp/utils/htmx.py`: `read_flag`, `read_switch`, `read_active_only`, `read_page`,
  `read_sort`.
- `static/css/filters.css`, which holds all of the filter CSS (541 lines).
- The Unity navy panel (`.filter-sidebar`: gold uppercase title, "Clear filters") is
  already the house look for page filters. Unity's events page draws the same thing: a
  `#00357a` column, a `#fdd509` uppercase heading, white checkboxes, and "× Clear
  filters". So the Unity reference is mostly met already.

**What has drifted is the code around those atoms:**
- Four in-memory facet engines.
- 21 copies of `[x for x in args.getlist(n) if x]`, plus 3 copies of `getlist(..) or None`.
- Hidden chip-backing forms written by hand on every chip page (14+ hidden
  `<select multiple>`).
- About 10 date-window idioms, about 7 "active only" encodings, and 3 search param names
  with 5 different triggers.
- 6 unrelated clear and filter-summary styles.

**The OO answer:** yes, but for one class family only: an **in-memory facet engine**.
- It has four implementations, which clears querykit's own "move in on the third caller"
  rule.
- One of the four has a real count bug (item 1).
- A site-wide `FilterSpec` hierarchy is **not** warranted. Each page's parsing is specific
  to that page. `audit_filters` and `xras_filters` were kept apart on purpose.
- Everything else is fixed with small helpers and macros, not classes.

**Design stance: two tiers, one vocabulary.**
- A page-level filter is the navy `filter_panel_shell`, like Unity events' filter column.
- An in-card filter is a facet-chip grid.
- Every surface picks one tier and builds from the shared atoms: search, switch, window,
  chips.
- The one distinctive element is the chip grid with counts. Everything else gets quieter
  and uniform.
- Write the tier rule into the `filters.css` header and `wire-dashboard-feature` §1.

## Decisions already made

- **All ten findings are picked**, plus multi-select chips (3b). One PR against `staging`,
  with one commit per item in the order below. Each commit is green on its own:
  `pytest tests/unit/gates tests/unit/webapp` plus its domain directory.
- **Chips become multi-select.** Ben: "multi-select is more intuitive and standard". A
  click toggles a value in or out. Values within a dimension are ORed; dimensions are
  ANDed. Self-exclusion counts already suit OR.
- **Selected-pill styling stays Unity:** selected is white, unselected is solid blue.
  Item 7 changes markup only, never the look.
- **Search param names keep their current spellings** (`q` / `search` / `actor`), so URLs
  and existing tests hold.
- **Ledger only, do not build:**
  - a `ViewState`-like registry for the jobs explorer's 5 overlapping key lists (the model
    is `db_browser/params.py`)
  - one macro for the notifications and scheduled-tasks filter cards
  - debounce normalization
  - a shared uppercase-label CSS block
- **Work locally.** Push as a draft only when Ben wants remote CI.
- **An item that turns out larger than its size here stops and is reported**; don't grow it.

## Already done (planning session)

- Docker cleanup:
  - `docker builder prune` reclaimed 5.8 GB.
  - Three anonymous volumes removed.
  - The two-day-old `dev_server_alt` preview of the `alloc-sunburst` worktree on :5051 was
    stopped.
  - Kept on purpose: the named volumes `project-samuel_postgres-data` and
    `hpc-sched-openpbs_pbs-home`, which are unused but hold data. `docker volume prune`
    lists them as dangling, so remove volumes by name, never by prune.
- Nothing else is built.

## Plan

**Setup:**
```bash
git switch -c filters-sweep origin/staging      # in devel/; the untracked handoff docs carry over
docker compose up -d --build samuel-dev samuel
docker exec samuel-cache redis-cli -n 0 FLUSHDB
```

### Progress

- [x] **1. `FacetSet`: one in-memory facet engine.** Fixes a bug, so behavior changes.
  - Sites:
    - `admin/account_requests_routes.py:88-108`: `_apply(skip=)` / `_facet`. This is the
      clean shape; start here.
    - `xras/card_routes.py:363-460`: the accounts card.
    - `xras/_shared.py:350` (`_activity_facets`), `:504` (`_request_facets`), `:520`
      (`_account_facets`).
    - `xras/remediation.py:580-710`: `_selected_facets`, `_apply`, and the five `_facet`
      helpers, with 6 exclusion calls wired by hand at :168-203.
  - Shape: new `webapp/utils/facets.py`.
    - `Facet(name, key, order=None, labels=None, hide_zero=False, multi=True)`.
    - `FacetSet(facets).read(args)` → `selected`.
    - `.apply(rows, selected, skip=None)`.
    - `.strip(rows, selected, dim)` → `[{'value', 'label', 'count'}]`.
  - `hide_zero` keeps the two policies that exist today:
    - account requests hides zero-count values unless they are selected;
    - the XRAS cards and `build_facet_strip` show every vocabulary value, even at zero.
  - **The bug:** in `card_routes.py:407-418`:
    - the `origin` selection is never applied to the role or remedy counts;
    - `request_number` is applied to no dimension's counts;
    - `_request_facets` is scoped by remedy only.

    Write a regression test first. It should select `origin` plus a `request_number` and
    assert that every strip's counts match the rows they scope.
  - Unit tests: self-exclusion, order, `hide_zero`, values outside the vocabulary
    appended, and multi-value OR.
  - Templates may keep their context keys. Moving to one `facets` dict is fine if it
    removes code.
- [x] **2. `read_multi(args, name)`** in `utils/htmx.py`. `FacetSet.read` uses it.
  - Replace the getlist comprehensions in `admin/notifications_routes.py:110`,
    `admin/tasks_routes.py:88`, `xras/card_routes.py`, `xras/remediation.py`,
    `xras/_shared.py:209-210`, and `allocations/blueprint.py:1199`.
  - Mechanical; no output change.
- [x] **3. `facet_form` macro, plus one generic gate.**
  - Today:
    - `allocations/xras.html:99-133` has 3 hand-written forms with 14 hidden selects, and
      its own comment says a missing line silently kills the chips. Also hand-written:
      `admin/notifications.html:152-160` and `admin/scheduled_tasks.html:125-133`.
    - `admin/account_requests.html:33` loops over its dimensions, which is the right
      pattern.
  - Add `facet_form(form_id, fields, sort=None, hx_get=, hx_target=, ...)` to
    `fragments/facet_chips.html`. It emits the hidden form plus `sort_by` / `sort_dir`.
  - Add a `facet_grid_row(label)` call-macro for the "View" switch and "Search" rows. They
    are written out by hand in `account_requests_card.html:69-97`,
    `xras_activity_card.html:93-109`, and `xras_remediations_card.html:259-300`.
  - New gate in `tests/unit/gates/`. It renders every chip card and asserts that each
    `data-field` resolves to a control inside its `data-form-id` form.
    - It replaces `test_xras_accounts_card.py:491` and `test_xras_remediations.py:751`.
    - The second already drifted: its hand-written list leaves out `blockers`.
- [x] **3b. Multi-select chips.** Changes visible behavior on about 10 surfaces.
  - `static/js/actions.js:158` `set-filter-submit`:
    - When the target is a `<select multiple>`, toggle that option's `selected`. Today it
      does `field.value = value` at :173, which selects one option and clears the rest.
    - Single-value targets keep the replace behavior: text inputs, single selects, and the
      jobs explorer's plugin-backed fields (`_jobs_facet_chips.html`).
  - `facet_row` (`facet_chips.html:44`):
    - Always emit the chip's own `data-value`, plus `aria-pressed`.
    - Today an active chip emits an empty value, which clears the dimension. A toggle now
      does that job.
  - `facet_form`:
    - Emits `<select multiple hidden>` for each multi dimension, and a hidden input for a
      `Facet(multi=False)` dimension.
    - Last seen (`users_last_seen.html:45-48`) and mnemonic codes
      (`mnemonic_codes_table_htmx.html:12-39`) move off their hidden `<input>`s. Their
      routes (`last_seen_routes.py:47`, `orgs_routes.py:432`) read through `read_multi` /
      `FacetSet`.
  - Notifications and tasks: `querykit` `owned_filter` already takes lists. Confirm this
    in `src/querykit/faceted.py:89`.
  - The XRAS action log's visible Status / Action type multiselects (`xras_filters.html`)
    and its chips (`xras_table.html:53-55`) write the same fields. They now agree instead
    of a chip collapsing a multi-selection.
  - Tests:
    - a render test: two values selected → both chips `is-active` and rows are the union;
    - an e2e click test on one chip page: select two, deselect one.
- [x] **4. One shared `sort_rows`.**
  - Move `xras/_shared.py:313` `sort_rows` (None sorts last) to `webapp/utils`.
  - Delete `account_requests_routes.py:121` `_sort_views`, which is identical.
  - Fold in `orgs_routes.py:385` `_sort_inventory`. orgs then adopts `read_sort`
    (`orgs_routes.py:432-439`), which passes `sort_by` to the template without a whitelist
    check today.
- [ ] **5. XRAS action log becomes querykit's third caller.** (Partly done: the strips use
  `build_facet_strip`. The `LogSpec` retrofit is deferred; see ledger entry 10.)
  - `xras/card_routes.py:169-206` makes two hand-written `summarize_xras_actions` calls
    and builds the strip by hand. The `action_type_facets` block at :202 is exactly
    `build_facet_strip(counts)`.
  - Add a `LogSpec` for it; `src/querykit/README.md` already names it.
  - Run a parity capture of the counts before and after, on MySQL **and** Postgres. Keep
    the script untracked under `utils/profiling/`.
  - Add a perf-tier pin if the query count moves.
- [x] **6. One look for in-card filters.** A deliberate visual change.
  - **Clear all:** `facet_clear_all(form_id)`, rendered when any facet is active; it
    blanks every facet field. The XRAS empty states (`xras_activity_card.html:376-380`,
    `xras_remediations_card.html:341`) tell the user to "clear any filters" but have no
    button; link them to it.
  - **Search:** `filter_search(name, form_id=, ...)`, one idiom.
    - Trigger: `input changed delay:300ms, search`.
    - Label: visually hidden, with an icon.
    - The name stays per surface.
    - Callers: last seen, mnemonic (`keyup`), account requests, remediations
      (`xras_remediations_card.html:289-299`), notifications and tasks (submit-only
      today), rate limits (`rate_limits.html:90-103`, `keyup` 500 ms, `name=actor`).
  - **Active switch:** `active_switch(...)` for the 8 card-header switches:
    - `admin/facilities.html:11`, `events.html:17`, `organizations.html:11`,
      `resources.html:11`, `contracts.html:152`;
    - `admin/fragments/project_directories_card.html:28`,
      `project_access_grid_htmx.html:30`, `roles_grants_card.html:13`.

    Keep the read side on `read_active_only` (CLAUDE.md §10).
  - Before/after screenshots in all six states, reviewed by eye. Measure contrast in the
    page.
- [x] **7. One pill markup.** Must look identical.
  - The selected pill is `btn-outline-secondary active` in `window_pills.html`,
    `jobs_card.html:297`, `metric_pills`, and the disk pills.
  - It is `btn-secondary active` in `time_range_picker.html`, `pickers.js` (drp
    `markActive`), `status/partials/user_proj_chart.html`,
    `system_user_proj_chart_card.html:31`, `used_sunburst.html:14`, and
    `chart_expanded.html:8`.
  - `dashboard.css:820-842` normalizes both. Pick one markup form.
  - Proof: run `ui_snapshots.py --styles` on both sides, then `--compare` must report 0
    differing elements.
- [x] **8. The two hand-built navy panels move onto `filter_panel_shell`.** Must look
  identical.
  - `allocations/projects.html:13-67` (plain GET): use `action=` / `clear_url=`. Its form
    currently sits inside the collapse rather than wrapping the header; check the
    `:has(+ .collapse)` CSS still matches.
  - `admin/projects.html:73-127` (expirations): replace `expirations-clear`
    (`dashboard-init.js:138`) with `form-reset-submit`, or keep the JS reload if the export
    needs it.
  - Fix the stale reasoning at `xras_filters.html:19-23`.
  - Same `--compare` proof as item 7.
- [x] **9. One date-window parser.** Fixes a bug, so behavior changes.
  - Add `parse_date_window(args, default_days, upper_bound=True)` in `webapp/utils`.
  - Sites:
    - the user dashboard's 90-day parse, copied 4× (`user/blueprint.py:489, 711, 895,
      1042`);
    - the days clamp, copied 2× (`faceted_log.py:39`, `xras/_shared.py:286`);
    - audit `_parse_audit_filters` (`allocations/blueprint.py:1182`) vs XRAS
      `_parse_xras_filters` (`xras/_shared.py:191`).
  - The XRAS parser's own comment names the audit parser's sub-second upper-bound bug.
    Unify on the correct core and extend `test_audit_window_filters.py`.
- [x] **10. Use the helpers where routes bypass them.**
  - `read_flag`: `jobs/routes.py:596, 1524` (`in ('1','true','on')`), `orgs_routes.py:209`,
    `disk_scans/routes.py:121` (`_atime_recursive_flag`), and `force_refresh` at
    `allocations/blueprint.py:528, 1031`.
  - `read_page` / `read_sort`: `last_seen_routes.py:50-87` and `disk_scans/routes.py:151`.
  - Add direct unit tests for `read_page`, `read_sort`, `read_flag`, `build_facet_strip`
    and `parse_window`; none exist today.

## Verification

- Full `pytest` on MySQL, then the Postgres run, then `pytest -m perf -n 0` if query
  counts move (item 5).
- Gates: route-map parity, CSS tokens, CSS dead, template CSP lint, static assets, docs.
- Browser: serve the before side with `scripts/dev_server_alt.sh <staging worktree> 5051`
  and compare against samuel-dev :5050 (the branch).
  - Run `ui_snapshots.py` over XRAS (all panes), Admin → Accounts / Notifications /
    Scheduled tasks / Last seen / Mnemonic codes, the Jobs explorer, Allocations projects,
    and expirations.
  - Cover 3 layouts × 2 themes.
- Hand smoke checks:
  - On the XRAS accounts card, select one origin. The role and remedy counts should drop
    to match the rows.
  - On Notifications, the XRAS activity card, and Admin → Accounts:
    1. select two chips in one dimension: rows are the union and both chips are active;
    2. deselect one;
    3. Clear all.

## Close-out

- Ledger entry 10 in `docs/plans/UNPLANNED_CITY_LEDGER.md`, with:
  - mode and area: `templates` + `py`, filters;
  - the end commit;
  - what was done, with the PR;
  - anything tried and dropped, with numbers;
  - a metrics row from `scripts/sweep_inventory.py`.
- Move the ledger-only row above onto the entry's open list.
- Fix these stale comments:
  - the `filters.css` header, which still lists 3 chip callers;
  - `facet_chips.html`, which still points at `blueprint.py::xras_fragment` (it now lives
    in `card_routes.py`);
  - `filter_panel.html:18`, which still says "five panels".
- Add the two-tier rule to the `filters.css` header and `wire-dashboard-feature` §1.
- Growth rule: if a new class of drift turned up, add its heuristic to the sweep skill or a
  detector to `scripts/sweep_inventory.py`.
