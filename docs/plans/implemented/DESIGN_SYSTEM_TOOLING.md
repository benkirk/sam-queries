# Design-System Tooling — handoff (component gallery)

> **Status.** Two deliverables, **both DONE** on branch **`ux_gallery`**,
> **PR #478** vs `staging` (https://github.com/benkirk/sam-queries/pull/478).
> - **Deliverable A (the `wire-dashboard-feature` skill + a CLAUDE.md §9 modal
>   rule) — DONE (2026-08-23).**
> - **Deliverable B (the `/dev/gallery` component gallery) — DONE (2026-08-24),**
>   three commits (B1 flag+blueprint, B2 template+axes, B3 tests). Flag
>   `COMPONENT_GALLERY_ENABLED` mirrors `FLASK_ADMIN_ENABLED` (ON dev, OFF prod).
>   13 tests pass incl. a six-state render smoke; browser-verified light+desktop
>   and dark+mobile with zero console errors.
>
> Only the two deferred follow-ups remain (directions 3 + 4 below). This doc is
> written to be read cold; it is grounded in the tree (line numbers verified
> 2026-08-23), not the original brief. The build sections are kept as the record
> of how B was built.

## Where it landed

Deliverable B shipped as three commits on `ux_gallery`:
`src/webapp/config.py` (flag), `src/webapp/run.py` (gated register),
`src/webapp/dashboards/gallery/{__init__,blueprint,specimens}.py`,
`src/webapp/templates/dashboards/gallery/index.html`,
`tests/unit/test_component_gallery.py`. The build spec that produced them is
preserved below (*Deliverable B — the build*). One trap worth recording:
`{% import 'theme_toggle.html' as theme_toggle %}` collides with base.html's
`{% from … import theme_toggle %}` — alias the namespace to a name other than
the macro's (the gallery uses `theme_frag`).

---

## Context

Every major dashboard feature ends in a manual "UX-polish dance": re-deriving
the shared UI patterns (entity links, modals, tables, widths, axes) and
re-learning recurring traps — the canonical one being the PR #464 bug, where a
`data-bs-toggle="modal"` inside a fragment that swaps into an *already-open*
modal silently closes it. The app already has ~32 shared macros in
`src/webapp/templates/dashboards/fragments/`, a role-token CSS layer, `sam.fmt`,
and structural gates. What was missing: (1) a way to apply those conventions
without re-deriving them (the skill — done), and (2) a visual reference to
eyeball components against (the gallery — this handoff).

Deliverable B is a **dev-only `/dev/gallery` page** that renders each shared
macro in representative states across the theme × layout axes. A visual
reference for a human and a render-smoke surface for Claude.

Directions 3 (bug-class gates) and 4 (component-index doc) stay deferred — see
*Deferred follow-ups*.

---

## Foundation check — no pre-refactor pass

We asked whether obvious cruft should be fixed first. Answer: **build directly.**
The one flagged asymmetry is deliberate, the rest are intentional or
high-churn/low-value.

- **`theme` is a global context processor; `layout` is threaded at call sites.**
  This looks like an accident but is documented and deliberate
  (`docs/plans/implemented/DARK_MODE.md`, § "One transport channel, not two").
  `theme` is *declared* by a click that reloads, so it is global chrome rendered
  onto `data-bs-theme` on the `<html>` root for a flash-free paint. `layout` is
  *discovered* client-side per request via a `?layout=` param and is fed into
  `chart_view` / `user_aware_cache_key` at the call sites. `base.html` and
  `login.html` never read `{{ layout }}`, so a global `layout` processor would
  serve no chrome and merely duplicate a value the cache layer already reads.
  **Leave it;** the gallery passes `layout=read_layout()` like every other
  layout-aware route (the house pattern, not a workaround).
- **Fragment file ↔ macro name mismatches** (`facet_chips.html` → `facet_row`,
  `filter_panel.html` → `filter_panel_shell`, `_breadcrumb.html` → `breadcrumb`)
  — cosmetic; renaming touches ~17 import sites for zero behavioral gain. Skip.
- **`form_fields.*` reading `form` / `errors` from context** — an implicit
  dependency, but making it explicit touches every form template. The gallery
  just supplies `form={}` / `errors={}`.

---

## Verified wiring facts (build against these)

**Config** (`src/webapp/config.py`):
- `FLASK_ADMIN_ENABLED` default `'1'` is in `SAMWebappConfig` at **line 48**;
  the `'0'` override is `ProductionConfig` at **line 278**. Idiom:
  `os.getenv('NAME', '1').lower() in ('1', 'true', 'yes')`.
- `get_webapp_config()` (line 413) reads `FLASK_CONFIG`, default `development`.
  So the flag is **ON in webdev, OFF only in production** — mirror this exactly.

**Registration** (`src/webapp/run.py`):
- Dashboard blueprint imports: lines 39–43. Registration: lines 397–404. The
  `init_admin` kill-switch gate is **lines 438–439**:
  `if app.config.get('FLASK_ADMIN_ENABLED', False): init_admin(app)`.
- `theme` context processor: lines 349–352 (there is no `layout` one).

**Smallest blueprint pattern** is the allocations one
(`src/webapp/dashboards/allocations/__init__.py`): define `bp` with its own
`url_prefix` in `__init__.py`, register with a bare `app.register_blueprint(bp)`.

**Base template**: pages `{% extends 'dashboards/base.html' %}`. It provides the
blocks `title`, `content`, `extra_css`, `extra_js`, `breadcrumbs`, and the
`content_container_class` etc.; it already loads htmx, Bootstrap, the theme
toggle (`dashboards/fragments/theme_toggle.html`), `layout-axis.js`,
`collapse-chevron.js`, `tooltip-init.js`, and the modal JS. `theme` is a global
template var; `layout` is not.

**Route-map parity** (`tests/unit/test_route_map_parity.py`) enumerates a
**hardcoded** `DASHBOARD_BLUEPRINTS` tuple (lines 35–43). A new blueprint is
ignored unless added. The gallery is dev-only → **leave it out**; no snapshot
regen.

**Axes readers** live in `src/webapp/utils/htmx.py`: `read_layout(default=
'desktop')` (line 17), `read_theme(default='light')` (line 55), and
`read_active_only(args, default=False)` (line 116).

---

## Macro inventory (ground truth, `dashboards/fragments/`)

31 `.html` files. **Three define no macros** and are `{% include %}` partials,
not `{% import %}` targets: `htmx_success.html`, `mobile_nav.html`,
`group_members_fragment.html`.

| File | Macros |
|---|---|
| `badges.html` | `status_badge` (state vocab incl. active/inactive/locked/expired/received/processed/manual/failed/sent/queued/suppressed/running/succeeded/partial/skipped; unknown → `bg-secondary`) |
| `collapse.html` | `collapse_toggle` |
| `help.html` | `help_icon`, `term` (keyed to a `glossary.g_*` term) |
| `glossary.html` | 27 `g_*` term macros (content for `help`) |
| `modals.html` | `confirm_modal`, `modal_scaffold` |
| `modal_form.html` | `htmx_form` |
| `plugin_state.html` | `plugin_disabled`, `plugin_error`, `plugin_empty` (no macro named `plugin_state`) |
| `theme_toggle.html` | `theme_toggle` |
| `form_fields.html` | `text_field`, `number_field`, `date_field`, `datetime_field`, `textarea_field`, `select_field`, `multiselect_filter`, `checkbox_field`, `readonly_display`, `fk_search_field`, `form_errors_panel` (private `_label` etc.) |
| `search_box.html` | `active_toggle_search` |
| `pagination.html` | `pagination` |
| `facet_chips.html` | `facet_row` |
| `filter_panel.html` | `filter_panel_shell` |
| `sort_link.html` | `sort_header`, `sort_link` |
| `window_pills.html` | `window_pills` |
| `date_range_picker.html` | `drp_pills`, `drp_custom`, `date_range_picker` |
| `time_range_picker.html` | `time_range_picker` |
| `ladder_range.html` | `ladder_range` (**layout-aware**) |
| `age_band_range.html` | `age_band_range` (**layout-aware**) |
| `audit_filters.html` | `audit_filters` (**layout-aware**) |
| `xras_filters.html` | `xras_filters` (**layout-aware**) |
| `_breadcrumb.html` | `breadcrumb` (distinct from nav-registry `breadcrumbs`) |
| `contract_bits.html` | `contract_user_link`, `nsf_program_link` (both emit modal links), `contract_status_badge` |
| `user_rows.html` | `user_table_head`, `render_user_rows` |
| `xras_person_detail.html` | `person_detail` |
| `action_buttons.html` | `edit_modal_button`, `delete_row_button` (both emit modal openers) |
| `breadcrumbs.html` | `breadcrumbs` (needs `request.endpoint` + `nav_locate()`) |
| `page_tabs.html` | `page_tabs` (needs `url_for(tab.endpoint)`) |

Layout-aware (take a `layout` arg, forward it): `ladder_range`, `age_band_range`,
`audit_filters`, `xras_filters`.

---

## The shell-contract mechanic (read before choosing specimens)

The gallery page `{% extends %}` base, so `test_page_template_targets_resolve`
(`tests/unit/test_modal_shell_contract.py`) applies. **It scans template source
for literal `data-bs-target` / `hx-target="#id"` strings across a page's
`{% extends %}` / `{% include %}` closure — it does NOT follow `{% import %}`.**
Consequences:

- Render every macro via `{% import 'dashboards/fragments/X.html' as x %}` +
  `{{ x.macro(...) }}`. Imported macro source never enters the closure, so a
  modal-opener macro renders **inert** (its button opens nothing) without
  tripping the gate. That is acceptable for a visual reference — label it.
- **Do NOT `{% include %}` a fragment pinned in `HTMX_FRAGMENT_SHELL_DEPS`.**
  From `fragments/` that is `contract_bits.html` and `user_rows.html`. An
  include makes the fragment statically reachable, drops it from
  `_htmx_only_fragment_deps()`, and breaks `test_htmx_fragment_shell_deps_match_pin`.

---

## Deliverable B — the build

**B1 — Config flag** (`src/webapp/config.py`). Add `COMPONENT_GALLERY_ENABLED`
mirroring `FLASK_ADMIN_ENABLED` exactly: default `'1'` in `SAMWebappConfig`
(beside line 48), `'0'` in `ProductionConfig` (beside line 278). Same idiom and
a one-line comment in the FLASK_ADMIN style.

**B2 — Blueprint** (`src/webapp/dashboards/gallery/`, allocations-style):
- `__init__.py`: `bp = Blueprint('component_gallery', __name__,
  url_prefix='/dev/gallery')`, `__all__ = ['bp']`, then `from . import blueprint`.
- `blueprint.py`: one `@bp.route('/')` `@login_required` view that builds plain
  dicts / lists / `datetime`s (no DB) and renders
  `dashboards/gallery/index.html` with `specimens=SPECIMENS` and
  `layout=read_layout()` (theme is global).
- `specimens.py`: the specimen manifest (B3).

Wire into `run.py`: import beside line 43; register with a **gated** block
mirroring the `init_admin` gate (lines 438–439):

```python
if app.config.get('COMPONENT_GALLERY_ENABLED', False):
    app.register_blueprint(component_gallery_bp)
```

Do **not** cache the view — it must reflect live template edits.

**B3 — Specimen manifest** (`specimens.py`). A list of `{group, name, note, …}`;
the template `{% import %}`s each macro and renders it with the sample context.
Tiers:

- **(a) cheap, zero-context (~12):** `badges.status_badge` (iterate the state
  vocab), `collapse.collapse_toggle`, `help.help_icon` / `help.term`,
  `modals.modal_scaffold`, `modal_form.htmx_form` (`{% call %}`),
  `plugin_state`'s three macros, `theme_toggle.theme_toggle`, all 11
  `form_fields.*`, `search_box.active_toggle_search`. Import `form_fields.html`
  **with context** (`{% import … with context %}`) and supply `form={}` and
  `errors={}` (add a populated `errors` specimen to show `form_errors_panel`).
  `htmx_success.html` renders via `{% include %}` with `message` / `detail`.
- **(b) small fixture (~14):** `pagination.pagination`, `facet_chips.facet_row`,
  `window_pills.window_pills`, `date_range_picker.*` / `time_range_picker`,
  `filter_panel.filter_panel_shell` (`{% call %}`), `_breadcrumb.breadcrumb`,
  `sort_link.sort_link` / `sort_header` (context supplying `sortable_columns`,
  `sort`, `fragment_url`, `target_id`, `form_id`), `ladder_range.ladder_range` /
  `age_band_range.age_band_range` (**pass `layout`**), `audit_filters` /
  `xras_filters` (**pass `layout`**), `xras_person_detail.person_detail`,
  `user_rows.render_user_rows(users, can_view_users=false)` (non-linking mode),
  `contract_bits.contract_status_badge` (fake obj with `.is_active` /
  `.is_future`).
- **(c) inert openers + note-and-skip:**
  - `action_buttons.edit_modal_button` / `delete_row_button`,
    `contract_bits.contract_user_link` / `nsf_program_link`,
    `user_rows.render_user_rows(can_view_users=true)` — import (not include) so
    they render but open nothing; label each "opener wired to a shell on real
    pages".
  - Note-and-skip with a placeholder card (need live app/nav/DB context):
    `breadcrumbs.breadcrumbs`, `page_tabs.page_tabs`, `mobile_nav` (include),
    `group_members_fragment` (include).

**B4 — Axes.** The view passes `layout=read_layout()`; the template forwards
`layout=` to the layout-aware macros. Offer a theme toggle (reuse
`theme_toggle`) and plain layout links (`?layout=mobile|tablet|desktop`, carried
by the `sam_layout` cookie/param) so one page flips all six states with no JS.

**B5 — Chrome/CSP.** Extend `dashboards/base.html` (gets htmx/Bootstrap/theme/
collapse JS for free). Keep the wrapper markup CSP-clean: no inline `<script>`,
`on*`, `hx-on:`, or `<style>`; assets via `url_for('static', …)`. Any switcher
JS goes in `src/webapp/static/js/`; any styling in a static CSS file. Gates:
`tests/unit/test_template_csp_lint.py`, `tests/unit/test_static_assets.py`.

**B6 — Tests** (`tests/unit/test_component_gallery.py`): (1) flag on →
`GET /dev/gallery` returns 200 and renders (no 500); (2) `ProductionConfig`
defaults the flag off → route absent; (3) confirm the page does not trip the
static / CSP / modal-shell gates (they scan all templates automatically). Do
**not** touch `test_route_map_parity` (gallery stays out of the allowlist).

**Files:** `config.py` (flag); `run.py` (import + gated register);
`src/webapp/dashboards/gallery/{__init__,blueprint,specimens}.py` (new);
`src/webapp/templates/dashboards/gallery/index.html` (new); optional
`static/js/gallery*.js` + gallery CSS (only if a JS switcher is added);
`tests/unit/test_component_gallery.py` (new).

---

## End-to-end verification

- `docker compose up webdev --watch`; visit `http://localhost:5050/dev/gallery`
  (stub click-login, any RBAC tier). Flip `?theme=dark`,
  `?layout=mobile|tablet`, and the navbar theme toggle; confirm every tier-(a)
  and tier-(b) macro renders across the six states.
- Under the prod gate (`FLASK_CONFIG=production`, or the flag off) the route is
  absent (404).
- `pytest tests/unit/test_component_gallery.py tests/unit/test_static_assets.py tests/unit/test_template_csp_lint.py tests/unit/test_modal_shell_contract.py`.

---

## Deferred follow-ups

- **Bug-class gates** (direction 3): a static gate that fails on
  `data-bs-toggle="modal"` inside a fragment that swaps into a modal body —
  would have caught the PR #464 bug in pytest instead of the browser.
  Highest-leverage next step once the gallery lands.
- **Component-index doc** (direction 4): a one-page "need X → macro Y, rules Z"
  map (the rules currently live as inline template comments and in the skill).

**Reference example:** PR #464 (branch `xras_ux`) is the concrete case the skill
encodes — the table modal-links, the collapsible-section cards, the inline
roster editor, and the self-closing-modal bug.
