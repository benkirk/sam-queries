# Plan: fs-scans disk-usage plugin + wire-up (analogous to job_history)

## Context

SAM already consumes the peer repo's `job_history` module as an optional plugin
(`require_plugin(HPC_USAGE_QUERIES)`) to show **per-compute-job** history inside a
project's resource-details page. The peer repo's PR#77 (`fs_scans_module` branch,
checked out locally, unpublished — **we are the only customer and control both
repos**) adds a parallel `fs_scans` module that answers **filesystem-scan**
questions over a CNPG/Postgres backend.

We want an entirely analogous integration for **disk** resources: surface
filesystem-scan data on the disk resource-details page, **restricted solely to a
project's directories**. It must work for Campaign_Store now and Destor later
(the scan DB currently exists only for Campaign_Store).

Three eventual views (tabs), one per facade method:
1. **Large directories** (sortable) — `FsScanQueries.list_directories()`
2. **User / group counts** — `FsScanQueries.owner_summary()` / `group_summary()`
3. **Access-history histogram** — `FsScanQueries.access_history()`

Per the user's decisions: scope = **full project subtree**; collection selection =
**derive from the project's directory paths** (avoid the slow all-collections
path-filter scan); **no CLI** — validate the module + wire-up with a throwaway
script, then build the web UI.

This plan covers **Part 1 (module + wire-up)** in depth and sketches **Part 2 (web
UI)** so the design is coherent. Part 1 lands first; Part 2 follows once the
wire-up is verified.

---

## Key facts established during exploration

- **Plugin framework** — `src/sam/plugins.py`: generic `Plugin(name, package,
  install_hint)` descriptor with `.load()` (via `importlib`) and `.available`.
  Adding a plugin = one new module-level constant. `fs_scans` ships in the **same
  `hpc-usage-queries` wheel** already pulled by the `[hpc]` extra (with
  `[postgres]`), so **no new pip dependency** — just a new descriptor.
- **fs_scans API** (peer repo, `from fs_scans import FsScanQueries`):
  - `FsScanQueries(filesystems="all" | name | [names])` — **owns its own sessions**
    (opens/closes per query) and returns **plain dicts**. This differs from
    `JobQueries(session, machine=…)` where SAM injects the session.
  - `list_directories(*, path_prefixes=…, sort_by="size", limit=50, single_owner=…,
    min_depth/max_depth, accessed_before/after, …) -> list[dict]`
  - `owner_summary(*, breakdown=False, **filters)` / `group_summary(…)` — accept
    `path_prefixes`, `limit`, `sort_by`.
  - `access_history(*, owner_uid=None, path_prefixes=…, min_depth/max_depth)
    -> dict|None` (10 pre-bucketed bands; fast pre-computed path when unfiltered,
    on-the-fly when path-filtered).
  - `resolve_usernames(uids)` / `resolve_groupnames(gids)` → `{id: name}`.
  - Connection config via env: `FS_SCAN_DB_BACKEND=postgres`,
    `FS_SCAN_PG_{HOST,PORT,USER,PASSWORD,DB,REQUIRE_SSL}`. One DB per filesystem,
    **one schema per collection** (`cisl`, `aiml`, `cgd`, …). Engine helpers
    `get_engine()/get_session()` are memoized internally (`clear_engine_cache()`).
  - `query_engine.normalize_path()` strips known mount prefixes
    (`/glade/campaign`, `/gpfs/csfs1`, `/glade/derecho/scratch`, `/lustre/desc1`).
- **The scoping linchpin** — a project's directory paths come from the
  `project_directory` table (`ProjectDirectory`, `src/sam/projects/projects.py`):
  `project_id` FK + `directory_name` (full mount-prefixed path,
  e.g. `/glade/campaign/cisl/csg`). The existing disk page already walks the
  **active subtree** and collects these:
  - `build_disk_subtree(session, project, resource_name)` —
    `src/sam/queries/disk_usage.py` — yields a node tree whose nodes carry
    `fileset_paths` (active `ProjectDirectory.directory_name` values for the
    subtree).
  - `_collect_directory_to_projcode(scope_node)` —
    `src/webapp/dashboards/user/blueprint.py` — flattens that tree to
    `{directory_name: projcode}`. Its **keys are exactly the `path_prefixes`** to
    hand the facade.
  - Resource membership: a project is on Campaign_Store iff it has an active
    `Account` for `(project_id, resource_id=Campaign_Store)`; paths are stored
    full (no normalization in SAM).
- **Web mirror targets** — `src/webapp/jobs/{session,routes,service}.py`:
  - `session.py`: `init_job_history(app)` loads the plugin once at startup,
    pre-warms a per-machine engine, attaches a `SET application_name` connect
    listener (CNPG `pg_stat_activity` attribution), stashes state on
    `app.extensions`, exposes `is_enabled()/get_module()`. Graceful-degrades if
    the plugin is absent.
  - `service.py`: thin wrapper that **refuses an unscoped query** (always pins to
    the project) and returns dicts.
  - `routes.py`: HTMX fragment gated on `is_enabled()`; renders a "feature
    unavailable" partial instead of 404 when the plugin is missing.
  - Disk page: `_render_disk_resource_details()` (blueprint.py) +
    `templates/dashboards/user/resource_details_disk.html` (collapsible cards,
    `fmt_size`/`fmt_number`/`fmt_pct` filters). Tabs pattern to mirror lives in
    `templates/dashboards/admin/edit_project.html`
    (`hx-trigger="shown.bs.tab once"`).

---

## Part 1 — Plugin descriptor + wire-up (land first)

### 1.1 Plugin-side helper (peer repo, in tandem — reduces SAM friction)
Because we own `fs_scans` and it owns the mount-prefix knowledge, add a tiny
**public** helper there rather than reimplementing prefix-stripping in SAM:
- Export from `fs_scans/__init__.py`: `FsScanQueries`, `get_engine`,
  `list_pg_schemas`, `filesystem_available`, and a new
  **`collection_for_path(path) -> str | None`** (normalize → first path component;
  return `None` if it can't be mapped). Reuse the existing `normalize_path`.
- Confirm `owner_summary`/`group_summary`/`list_directories`/`access_history` all
  honor `path_prefixes` (agent confirmed; verify by reading the facade).
- No behavior change to the CLI — purely additive public surface.

### 1.2 SAM plugin descriptor — `src/sam/plugins.py`
Add alongside `HPC_USAGE_QUERIES`:
```python
FS_SCANS = Plugin(
    name="Filesystem Scans",
    package="fs_scans",
    install_hint="pip install -e '.[hpc]'  # ships in hpc-usage-queries[postgres]",
)
```

### 1.3 Loader — `src/webapp/fs_scans/session.py` (mirror `jobs/session.py`)
- `init_fs_scans(app)` called once from `create_app` (`src/webapp/run.py`, next to
  `init_job_history(app)`):
  - Read config: `FS_SCANS_ENABLED` / collection list, `FS_SCAN_DB_BACKEND`, and
    the `FS_SCAN_PG_*` vars (consumed by the plugin itself at engine creation).
  - `mod = FS_SCANS.load()`; on `ImportError`/any failure → log warning, mark
    disabled, continue booting (identical posture to job_history).
  - Discover collections via `mod.list_pg_schemas()`; pre-warm each with
    `mod.get_engine(collection)` and attach the **same `_attach_application_name`
    connect listener** (`sam-webapp:{pod}:fs_scans:{collection}`). The facade
    reuses these memoized engines on later `FsScanQueries(...)` calls. Pre-warming
    doubles as a health check for the Admin → Configuration DB card.
  - Stash `{module, collections, enabled}` on `app.extensions['fs_scans']`.
- Expose `is_enabled(app=None)` and `get_module(app=None)`.

### 1.4 Project → path-prefixes + collections — scoping helper
New `src/webapp/fs_scans/scope.py` (or a function in `service.py`):
```
resolve_scan_scope(session, project, resource_name)
  -> (path_prefixes: list[str], collections: list[str])
```
- Reuse `build_disk_subtree(session, project, resource_name)` +
  `_collect_directory_to_projcode(...)` to get the full subtree's
  `directory_name` set (the existing disk-page scope = **full subtree**, matching
  the chosen scope).
- `path_prefixes` = those full paths (the facade normalizes them).
- `collections` = `sorted({mod.collection_for_path(p) for p in path_prefixes} - {None})`.
- If `collections` is empty (project has no scannable dirs) → caller renders an
  empty/disabled state, never an unscoped query.

### 1.5 Service layer — `src/webapp/fs_scans/service.py` (mirror `jobs/service.py`)
Project-scoped wrappers that **refuse to run without path_prefixes** (the
fs_scans analogue of the job service's account pin — prevents cross-project
leakage):
- `scan_directories(project, resource_name, *, sort_by, limit, single_owner=…, …)`
- `scan_owner_summary(project, resource_name, *, limit, …)`
- `scan_group_summary(project, resource_name, *, limit, …)`
- `scan_access_history(project, resource_name, *, owner_uid=None, …)`

Each: call `resolve_scan_scope(...)` → build `FsScanQueries(filesystems=collections)`
→ call the matching facade method with `path_prefixes=path_prefixes` → return
dicts. Resolve UID/GID → names via `resolve_usernames`/`resolve_groupnames` for
display. (No injected session: the facade owns its sessions.)

### 1.6 Config / env wiring
- `.env.example` + `compose.yaml`: add `FS_SCAN_DB_BACKEND=postgres`,
  `FS_SCAN_PG_HOST=host.docker.internal`, `FS_SCAN_PG_PORT/USER/PASSWORD/DB`,
  `FS_SCANS_ENABLED`. Mirror the job_history block (incl. `host.docker.internal`
  routing and the `HPC_USAGE_QUERIES_REF=fs_scans_module` build arg already in
  use). Tests disable it (`FS_SCANS_ENABLED=0` / empty collection list), same as
  `JOB_HISTORY_MACHINES=[]`.
- Plugin install is already covered by the `[hpc]` extra — no `pyproject.toml`
  dependency change.

### 1.7 Validation (throwaway script — the chosen verification surface)
A scratch script (e.g. `/tmp/scan_smoke.py`, not committed) run inside the app
context against the live CNPG backend:
- For `NRAL0002` and `NMMM0003` on `Campaign_Store`: print
  `resolve_scan_scope(...)` (paths + derived collections), then the first rows of
  `scan_directories`, `scan_owner_summary`, `scan_group_summary`, and the
  `scan_access_history` bucket totals.
- Confirms: plugin loads, engines warm, scope derivation yields the right
  collections (`cisl`, `aiml`, …), and results are non-empty and scoped to the
  project's directories only.

---

## Part 2 — Web UI (follow-on, after Part 1 verified)

On `resource_details_disk.html` (DISK resources only), add a **Filesystem Scans**
section with three Bootstrap nav-tabs, each lazy-loaded via
`hx-trigger="shown.bs.tab once"` (pattern from `edit_project.html`):
1. **Large directories** → fragment route calling `scan_directories` (sortable
   headers like the jobs table; `fmt_size`/`fmt_number`).
2. **User / group counts** → `scan_owner_summary` / `scan_group_summary`
   (sub-toggle owner vs group).
3. **Access history** → `scan_access_history` (render the 10 bucket bands;
   reuse/adapt the existing histogram rendering).

- New blueprint `src/webapp/fs_scans/routes.py` mirroring `jobs/routes.py`:
  `@require_project_access`, gate on `fs_scans.session.is_enabled()`, render a
  graceful "fs-scans plugin not loaded" partial when disabled (mirror
  `partials/jobs_fragment.html`).
- New partials under `templates/dashboards/user/partials/` (one per tab) +
  optional `_disk_scans_macros.html`.
- Wire the tabs into `_render_disk_resource_details()` context; only emit the
  section when the project has an active Campaign_Store/Destor account **and**
  `is_enabled()`.

---

## Files to create / modify

**Part 1**
- `src/sam/plugins.py` — add `FS_SCANS` descriptor (modify).
- `src/webapp/fs_scans/__init__.py`, `session.py`, `service.py`, `scope.py` (create).
- `src/webapp/run.py` — call `init_fs_scans(app)` near `init_job_history(app)` (modify).
- `compose.yaml`, `.env.example` — `FS_SCAN_*` / `FS_SCANS_ENABLED` block (modify).
- Peer repo `fs_scans/__init__.py` (+ small helper) — add `collection_for_path`
  and confirm public exports (modify, in tandem).

**Part 2**
- `src/webapp/fs_scans/routes.py` (create); register blueprint in `run.py`.
- `templates/dashboards/user/resource_details_disk.html` — tabs section (modify).
- `templates/dashboards/user/partials/fs_scans_*.html` (+ macros) (create).
- `src/webapp/dashboards/user/blueprint.py` — pass scans context in
  `_render_disk_resource_details()` (modify).

---

## Verification

1. **Part 1**: `export HPC_USAGE_QUERIES_REF=fs_scans_module && make docker-restart`;
   run the throwaway smoke script for `NRAL0002` / `NMMM0003` on Campaign_Store and
   confirm scoped, non-empty results for all three view methods. Confirm the app
   still boots with the plugin **disabled** (graceful degradation).
2. **Part 2**: visit
   `http://127.0.0.1:5050/user/resource-details/NMMM0003?resource=Campaign_Store&from=admin`
   (and `NRAL0002`); exercise all three tabs (lazy-load fires once on tab show),
   verify sizes/counts/histogram render via `fmt_*` filters and match the
   `fs-scans-query` / `fs-scans-analyze` CLI for the same paths. Confirm the
   section is absent/disabled when the plugin is off.
3. Add tests mirroring `tests/unit/test_webapp_jobs.py` (monkeypatch
   `FS_SCANS.load`) for the loader + service scoping (refuses unscoped; derives
   collections from subtree paths).

## Notes
- Keep the JSON-envelope / exit-code conventions in lockstep with the peer repo
  (CLAUDE.md "Related projects") — relevant if a `--format json` API surface is
  added later; not needed for the web-only path.
- Mirror the job_history `application_name` tagging for CNPG attribution
  (memory: csg-postgres application_name convention).
