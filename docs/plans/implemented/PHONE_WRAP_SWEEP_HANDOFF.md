# Handoff: round D, the phone-width wrap sweep

**Status:** built 2026-10-06 on `phone-wrap-sweep`, off `origin/staging` after #741 (which carried
#742 and #743). Ledger entry 16 is the record. The plan below is kept as written, and the as-built
section says where the work departed from it.

## As built

- **D.1.** The rule went in as written. A census of its reach found about 30 cells:
  - 7 were already nowrap, and 20 hold an icon or a short identifier.
  - One cell needed an exception: the XRAS audit errors cell ("N errors" plus a 38ch excerpt). It
    puts its chevron and count in a `text-nowrap` span, which the rule does not match, so the
    excerpt still wraps.
  - The long event and opportunity group headers already wrap their chevron in a span, so they
    stay out of the rule.
- **D.2 departed from the plan: floats, not a nowrap flex row.**
  - A flex row of [content | chevron] reserves the chevron's column on every line, which made four
    of six `/user/` headers one line taller at 360px.
  - Instead the chevron comes first and `float-end`, as on Resource Details. On a phone the project
    card's title row becomes block flow, because a flex row is its own formatting context and would
    sit a column narrower beside the float. The title then flows around the chevron on its first
    line only.
  - Residual cost: a title that fits its line with under 20px to spare gives its last word to the
    chevron (SCSG0001 83 to 107px tall, SSSG0001 73 to 81). SVST0002 went 147 to 131, and
    User / Resource Access 112 to 88.
- **D.3.** The access grid uses `.accordion-chevron`, and its two `admin.css` rules are deleted.
  There were three bare `btn  btn-link` section chevrons, not one: Project Members, Project
  Hierarchy, and the modal's Project Hierarchy. All three became the new
  `collapse.section_toggle(target_id, label)`.
- **D.4 departed from the plan: the gate is browser-tier.**
  - `scripts/ui_snapshots.py` gained `--wraps`, a `wraps` page set and `--width`. `--width` was
    used in place of a new layout, because a layout name is also the `sam_layout` cookie value.
  - The gate is `e2e/test_phone_wraps.py`, not a unit fixture test. The unit tier has no browser,
    and the existing `--headers` JS had no test either.
  - Its fixture case injects a squeezed drill table into a real page, because CI renders no jobs or
    disk-scan rows. A control case with wrapping forced back on proves the detector sees a wrap.
- **The census is noisy.** Lazy tabs and drills load unevenly under fixed sleeps. SCSG0001 Derecho
  gave 9 hits at 360px and 82 at 390px on the same code. `check_wraps` forces everything open three
  times before it samples.

## Context

Ben (2026-10-06, during round C): "carets with project codes wrap lines". Round C fixed one case
(the histogram owner rows took `text-nowrap`) and logged the class of problem as the first open item
of ledger entry 15. This round fixes the class.

Two phone rules cause it:
- `dashboard.css` (the phone block, ~:472) sets `.main-content .d-flex:not(.flex-column) { flex-wrap: wrap }`
  and `.card-header.d-flex { flex-wrap: wrap }`.
- A table's one auto-width column (the label or name) is squeezed to whatever the `.col-num` /
  `.col-shrink` columns leave, 46–124px on a 360–390px phone.

## The census

Taken with `utils/profiling/wrap_sweep/wrap_sweep.py` (untracked). On every page it opens every
collapse and tab, then checks each chevron or caret against the text it belongs to. A wrap is either
the icon sitting on a line above its first word, or a trailing icon alone on the last line.

- **Pages:** 50 pages, at 390px and 360px, on the branch server.
- **Validation:** the detector found 43 wraps on staging's explorer, all of which round C fixed.
- **Raw results:** `census-2026-10-06-360.json` (all pages) and `-390-partial.json` (pages 34–50; the
  first run stalled and was restarted).
- **Clean pages:** every admin card, the allocations pages, the status machine pages, `/database`
  and the gallery have no wraps.

**Finding 1: a chevron sits above its label in a drill row.** 205 of 213 hits at 360px. Every one
is a `<td>` whose first child is a `.collapse-icon` or a `drill_toggle` button:

| surface | template | example | cell |
|---|---|---|---|
| By User / By Project panels (all four hosts) | `jobs/jobs_usage_panel.html` | `benkirk`, `CESM0023` | 113–135px wide, rows 72px |
| disk By user / By group | `disk_scans/disk_scans_entities.html` | `fredc`, `cmipap` | 78px, rows 61px |
| timeline band rows | `jobs/jobs_timeline.html` | `2026-07-09` | 68px, rows 76px |
| distribution band rows (Access, File sizes, My Data) | `disk_scans/disk_scans_distribution.html` | `10 KiB - 100 KiB` | 46px, rows 115px |

**Finding 2: a trailing chevron sits alone under a wrapped flex header.** 3 sites:

| site | template | measured |
|---|---|---|
| project card header (the `/user/` dashboard, admin Expirations) | `user/partials/project_card.html` ~:259 and ~:368 | SVST0002 147px tall, the chevron alone on a third line |
| "User / Resource Access" | `admin/edit_project.html` ~:219 | 112px tall; the chevron drops under the subtitle |
| "Project Hierarchy" toggle | `user/partials/project_card.html` ~:395 | the chevron button wraps past "Active only" |

Resource Details' nine card headers do not wrap: their chevron is `float-end` in a block header.

## Rounds of work (one commit each; aesthetic contract as before)

1. **D.1 Drill rows: one rule.** Add this to `components.css` next to `.cell-truncate`:
   ```css
   .table td:has(> .collapse-icon:first-child),
   .table td:has(> button[data-bs-toggle="collapse"]:first-child) { white-space: nowrap; }
   ```
   - Injected into the page, it took the four worst pages (CESM0002 Campaign_Store, SCSG0001
     Derecho, the CESM0002 explorer, `/user/data`) from 128 wraps to 0 at 360px.
   - The cost: those tables grow wider and scroll in their `table-responsive` wrappers. Measure the
     widest (By User on a long username, the distribution band label) before and after.
   - `:has()` is already in use (wire-dashboard §10).
   - Alternative: `text-nowrap` at each of the 6 template sites. Pick the rule unless a measured
     table grows wider than it should. Memory: simple rule over precise.
2. **D.2 Headers: the chevron is never the item that wraps.** In each of the three headers, move the
   chevron out of the wrapping group: the header is a nowrap row of [content (`flex: 1 1 auto;
   min-width: 0`, wraps inside itself)] + [chevron (`flex: none`, top-aligned)].
   - The project card is the main case. Its right-hand badge group (Lead / % used / Notified) may
     still wrap under the title, but the chevron stays top-right.
   - Check the admin Expirations card, which renders the same macro.
   - `float-end`, as Resource Details uses, is the other option where the header can stop being
     flex.
3. **D.3 One chevron vocabulary** (found during the census):
   - Three rotation idioms exist: `.collapse-icon` (rows, from `aria-expanded`), `.accordion-chevron`
     (headers, from `aria-expanded`) and `.access-grid-chevron` (`.collapsed` class, `admin.css`
     ~:59). Move the access grid onto `.accordion-chevron` and delete its two rules
     (`test_css_dead`).
   - The Project Hierarchy toggle is a `btn  btn-link` (double space) with a bare chevron. It never
     rotates and has no `aria-label`. It becomes `drill_toggle`, or the header idiom from D.2.
4. **D.4 Make the detector permanent** (the sweep skill's growth rule).
   - Promote the measurement in `wrap_sweep.py` (its `MEASURE` script) into
     `scripts/ui_snapshots.py --wraps`, beside `--headers`, with a fixture test in
     `tests/unit/gates/test_ui_snapshots.py`.
   - Keep the force-open step's rule: rows get `.show` only (opening them through Bootstrap fires
     every lazy drill; it stalled on status Job History's 600 drawers). Cards and tabs open through
     Bootstrap and clicks.
   - Optionally add a static ratchet. Its reach is limited: the rule in D.1 makes the cell markup
     safe by construction, so the gate only protects the headers.

## Proof

- Run `wrap_sweep.py` (or `ui_snapshots.py --wraps` after D.4) at 360 and 390px over the same 50
  pages: 0 hits.
- Six-state shots against staging for the touched surfaces.
- `getBoundingClientRect` for the project card header's height (SVST0002 147px today) and the widest
  table each rule widens.
- Gates: `test_collapse_trigger_rows`, `test_css_dead`, `test_css_tokens`, `test_static_assets`,
  `test_ui_snapshots`.
- The jobs and disk-scans tests do not pin these cells.

## Setup notes

- **Servers:** `ALT_FS_SCANS_ENABLED=1 scripts/dev_server_alt.sh <worktree> <port>`, with the
  branch on 5052 and staging on 5053.
- **Login limit:** the stub login allows 20 an hour per server. Save one session
  (`smoke.py`-style: log in once, `context.storage_state(path=...)`) and pass it to every run;
  `ui_snapshots.py --storage-state` takes the same file.
- **Run time:** the full census takes about 30 minutes per width, mostly the machine-wide status Job
  History and its tabs.
- **Not covered:** the census is phone-only. A tablet (768px) pass is cheap if wanted.
