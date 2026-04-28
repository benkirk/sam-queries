# Layer 2 — Populate `disk_activity` / `disk_charge` During Import

> **Branch**: `disk_activity` (current).
> **Source**: refines `docs/plans/DISK_ACTIVITY.md` based on
> verification of current code state on 2026-04-28.

## Context

The disk import pipeline today writes only `disk_charge_summary`
(per-(user, project, day) rollup, with `act_*` mirror columns from
commit 711ef27 and SUM-at-import via `_group_disk_entries`). Per-fileset
detail is lost — the dashboard tree node lists fileset paths as a
comma-joined string with a single bytes badge, and we cannot answer
"which fileset is filling up?" from SAM.

`disk_activity` and `disk_charge` ORM models exist (`src/sam/activity/disk.py`)
with the legacy two-tier shape. Locally they're empty (0 rows); on
prod they carry 5.3M / 934K rows from the legacy Java collector that
ran through 2026-04-25.

This plan adds tier-1 (`disk_activity`) and tier-2 (`disk_charge`)
writes alongside the existing tier-3 (`disk_charge_summary`) write.
**No schema changes** — the existing tables and unique key
`(directory_name, username, activity_date, projcode)` already provide
everything needed. Per-fileset queries become possible by reading
`disk_activity` directly. The hot-path dashboard rollup keeps reading
`disk_charge_summary`; the tree node gains a per-fileset breakdown
when the project has >1 active fileset on the resource.

## Verified state (anchors for the implementation)

- `_run_disk()` lives in `src/cli/accounting/commands.py` (around lines
  401–777) on a runner class. `_resolve_for_row` is a **closure**
  defined at lines 571–590, sharing `pd_path_to_project`,
  `account_cache`, `self.session`, and `resource` with the surrounding
  scope. The new writer must be a method on the same class so it
  inherits this state.
- Insertion point for tier-1/tier-2 writes: **after** the `if dry_run:
  return 0` gate at line 599, **before** the existing tier-3 delete at
  line 601. This way `--dry-run` skips tier-1/tier-2 automatically.
- `DiskActivity` ORM (`src/sam/activity/disk.py:9-55`) declares only
  `ix_disk_activity_directory` and `ix_disk_activity_cos`, but the
  live DB carries `disk_activity_unique_idx UNIQUE (directory_name,
  username, activity_date, projcode)`. **ORM drift** — the model must
  add `Index('disk_activity_unique_idx', ..., unique=True)` so
  `tests/integration/test_schema_validation.py` stays green.
- `DiskCharge.disk_activity_id` is UNIQUE (line 66) → enforces 1:1.
  Prod FK `fk_disk_charge_disk_activity` is plain RESTRICT
  (verified via `SHOW CREATE TABLE disk_charge` on
  `sam-sql.ucar.edu`) — **no `ON DELETE CASCADE`**, so the ORM
  matches prod and stays cascade-free. Idempotency delete is
  two-step: delete `disk_charge` rows first, then `disk_activity`.
- `disk_cos_id = 0` (description "No class of service") exists in the
  seed — no runtime guard needed.
- `DiskUsageEntry` (`src/cli/accounting/disk_usage/base.py:10-47`) has
  all the attributes the writer needs: `directory_path`, `username`,
  `projcode`, `activity_date`, `reporting_interval`, `bytes`,
  `number_of_files`, `terabyte_years`, `charges`, `user_override`,
  `account_override`, `act_username`.
- Tree node from `build_disk_subtree` (`src/sam/queries/disk_usage.py:358-462`)
  already attaches `fileset_paths: list[str]` (line 423-429) — Layer 2
  enriches this into a per-fileset payload when >1 fileset.

## Design

### 1. Fix ORM schema drift on `DiskActivity`

`src/sam/activity/disk.py:13-16` — add the unique index that the live
schema enforces:

```python
__table_args__ = (
    Index('disk_activity_unique_idx',
          'directory_name', 'username', 'activity_date', 'projcode',
          unique=True),
    Index('ix_disk_activity_directory', 'directory_name'),
    Index('ix_disk_activity_cos', 'disk_cos_id'),
)
```

`tests/integration/test_schema_validation.py` will now compare the
model and the DB index list 1:1.

### 2. New upsert helpers in `src/sam/manage/summaries.py`

Place next to `upsert_disk_charge_summary`. Both follow the existing
return shape `(record, 'created' | 'updated')`.

```python
def upsert_disk_activity(
    session, *,
    directory_name: str,
    username: str,
    projcode: Optional[str],
    activity_date: datetime,
    reporting_interval: int,
    bytes: int,
    number_of_files: Optional[int],
    resource_name: str,
    load_date: datetime,
    disk_cos_id: int = 0,
    error_comment: Optional[str] = None,
    processing_status: bool = True,
) -> Tuple[DiskActivity, str]:
    """SELECT by (directory_name, username, activity_date, projcode);
    INSERT or UPDATE in place. file_size_total is mirrored from bytes
    (legacy column kept for parity)."""

def upsert_disk_charge(
    session, *,
    disk_activity_id: int,
    account_id: int,
    user_id: int,
    charge_date: datetime,
    activity_date: datetime,
    terabyte_year: float,
    charge: float,
) -> Tuple[DiskCharge, str]:
    """SELECT by disk_activity_id (UNIQUE); INSERT or UPDATE."""
```

No DB-resolve logic in these helpers — the caller supplies all FKs
already resolved (consistent with how `upsert_disk_charge_summary`
already accepts pre-resolved `user`, `project`, `account`).

### 3. New writer method `_write_disk_activity_and_charge()`

Add to the same runner class as `_run_disk` in
`src/cli/accounting/commands.py`. Method, not closure, but called
from `_run_disk` after the closure scope so it can take the closure
as a callable parameter:

```python
def _write_disk_activity_and_charge(
    self,
    entries: list[DiskUsageEntry],
    *,
    snap_date: date,
    resource,
    resource_name: str,
    resolve_for_row: Callable,  # the _resolve_for_row closure
    chunk_size: int,
    skip_errors: bool,
) -> tuple[int, int, int]:  # (n_activity, n_charge, n_skipped)
    now = datetime.now()

    # --- Idempotency: delete pre-existing tier-1/tier-2 for this
    # (resource_name, snap_date). FK has no CASCADE → two-step.
    activity_ids_subq = (
        self.session.query(DiskActivity.disk_activity_id)
        .filter(DiskActivity.activity_date == snap_date)
        .filter(DiskActivity.resource_name == resource_name)
        .subquery()
    )
    with management_transaction(self.session):
        self.session.query(DiskCharge).filter(
            DiskCharge.disk_activity_id.in_(select(activity_ids_subq))
        ).delete(synchronize_session=False)
        self.session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap_date,
            DiskActivity.resource_name == resource_name,
        ).delete(synchronize_session=False)

    # --- Chunked write
    for chunk in _chunked(entries, chunk_size):
        with management_transaction(self.session):
            for e in chunk:
                # Skip synthetic gap rows — no real directory.
                if e.user_override is not None:
                    continue
                # Skip rollup-sentinel rows — not per-fileset.
                if e.username in _DISK_ROLLUP_USERNAMES:
                    continue

                # Tier-2 resolution first; result drives processing_status.
                ok, project, account = resolve_for_row(e)
                user = None
                err = None
                if not ok:
                    err = f"unresolved: projcode={e.projcode!r} path={e.directory_path!r}"
                else:
                    try:
                        user = _resolve_user(self.session, e.username, None)
                    except ValueError as exc:
                        err = str(exc)

                activity, _ = upsert_disk_activity(
                    self.session,
                    directory_name=e.directory_path,
                    username=e.username,
                    projcode=e.projcode,
                    activity_date=e.activity_date,
                    reporting_interval=e.reporting_interval,
                    bytes=e.bytes,
                    number_of_files=e.number_of_files,
                    resource_name=resource_name,
                    load_date=now,
                    disk_cos_id=0,
                    error_comment=err,
                    processing_status=err is None,
                )
                n_activity += 1

                if err is not None:
                    n_skipped += 1
                    continue

                upsert_disk_charge(
                    self.session,
                    disk_activity_id=activity.disk_activity_id,
                    account_id=account.account_id,
                    user_id=user.user_id,
                    charge_date=now,
                    activity_date=e.activity_date,
                    terabyte_year=e.terabyte_years,
                    charge=e.charges,
                )
                n_charge += 1

    return n_activity, n_charge, n_skipped
```

`_resolve_user` is already imported in `commands.py` (or trivially
importable from `sam.manage.summaries`). `select` from sqlalchemy.

### 4. Wire-up in `_run_disk()`

Insert one block at `commands.py` between line 599 (`return 0` on
dry-run) and line 601 (existing tier-3 delete):

```python
# ---- 7d. Tier-1 / Tier-2: populate disk_activity + disk_charge -----
n_act, n_ch, n_skip_tier12 = self._write_disk_activity_and_charge(
    entries,
    snap_date=snap_date,
    resource=resource,
    resource_name=resource_name,
    resolve_for_row=_resolve_for_row,
    chunk_size=chunk_size,
    skip_errors=skip_errors,
)
if self.ctx.verbose:
    self.console.print(
        f"[dim]Wrote {n_act} disk_activity / {n_ch} disk_charge "
        f"row(s) for {resource_name} on {snap_date} "
        f"({n_skip_tier12} unresolved → tier-1 only).[/dim]"
    )
```

Note: the writer iterates **raw `entries`** (per-fileset), not the
post-`_group_disk_entries` rollup. That's the whole point — tier-1
keeps directory granularity that tier-3 sums away.

### 5. Per-directory query in `src/sam/queries/disk_usage.py`

```python
def get_directory_usage_at(
    session, *,
    project_id: int,
    resource_name: str,
    activity_date: date,
) -> list[dict]:
    """Per-directory snapshot for one (project, resource, date).
    Uses tier-1/tier-2 join so unresolved rows are excluded."""
    rows = (
        session.query(
            DiskActivity.directory_name,
            func.sum(DiskActivity.bytes).label('bytes'),
            func.sum(DiskActivity.number_of_files).label('files'),
        )
        .join(DiskCharge,
              DiskCharge.disk_activity_id == DiskActivity.disk_activity_id)
        .join(Account, Account.account_id == DiskCharge.account_id)
        .filter(
            Account.project_id == project_id,
            DiskActivity.resource_name == resource_name,
            DiskActivity.activity_date == activity_date,
        )
        .group_by(DiskActivity.directory_name)
        .all()
    )
    return [
        {'name': r.directory_name, 'bytes': int(r.bytes or 0),
         'files': int(r.files or 0)}
        for r in rows
    ]


def bulk_get_directory_usage_at(
    session, *,
    project_resource_pairs: list[tuple[int, str]],
    activity_date: date,
) -> dict[tuple[int, str], list[dict]]:
    """Bulk variant — single query for many (project_id, resource_name)
    pairs. Mirrors `bulk_get_subtree_disk_capacity`'s pattern: build
    one IN-list, GROUP BY (project_id, resource_name, directory_name),
    bucket results into the result dict."""
```

### 6. Dashboard wiring — `build_disk_subtree` enrichment

In `src/sam/queries/disk_usage.py:358-462`, after the existing
fileset-paths fetch (line 423-429), add a per-fileset bytes lookup
when the project has >1 active `ProjectDirectory` on this resource.
Single-fileset projects keep the current path-list rendering
(unchanged).

Replace `fileset_paths: list[str]` with a richer payload only when
needed:

```python
# Current:
node['fileset_paths'] = dirs_by_project_id.get(p.project_id, [])

# Layer 2:
paths = dirs_by_project_id.get(p.project_id, [])
node['fileset_paths'] = paths
if len(paths) > 1 and node.get('activity_date'):
    node['directories'] = bulk_lookup.get(
        (p.project_id, resource_name), []
    )
```

`bulk_lookup` is computed once outside the per-node loop using
`bulk_get_directory_usage_at` for all `(project_id, resource_name)`
pairs that have multi-fileset projects. Single-fileset nodes pay
nothing.

### 7. Template rendering

`src/webapp/templates/dashboards/user/resource_details_disk.html` —
extend `render_disk_tree_node` macro (lines 9-59). When
`node.directories` is present, render a small list under the node
header with each directory's bytes badge:

```jinja
{% if node.directories %}
  <ul class="disk-fileset-list">
    {% for d in node.directories %}
      <li><span class="path">{{ d.name }}</span>
          <span class="bytes">{{ d.bytes | fmt_size }}</span>
          <span class="files">{{ d.files | fmt_number }} files</span></li>
    {% endfor %}
  </ul>
{% endif %}
```

Single-fileset projects (no `directories` key) render unchanged.

### 8. Backfill

After deploy, re-run the existing CLI loop over local snapshots:

```bash
source etc/config_env.sh
for rep in data/project_user_usage/acct.glade.2026-*; do
    sam-admin accounting --resource Campaign_Store --disk \
        --user-usage "$rep" --verbose --skip-errors
done
for rep in data/project_user_usage/acct.quasar.2026-*; do
    sam-admin accounting --resource Quasar --disk \
        --user-usage "$rep" --verbose --skip-errors
done
```

Each snapshot now writes 3 tables. Idempotent under the
per-(resource, date) two-step delete.

## Files

### Modified

- `src/sam/activity/disk.py` — add `disk_activity_unique_idx` UNIQUE
  index to `DiskActivity.__table_args__` (drift fix).
- `src/sam/manage/summaries.py` — add `upsert_disk_activity()` and
  `upsert_disk_charge()` next to `upsert_disk_charge_summary()`.
- `src/cli/accounting/commands.py` — add `_write_disk_activity_and_charge()`
  method on the disk runner class; insert call between dry-run gate
  (line 599) and existing tier-3 delete (line 601).
- `src/sam/queries/disk_usage.py` — add `get_directory_usage_at()` +
  `bulk_get_directory_usage_at()`; extend `build_disk_subtree` to
  attach `directories` payload for multi-fileset nodes; mirror in
  `bulk_get_subtree_disk_capacity` if it feeds the dashboard.
- `src/webapp/templates/dashboards/user/resource_details_disk.html` —
  per-fileset render under multi-fileset tree nodes.

### New tests

- `tests/unit/test_accounting_disk_admin.py`:
  - `test_import_writes_disk_activity_per_directory` — multi-directory
    project, 4 input rows → 4 disk_activity rows + 4 disk_charge rows.
  - `test_disk_activity_re_import_replaces_per_resource_date` — second
    run for same (resource, date) deletes + re-inserts, no duplicates.
  - `test_disk_activity_skipped_for_unidentified_and_total` — gap rows
    and rollup sentinel rows produce no tier-1 row.
  - `test_disk_activity_unresolved_writes_tier1_only` — unresolved
    directory writes disk_activity with `processing_status=False` and
    no disk_charge row.
- `tests/unit/test_disk_usage_queries.py`:
  - `test_get_directory_usage_at_returns_per_directory_rows`.
  - `test_bulk_per_directory_query_pairs`.
- `tests/unit/test_query_functions.py` — extend
  `TestDiskCapacityInDashboardData` with a multi-fileset assertion:
  the project's tree-node payload now carries `directories`.

### Reused (no change)

- `_group_disk_entries()` and the `disk_charge_summary` upsert path —
  Layer 1 stays.
- `Account.current_disk_usage()`, `Project.is_active`,
  `ProjectDirectory.is_active` hybrids.
- `bulk_get_subtree_disk_capacity()` — provides the project-level
  capacity for the headline bar (still tier-3-fed).
- `_resolve_for_row` closure, `_resolve_user`, `tib_years`,
  `mark_disk_snapshot_current`, `_DISK_ROLLUP_USERNAMES`,
  `DISK_CHARGING_TIB_EPOCH`.

## Verification

### Unit / integration

```bash
source etc/config_env.sh
pytest tests/unit/test_accounting_disk_admin.py \
       tests/unit/test_disk_usage_queries.py \
       tests/unit/test_query_functions.py \
       tests/integration/test_schema_validation.py -v
```

The schema-validation test catches any DiskActivity ORM/DB drift
(post-drift-fix it should still pass). Per-perf budgets must hold —
the new writer adds ~2 queries per chunk (delete + batched insert),
independent of project count.

### Live sanity check

After local re-import of one snapshot:

```sql
-- Per-fileset rows present for a multi-fileset project
SELECT directory_name, SUM(bytes), SUM(number_of_files), COUNT(*)
FROM disk_activity
WHERE activity_date = (SELECT MAX(activity_date) FROM disk_activity)
  AND resource_name = 'Campaign_Store'
GROUP BY directory_name
ORDER BY 2 DESC LIMIT 20;

-- 1:1 with disk_charge for resolved rows
SELECT
  (SELECT COUNT(*) FROM disk_activity
   WHERE activity_date = :d AND resource_name = 'Campaign_Store'
     AND processing_status = 1) AS n_resolved,
  (SELECT COUNT(*) FROM disk_charge dc
   JOIN disk_activity da ON da.disk_activity_id = dc.disk_activity_id
   WHERE da.activity_date = :d AND da.resource_name = 'Campaign_Store')
   AS n_charges;
```

Webapp Resource Details for a multi-fileset project on Campaign_Store:

- Tree node lists each fileset with its own bytes badge.
- Per-fileset bytes sum to the project-level bar value (off by
  rounding only).
- Quasar / Stratus paths do not appear in the Campaign_Store view
  (filtered by `resource_name`).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Coexistence with prod's legacy writer | UNIQUE key is shared; the new UPSERT matches existing rows and updates them. Verify row-count delta on the first prod backfill before/after. |
| Row volume | ~2K rows/snapshot/resource × ~52 snapshots/year ≈ 100K disk_activity rows/year/resource. Prod already runs at 5.3M since 2012. Negligible. |
| FK without CASCADE → orphan disk_charge rows on partial failure | Idempotency block runs both deletes inside one `management_transaction` — atomic. |
| `_resolve_user` failure mid-write | Tier-1 row written with `processing_status=False`, error in `error_comment`; tier-2 skipped. Audit trail preserved without aborting. |
| ORM drift on `DiskActivity` indexes | Drift fix in step 1 + `tests/integration/test_schema_validation.py` catches future drift. |
| Performance of multi-fileset bulk query in dashboard | Single bulk query keyed by `(project_id, resource_name, directory_name)` with one JOIN. Cap at projects with >1 fileset; everyone else pays nothing. |

## Out of scope

- Adding `directory_id` to `disk_charge_summary` (the alternative
  schema-change path in `docs/plans/DISK_PER_FILESET.md`).
- Cross-resource fileset views.
- Recomputing historical `disk_charge_summary` from disk_activity
  (legacy `calculateDiskChargeSummaries` path).
- Per-fileset stacked-area chart.
- Renaming `terabyte_years` → `tebibyte_years`.

## Companion doc updates

- `docs/plans/DISK_PER_FILESET.md` — replace the "schema-add" sketch
  with a pointer to this plan and a note that we chose the
  no-schema-change path.
- `docs/plans/DISK_ACTIVITY.md` — mark "DONE" once Layer 2 ships and
  this plan is verified end-to-end.
