# Filesystem Scans — whole-FS view as a Status dashboard tab

**Status:** planned, not started. Its own PR vs `staging`. **SAM-only — no
plugin change** (every plugin endpoint this needs already exists).

**Sequencing:** land PR #328 (`fs_scans_ux_polish`: Dirs column + explorer
Scope panel) first; this is orthogonal and starts from a fresh branch off
`staging`.

---

## Goal

Surface the **unrestricted, whole-filesystem** scan rollup that already exists
behind `VIEW_ALL_FILESYSTEM_DATA` but has **no UX route** today (reachable only
by hand-typing `…/disk-scans/resource/<resource>/explore`).

Make it a **conditional "Filesystem Scans" tab on the Status dashboard**
(`/status`), visible only with `VIEW_ALL_FILESYSTEM_DATA`. The tab holds **one
subtab per scan-capable disk resource** (Campaign_Store today, Destor soon).
Each subtab renders the *same card* we show on a project's disk
resource-details page — **Large Directories + User/Group entities + Access /
File-size histograms** — but **rooted over the whole filesystem** instead of a
project. The card's "Large Directories" detail opens the existing unscoped
explorer, which is how the whole-FS view finally becomes *findable*.

---

## Why Status is the right home

- Status already surfaces filesystems: `templates/dashboards/status/partials/
  filesystem_table.html` (capacity/health per system).
- The tab shell (`templates/dashboards/status/dashboard.html`) already has both
  patterns we need:
  - **conditional tab** — Reservations is `{% if reservations or
    google_calendar_embed_url %}`.
  - **RBAC gating** — `{% if has_permission(Permission.EDIT_SYSTEM_STATUS) %}`
    wraps the outage controls. (`has_permission` + `Permission` are template
    globals.)
- The multi-FS seam is already designed: `disk_scans/session.py:
  collections_for_resource()` is documented as *"the seam where a future
  resource→database map plugs in… once a second resource (e.g. Destor)
  ships"*.

## Decisions locked in

- **Subtab enumeration: a config list** of scan-capable disk resources (explicit;
  Destor is a one-line add) — NOT derived from the `Resource` table.
- **No blueprint move.** Keep `disk_scans` at `/dashboards/user/disk-scans`.
  The URL prefix is cosmetic; resource-mode routes already live under it and the
  Status tab `hx-get`s them regardless. Renaming = route churn + bookmark
  breakage + collision with the just-shipped scoped explorer, for zero
  functional gain.
- **The real refactor** is extracting the scans **card** into a
  mode-parameterized partial shared by the project disk page and the Status tab.

---

## What already exists vs. what's missing

| Card piece | Whole-FS (resource-mode) state |
|---|---|
| **Large Directories** | ✅ Built: `service.scan_directories_resource` + routes `directories_resource_fragment` (`/resource/<r>/directories`) & `directories_resource_page` (`/resource/<r>/explore`), both `VIEW_ALL_FILESYSTEM_DATA`-gated. `_resource_ctx()` + `_render_directories_fragment(mode='resource')` already exist. |
| **User / Group entities** | ❌ project-scoped only (`entities_fragment` → `scan_owner_summary`/`scan_group_summary` via `_scoped(...)`). |
| **Access-history histogram** | ❌ project-scoped only (`access_history_fragment` → `_render_distribution` → `scan_access_history`). |
| **File-sizes histogram** | ❌ project-scoped only (`file_sizes_fragment` → `_render_distribution` → `scan_file_sizes`). |

**Perf bonus:** unscoped histograms/entities run with `path_prefixes=None`,
which is the plugin's **whole-collection-root fast path** (pre-computed
`access_histogram`/`size_histogram` tables) — so the whole-FS card is *cheaper*
to render than the scoped one, not more expensive.

---

## Implementation steps

### 1. Service — three `*_resource` siblings
`src/webapp/disk_scans/service.py`. Mirror the existing `scan_directories_resource`
(which uses `collections_for_resource(resource_name)` + `path_prefixes=None`
instead of `_scoped(...)`). Add:

- `scan_owner_summary_resource(resource_name, *, limit, subpath=None)`
- `scan_group_summary_resource(resource_name, *, limit, subpath=None)`
- `scan_access_history_resource(resource_name, *, owner_uid=None, subpath=None)`
- `scan_file_sizes_resource(resource_name, *, owner_uid=None, subpath=None)`

(4 fns — owner + group are separate today.) Each: `mod = get_module()` guard →
`collections = collections_for_resource(resource_name)` → empty-guard →
`FsScanQueries(filesystems=collections)` → same `_compute()` body + `cached_scan`
key as the scoped version, but `path_prefixes = [subpath] if subpath else None`.
The histogram band-enrichment (`_atime_band_bounds` / `_size_band_bounds`) is
scope-independent — reuse verbatim.

**Refactor option:** extract the shared `_compute` bodies so scoped and resource
fns differ only in how they obtain `(collections, path_prefixes)`. Nice-to-have,
not required.

### 2. Routes — three resource-mode fragments
`src/webapp/disk_scans/routes.py`, all `@login_required` +
`@require_permission(Permission.VIEW_ALL_FILESYSTEM_DATA)`, using `_resource_ctx`:

- `GET /resource/<resource>/entities` → `entities_resource_fragment`
- `GET /resource/<resource>/access-history` → `access_history_resource_fragment`
- `GET /resource/<resource>/file-sizes` → `file_sizes_resource_fragment`

Refactor `entities_fragment` and `_render_distribution` to take **mode +
ctx-builder + service-fn** the way `_render_directories_fragment(mode=...)`
already does, so the project and resource routes share one body each. Key point:
`_render_distribution` currently hardcodes `ctx['scoped_project']` and
`url_for(endpoint, projcode=...)` — parameterize both (resource mode passes
`resource=` and calls the `*_resource` service fn). The shared
`disk_scans_distribution.html` / `disk_scans_entities.html` partials already
treat `project`/`scope` as optional (drill-down links are guarded by
`{% if project %}` — in resource mode they degrade to non-clickable, which is
correct: the per-user→directories drill needs a projcode; the whole-FS card's
drill path is via Large Directories → unscoped explorer instead).

### 3. Config list of scan-capable disk resources
`src/webapp/config.py` (near the other `FS_SCAN_*` vars). Add e.g.
`FS_SCAN_RESOURCES = [...]` — list of disk **resource names** that have scans
(`['Campaign_Store']` today; append `'Destor'` when it ships). Pair with the
`collections_for_resource` seam (which is where the resource→DB branch will go).
Expose a tiny helper (e.g. `service.scan_capable_resources()` or a status
blueprint local) that returns the configured names, optionally intersected with
"has warmed collections" so a misconfigured entry doesn't render an empty subtab.
**No hardcoded resource IDs** (see `feedback_no_duplicate_db_ids`).

### 4. Extract the scans **card** into a shared partial
Today the card is assembled inline in
`templates/dashboards/user/resource_details_disk.html` (the tabs: Large Dirs /
entities / histograms, each lazy-loading a `disk_scans.*_fragment` via hx-get).
Extract into e.g. `templates/dashboards/user/partials/disk_scans_card.html`
parameterized by:
- `mode` (`'project'` | `'resource'`)
- the set of fragment endpoints to hit (project: `*_fragment` with `projcode`;
  resource: `*_resource_fragment` with `resource`)
- `target_id` prefix (so multiple cards/subtabs on one page don't collide)

Re-include it from `resource_details_disk.html` (project mode) to prove parity,
then include it in the Status tab (resource mode). The "Open full view ↗" button
points at `directories_page` (project) or `directories_resource_page` (resource).

### 5. Status tab + per-FS subtabs
- `templates/dashboards/status/dashboard.html`: add
  `{% if has_permission(Permission.VIEW_ALL_FILESYSTEM_DATA) %}` nav-tab
  "Filesystem Scans" + a `tab-pane`. (Mirror the Reservations conditional-tab
  markup.)
- New partial e.g. `status/partials/filesystem_scans.html`: a subtab strip (one
  `nav-link` per configured resource) + a `tab-pane` per resource that
  lazy-loads `disk_scans_card.html` in `mode='resource'` on
  `shown.bs.tab once` (see `feedback_persisted_collapse_htmx` — bind to the
  shown event, not click; lazy so closed subtabs cost nothing).
- `dashboards/status/blueprint.py index()`: pass
  `fs_scan_resources=service.scan_capable_resources()` (guarded so the tab is
  empty/hidden when the plugin is off) and ensure `has_permission`/`Permission`
  are in the template context (they're globals already).

### 6. Tests (`tests/unit/test_webapp_disk_scans.py` + status tests)
- Each `*_resource` service fn forwards `path_prefixes=None`, uses
  `collections_for_resource`, and keys the cache distinctly (monkeypatch a fake
  `mod`, as the existing resource tests do).
- Each resource-mode route is **403 without** `VIEW_ALL_FILESYSTEM_DATA` and
  **200 with** it (mirror `directories_resource_*` gating tests).
- Status `index()` shows the "Filesystem Scans" tab only with the permission,
  and renders one subtab per configured resource.
- Card partial renders in both modes (project link vs resource link present).

---

## Gotchas / notes

- **Drill-down in resource mode:** the histogram band→user→directories drill and
  the entities row→directories drill both build `url_for('…directories_fragment',
  projcode=project.projcode)` — they need a projcode and are correctly
  **suppressed when `project` is None** (templates already guard on `project`).
  The whole-FS card's drill story is **Large Directories → unscoped explorer**
  (already built), not the per-user drill. Confirm the guards hold; don't try to
  synthesize a projcode.
- **`VIEW_ALL_FILESYSTEM_DATA` audience:** its enum value starts with `view_`, so
  it's swept into `ALL_VIEW` → the `nusd`, `csg`, `ssg` group bundles all hold it
  (plus `benkirk`). Real operator audience, gate is meaningful.
- **No new plugin endpoints** — `FsScanQueries` already supports
  `path_prefixes=None`; this is pure SAM wiring.
- **Webdev watch-sync** gotcha unchanged: after any `webdev` restart, re-`touch`
  edited files to force `develop.watch` sync (no source bind-mount).

## Key files

- `src/webapp/disk_scans/service.py` — `scan_*_resource` siblings.
- `src/webapp/disk_scans/routes.py` — 3 gated resource-mode routes; parameterize
  `entities_fragment` + `_render_distribution` on mode.
- `src/webapp/disk_scans/session.py` — `collections_for_resource` (the
  resource→DB seam; already present).
- `src/webapp/config.py` — `FS_SCAN_RESOURCES` config list.
- `templates/dashboards/user/partials/disk_scans_card.html` — extracted shared
  card (new).
- `templates/dashboards/user/resource_details_disk.html` — include the extracted
  card (project mode).
- `templates/dashboards/status/dashboard.html` + `status/partials/
  filesystem_scans.html` — gated tab + per-FS subtabs.
- `src/webapp/dashboards/status/blueprint.py` — pass the resource list.
