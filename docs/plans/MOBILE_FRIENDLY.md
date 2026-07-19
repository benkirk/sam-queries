# MOBILE_FRIENDLY — low-hanging mobile polish (handoff)

**Status:** planned, not started. Written 2026-07-19 at the end of the
`nav_polish` session, while the mobile survey was fresh.

**Stacking:** branch off `nav_polish` (or `staging` once PRs #358 + #359 have
merged). #358 made every top-level tab a routable page; #359 added navbar
dropdowns, the mobile offcanvas drawer, breadcrumbs, and the first round of
mobile fixes. This PR is round two: **low-hanging fruit only** — better, not
mobile perfection.

**Explicit non-goals (owner's call):**
- No chart refactors. SVG/matplotlib charts stay exactly as they are.
- No redesigns of data-heavy views; wide tables may simply scroll.

---

## What #359 already fixed (don't redo)

- Brand row fits a phone (compact logo / site-name / hamburger below `md`).
- `.btn` CTA scale + `h1` scale down below `md`; page-header flex rows and
  `.btn-group` preset strips wrap (`dashboard.css` "MOBILE COMPACTION" block).
- Allocations *summary* tables wrapped in `.table-responsive`
  (`allocations/projects.html`).
- Mobile nav = offcanvas drawer (`fragments/mobile_nav.html`); breadcrumbs
  everywhere; `.back-link` machinery gone.

---

## Measurement recipe (IMPORTANT — learned the hard way)

- Playwright at 390×844 (iPhone), 820×1180 (iPad), 1280 desktop. Login via
  Quick Login buttons (benkirk / sureshm / bdobbins).
- The metric: `document.documentElement.scrollWidth == clientWidth` at 390px.
- **Measure AFTER htmx fragments settle** — most tables arrive via
  `hx-trigger="load"` / `intersect`. A page can measure clean at load and
  blow out to 1300px once its fragment lands (this is exactly how the
  transactions table was missed at first).
- `/allocations/projects` is Redis-cached per-user — template changes won't
  show until cache expiry; append a junk query param (`?cachebust=1`) to get
  a fresh render (cache key includes the query string).
- Overflow-root finder (paste into browser_evaluate): walk `body *`, report
  elements whose right edge exceeds `clientWidth` while their parent's does
  not, skipping `position:fixed` and `.offcanvas`.

---

## Work items, in priority order

### 1. Unwrapped wide tables (HIGH — the main remaining overflow source)

`/allocations/transactions` at 390px: fragment table is **1304px wide with no
scrollable ancestor** → document scrolls to 1316px and the filter panel looks
half-width. Same class of bug lurks in every unwrapped fragment.

Verified inventory (grep: templates with `<table` and zero
`table-responsive|overflow-x`), heaviest first:

- `dashboards/allocations/partials/transactions_table.html`
- `dashboards/allocations/partials/adjustments_table.html`
- `dashboards/allocations/partials/project_table.html` (drill-down)
- `dashboards/status/partials/node_status.html` (2 tables)
- `dashboards/status/partials/login_nodes_table.html`
- `dashboards/shared/project_tree.html` (2)
- `dashboards/user/partials/user_subtree.html`, `day_subtree.html` (2 each)
- `dashboards/admin/fragments/project_allocation_tree_htmx.html`,
  `project_linked_elements_htmx.html` (3), `extend_allocations_form_htmx.html`,
  `renew_allocations_form_htmx.html`, `rate_limits_blocks.html`,
  `rate_limits_offenders.html`

Two implementation options — pick ONE:
- **(a) Global CSS rule** (one-liner, covers everything incl. future tables):
  ```css
  @media (max-width: 767.98px) {
      .main-content table.table { display: block; overflow-x: auto; }
  }
  ```
  Tradeoff: `display:block` tables no longer stretch to 100% width when
  narrower than the screen (cosmetic). Test bordered/hover styles + the
  expandable-row tables (allocations summary, project cards) carefully —
  `data-action` row toggles must keep working.
- **(b) Targeted `.table-responsive` wrappers** in the ~14 templates above
  (mechanical, zero side effects, more churn). The #359 commit
  `Mobile: eliminate document-level horizontal overflow` shows the pattern.

Either way, re-run the measurement recipe on: transactions, adjustments,
projects drill-down (expand a facility → type → project table),
status derecho/casper (compute-node tables), admin rate_limits, an admin
project card with allocation tree.

### 2. Modals on phones (MEDIUM, one class per modal)

Bootstrap's `modal-fullscreen-sm-down` on the `modal-dialog` div makes big
modals fullscreen below `sm`. Candidates (all `modal-lg`):
- `shared/project_details_modal.html`, `user/fragments/user_details_modal.html`
- `allocations/partials/audit_details_modal.html` (in
  `dashboards/allocations/`), usage modal in `allocations/projects.html`,
  create-adjustment modal in `allocations/adjustments.html`
- member/allocation modals (`project_members/fragments/member_modals_htmx.html`,
  `user/fragments/allocation_modals.html`), admin modal fragments
  (`project_modals`, `resources_modals`, `facility_modals`,
  `organization_modals`, `exemption_modals`, `project_directory_modals`)
Tables *inside* modals are covered by item 1's rule if option (a) is chosen.

### 3. Filter forms stack on phones (MEDIUM)

`.filter-sidebar` forms use inline fixed widths (`style="width: 160px"` etc.)
via `audit_filters.html` macro + the allocations projects filter + admin
expirations filter. They wrap acceptably but look ragged at 390px.
Cheap CSS-only fix — below `sm`, stack and stretch:
```css
@media (max-width: 575.98px) {
    .filter-sidebar form > div { width: 100%; }
    .filter-sidebar input.form-control,
    .filter-sidebar select.form-control { width: 100% !important; }
}
```
(`!important` needed to beat the inline styles; alternatively strip the
inline widths into classes while in there.)

### 4. Card headers with action buttons (LOW, cosmetic)

"Search Projects" + "Create Project" (admin/projects.html) and similar
`card-header d-flex` rows squeeze at 390px (uppercase h5 wraps letter-ish).
Let card-header flex rows wrap below `md` (same pattern as the #359
page-header rule, scoped to `.card-header.d-flex`).

### 5. Tab-strip scroll affordance (LOW, nice-to-have)

`page_tabs` strips scroll horizontally by design but give no hint that more
tabs exist off-screen (e.g. "Filesystem Scans" hidden past "JupyterHub").
Cheap: right-edge fade via `mask-image: linear-gradient(...)` on `.nav-tabs`
below `md`, or a small scroll shadow. Pure CSS, no JS.

### 6. Tap targets (LOW)

- Expandable rows (`data-action="alloc-toggle-facility"` etc.) and sortable
  column-header links are finger-hostile. Below `md`: bump row padding
  (`.expandable-row td { padding-y: 0.625rem }`) and sort-link hit area.
- Table font could drop a notch on mobile (`.table { font-size: 0.8125rem }`)
  to trade less scrolling for readability — owner call, try it visually.

---

## Verification checklist (end of PR)

- Playwright at 390 / 820 / 1280 as benkirk + bdobbins:
  - `scrollWidth == clientWidth` on all four sections' pages **after
    fragments load and after expanding drill-downs**.
  - Open each converted modal at 390 — usable, dismissible.
  - Filter forms: fields full-width and stacked at 390, unchanged ≥ 768.
  - No regressions at desktop width (esp. table layouts and expandable rows).
- `pytest` (~70s). Note: unit env has no warmed fs-scan collections and CI
  for a PR based on a non-staging branch may only run GitGuardian — run the
  suite locally.
- Flush Redis (or cachebust) when eyeballing `/allocations/projects` changes.
