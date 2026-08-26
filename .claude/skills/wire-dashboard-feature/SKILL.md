---
name: wire-dashboard-feature
description: >-
  Adding or changing UI on a Flask dashboard page (tables, modals, htmx
  fragments, forms, charts). Load before writing dashboard templates or routes
  to reuse SAM's shared macros, avoid the modal and collapse capture-phase
  traps, and run the right gates plus a browser smoke.
---

# Wire a dashboard feature

An ordered checklist for adding or changing UI on a SAM dashboard page. The
rules themselves live in the always-loaded `CLAUDE.md`; this is the *procedure*
that applies them, plus the traps that only bite at author time. Each step
names the canonical section — read that section for the full rule, not a paste
of it here.

Work top to bottom. Steps 1–3 are reuse and formatting; 4 is the write path;
5–11 are the trap-prone surfaces; 12 is the smoke and gate run — read it
before the browser pass, because two caches on webdev will show you stale
markup and stale CSS.

## 1. Reuse before authoring

Reach for an existing macro in `dashboards/fragments/` before writing markup.
The families:

- **Entity links / rosters** — `contract_bits` (user / NSF-program links),
  `user_rows.render_user_rows`.
- **Status** — `badges.status_badge` (its state vocab is the source of truth;
  an unknown state falls back to a neutral `bg-secondary` badge).
- **Collapse** — `collapse.collapse_toggle`.
- **Filters** — `facet_chips.facet_row`, `filter_panel.filter_panel_shell`,
  `audit_filters` / `xras_filters`, the range sliders `ladder_range` /
  `age_band_range`, `search_box.active_toggle_search`.
- **Forms** — all of `form_fields.*` (`text_field`, `number_field`,
  `date_field`, `datetime_field`, `textarea_field`, `select_field`,
  `multiselect_filter`, `checkbox_field`, `readonly_display`, `fk_search_field`,
  `form_errors_panel`), and `modal_form.htmx_form`.
- **Tables** — `pagination.pagination`, `sort_link.sort_link` /
  `sort_header`.
- **Modals** — `modals.modal_scaffold`, `action_buttons.edit_modal_button` /
  `delete_row_button`.
- **Pickers** — `date_range_picker`, `time_range_picker`, `window_pills`.
- **Help** — `help.help_icon` / `help.term`, keyed to a `glossary.g_*` term.
- **Queue-vs-everything switch** — a form-bound `show_all` checkbox inside
  the swap target (`xras_activity_card.html`, `xras_remediations_card.html`),
  read with `read_flag`, scoped through `_shared.scope_rows`; the header badge
  says `N <queue>` + `M more with <switch label>`. Copy the idiom, not the
  markup.

Open `/dev/gallery` (dev builds only) to see each of these rendered in light and
dark across the three layouts, and to copy the exact call.

## 2. Links and entities

The entity-modal idiom is a plain `<a>` or a `btn-link btn-entity` with an
inner `.font-monospace` span. Do not wrap it in a `.btn` that upper-cases the
code. Prefer the shared link macros over hand-rolled anchors.

## 3. Formatting

Route every number, date, percentage, and size through `sam.fmt` — the Jinja
filters `fmt_number`, `fmt_date`, `fmt_size`, `fmt_pct`, `fmt_hours`, and the
rest. Never `strftime` or a raw `'{:,}'` in a template or in CLI display code.
See CLAUDE.md § Display Formatting.

## 4. Writes (POST / PUT)

- Protect the route with a decorator from `webapp/api/access_control.py`; the
  view receives the resolved object, not the id (CLAUDE.md §8).
- Load input through a schema in `sam.schemas.forms` — add one if none fits.
  No inline `strptime` / `float()` / `int()` ladders, no manual empty-string
  dropping (CLAUDE.md §9).
- Pick the smallest handler tier that fits: `handle_htmx_form_post`, then a
  `CrudSpec`, then an `HtmxFormHandler` subclass (CLAUDE.md §9).
- Gate a PUT's update dict on keys present in the original `request.form`, not
  on the loaded output — `load_default` fills absent fields with None and would
  clear them.

## 5. Modals — the fragment / open-modal trap (PR #464)

A fragment that swaps into an already-open modal body must **not** carry
`data-bs-toggle="modal"` for that same modal. Bootstrap fires the toggle on the
open modal and hides it, so the control looks dead and nothing reaches the
console. The layout:

- **Openers live on the card**, where the modal is still closed — a
  `data-bs-toggle="modal"` on a table row or button that opens the shell.
- **In-modal controls only `hx-target` the body** (e.g. `#auditDetailsModalBody`)
  — a Back link, a Save button, a sub-form — never a toggle.

When a fragment references a modal shell id its host page must supply, add the
fragment to `HTMX_FRAGMENT_SHELL_DEPS` in `test_modal_shell_contract.py`. That
test's comment on the `_xras_remediation_actions.html` entry is the worked
example of this exact rule.

## 6. Collapse triggers

Never put a link or button inside a `data-bs-toggle="collapse"` trigger cell or
row. Bootstrap's collapse data-api fires in the capture phase, so a nested
button toggles the row too and no `stopPropagation` on the button can prevent
it. Use `collapse.collapse_toggle`, make the toggle non-link `<td>`s, and render
the chevron with `.collapse-icon`. Gate: `test_collapse_trigger_rows`.

## 7. Action cells never wrap

Two icon buttons side by side are ~105 px. Without `nowrap` the auto table
layout shrinks the Actions column to the widest *single* button and the rest
stack, so every row doubles in height — at desktop width, not just on a
phone (94 px rows on the NSF Programs tab; 127 px on the XRAS Activations
card). The idiom is `<td class="text-end text-nowrap">`; a strip is
`<div class="btn-group btn-group-sm flex-nowrap">`, icon-only with the verb in
`title` + `aria-label` (the `action_buttons` macros' shape). Consequential
verbs (Withdraw, Delete, Merge) keep their words. Gate:
`test_action_cells_nowrap`.

## 8. Active-only toggles

An unchecked htmx checkbox sends no key, so a missing `active_only` means
"include inactive rows". Read it with `read_active_only` from
`webapp/utils/htmx.py` (absent = off), never a hand-rolled comparison
(CLAUDE.md §10).

## 9. Static assets and CSP

Reference every asset with `url_for('static', filename=...)` so it gets its
`?v=` cache tag; never a literal `/static/...` path, and never append `?` or
`#` after the `url_for` result (CLAUDE.md §11). No inline `<script>`, no `on*`
handler attributes, no `hx-on:`, no `<style>` block — behavior goes in a
static JS file, styling in a static CSS file. Gates: `test_static_assets`,
`test_template_csp_lint`.

## 10. CSS

Tokens only — `var(--surface-*)`, `var(--text-*)`, `var(--border-default)` —
never a literal; `test_css_tokens` is an equality ratchet per file, so a new
literal fails and a removed one must update the allowlist. `:has()` is
already in use, so a CSS-only selected state (`.x:has(:checked)`) is fine.
Bootstrap utilities are `!important`: `.border` on an element beats your
rule's `border-color`, so style the component's own border (a
`list-group-item` draws one) and drop the utility.

## 11. Render axes (theme × layout)

`theme` is a global template variable; `layout` is not — a layout-aware route
passes `layout=read_layout()` and forwards it into every layout-aware macro and
chart. A fragment renderer that relays to a delegate must forward the `layout`
it was given, or the fragment silently renders at desktop forever. See
CLAUDE.md § Charts.

## 12. Before commit — smoke and gates

1. **Two caches lie on webdev.** Card fragments are Redis-cached per user,
   so a template edit does not show until
   `docker exec samuel-cache redis-cli -n 0 FLUSHDB` (or
   `sam-admin cache --refresh`). The static `?v=` content hash is memoized
   per process, so a CSS/JS edit is served under the *old* URL until webdev
   restarts — for a quick check, inject a fresh
   `<link href="/static/css/x.css?fresh=1">` from the console; for real,
   restart. Measure "no change" against these before doubting the edit.
2. Tab state persists: click the tab you are testing first — only the active
   pane is in the accessibility snapshot, and `find` matches nothing in a
   hidden one.
3. **A fragment with no live data** (a modal that needs a candidate nobody
   has): set `SAM_DB_*` from `LOCAL_SAM_DB_*`, then
   `create_app()` + `render_template(...)` inside `app.test_request_context()`
   with hand-built dicts, and inject the HTML into the real page's modal body
   (`#auditDetailsModalBody`) via Playwright `evaluate` so the real CSS and JS
   apply. Screenshots go to `.playwright-mcp/<name>.png` (gitignored); the
   default path is the worktree root.
4. Browser-smoke the new UI at **3 layouts × 2 themes** (mobile / tablet /
   desktop, light / dark). Flip layout with `?layout=mobile|tablet|desktop` and
   theme with the navbar toggle. Open `/dev/gallery` to eyeball any shared
   component you touched. Measure row heights and cell widths with
   `getBoundingClientRect()` rather than eyeballing a scaled screenshot.
5. Direct-render tests (`render_template` from a test with a literal context)
   hand Jinja `Undefined` to any key you add later: `{% if x > 0 %}` raises,
   `{% if x %}` is fine — guard new context keys by truthiness.
6. Run the structural gates:
   `pytest tests/unit/test_modal_shell_contract.py tests/unit/test_collapse_trigger_rows.py tests/unit/test_action_cells_nowrap.py tests/unit/test_static_assets.py tests/unit/test_template_csp_lint.py tests/unit/test_css_tokens.py tests/unit/test_route_map_parity.py`
   plus the feature's own tests.
7. If routes changed, regenerate the route-map snapshot
   (`ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`) and commit
   the diff.
