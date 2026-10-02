# Admin table polish — Resources, Organizations, Contracts, Facilities

**Status: implemented, awaiting review** · branch `admin-table-polish` (from `origin/staging`)

## Why

The newer tables (Admin → Accounts, Events, the XRAS cards) read tight: narrow
columns pinned to their content, glyph cells with tooltips, monospace codes,
ellipsis truncation, status badges, group header rows with count badges. The
older admin CRUD tables at `/admin/{resources,organizations,contracts,facilities}`
do not, and the gap is structural, not cosmetic:

- 54×38 px icon buttons (`.btn.btn-sm` = 0.45rem/0.9rem padding, 3 px border,
  `dashboard.css:796`) set every row to ~56 px. Measured on `/admin/organizations`.
- Inactive rows are `opacity-50` and nothing else — no word says *why* a row is
  dim. Contracts ignores the `contract_status_badge` macro it already has.
- Description / Comment columns never truncate; counts are left-aligned in some
  tables and right-aligned in others; group rows overload columns (Queues puts
  the resource name under "Queue Name" and a count under "Queues").
- Create buttons are full-size `btn-primary`s in each tab's footer, below the table.
- Inline styles (chevron transitions, depth padding, nested font sizes).

The reference tables share one latent issue: `.btn-group .btn-outline-secondary`
(`dashboard.css:828`) is the *toggle-bar* idiom, so a neutral row action inside a
`btn-group` renders solid NCAR blue (the Dismiss button on Account Requests).
Their strips are also 36 px tall, so those rows are 53 px too.

## Design vocabulary (one place: `components.css` § Data tables)

Bound by the existing brand (NCAR Unity: Poppins, NCAR blue, square corners,
uppercase headers) — this is discipline, not a new look. One memorable move:
**actions recede until wanted** — a compact, borderless icon strip whose glyphs
sit at secondary text color and take their semantic color on row hover/focus.

| class | role |
|---|---|
| `.col-shrink` | `width:1%; white-space:nowrap` — the column takes its content's width |
| `.col-num` | `.col-shrink` + right-aligned + `tabular-nums` |
| `.col-chevron` | 1.5rem expand column |
| `.cell-truncate` | `max-width:0` + ellipsis; pair with `title=` for the full text |
| `.btn-row` | 1.75rem icon button, 1 px transparent border, no uppercase/letter-spacing; `.btn-row-danger` tints red on hover |
| `.row-actions` | `d-inline-flex gap` strip, never wraps |

The fill column is whatever is left: every other column is `col-shrink`, so a
`cell-truncate` column takes the slack with no `width:99%` hacks.

## Progress

- [x] 0. Seed the local dev DB (scratch script, not committed): 3 events, 7 account
      requests, enrollments, so the reference tables have rows to measure.
- [x] 1. CSS vocabulary + `action_buttons` macros emit `.btn-row` + `aria-label`.
- [x] 2–5. Resources, Organizations (doubled heading dropped), Contracts (status
      badge for expired, loading placeholder), Facilities. Rows 56 px -> 45 px.
- [x] 6. Bugs: the sub-tab survives a save (`_reloadAdminCard` sends `tab`);
      grouped deletes reload the card (`reload_event`); org empty-state colspan.
      Found on the way: with `tab` in the reload URL the post-save request hits
      the page-load cache key exactly, so the orgs card and contracts table pass
      `forced_update=fresh_requested` and the reload sends `X-SAM-Fresh`.
- [x] 7. Tooling: `tests/unit/webapp/test_admin_table_conventions.py` (rendered,
      mutation-checked), `e2e/test_admin_table_density.py` (48 px budget, strip on
      one line, tab survives a save), `scripts/ui_snapshots.py`, a `/dev/gallery`
      specimen.
- [x] 8. The reference cards' strips (Accounts, Events, Invitations, XRAS
      Activations and Pending Users) moved to `.btn-row`; meaning kept as color at
      rest (`-primary`, `-attention`, `-success`, `-danger`).
- [x] Added in review: `.badge.bg-secondary` joins the muted badge palette, so
      count badges stop being the loudest thing in a row app-wide (78 uses), and
      admin.css's larger `.badge` override is gone.
- [x] 9. Skills: `wire-dashboard-feature` §7 is the vocabulary; `update-vendored-assets`
      points at the snapshot script.

## Out of scope (noted, not fixed)

- Panel Sessions have an edit route and form but no listing or modal shell.
- No server-side search/pagination on these cards (Contracts renders ~2,200 rows
  with Active only off, inside collapsed groups).
- Stale "four tabs" docstrings in `facilities_routes.py:59`, `resources_routes.py:69`.
- The institutions fragment is cached too, and a post-save card reload lazy-loads
  it without `X-SAM-Fresh`: an institution edit can still show stale for 300 s.

## Verification

- Structural gates (`pytest -m gate`) + the admin CRUD suites
  (`tests/unit/webapp/test_admin_*`, `test_htmx_*_admin.py`).
- Browser pass on a second dev server from this worktree (port 5051), 3 layouts ×
  2 themes, measured with `getBoundingClientRect()`; `make e2e`.
