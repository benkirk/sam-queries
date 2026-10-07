# CSS area sweep: handoff

**Status: done, draft PR #723 against staging (rebased after #722 merged), 2026-10-04.**
Branch `sweep-css-2026-10`, off `sweep-js-2026-10` and stacked on #722 as a draft PR with
`--base sweep-js-2026-10`. If #722 has merged by then, branch from `origin/staging` instead.

Third run of `.claude/skills/unplanned-city-sweep/` (area mode, `css`), after the js sweep (#722,
ledger entry 2). Load that skill plus `wire-dashboard-feature` (§10 tokens, §12 smoke and gates).
`frontend-design` only sets the bar: **nothing in this sweep should look different.** Every finding
below was read and grepped in the planning session. Re-verify each `file:line` before editing,
since #722 commits may shift lines.

## Inventory, at `sweep-js-2026-10` (`d94246e6`)

`scripts/sweep_inventory.py --area css --top 60 --jscpd`:

- 9 files, 4,817 lines, 70 `!important`. `dashboard.css` alone has 2,489 lines, 314 rules and
  60 `!important`.
- `css-dead`: 38 classes, 17 with a dynamic stem.
- `css-shape`: 14 repeated declaration blocks.
- jscpd: 18 clones, 3.24% of lines. The console output was cut short; see F1.

How the leads read:

- **css-dead.** 21 classes have no dynamic stem. 19 are truly dead: only their own CSS file
  names them, and nothing in templates, JS or Python does. `.col-chevron` and `.resource-selector`
  were tagged "dynamic?" wrongly. `col-{{ loop.index }}` in `db_browser/table.html:59` builds an
  `id`, not a class (F3). The 17 real dynamic stems (`burn-N`, `cal-past/-future`, `severity-*`,
  `status-*`, `db-binary`) are live.
- **Kept on purpose:** `.table-danger` / `.table-success` dark-mode rules, `dashboard.css:1068-1090`.
  Nothing uses them today. They guard Bootstrap's contextual table variants so the first use
  renders correctly in dark mode (F4 records the reason).
- **jscpd.** These clones are dropped:
  - Six hits are the `/* ==== */` section banners in `dashboard.css` (`:319` paired with `:512`,
    `:530`, `:1577`, `:1814` and `:1945`). They are false positives (F2).
  - The `variables.css:275-283` / `:395-403` pair is the dark token block, which has to appear
    under both `@media (prefers-color-scheme: dark)` and `[data-bs-theme=dark]`. Required
    duplication.

  The real clones are findings 2 and 3.
- **`!important`.** Most sit on rules that must beat Bootstrap's own `!important` utilities, and
  they are needed:
  - the muted badge palette, `dashboard.css:2408+`
  - the text-color utilities, `:2254-2268`
  - the tab counter badges, `:1350-1390`

  Ten sit on dead classes and leave with finding 1.

## Findings, ranked

### 1. Delete 19 dead classes (removes 10 `!important`)

| file | classes |
|---|---|
| `dashboard.css` | the UTILITY CLASSES block `.text-ncar-navy`, `.text-ncar-blue`, `.text-muted-custom`, `.border-ncar-blue`, `.bg-ncar-light` (`:2231-2252`, all `!important`); `.card-bordered` (`:649-655`, an opt-in modifier nobody opted into); `.spinner-border-primary` (`:949`); `.navbar-brand` (`:2280`, inside a media block) |
| `components.css` | `.stat-box` (`:30`), `.table-col-expand` (`:96`), `.table-col-actions` (`:104`), `.dropdown-list` and `.dropdown-list.active` (`:128-134`), `.section-spacing` (`:172`), `.table .col-chevron` (`:393`) |
| `admin.css` | `.logout-link` (`:65`) |
| `allocations.css` | `.date-filter-form` (`:71`), `.resource-selector` and `.resource-selector .form-check` (`:75-79`) |
| `status.css` | `.border-status-success/-info/-primary/-warning` (`:120-123`, all `!important`) |

Also:
- Fix the comment at `auth.css:32`, which says "matching .card-bordered".
- Drop any section banner left empty.
- `.table-col-usage` (`components.css:100`) is live; keep it.

Re-check with `command grep -rn '<class>' src` before deleting. In this shell `grep` is ugrep,
which ignores `--include`.

### 2. One `.sortable-header` rule

The same four rules (base, `::after`, `.sort-asc::after`, `.sort-desc::after`) exist three times:

- `admin.css:35-62`
- `allocations.css:41-68`
- `components.css:263-270`, which `dashboards/base.html:25` loads on every dashboard page

`admin.css` (`base_admin.html:16`) and `allocations.css` (`base_allocations.html:12`) load
after it with identical values. Delete those two copies with their banners, and the
"Mirrors the rule duplicated in admin.css / allocations.css" line in `components.css:265`.

### 3. One page watermark

`dashboard.css:13-22` (`body`) sets `background-color`, the `UCAR-Waves-Lines-Only-66.png`
watermark (position, repeat, size), `min-height`, `font-family` and `color`.

- `auth.css:14-29` (`body`) and `register.css:11-17` (`.register-page`, the `<body>` class
  from `base_register.html:22`) repeat all of those.
- Both pages load `dashboard.css` first: `auth/login.html:16-17` and
  `register/base_register.html:19-20`.
- Keep only each page's own layout. In `auth.css` that is `display:flex` and the centering. In
  `register.css` it is `margin`, `padding` and `display`.
- Reword the `auth.css` comment about matching the dashboard watermark: it now inherits it.

### 4. Move the filter-UI family to `css/filters.css`

The block is `dashboard.css:1439-1949`, about 510 lines: FACET CHIPS (`:1439`), LADDER RANGE
(`:1582`) and FILTER SIDEBAR (`:1819`). These are shared components, not dashboard chrome.

- **Link** `filters.css` in `dashboards/base.html` immediately after `dashboard.css` and before
  `components.css`, so the cascade order is unchanged.
- **Cascade check, done in planning:** after line 1949, the only filter-UI reference in
  `dashboard.css` is a comment (around `:2468`). Nothing in `components.css`, `admin.css`,
  `allocations.css` or `status.css` targets `facet`, `ladder` or `filter-sidebar`.
- **Who uses it:** every filter-UI user renders under `dashboards/base.html`, including the DB
  browser (`db_browser/base.html` extends it). The login and register pages load
  `dashboard.css` but use none of these classes.
- **Comments to fix:** `allocations.css:5` says the shared filter sidebar lives in
  `dashboard.css`, and the FILTER SIDEBAR banner says "Defined here (not allocations.css)".
- **Gates:** `test_static_assets` requires a `url_for` link, and `test_css_tokens` is a
  per-file ratchet, so add an entry for the new file if any rule in the block carries a
  literal color.

### 5. Ratchet: no new dead classes

Add `tests/unit/gates/test_css_dead.py`. It loads `scripts/sweep_inventory.py` the way
`test_sweep_inventory.py` does, and asserts that `css_dead()` over the real tree returns no
class that is neither dynamic nor in `CSS_KEEP` (F4). It is an equality ratchet at zero, so a
newly styled class nothing names fails.

**Dropped:**
- jscpd's banner clones.
- The light/dark token pair.
- The Bootstrap-fighting `!important`.
- Splitting PROJECT CARDS / PROJECT TREE (`dashboard.css:1950-2186`, about 240 lines): user
  dashboard specific but not crowding anything. Ledger open item.

## Skill friction, fixed first in the same PR

| # | friction seen this run | fix |
|---|---|---|
| F1 | `run_jscpd` prints only the last 40 console lines, which lost 8 of 18 clones. | Parse the output and print every pair as `fileA:lines <-> fileB:lines` plus the totals row. Uncapped; `--top` does not apply. |
| F2 | jscpd counts `/* ==== */` section banners as clones (6 false positives). | Try `--ignore-pattern` for banner lines. If jscpd does not honor it for CSS, the skill notes the false positive instead. |
| F3 | `css_dead` calls a class dynamic when its stem appears anywhere (`scripts/sweep_inventory.py`, `css_dead`, the `stem in seen` test). | A stem is dynamic only when followed by an interpolation (`{{`, Jinja `~`, JS `+`, `${`) and not inside an `id=` / `for=` / `name=` / `data-*=` value. Add a fixture case with `id="col-{{ i }}"`. |
| F4 | No way to mark a class kept on purpose. | `CSS_KEEP = {"table-danger": "dark-mode guard for a Bootstrap variant", ...}`. Kept classes are reported separately, and a keep entry no CSS styles any more is reported as stale. |
| F5 | No repeatable "no visual change" check. Every front-end sweep would write a throwaway probe. | `scripts/ui_snapshots.py --styles`: per page × layout × theme, also dump `getComputedStyle` for every element (path-keyed) as JSON. `--compare A B` prints differing elements and exits 1 on any difference. Move the `playwright` import inside `main()` so the pure compare helper is unit-testable without the `[e2e]` extra. The skill's front-end bullet names it. |
| F6 | No safe launcher for a second dev server. Hand-copying a running server's env (`ps eww`) printed `JIRA_TOKEN` into the #722 session transcript. | `scripts/dev_server_alt.sh <worktree> <port>`: source `etc/config_env.sh` (loads `.env`), map the local DB settings, force `NOTIFY_ENABLED=0 NOTIFY_TRANSPORT=null XRAS_API_KEY= XRAS_OUTGOING_ENABLED=0 JIRA_ENABLED=0 FS_SCANS_ENABLED=0`, set `MPLBACKEND=Agg`, its own `CACHE_REDIS_URL` DB index, `WEBAPP_PORT` and `PYTHONPATH=<worktree>/src`, then exec `src/webapp/run.py`. Never echo the environment. Point the sweep skill's front-end bullet and `wire-dashboard-feature` §12 at it. |

For F6, read `.env` key *names* only (`cut -d= -f1`) to find the right variables, never values.
The memory note `reference_second_dev_server_from_worktree.md` has the manual recipe it replaces.

## Verification

1. **Before:** `git worktree add <scratch>/css-base sweep-js-2026-10`, then
   `scripts/dev_server_alt.sh <scratch>/css-base 5053`.
2. **After:** `scripts/dev_server_alt.sh . 5052` on the branch.
3. `scripts/ui_snapshots.py --styles --base-url http://localhost:505x --out <scratch>/{before,after}`
   for these pages:
   - `/dev/gallery`, which renders the filter sidebar, facet chips and ladder range
   - `/auth/login`
   - the register page, if `ACCOUNT_REGISTRATION_ENABLED` is on locally
   - `/admin/resources?tab=machines` and `/admin/organizations`
   - a jobs-explorer or other filter-sidebar page
   - a `/database` table view

   Use all layouts and both themes.
4. `ui_snapshots.py --compare before after` must report **zero** differing elements. If it reports
   any, re-run after each commit to find the one that moved.
5. Gates:
   - `pytest tests/unit/gates/test_css_tokens.py tests/unit/gates/test_static_assets.py
     tests/unit/gates/test_template_csp_lint.py tests/unit/gates/test_docs.py
     tests/unit/gates/test_sweep_inventory.py tests/unit/gates/test_css_dead.py`
   - then all of `tests/unit/gates` and `tests/unit/webapp`
6. Stop both servers and `git worktree remove` the base.

## Progress

- [x] Branch `sweep-css-2026-10` (off `sweep-js-2026-10`, or off `origin/staging` if #722 merged)
- [x] Commit A: F3 + F4 + F1 + F2 in `scripts/sweep_inventory.py`, fixture tests
- [x] Commit B: F5 (`ui_snapshots.py --styles/--compare`) + F6 (`scripts/dev_server_alt.sh`); skill and `wire-dashboard-feature` point to them
- [x] Before capture from the base server
- [x] Commit 1: delete the 19 dead classes
- [x] Commit 2: one `.sortable-header`
- [x] Commit 3: one page watermark
- [x] Commit 4: `css/filters.css`
- [x] Commit 5: `test_css_dead.py` ratchet
- [x] After capture; `--compare` reports zero differences
- [x] Close-out: ledger entry 3 (area css, end commit = the `origin/staging` commit the branch started from), metrics row from a whole-tree run, clear the three css untriaged bullets, PROJECT CARDS/TREE as open, F1–F6 listed
- [x] Draft PR (`--base sweep-js-2026-10`); retarget to `staging` once #722 merges

Run notes: the dead set was 21 classes (the table above lists 21; "19" was a miscount). The
compare needed one fix found on the first two-server run: a resolved `url()` names its server's
origin, so `--compare` drops it. All 78 captures matched before and after.
