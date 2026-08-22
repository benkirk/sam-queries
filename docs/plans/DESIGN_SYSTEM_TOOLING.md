# Design-System Tooling — handoff (skill + component gallery)

> **Status:** planned, not started. A self-contained brief for a fresh session.
> Grounded in three research passes (Claude Code skill format; the app's
> config/blueprint/axes wiring; a macro-by-macro renderability audit of
> `dashboards/fragments/`).

## Context

Every major feature ends in a manual "UX-polish dance": wiring new UI into the
project's shared patterns (entity links, modals, tables, widths) and catching
recurring bugs — e.g. the PR #464 bug where a `data-bs-toggle="modal"` inside a
fragment that swaps into an *already-open* modal silently **closes** it. The
project already has a strong design foundation: ~32 shared macros in
`src/webapp/templates/dashboards/fragments/`, a role-token CSS layer + raw-colour
gate, `sam.fmt` for all formatting, and structural gates
(`modal_shell_contract`, `collapse_trigger_rows`, `route_map_parity`,
`static_assets`, `template_csp_lint`). What's missing is (1) a way to **apply
those conventions without re-deriving them each feature**, and (2) a **visual
reference** to eyeball components against.

This adds both (the two directions chosen from four discussed):
- **A project skill** (`wire-dashboard-feature`) — a task checklist Claude
  auto-loads when adding/changing dashboard UI.
- **A dev-only component gallery** (`/dev/gallery`) — renders every shared macro
  in representative states across the theme × layout axes.

Directions **3 (bug-class gates)** and **4 (component-index doc)** are
deliberately deferred (noted as follow-ups below).

---

## Deliverable A — the `wire-dashboard-feature` skill

**Format** (confirmed against Claude Code v2.1.218+): a project skill lives at
`.claude/skills/wire-dashboard-feature/SKILL.md`. Frontmatter fields are all
optional; `description` drives auto-loading; the `/command` name **is** the
directory name. Keep SKILL.md < 500 lines; sibling files (referenced from the
body) hold any overflow. There is **no** `.claude/skills` dir yet — this is the
first.

**Frontmatter** (leave it model-invocable so it auto-applies; also `/wire-dashboard-feature`):
```yaml
---
description: >-
  Adding or changing UI on a Flask dashboard page (tables, modals, htmx
  fragments, forms, charts). Load before writing dashboard templates/routes to
  reuse SAM's shared macros, avoid the modal/collapse capture-phase bugs, and
  run the right gates + browser smoke.
---
```

**Body = an ordered checklist**, distilled from CLAUDE.md §7–§11 plus the PR #464
lessons. **State the rule and point to the canonical CLAUDE.md section — do NOT
re-paste CLAUDE.md at length** (it's already always-loaded; the skill's value is
the *procedure* + the freshly-learned pitfalls):

1. **Reuse before authoring** — reach for an existing macro in
   `dashboards/fragments/` (entity links `request_cell`/`render_user_rows`/
   `contract_user_link`; `badges.status_badge`; `collapse_toggle`; `facet_row`;
   `form_fields.*`; `pagination`; `filter_panel_shell`). Open `/dev/gallery` to
   pick one.
2. **Links/entities** — entity-modal idiom is a plain `<a>` / `btn-link
   btn-entity` with inner `.font-monospace` (avoids `.btn` uppercasing).
3. **Formatting** — only `sam.fmt` filters (`fmt_number/fmt_date/fmt_size/…`);
   never `strftime` / `'{:,}'`.
4. **Writes** — §8 access decorators (view receives the object); §9 handler
   tiers (`handle_htmx_form_post` → `CrudSpec` → `HtmxFormHandler`); forms in
   `sam.schemas.forms`.
5. ⚠️ **Modals — the PR #464 rule:** a fragment that swaps into a modal body must
   **not** carry `data-bs-toggle="modal"` for that same modal (Bootstrap fires
   toggle on the open modal and hides it — "the button does nothing"). Openers
   live on the *card* (modal closed); in-modal controls only `hx-target` the
   body. Update `HTMX_FRAGMENT_SHELL_DEPS` when a fragment references a new shell.
6. **Collapse triggers** — never a link/button inside a `data-bs-toggle="collapse"`
   trigger cell/row (capture phase); use `collapse.html`, toggle non-link cells,
   chevron via `.collapse-icon`.
7. **Active-only toggles** — `read_active_only` (absent = off).
8. **Static/CSP** — `url_for('static', …)` only; no inline `<script>`/`on*`/
   `hx-on`/`<style>` (static JS + `data-*` + JSON script blocks).
9. **Axes** — pass `read_theme()`/`read_layout()` to charts; a fragment renderer
   relaying to a delegate must forward them.
10. **Before commit — smoke + gates:** browser-smoke the new UI at **3 layouts ×
    2 themes** (open `/dev/gallery` to eyeball any shared components touched);
    run `test_modal_shell_contract`, `test_collapse_trigger_rows`,
    `test_route_map_parity` (regen if routes changed), `test_static_assets`,
    `test_template_csp_lint`, and the feature tests.

**Also (same PR, small):** add the modal-fragment rule (#5) to CLAUDE.md's §9
modal guidance so the canonical doc carries it and the skill can reference it.

**Files:** `.claude/skills/wire-dashboard-feature/SKILL.md` (new); optional
`.../reference.md`; `CLAUDE.md` (one-paragraph modal-fragment rule).

**Verify:** `/skills` lists it; `/wire-dashboard-feature` loads it; every
section/path it references resolves.

---

## Deliverable B — the `/dev/gallery` component gallery

Dev-only page rendering each shared macro in representative states, viewable
across the theme × layout axes. A visual reference for Ben and a
render-smoke/self-check surface for Claude.

**B1 — Config flag** (`src/webapp/config.py`). Add `COMPONENT_GALLERY_ENABLED`,
mirroring `FLASK_ADMIN_ENABLED` exactly: default `'1'` in `SAMWebappConfig`
(~line 50), `'0'` in `ProductionConfig` (~line 305). `FLASK_CONFIG` selects the
class (`development` default in webdev; `production` in helm).

**B2 — Blueprint.** New `src/webapp/dashboards/gallery/` package (or single
`gallery.py`): `bp = Blueprint('component_gallery', __name__,
url_prefix='/dev/gallery')`, one `@bp.route('/')` `@login_required` view. Import
in `run.py` (~lines 45-67) and register with a **gated** block mirroring the
`FLASK_ADMIN_ENABLED`/`init_admin` gate at `run.py:468-471`:
```python
if app.config.get('COMPONENT_GALLERY_ENABLED', False):
    app.register_blueprint(component_gallery_bp)
```
⚠️ **Do NOT cache** the view (no `@cache.cached` — it must reflect live template
edits; `user_aware_cache_key` at `extensions.py:26-91` already partitions by
layout+theme if caching were ever wanted).

**B3 — Specimen manifest** (`.../gallery/specimens.py`). A list of specimens
`{group, name, sample_kwargs/context, note}`; the route builds plain
dicts/lists/`datetime`s (no DB) and the template `{% import %}`s each macro and
renders it. Tiers (from the macro audit):
- **(a) cheap (~10):** `badges.status_badge` (iterate its state vocab),
  `collapse_toggle`, `help_icon`/`term` (wire a `glossary.*` string),
  `modals.modal_scaffold`, `modal_form.htmx_form` (`{% call %}`),
  `htmx_success`, `plugin_state.*`, `theme_toggle`, **all** `form_fields.*`
  (text/number/date/select/checkbox/readonly/fk_search/errors_panel),
  `search_box.active_toggle_search`.
- **(b) small fixture (~14):** `pagination` (`page={'n':2,'per_page':25}`,
  `total=200`, `sort={…}`), `facet_row` (`values=[{'value','count','label'}]`,
  `active=[…]`), `window_pills`, `date_range_picker`/`time_range_picker`
  (datetimes), `filter_panel_shell` (`{% call %}`), `_breadcrumb.breadcrumb`,
  `sort_link` (render in a context supplying `sortable_columns/sort/
  fragment_url/target_id/form_id`), `ladder_range`/`age_band_range` (bands+fields
  dicts), `audit_filters`/`xras_filters` (age_bands list + string vocab),
  `xras_person_detail.person_detail` (fake person dict),
  `user_rows.render_user_rows(users, can_view_users=false)`,
  `contract_bits.contract_status_badge` (fake obj w/ `.is_active/.is_future`).
- **(c) app-context/DB/shell — degrade or note-and-skip:** `action_buttons.*`
  with `permission=None` (renders; opener inert — note it needs a shell);
  `contract_bits` links + `user_rows(can_view_users=true)` — only if the host
  shells `userDetailsModalBody`/`nsfProgramContractsModalBody` are included
  (they're the two fragments this dir pins in `HTMX_FRAGMENT_SHELL_DEPS`), else
  show the non-linking mode; **skip with a one-line placeholder card**:
  `page_tabs`, `breadcrumbs` (nav registry), `mobile_nav`,
  `group_members_fragment` — these genuinely need live app/nav/request context.

**B4 — Axes.** The view reads `read_theme()`/`read_layout()`
(`utils/htmx.py:17,55`) and passes `layout=` to the layout-aware macros
(`ladder_range`, `age_band_range`, the pickers, `audit_filters`/`xras_filters`).
The page offers a **theme toggle** (reuse the `theme_toggle` macro) and **layout
links** (`?layout=mobile|tablet|desktop`, already supported by the `sam_layout`
cookie/param) so one page flips through all six states. Plain `?layout=` links
need no JS; any switcher JS goes in a static file (CSP).

**B5 — Chrome/CSP.** The gallery's own wrapper markup must pass
`test_template_csp_lint` (no inline `<script>`/`on*`/`hx-on`/`<style>`) and
`test_static_assets` (assets via `url_for('static')`). Gallery JS → `static/js/`;
gallery styling → a static CSS file.

**B6 — Tests** (`tests/unit/test_component_gallery.py`): (1) flag on →
`GET /dev/gallery` returns 200 and renders (no 500), with a per-tier marker;
(2) `ProductionConfig` defaults the flag **off** (mirror the FLASK_ADMIN default
test if one exists); (3) static/CSP gates stay green (they scan all templates
automatically). Check whether `test_route_map_parity` enumerates this blueprint;
regen its snapshot only if it does.

**Files:** `src/webapp/config.py` (flag); `src/webapp/run.py` (import + gated
register); `src/webapp/dashboards/gallery/{__init__,blueprint,specimens}.py`
(new); `src/webapp/templates/dashboards/gallery/index.html` (new — imports the
macros); optional `static/js/gallery.js` + a gallery CSS file (only if a JS
switcher is added); `tests/unit/test_component_gallery.py` (new).

**Verify (end-to-end):**
- `docker compose up webdev --watch`; visit `http://localhost:5050/dev/gallery`
  (stub click-login, any RBAC tier). Flip `?theme=dark`, `?layout=mobile|tablet`,
  and the theme toggle; confirm every tier-(a)/(b) macro renders in all six
  states.
- Under the prod gate (`FLASK_CONFIG=production` or the flag off) the route is
  **absent (404)**.
- `pytest tests/unit/test_component_gallery.py tests/unit/test_static_assets.py \
    tests/unit/test_template_csp_lint.py tests/unit/test_route_map_parity.py`.

---

## Sequencing, PRs, and scope

1. **Skill first** — fast, immediately useful; folds the modal-fragment rule into
   CLAUDE.md. → one small PR vs `staging`.
2. **Gallery** — config flag → blueprint → specimens → template → axes → tests.
   → a second PR vs `staging`.
3. **Cross-link** — the skill's smoke step points at `/dev/gallery`.

**Deferred (not this handoff, note as follow-ups):**
- **Bug-class gates** (direction 3): e.g. a static gate failing on
  `data-bs-toggle="modal"` inside a fragment that swaps into a modal body — would
  have caught the PR #464 bug in pytest instead of the browser. Highest-leverage
  next step once these two land.
- **Component-index doc** (direction 4): a one-page "need X → macro Y, rules Z"
  map (the rules currently live as inline template comments).

**Reference example:** PR #464 (branch `xras_ux`) is the concrete case the skill
encodes — the table modal-links, the collapsible-section cards, the inline roster
editor, and the `data-bs-toggle` self-closing-modal bug.
