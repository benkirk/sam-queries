# fs-scans Phase 3 — Reusable "Large directories": filter page, per-user drill-down, project + resource (elevated) modes

## Context

Phase 2 shipped the **Filesystem Scans** card on the disk resource-details page
(four tabs: Large directories, User/group counts, Access history, File sizes).
The "Large directories" table is the most valuable view but currently exposes
only `sort_by` + `limit`. The plugin facade (`fs_scans/queries/facade.py
:list_directories`) **already supports** far more — `sort_by` (size/files/dirs),
`accessed_before`, `accessed_after`, `leaves_only`, and `owner_id` — none of it
surfaced. **No plugin change is needed.**

Goal (four threads):
1. **Tab**: expose the three primary *server-side* sorts (size / #files / #dirs)
   as a pill — like the File Sizes Data/Files pill — since top-50-by-size is a
   genuinely different row set than top-50-by-files (facade sorts then `LIMIT`s).
2. **Standalone page** ("Open full view ↗" from the tab) hosting a **filters
   panel** above the reusable table: accessed-before/after dates, a leaves-only
   toggle, and a user picker. North-star: grow this into a file-browser UI.
3. **Per-user drill-down**: make User/group-tab *owner* rows expandable; expanding
   loads the default Large-directories search filtered to that user (`owner_uid`).
4. **Two scope modes** for the page + reusable fragment (built together, one PR):
   - **Project mode** (existing, unchanged): `require_project_access`, scoped to
     the project's `path_prefixes`. Members explore within their tree.
   - **Resource mode** (new, elevated/global RBAC): browse the *entire* resource's
     collections (Campaign_Store ↔ `campaign` CNPG; future Destor), no project
     scoping. This is where the file-browser UI shines — start at collection roots,
     drill via `?fileset=`.

Caching: filtered/owner permutations go into a **separate short-TTL cache bucket**
so a heavy interactive filterer can't crowd the hot default-path entries that
passive landers depend on (both Redis-backed; explorer bucket self-expires).

## Handoff / how to resume
This doc is the implementation handoff — start a fresh session and work top to
bottom. SAM branch `fs_scans_plugin_phase2` (PR #322), built on `fs_scans_plugin`.
Decisions already locked with the user: filters panel = dates + leaves-only +
user picker; tab = sort pill + "open full view" link (supplement, not replace);
caching = two Redis buckets (default 8-day, explorer 30-min TTL); resource mode
gated by a **global** permission; **both modes built in one PR**.

## Plugin / PR #77 (still open — fix friction there if cleaner)
The facade (`/Users/benkirk/codes/hpc-usage-queries/devel`, branch
`fs_scans_module`, **PR #77 open, checked out locally**) already exposes every
filter this phase needs (`list_directories(sort_by, accessed_before,
accessed_after, leaves_only, owner_id, path_prefixes=None, limit, …)`) — **no
plugin change is planned.** But PR #77 has NOT merged, so if friction appears
during implementation that's better solved in the plugin — e.g. a missing/oddly-
named facade param, the `filesystem` key absent from directory rows, or the
real **resource→CNPG-database mapping** needed for Destor (today
`collections_for_resource` just returns `get_collections()`) — fix it in the
plugin and land both in lockstep. No backwards-compat concern; they deploy
together (conda-env rebuilds on the local checkout). Run plugin tests with
`env -u FS_SCAN_DB_BACKEND -u FS_SCAN_PG_DB pytest`.

## Key facts (from exploration)

- Facade `list_directories(..., sort_by, accessed_before, accessed_after,
  leaves_only, owner_id, path_prefixes, limit, ...)` — all filters exist;
  `_DIR_SORT_KEYS` includes `size|files|dirs` (+ `_r`/`_nr` variants, `atime_r`,
  `path`, `depth`). Rows: `path, depth, total_size_r, file_count_r, dir_count_r,
  max_atime_r, owner_uid, owner_gid` (no `filesystem` key — template already
  doesn't render it).
- `directories_fragment` (`src/webapp/disk_scans/routes.py:103`) already takes
  `?sort_by/limit/scope/fileset/resource/target_id`; `_DIR_SORT_WHITELIST =
  {'size','files','atime_r','path','dirs'}`. Service `scan_directories`
  (`service.py:115`) already has `single_owner/min_depth/max_depth` plumbing —
  add the rest alongside.
- Cache (`disk_scans/cache.py`) is a **bounded LRU** (`TTLCacheAdapter` maxsize
  256, or Redis `allkeys-lru`). Key already folds `opts` in. Single lazy
  module-level `_adapter`; a second bucket = a second named adapter.
- `sortable_table.js` **multi-tbody mode** (`tbody.sortable-group`) is purpose-
  built to keep "a user's row + adjacent lazy-subtree placeholder" together when
  sorting — exact precedent for the owner drill-down (see `user_subtree.html`).
- Filter-panel house style: hidden form + `hx-include`; `type="date"` inputs
  (`audit_filters.html`); `form-check`/`form-switch` toggle with
  `hx-trigger="change"`; `fk_search_field` macro (`form_fields.html` + `fk-picker.js`)
  for the user picker; reusable-macro pattern keyed by `form_id`/`target_id`
  (`audit_filters` reused across Transactions/Adjustments tabs).
- Resource→collection: **no SAM-side map today** — `session.get_collections()`
  returns all warmed schemas, which today *are* the whole `campaign` DB (one DB
  per resource via the plugin's `FS_SCAN_PG_DB`). Unrestricted query is just
  `path_prefixes=None` → facade `_resolve_scope` returns `{fs: None}` → whole-
  collection fast path. (`resolve_scan_scope` ties resource→collections *via a
  project's `ProjectDirectory` paths*; resource mode bypasses that.)
- RBAC: `rbac.py` has `@require_permission(Permission.X)` (global, 401/403) and
  `@require_permission_any_facility(...)`; `Permission` enum + `GROUP_PERMISSIONS`
  bundles (csg/ssg/nusd). No existing permission fits "browse all filesystem
  data" → add one, gated globally.

## Changes

### 1. Service — `src/webapp/disk_scans/service.py` (mode-agnostic core)
Factor a private `_scan_directories(collections, path_prefixes, *, sort_by, limit,
owner_uid, accessed_before, accessed_after, leaves_only, mode)` that builds
`FsScanQueries(filesystems=collections)`, assembles `opts`, picks the bucket
(`'filtered'` when any of owner_uid/accessed_before/accessed_after/leaves_only is
set, else `'default'`), and calls `cached_scan(..., bucket=…)`. It forwards the
new kwargs to the facade (`owner_id=owner_uid`, `accessed_before=…`,
`accessed_after=…`, `leaves_only=…`).

Two public wrappers resolve scope, then delegate:
- `scan_directories(session, project, resource_name, *, …)` — **project mode**
  (existing safety intact): `_scoped(...)` → `(collections, path_prefixes)`;
  refuses unscoped (empty → `[]`). Gains the four new filter kwargs.
- `scan_directories_resource(session, resource_name, *, subpath=None, …)` —
  **resource mode** (NEW): `collections = collections_for_resource(resource_name)`
  (see session helper); `path_prefixes = [subpath]` if a fileset is given else
  `None` (whole-collection fast path). Deliberately *not* project-scoped — only
  reachable behind the elevated route. Same filter kwargs + bucket logic.

### 2. Cache — `src/webapp/disk_scans/cache.py` (two buckets, short-TTL explorer)
Generalize the single `_adapter` into a small per-name registry
(`_adapters: dict[str, CacheBase]`) with `get_cache_adapter(bucket='default')`.
**Both buckets use the same backend** (`RedisTTLAdapter` when `CACHE_REDIS_URL`
is reachable — shared across gunicorn workers — else per-worker
`TTLCacheAdapter`), differing only in name, size, and TTL:

- **`default` bucket** (`fs_scans`) — passive/landing + tab-pill queries. Size
  `FS_SCANS_CACHE_SIZE` (256), TTL `FS_SCANS_CACHE_TTL` (691200 = 8 days; the
  scan-date key is what guarantees freshness, TTL is a backstop).
- **`filtered` bucket** (`fs_scans_filtered`) — owner/date/leaves-only queries.
  Size `FS_SCANS_FILTERED_CACHE_SIZE` (default 128), **TTL
  `FS_SCANS_FILTERED_CACHE_TTL` (default 1800 = 30 min)**. The short TTL keeps
  the volatile exploration permutations from accumulating, so they hold only a
  small, transient slice of Redis and rarely linger long enough to pressure
  eviction of the long-lived default entries. Still Redis-backed, so repeated
  filters are shared across workers.

Note (document in code): this is a *soft* protection — `allkeys-lru` is
instance-global, so under genuine Redis memory pressure recent filtered writes
could still evict default entries; the 30-min TTL minimizes that window by
keeping the filtered footprint small and self-expiring. (Chosen over an
off-Redis filtered bucket to keep cross-worker sharing.)

`cached_scan(..., bucket='default')` selects the adapter; service passes
`bucket='filtered'` when any nonstandard filter is set. Admin hooks
(`purge_fs_scans_cache`, `fs_scans_cache_info`) cover both buckets; wire both into
`webapp/caching/__init__.py` `stats()/clear()/adapters()` (the Config card's
`scans` entry becomes a 2-row list — adjust `configuration_card.html` to loop;
show each bucket's TTL so the 30-min explorer TTL is visible).

### 3. Routes — `src/webapp/disk_scans/routes.py`
Shared filter parsing helper `_dir_filters()`: read `owner_uid` (`type=int`),
`leaves_only` (truthy), `accessed_before`/`accessed_after` (`YYYY-MM-DD` →
datetime, mirror `jobs_fragment` query-date parsing; GET, not POST/PUT, so no
forms-schema needed), `sort_by` (whitelist), `limit`. Used by all four routes.

**Project mode** (existing blueprint, `require_project_access`):
- `directories_fragment` (`/<projcode>/directories`) — add `_dir_filters()` →
  `scan_directories(...)`. Thread active filters into `_common_ctx` so the
  hidden form carries them across sort re-fetches.
- `directories_page` (NEW, `/<projcode>/directories/explore`),
  `@login_required @require_project_access` — renders the standalone page in
  project mode (header shows project + resource).

**Resource mode** (NEW, elevated — `@login_required
@require_permission(Permission.VIEW_ALL_FILESYSTEM_DATA)`):
- `directories_resource_fragment` (`/resource/<resource>/directories`) →
  `scan_directories_resource(...)`. Same `_dir_filters()` + `?fileset=` subpath.
- `directories_resource_page` (`/resource/<resource>/explore`) — standalone page
  in resource mode (header shows resource + a fileset breadcrumb from collection
  roots; file-browser entry).

Both fragment routes render the **same** `disk_scans_directories.html` partial;
both pages render the **same** page template, parameterized by `mode`
('project'|'resource'), `fragment_url` (which fragment to lazy-load), and the
scope context. `target_id` namespacing already prevents DOM collisions when the
fragment appears in multiple places.

### 3b. Resource→collection helper + RBAC permission
- `src/webapp/disk_scans/session.py`: add `collections_for_resource(resource_name)`
  → today returns `get_collections()` (single `campaign` DB = all warmed
  collections). This is the **seam** where a future resource→DB map (Destor)
  plugs in; document that and keep it the only place resource→collections is
  decided for unscoped queries. Returns `[]` when the plugin is off.
- `src/webapp/utils/rbac.py`: add `Permission.VIEW_ALL_FILESYSTEM_DATA`
  ("Browse all filesystem-scan data across a disk resource, unscoped"). Grant it
  globally via `GROUP_PERMISSIONS` to the operator bundles (csg/ssg/nusd) — **not**
  facility-scoped (campaign collections don't map onto UNIV/WNA/NCAR facilities).
  No `USER_FACILITY_PERMISSIONS` entry.

### 4. Templates
- **`disk_scans_directories.html`** (reusable fragment): add `owner_uid`,
  `accessed_before`, `accessed_after`, `leaves_only` to the hidden
  `#disk-scans-dir-params-{{target_id}}` form so sort re-fetches preserve the
  active filter. Optional: a small "filtered: user X · accessed before Y" chip.
  Keep server-side sort links.
- **`resource_details_disk.html`** (Large directories tab): add a **sort pill**
  (Size / # Files / # Subdirs) mirroring the File Sizes Data/Files pill — each
  `hx-get …?sort_by=size|files|dirs` re-queries server-side; plus an
  **"Open full view ↗"** link to `directories_page` carrying resource/scope/fileset.
- **`disk_scans_directories_page.html`** (NEW standalone page, both modes):
  extends the dashboard base; parameterized by `mode` ('project'|'resource') and
  `fragment_url`. Header: project+resource (project mode) or resource + a
  **fileset breadcrumb** from collection roots (resource mode — the file-browser
  entry; each crumb re-targets the fragment with `?fileset=`). The **filters
  panel** (partial below) sits above a table-fragment container that lazy-loads
  the relevant fragment with the panel's params; a `limit` selector
  (50/100/250/500). Deeper file-browser (live subdir descent) can grow from the
  breadcrumb later.
- **`_disk_scans_dir_filters.html`** (NEW partial/macro, audit_filters-style;
  mode-agnostic): hidden form + visible controls — two `type="date"` inputs
  (accessed after/before), a leaves-only `form-check form-switch`, an
  `fk_search_field` user picker (→ `owner_uid`; in resource mode this filters
  across the whole resource), a Search button; `hx-get` the `fragment_url` into
  the table container with `hx-include`.
- **`disk_scans_entities.html`** (owner drill-down): for `kind == 'owner'`, render
  each row as `<tbody class="sortable-group">` containing the data `<tr>` (with an
  expand chevron + `data-bs-toggle="collapse"`) and a `<tr class="collapse">`
  detail row whose inner div lazy-loads `directories_fragment?owner_uid=<uid>
  &resource=…&scope=…&fileset=…&target_id=…` on `shown.bs.collapse once`
  (memory feedback_persisted_collapse_htmx). Update the `<thead>` to multi-tbody
  expectations. Group mode (`kind == 'group'`) unchanged — no expansion (facade
  filters directories by uid only).

### 5. Registration + entry points
All four routes are on the existing `disk_scans` blueprint — no new
`register_blueprint` needed. Resource-mode page entry point: a link from the
admin/operator area (e.g. Admin dashboard or the disk resource card) guarded by
the same `VIEW_ALL_FILESYSTEM_DATA` permission in the template
(`{% if current_user.has_permission(...) %}`), so non-operators never see it.

## Security note
Resource mode exposes **every user's** paths / sizes / owner UIDs across an
entire disk resource — cross-project, cross-user visibility. It is therefore
gated by the global `VIEW_ALL_FILESYSTEM_DATA` permission at the route (not just
the template link), and the service's project-scoping safety invariant
(`_scoped` refusing unscoped queries) is left fully intact for project mode —
resource mode reaches the unscoped path only through its own dedicated,
permission-gated wrapper. The owner picker + per-user filter in resource mode is
an operator capability by design.

## Caching policy (summary)
- **Default bucket** (`fs_scans`, Redis-shared when available, 8-day TTL):
  no-filter + sort_by + limit — what passive landers and the tab pill hit.
  High reuse, cross-worker, long-lived.
- **Filtered bucket** (`fs_scans_filtered`, Redis-shared, 30-min TTL): any of
  owner_uid / accessed_before / accessed_after / leaves_only set — heavy
  interactive use and the per-user drill-down. Shared across workers, but
  self-expires fast so its volatile permutations stay a small, transient
  footprint rather than crowding the long-lived default entries.

## Tests — `tests/unit/test_webapp_disk_scans.py`
- `scan_directories` forwards `owner_uid/accessed_before/accessed_after/
  leaves_only` to the fake facade and into `opts`.
- Bucket selection: a filtered call routes to `fs_scans_filtered`; a default call
  to `fs_scans` (assert via the two adapters' `info()` currsize, or a spy).
- Filtered bucket carries the short TTL: assert `fs_scans_filtered` adapter
  `info()['ttl'] == 1800` (and default == 691200), so the 30-min explorer TTL is
  actually applied.
- `directories_fragment?owner_uid=…&leaves_only=1&accessed_before=2026-01-01`
  → 200, rows render, hidden form carries the filters.
- `directories_page` → 200 with the filters panel present.
- entities `kind=owner` → rows are `sortable-group` tbodies with an expand toggle
  and a detail container holding `owner_uid=`; `kind=group` → no expansion.
- Admin Config card lists both scan buckets.
- **Resource mode**: `directories_resource_fragment` / `_page` → **403 without**
  `VIEW_ALL_FILESYSTEM_DATA`, **200 with**. `scan_directories_resource` queries
  `collections_for_resource(...)` with `path_prefixes=None` (whole-collection),
  bypassing project scope. Project-mode `_scoped` still refuses unscoped queries
  (unchanged). `collections_for_resource` returns `get_collections()` (and `[]`
  when the plugin is off).

## Verification
- `docker compose up webdev --watch`.
- Disk page → Filesystem Scans → **Large directories**: toggle the Size/#Files/
  #Subdirs pill, confirm each re-queries a *different* top-N set (not a client
  re-sort). Click **Open full view ↗**.
- Standalone page: set accessed-before/after, toggle leaves-only, pick a user;
  confirm the table re-queries and matches `fs-scans query --sort-by … --accessed-before …
  --leaves-only --owner …` for the same scope.
- **User / group counts** → expand a user row → confirm it loads that user's top
  directories (sortable, stays paired under client-side re-sort).
- **Resource mode**: as an operator (e.g. benkirk/csg), open
  `/dashboards/user/disk-scans/resource/Campaign_Store/explore` → confirm it
  browses all collections unscoped, the user picker filters across the whole
  resource, and the breadcrumb drills via `?fileset=`. As a plain project member,
  confirm the entry point is hidden **and** the route returns 403.
- Admin → Configuration: confirm both `fs_scans` and `fs_scans_filtered` buckets
  appear; exercise filtered queries and watch only the filtered bucket grow.
- `source etc/config_env.sh && pytest tests/unit/test_webapp_disk_scans.py -v`.
