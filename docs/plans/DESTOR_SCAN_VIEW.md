# Destor parity for the fs-scans Filesystem-Scans web UI

**Status:** SAM consumer side implemented on branch `destor_scan_view`
(PR → `staging`). Depends on the `fs_scans` plugin's `database=` selector
(hpc-usage-queries PR #94, branch `fs_scan_plugin_multi_db`).

## Why

Campaign_Store filesystem-scan analytics (project disk-page card + gated
Status whole-FS tab) read from one CNPG database (`campaign`). Destor is a
*second* CNPG database (`destor`) on the same `csg-postgres` cluster — note the
DB is named `destor`; `desc1` is only the Lustre mount `/lustre/desc1`. This
change gives Destor the same scan-view experience, reading from `destor`.

## The cross-repo prerequisite (done)

The plugin was hard-wired to a single database (`FS_SCAN_PG_DB`). It now
accepts a backward-compatible `database=` on `get_engine` / `get_session` /
`list_pg_schemas` / `get_all_filesystems` / `FsScanQueries`, defaulting to
`PG_DB_NAME`. SAM holds a separate `FsScanQueries(database=…)` per resource —
no global `FS_SCAN_PG_DB` swap (which would be unsafe under gthread workers).

## What changed on the SAM side

- **Config** (`webapp/config.py`): `FS_SCAN_RESOURCE_DATABASES` (resource NAME
  → CNPG database, parsed from `Name:db,Name2:db2`; default
  `Campaign_Store:<FS_SCAN_PG_DB>,Destor:destor`). `FS_SCAN_RESOURCES` default
  now includes `Destor`.
- **Session loader** (`webapp/disk_scans/session.py`): `init_fs_scans` warms
  one engine set **per distinct database**. State is keyed by database:
  `state['databases'] = {db: {'collections': [...], 'engines': {coll: engine}}}`
  (collection names can repeat across databases, so a flat dict would collide).
  New seams: `database_for_resource(resource)` (reads the config map; safe →
  `None` outside an app context) and `get_databases()`.
  `collections_for_resource(resource)` resolves the resource's database, then
  returns *that* database's warmed collections. One unreachable database
  (e.g. destor not yet provisioned) is skipped, not fatal.
- **Service** (`webapp/disk_scans/service.py`): every public wrapper resolves
  `database_for_resource(resource_name)` and threads it into the query cores →
  `FsScanQueries(filesystems=…, database=…)`. `_scoped` now intersects
  reachability against `collections_for_resource(resource_name)` (the
  resource's database), not the global union.
- **Cache** (`webapp/disk_scans/cache.py`): `cached_scan(database=…)` folds the
  database into the cache key, so a collection name shared across databases
  can't share an entry.
- **Admin card** (`webapp/utils/config_inspect.py`): iterates the per-database
  state (one health row per database) and pins each scan-date probe to its
  database (`FsScanQueries(filesystems=[c], database=db)`).
- **Deploy**: `helm/values.yaml`, `.env.example`, `compose.yaml` document/set
  `FS_SCAN_RESOURCE_DATABASES`.
- Routes / templates / `scope.py` unchanged — they already pass `resource_name`
  through; once Destor warms collections, `scan_capable_resources()` surfaces
  a Destor subtab and the disk-page card renders for Destor resources.

## Ops precondition (verified satisfied 2026-06-23)

The webapp role reaches `destor` with the **same** host/credentials as
`campaign` (the plugin's `database=` selector only swaps the DB name). The
application role `pguser` **already has** `CONNECT` on `destor` plus `USAGE` +
`SELECT` on every collection schema (`espat`, `gdex`, `glade_p_archive`,
`mirrors`, `p`) — provisioned identically to `campaign`, confirmed by direct
inspection. So **no grant was required**. If a future collection schema is
added to `destor` without the same grants, the webapp would see an empty
collection; re-run the campaign-style grants in that case.

## Verification

- Unit: `tests/unit/test_webapp_disk_scans.py` — per-database warming,
  `collections_for_resource` / `database_for_resource` routing, database
  threading to the facade (project + resource mode), cache key includes
  database, one-unreachable-database resilience.
- E2E (containers built with the multi-db plugin): a Destor-resourced project
  disk page shows the Filesystem Scans card (badge = a `destor` scan date); the
  Status → Filesystem Scans tab shows a **Destor** subtab beside Campaign_Store;
  a Campaign_Store project is unaffected (no cross-DB leakage); Admin →
  Configuration shows both databases' collections + scan dates.

## Out of scope

CLI disk-charging and quota reconciliation parity for Destor (`GladeCsvReader`
already maps Destor; `DestorQuotaReader` still raises `NotImplementedError`).
