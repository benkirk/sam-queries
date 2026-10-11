---
paths:
  - "src/webapp/dashboards/**/*"
  - "src/webapp/templates/**/*"
  - "src/webapp/static/**/*"
  - "src/webapp/utils/**/*"
  - "src/webapp/register/**/*"
  - "src/webapp/db_browser/**/*"
  - "src/dbbrowse/**/*"
  - "src/sam/schemas/forms/**/*"
  - "tests/unit/webapp/**/*"
---

# Dashboards, forms, htmx and static assets

Loaded with dashboard routes, templates, static assets and form schemas. For new dashboard UI, also load the `wire-dashboard-feature` skill.

## Patterns (§9-§11)

### 9. Form Validation & HTMX Handlers

**The rule:** any validated POST/PUT route — HTMX (`webapp/dashboards/.../*_routes.py`)
**or** API — loads input via a schema from `sam.schemas.forms`. If no schema
fits, **add one first** (in the matching domain module, exported from
`forms/__init__.py`), then wire the route. Never write `datetime.strptime`,
`float()`, or `int()` coercion ladders inline in a handler.

**Schema layer (`HtmxFormSchema` base) gives you for free:**
- `unknown=EXCLUDE` (drops CSRF tokens, stray fields)
- **Automatic empty-string dropping** (`_strip_empty_strings` pre-load): pass
  `request.form` straight to `.load()` — optional Int/Float/Date fields fall
  back to `load_default`. Do NOT hand-roll `{k: v for ... if v != ''}` any more.
- `fields.List` values read via `getlist` (multi-checkbox friendly)
- `split_errors()` → `(field_errors, form_level)` for inline rendering;
  `flatten_errors()` for legacy panel lists
- `normalize_end_date()` (23:59:59 convention) and `assert_date_range()`

**Still manual:** unchecked checkboxes send no key, so inject explicit booleans
when the field means "unchecked ≠ unchanged":
`data['active'] = 'active' in request.form`. FK existence checks need the DB and
stay in the route/handler — use
`validate_fk_existence(db.session, (Model, id, 'label'), ...)` from
`webapp/utils/fk_validation.py`.

**Three tiers of handler — pick the smallest that fits:**

1. **Straight-line create/edit** → `handle_htmx_form_post(...)` from
   `webapp/utils/htmx.py` (kwargs: schema_cls, template, do_action,
   success_triggers, extra_context/context_fn, after_commit, ...).
2. **Standard admin CRUD quintet** (edit-form/edit/create-form/create/delete)
   → declare a `CrudSpec` and `register_crud(bp, spec)` from
   `webapp/dashboards/admin/crud.py`. Hard rule: an entity needing more than
   the spec expresses stays a bespoke route — don't grow the spec for one case.
3. **Complex flows** (partial/PUT gating, ORM cross-field checks,
   domain-exception mapping, custom success responses) → subclass
   `HtmxFormHandler` (`webapp/utils/form_handler.py`). Lifecycle:
   `form_input() → load() → clean() → [management_transaction: perform()] →
   after_commit() → on_success()`; every error path funnels through
   `render_errors()`. Raise `FormError('msg')` from `clean()`/`perform()` for
   user-facing rejections. Routes stay 2 lines: load entities,
   `return _XHandler(entity=obj).handle()`.

**Business rules live below the webapp** — `clean()` maps a domain error, it
doesn't define the rule, so a JSON API can reuse the handler's logic. Which
handlers already do: `docs/plans/HTMX_API_READINESS.md`.

**Error rendering is inline field errors** (`split_errors` → the
`form_fields.html` macros). Two caveats: (a) a template without those macros
needs the `FlattenedFieldErrors` mixin so field errors fold into the top panel;
(b) a field with no visible input (hidden picker values, cascading selects)
needs its errors rerouted to the panel in `render_errors()` — see
`_AddMemberHandler` / `_AddExemptionHandler` for the pattern.

**Typeahead/search endpoints** → `register_typeahead(bp, ...)` in
`webapp/utils/htmx.py` (rule+endpoint passed explicitly; search callable;
min_len). Endpoints whose branching is the feature stay hand-written.
`modal_triggers('reloadXCard')` builds the standard close-modal+reload
HX-Trigger payload.

**Modal fragments (the PR #464 trap):** a fragment that swaps into an
already-open modal body must **not** carry `data-bs-toggle="modal"` for that
modal — Bootstrap fires the toggle on the open modal and hides it, so the
control looks dead with nothing in the console. Openers live on the *card*
(modal closed); in-modal controls only `hx-target` the body. A fragment that
reaches for a host-page shell id is pinned in `HTMX_FRAGMENT_SHELL_DEPS`
(`tests/unit/gates/test_modal_shell_contract.py`); the `wire-dashboard-feature` skill
(`.claude/skills/`) carries the author-time checklist for wiring dashboard UI.

**PUT (partial update) gating:** load with `partial=True`, then gate the
updates dict on keys present in the original `request.form`, NOT on the loaded
output — `load_default` fills absent fields with None, which would silently
clear them.

### 10. "Active only" Toggles

**Absent means OFF.** htmx omits an *unchecked* checkbox from the request
entirely — a missing `active_only` is how "include inactive rows" arrives.
Parse with the shared helper, never a hand-rolled comparison:

```python
from webapp.utils.htmx import read_active_only
active_only = read_active_only(request.args)                 # checkbox-backed
active_only = read_active_only(request.args, default=True)   # FK picker, no checkbox
```

Template side: emit `value="1"` and wire both directions via the
`active_toggle_search` macro (`dashboards/fragments/search_box.html`); for a
card switch, use `hx-trigger="change"` + `hx-include="this"` (see the
Resources/Organizations/Facilities cards).

### 11. Static Assets & Cache Headers

**Always reference an asset with `url_for('static', filename=...)`** — never a
literal `/static/...` path. `webapp/utils/static_assets.py` appends
`?v=<content hash>` to every `url_for` static URL, and that parameter is what
buys the asset a one-year cache.

Flask's default is `Cache-Control: no-cache`, which makes a browser revalidate
every asset on every page load. Measured on nwc1 2026-08-17 (one pod, 4h37m):
**87.5% of requests were `/static`, and 96% of those answered 304** — body-less
round trips whose only purpose was to say "unchanged". Vendored Bootstrap was
revalidated ~145 times in 4½ hours. Those 304s were also the dominant consumer
of `max_requests` in `gunicorn_config.py`, whose point is to bound *application*
heap growth.

**ONE rule sets the header** — is `?v=` present:

| request | `Cache-Control` |
|---|---|
| carries `?v=` | `public, max-age=31536000, immutable` |
| no `?v=` | `public, max-age=3600` |

No path table, no vendor special case. The conservative branch is deliberately
the **default**, so an asset referenced some novel way degrades to a short TTL
rather than pinning a stale copy for a year — getting the rule wrong costs
efficiency, never correctness. The short branch is load-bearing for the assets
no `url_for` can reach: relative `url()` targets inside a stylesheet
(FontAwesome's `../webfonts/*`, the one `../img/` in the app's own CSS).

- **Content hash, not mtime.** A container rebuild restamps every mtime, so an
  mtime tag would invalidate every asset on every deploy — safe, but it would
  discard exactly the cache this exists to create.
- ⚠️ **Rendered HTML is itself cached** (`user_aware_cache_key`), so after a
  deploy that changes an asset, cached fragments keep emitting the old `?v=`
  until they expire. Covered by the existing post-deploy step,
  `sam-admin cache --refresh`. The asset is never unreachable — `v` is a cache
  key, not a lookup key.
- Gates in `tests/unit/gates/test_static_assets.py`: a real rendered page must emit
  **only** versioned static URLs, no template may hardcode `/static/`, and no
  template may append `?`/`#` after `url_for('static')` (that one is a
  correctness bug — the URL already carries a query string).

## Database browser (`/database`)

Read-only rows from **every** engine (`webapp/utils/engine_inventory.py`),
found by reflection, so a new table needs no edit. `src/dbbrowse/` (SQLAlchemy
only) + `src/webapp/db_browser/`; gate `ADMIN_DATABASE`, kill-switch
`DB_BROWSER_ENABLED`. Every query runs in `read_only_connection` (READ ONLY,
statement timeout, rollback; never `db.session`), no `COUNT(*)` on the page path.
❌ **DON'T** add a secret-shaped column undecided — `test_dbbrowse_redact.py` fails
until `dbbrowse/redact.py` redacts it or its `_KNOWN_SAFE` lists it.
Record: `docs/plans/implemented/DB_BROWSER.md`.

## Adding a Validated POST/PUT Route (HTMX or API)
1. Find or add the form schema in `src/sam/schemas/forms/` (export it).
2. Pick the handler tier per §9: `handle_htmx_form_post` → `CrudSpec` →
   `HtmxFormHandler` subclass.
3. FK existence checks in the route/handler `clean()`; PUT updates gated on
   original `request.form` keys.

## Webapp pitfalls

❌ **DON'T** hand-roll form POST bodies: no inline `strptime`/`float()`/`int()` ladders, no manual empty-string dropping (`_strip_empty_strings` does it), no bespoke try/except-ValidationError flows when `handle_htmx_form_post` / `CrudSpec` / `HtmxFormHandler` fits (§9)
❌ **DON'T** hardcode a `/static/...` path in a template — use
`url_for('static', filename=...)` so the asset gets its `?v=` cache tag (§11);
and never append `?`/`#` to a `url_for('static')` result, which already carries one

✅ **DO** load POST input via a `sam.schemas.forms` schema and pick the right §9 handler tier
✅ **DO** gate PUT update dicts on keys present in the original `request.form`, not the loaded output
