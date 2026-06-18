# fs-scans Phase 2 — Disk-scans web UI (handoff)

> **Stacked PR.** Branch off `fs_scans_plugin` (Phase 1), not staging/main, so
> this stacks on top of the wire-up. Phase 1 delivers the plugin + service
> layer this UI consumes; it can merge independently.

## Context

Phase 1 (branch `fs_scans_plugin`) wired SAM to the peer `fs_scans` plugin:
plugin descriptor, a Flask loader that warms one engine per CNPG collection,
a project-scoped service layer, and an Admin→Config health card. Phase 2 puts
a UI on it: a **Filesystem Scans** section on the disk resource-details page
with three tabs, each a lazy-loaded HTMX fragment, scoped to the project's
directories. Works for Campaign_Store now; Destor later (see Multi-DB note).

See memory `project_fs_scans_plugin.md` for the full architecture. The original
exploration + design is in `~/.claude/plans/consider-our-peer-repo-s-wondrous-canyon.md`.

## The service API to consume (already built, Phase 1)

`src/webapp/fs_scans/service.py` — all project-scoped, refuse unscoped queries,
return `[]`/`None` when the project has no scannable dirs or the plugin is off:

- `scan_directories(session, project, resource_name, *, sort_by='size', limit=50, single_owner=False, min_depth=None, max_depth=None) -> list[dict]`
  Rows: `path, depth, total_size_r, file_count_r, dir_count_r, max_atime_r, owner_uid, owner_gid, filesystem`.
- `scan_owner_summary(session, project, resource_name, *, limit=50) -> list[dict]`
  Rows: `owner_uid, total_size, total_files, directory_count, filesystem, username`.
- `scan_group_summary(...) -> list[dict]` — same shape, `owner_gid` + `groupname`.
- `scan_access_history(session, project, resource_name, *, owner_uid=None) -> dict|None`
  Dict: `bucket_labels` (10 bands `< 1 Month` … `7+ Years`), `buckets[label] = {data, files, owners}`,
  `total_data, total_files, directory, fast_path, username_map`.

Gating: `from webapp.fs_scans import is_enabled` (checks plugin loaded + ≥1
collection reachable). `session` = `db.session`.

**Perf:** scoping is fast for whole-collection roots (lab-parent projects) via
the plugin's root fast-path. Genuine sub-path filters (a single fileset deep in
a collection) are inherently slow (on-the-fly, 30–200s) — so **lazy-load each
tab** and consider a spinner + the existing nav-persistence `data-no-persist`.

## Integration points

- **Route/handler:** `_render_disk_resource_details()` in
  `src/webapp/dashboards/user/blueprint.py` (DISK branch of `resource_details`).
  It already builds the disk subtree and capacity/fileset/user cards. Add the
  scans section context here (only when `is_enabled()` and the project has a
  Campaign_Store/Destor account).
- **Template:** `src/webapp/templates/dashboards/user/resource_details_disk.html`
  (collapsible cards today). Add a **Filesystem Scans** card with Bootstrap
  nav-tabs.
- **Tab pattern (mirror):** `templates/dashboards/admin/edit_project.html` uses
  `data-bs-toggle="tab"` + `hx-get=... hx-trigger="shown.bs.tab once"` +
  `hx-target=#pane`. Lazy-load each tab's fragment on first show.
- **Gating/degradation (mirror):** `src/webapp/jobs/routes.py` +
  `partials/jobs_fragment.html` — render a "feature unavailable" partial (not
  404) when `is_enabled()` is False.
- **Formatting:** `fmt_size`, `fmt_number`, `fmt_pct`, `fmt_date` Jinja filters
  (`sam.fmt`). Never raw byte columns — sort by bytes, render one Size column
  via `fmt_size` (memory `feedback_size_columns`).

## The three tabs

1. **Large directories** → `scan_directories` (sortable headers like the jobs
   table; `path`, Size via `fmt_size`, files via `fmt_number`, last-access via
   `fmt_date`). `sort_by` whitelist: `size|files|atime_r|path|depth`.
2. **User / group counts** → `scan_owner_summary` / `scan_group_summary`
   (sub-toggle owner↔group; show resolved `username`/`groupname`, size, files).
3. **Access history** → `scan_access_history` (render the 10 buckets; reuse or
   adapt existing histogram rendering — check `partials/usage_chart.html` and
   the disk stacked-area helper for the charting approach in this repo).

## New files (suggested)

- `src/webapp/fs_scans/routes.py` — Blueprint `fs_scans`, one fragment route per
  tab, `@login_required @require_project_access`, gate on `is_enabled()`.
  Register in `run.py` (url_prefix e.g. `/dashboards/user/fs-scans`).
- `templates/dashboards/user/partials/fs_scans_*.html` (one per tab) +
  optional `_fs_scans_macros.html`.

## Open decisions

- **Charting** the access histogram: match whatever the disk page already uses
  (server-rendered SVG/stacked-area vs a JS lib) — inspect `usage_chart.html`.
- **Default tab** + whether to show the section for projects with no fileset on
  the resource (probably hide).
- **Sub-path slowness UX:** project-root scans are fast; if a fileset-level
  drill-down is added later it's slow — show a loading state and maybe cap.

## Multi-DB note (Destor)

Campaign_Store = CNPG `campaign` DB; Destor (coming) = `desc1` DB. Plugin
`FS_SCAN_PG_DB` is a single DB today, so serving both resources needs a plugin
enhancement (resource→database). Not required for Phase 2 (Campaign_Store only),
but design the route's `resource_name` plumbing so Destor slots in.

## Verification

- `docker compose exec webdev` reachable; plugin built from the merged peer ref.
- Visit `/user/resource-details/NMMM0003?resource=Campaign_Store&from=admin`
  and `NRAL0002`; exercise all three tabs (lazy-load fires once); confirm sizes
  /counts/histogram render via `fmt_*` and match `fs-scans query` /
  `fs-scans analyze` for the same paths.
- Confirm the section is absent/disabled when the plugin is off.
- Tests mirroring `tests/unit/test_webapp_jobs.py` (monkeypatch `FS_SCANS.load`).
