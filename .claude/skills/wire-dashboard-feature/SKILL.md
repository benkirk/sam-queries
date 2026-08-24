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
5–9 are the trap-prone surfaces; 10 is the pre-commit gate run.

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

## 7. Active-only toggles

An unchecked htmx checkbox sends no key, so a missing `active_only` means
"include inactive rows". Read it with `read_active_only` from
`webapp/utils/htmx.py` (absent = off), never a hand-rolled comparison
(CLAUDE.md §10).

## 8. Static assets and CSP

Reference every asset with `url_for('static', filename=...)` so it gets its
`?v=` cache tag; never a literal `/static/...` path, and never append `?` or
`#` after the `url_for` result (CLAUDE.md §11). No inline `<script>`, no `on*`
handler attributes, no `hx-on:`, no `<style>` block — behavior goes in a
static JS file, styling in a static CSS file. Gates: `test_static_assets`,
`test_template_csp_lint`.

## 9. Render axes (theme × layout)

`theme` is a global template variable; `layout` is not — a layout-aware route
passes `layout=read_layout()` and forwards it into every layout-aware macro and
chart. A fragment renderer that relays to a delegate must forward the `layout`
it was given, or the fragment silently renders at desktop forever. See
CLAUDE.md § Charts.

## 10. Before commit — smoke and gates

1. Browser-smoke the new UI at **3 layouts × 2 themes** (mobile / tablet /
   desktop, light / dark). Flip layout with `?layout=mobile|tablet|desktop` and
   theme with the navbar toggle. Open `/dev/gallery` to eyeball any shared
   component you touched.
2. Run the structural gates:
   `pytest tests/unit/test_modal_shell_contract.py tests/unit/test_collapse_trigger_rows.py tests/unit/test_static_assets.py tests/unit/test_template_csp_lint.py tests/unit/test_route_map_parity.py`
   plus the feature's own tests.
3. If routes changed, regenerate the route-map snapshot
   (`ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`) and commit
   the diff.
