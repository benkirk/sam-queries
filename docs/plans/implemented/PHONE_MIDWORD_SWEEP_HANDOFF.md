# Handoff: the mid-word break sweep (unplanned-city, area mode)

**Status:** built 2026-10-06 on `phone-midword`, off `origin/staging` after #745 (`5f27d3b7`).
Census taken 2026-10-06 on `phone-overflow` (#745's head); Ben picked findings 1-4 and the
container-query approach for finding 1. Ledger entry 17 is the record. The plan below is kept as
written, and the as-built section says where the work departed from it.

## As built

- **Round 1 departed from the plan: 24rem, not 30rem.** The user card (`user_card.html`, nine
  `.stat-item-inline` rows) sits in a `.col-lg-6` and measures 27rem of content at 1024px, so
  30rem would have stacked it on tablets. At 24rem a phone project card (17.5-19.4rem) and the
  nested Expirations card (13-15rem) still stack, and tablet and desktop are pixel-identical
  (`--element .project-stats-box --compare-pixels`: 0 of 10 differ). The 16rem rung is as written.
  - Containment: all 15 emit sites (10 files; `configuration_card.html` only names the class in
    a comment) sit in a `.col-*`, a `.modal-body` or a block div. Rendered at 360 and 1440px:
    `/user/`, `/user/info`, the Expirations card, the user, group and contract cards, the
    project-details, transaction and adjustment modals. No box was 0 wide. The XRAS action modal
    and the notify fragments have no local data; they share the measured modals' parent.
  - The skill line went into section 10; section 9 was reflowed to pay for it, so the file stays
    at 275.
- **Round 2 departed from the plan: the filter's home.** `fmt_*` filters are registered by
  `sam.fmt.register_jinja_filters`, which the notify sandbox also loads, so an HTML filter could
  not join them. `path_breaks` lives in `webapp/utils/template_filters.py` and is registered in
  `create_app()` right after them. The template is `dashboards/user/partials/project_card.html`.
- **Round 3** went in as written: 34rem and 31rem, numeric columns 87px and 99px at 360px.
- **Round 4** went in as written, with two findings of its own:
  - `.config-stats` is shared by the caching, server and rate-limits cards and the notification
    detail modal, so they stack on a phone too.
  - The census under-counted: `MIDWORD_JS` keyed one hit per (host, box signature, column), so a
    `<dl>` whose every `dd` split reported one. The promoted detector adds the word to the key.
    What that revealed on `/admin/configuration`: the Postgres URLs still split, because a URL
    has no break point and `word-break` is the only way to hold it on a phone. That is the one
    legal hit; the e2e gate does not visit that page.
- **Round 5** as written, plus the `SAMUEL_PRESENTATION.md` follow-up "Mobile table headers
  wrap mid-word" (found 2026-09-29) is ticked: #741's floors cleared Users, Jobs and the values,
  and round 3 cleared "Charges".

## Context

While fixing the project info grid in #745, a nested card's value column was squeezed to 69px and
words split mid-word ("Applicati / ons Lab"). Ben: "mid-word breaks columns may be more
widespread", then `/unplanned-city-sweep`.

Why it happens: Bootstrap's `.card` sets `word-wrap: break-word`. Inside any card, a column
narrower than its longest word splits the word instead of overflowing. So every mid-word break
marks a column too narrow for its content. The fix belongs to the layout, not to the text.

## The census

- **Script:** `utils/profiling/midword_sweep/midword_sweep.py` (untracked):
  `python midword_sweep.py <port> <width> <out.json> <storage-state.json>`.
  - It reuses `scripts/ui_snapshots.py`'s `WRAP_FORCE_OPEN_JS` / `WRAP_TABS_JS` /
    `WRAP_CLICK_TAB_JS` and its `wraps` page set (48 pages; both land with #745).
  - `MIDWORD_JS` flags any letter/digit run (`[A-Za-z0-9]{2,}`) whose client rects sit on two lines.
    It reports the cell or box, the table column, the computed `overflow-wrap` / `word-break`, and
    the width.
- **Raw results:** `utils/profiling/midword_sweep/midword-360.json` (360px, every collapse and tab
  open). No page errored.

| # | hits | component | width | cause |
|---|---|---|---|---|
| 1 | 38 | project info grid (`.project-stats-box`), single-line values: Lead "Muknahallipatna", Area "Magnetospheric" on `/admin/projects` → Project Expirations | 118px | the card is nested (card in card), so the grid is 232px (content 208): a 70px label track leaves 118px values |
| 2 | 36 | the same grid, Directories row (`/gpfs/csfs1/univ/ucir0064`), 4 pages incl. `/user/` | 204px | a path has no break point at `/`, so it splits inside its last segment |
| 3 | 6 | Resource Details usage tables, "Charges" header: History (`usage-table-5`) and By User (`usage-table-4`), 3 pages | 82-83px | `table-layout: fixed` with `min-width` 32rem / 26rem. "Charges" needs 85px with its help-term and 96px with the sort arrow |
| 4 | 2 | `/admin/configuration` (`.config-stats` dd, e.g. `DevelopmentConfig`) | 120px | `grid-template-columns: max-content 1fr` plus `word-break: break-word`; the long dt labels squeeze the dd |
| 5 | 2 | `/dev/gallery` specimen title `date_range_picker.date_range_picker` | 336px | a dotted identifier in a card header; dev-only |

**Clean:** every admin card, the allocations pages, the status pages, the job explorer,
`/database`. A top-level `/user/` card is fine: its grid is 304px (content 280), so values get 190px.
Only nested cards squeeze.

**Measured header needs** at 360px (unwrapped width, then the column's width):
- History: Users 62/82, Jobs 54/82, Core-Hours 114/82 (splits at its hyphen, a legal break),
  Charges 85/82.
- By User: Jobs 64/83, Core-Hours 123/83, Charges 96/83.

## Rounds of work (one commit each)

1. **Project info grid keys off its own width (container query).** In `dashboard.css`, beside the
   `.stat-item-inline` rules (~:1544):
   - `.project-stats-box { container: stats / inline-size; }`
   - `@container stats (max-width: 30rem)`: `.stat-item-inline > .stat-label/.stat-value` take
     `grid-column: 1 / -1`. This **replaces** #745's `@media (max-width: 575.98px)` rule for the
     same selectors.
   - `@container stats (max-width: 16rem)`: also `.stat-item > .stat-label/.stat-value`, so every
     row stacks label over value.
   - Expected result: the nested Expirations card (content 208px) stacks everything. A top-level
     `/user/` card at 360px (280px) stacks only Organizations / Contracts / Directories, as #745
     does. Tablet and desktop grids are wider than 30rem, so nothing changes there.
   - **Containment check first.** `inline-size` containment means the box can no longer size itself
     from its content.
     - Render every `.project-stats-box` caller (`grep -rl project-stats-box
       src/webapp/templates`, 11 files). None may sit in a shrink-to-fit parent (a table cell, an
       auto-width flex item), where it would collapse to 0.
     - Include the modals (`stats-two-col`: transaction, adjustment, XRAS action) at desktop and at
       phone.
     - If one collapses, put the container on a wrapper or opt that caller out.
   - This is the tree's first container query. Say so in the commit, and add one line to the
     wire-dashboard skill's §10 (CSS); the skill is at its 275-line budget, so trade a line.
   - Proof that desktop and tablet are unchanged: `ui_snapshots.py --element .project-stats-box` on
     base and branch, then `--compare-pixels`.
2. **Directory paths break at slashes.**
   - A Jinja filter `path_breaks` returns `Markup`: escape the path, then put `<wbr>` after each
     `/`. Escape first, so the path cannot inject markup.
   - Register it in `create_app()` beside the `fmt_*` filters. It is HTML, so it lives in the
     webapp, not `sam.fmt`.
   - Use it in `project_card.html`'s Directories row: `<code class="me-3">{{ dir | path_breaks
     }}</code>`, about :133 in `render_project_info`.
   - A unit test covers the `<wbr>` placement and that `<script>` comes out escaped.
   - Grep the other bare path renders and record them in the ledger as propagation candidates.
     Do not convert them.
3. **Usage-table floors** (`components.css` ~:206):
   - `.usage-table-5` min-width 32rem → 34rem (numeric columns 87px ≥ 85px).
   - `.usage-table-4` min-width 26rem → 31rem (99px ≥ 96px).
   - Both tables already scroll in their `table-responsive` on a phone.
   - Update the comment beside them ("Below these widths the fixed columns split header words")
     with the measured need.
4. **Config page, plus the detector.**
   - `.config-stats` (`components.css` ~:125): below 576px, `grid-template-columns: 1fr`, so dt
     sits over dd.
   - Promote `MIDWORD_JS` into `scripts/ui_snapshots.py` as `--midword`, beside `--wraps`. Factor
     `check_wraps`'s walk into one `open_everything(page)` (force-open ×3, then per tab ×2) that
     both checks share.
   - In `e2e/test_phone_wraps.py`, assert no mid-word hits on the pages already there, plus
     `/admin/projects` with its Expirations card opened (`[data-bs-target="#expirations-section"]`).
     CI's snapshot has projects.
   - A control case like the existing drill fixture: inject a 60px box holding a long word and
     expect one hit, so the detector is proven live.
5. **Ledger and docs.**
   - Ledger entry 17: area sweep "mid-word breaks", the end commit, the census table above. Open
     items: the gallery title (#5) and any path-render propagation candidates.
   - Add a "mid-word" heuristic line to `.claude/skills/unplanned-city-sweep/SKILL.md` (Legibility)
     per the growth rule.
   - Retire this handoff to `docs/plans/implemented/` with an as-built section.

## Proof

- `ui_snapshots.py --midword --pages wraps --layout mobile --width 360`, then again at 390: 0 hits
  except the gallery title.
- Before/after phone shots of each touched surface: the nested Expirations card, a `/user/` card,
  the Resource Details History and By User headers, `/admin/configuration`. Send them to Ben with
  SendUserFile.
- `getBoundingClientRect`: value widths in the nested card (118 → about 208), and table widths for
  round 3.
- Gates: `tests/unit/gates` (css_tokens, css_dead, docs), `test_ui_snapshots.py`, the filter's unit
  test, the full suite.
- `e2e/test_phone_wraps.py`: passes on the branch, fails on staging for the mid-word cases.

## Setup notes

- **Base and branch servers:** start them with
  `ALT_FS_SCANS_ENABLED=1 scripts/dev_server_alt.sh <worktree> <port>`.
  - Branch on 5052, from this worktree.
  - Base on 5054, from a scratch worktree at `origin/staging`
    (`git worktree add --detach <scratchpad>/before origin/staging`).
- **Stale-listener trap:** first check `lsof -ti tcp:<port> -sTCP:LISTEN` is empty. An old server
  holding the port makes `dev_server_alt.sh` exit with "Address already in use" while the old one
  keeps answering. Round D's first baseline came from a pre-#741 server this way. Confirm each
  listener's cwd with `lsof -a -p <pid> -d cwd`.
- **Playwright** is not in the conda env. Use `uv run -q --no-project --with playwright python ...`
  (or `--with pytest-playwright` for `e2e/`, with `SAM_E2E_STORAGE_STATE=<file>`). Chromium is
  cached.
- **Login limit:** the stub login allows 20 an hour per server. Save one session per port
  (`context.storage_state(path=...)`) and pass it everywhere (`--storage-state`).
- **CSS edits:** `touch src/webapp/utils/static_assets.py` to re-hash `?v=` on the running server.
- **Do not measure a fragment URL directly.** `/admin/expirations` and
  `/allocations/xras_remediations` load with no CSS; use their host pages (`/admin/projects`,
  `/allocations/xras`).
- **Smooth scrolling:** Bootstrap sets `scroll-behavior: smooth`, so pass `behavior: 'instant'` to
  `scrollIntoView` / `scrollTo` before a screenshot.
- **Run time:** the census takes about 25 minutes per width.
