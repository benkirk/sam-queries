# Layer 2 — Populate `disk_activity` / `disk_charge` During Import (Two-Tier Restoration)

> **Status**: Plan-only, to be revisited and implemented in a fresh session.
> **Branch**: open (probably a new branch `disk_activity_population`).
> **Prerequisite**: Layer 1 (`_group_disk_entries` SUM-at-import,
> commit `54d959d`) is shipped on `consistent-disk-rollup`.
> **Companion doc**: this plan supersedes the
> "schema-change" sketch in `docs/plans/DISK_PER_FILESET.md`. Update
> that doc to point here once Layer 2 lands.

## Context

The current SAM disk pipeline writes only `disk_charge_summary`
(per-(user, project, day) rollup). Per-fileset granularity is lost —
the dashboard tree node shows a flat path label without per-directory
bytes, and we can't answer "which fileset is filling up?" from SAM.

**Legacy SAM** maintained a two-tier design:

```
disk_activity        (per-(directory, user, day) raw snapshot rows)
    └── disk_charge  (per-activity FK → account, user, terabyte_year, charge)
            └── disk_charge_summary  (per-(user, project, day) rollup,
                                       built via SUM(...) GROUP BY ...)
```

Tier 1 carries the directory granularity. Tier 2 resolves directory →
account/user. Tier 3 is the rollup for fast dashboard math. The
`calculateDiskChargeSummaries` named query
(`legacy_sam/.../AccountingNamedQuery.xml`) joins all three.

We **already have all three ORM models** (`src/sam/activity/disk.py`,
`src/sam/summaries/disk_summaries.py`) and tables — production carries
5.3M `disk_activity` rows (2012 → 2026-04-25) and 934K `disk_charge`
rows under the legacy collector. Tiers 1 and 2 are **dormant** in our
new pipeline:

- No code in `src/` instantiates `DiskActivity()` or
  `DiskCharge()`.
- `sam-admin accounting --disk` (`src/cli/accounting/commands.py:401–777`)
  resolves directory → account → user, computes TiB-yr / charge, and
  writes only to `disk_charge_summary` via `upsert_disk_charge_summary`.

The Layer 1 SUM-at-import gives correct rollups but loses the per-
fileset detail. **Layer 2 restores the two-tier design** by also
populating `disk_activity` and `disk_charge` during the same import
pass — no schema changes. Per-directory queries become possible by
reading `disk_activity` directly.

## Goals

1. Populate `disk_activity` with one row per (directory, user, snapshot-date,
   projcode) from the input file, idempotently (re-run replaces).
2. Populate `disk_charge` with one row per `disk_activity` whose
   directory resolves to a SAM project + account + user.
3. Keep the existing `disk_charge_summary` writer unchanged — Layer 1
   stays. The dashboard's hot-path queries continue to read the rollup.
4. Add a per-directory query helper that reads `disk_activity` and
   surfaces per-fileset bytes / files for the disk Resource Details
   tree node.
5. **Zero schema changes** anywhere. Use exactly what the existing
   tables/columns already provide.

## Existing infrastructure to reuse

### Tier 1 — `DiskActivity` (`src/sam/activity/disk.py:9–55`)

Columns:

| Column | Type | Notes |
|---|---|---|
| `disk_activity_id` | INT PK | autoincrement |
| `directory_name` | VARCHAR(255) NOT NULL | from input row |
| `username` | VARCHAR(35) NOT NULL | from input row |
| `projcode` | VARCHAR(30) | input file's projcode label (often the umbrella, e.g. `"cesm"`) |
| `activity_date` | DATETIME NOT NULL | snapshot date |
| `reporting_interval` | INT NOT NULL | days the snapshot covers (typically 7) |
| `file_size_total` | BIGINT NOT NULL | legacy-mirror; populate `= bytes` |
| `bytes` | BIGINT NOT NULL | snapshot bytes |
| `number_of_files` | INT | snapshot file count |
| `load_date` | DATETIME NOT NULL | `datetime.now()` at import time |
| `disk_cos_id` | INT NOT NULL FK → `disk_cos` | use `0` (the seed "No class of service" row that already exists) |
| `error_comment` | TEXT | NULL on success, error string on partial-resolve |
| `processing_status` | BOOL | `True` on success, `False` on error |
| `resource_name` | VARCHAR(40) | from `--resource` CLI arg (`'Campaign_Store'`, `'Quasar'`, …) |

Plus `creation_time` / `modified_time` from `TimestampMixin`.

**Natural key for upsert**: `(directory_name, username, activity_date, projcode)`
— matches the legacy `disk_activity` unique constraint (verify on prod
via `SHOW INDEX FROM disk_activity` before committing).

### Tier 2 — `DiskCharge` (`src/sam/activity/disk.py:59–88`)

Columns:

| Column | Type | Notes |
|---|---|---|
| `disk_charge_id` | INT PK | autoincrement |
| `disk_activity_id` | INT NOT NULL FK, **UNIQUE** | enforces 1:1 with `disk_activity` |
| `account_id` | INT NOT NULL FK → `account` | from `_resolve_for_row` |
| `user_id` | INT NOT NULL FK → `users` | from `_resolve_user` (or project lead for `'total'` rollup) |
| `charge_date` | DATETIME NOT NULL | `datetime.now()` (when charge computed) |
| `charge` | FLOAT | TiB-yr × rate (today rate=1) |
| `terabyte_year` | FLOAT | from `tib_years()` |
| `activity_date` | DATETIME | mirror of `disk_activity.activity_date` |

The unique index on `disk_activity_id` enforces that a `disk_activity`
row has at most one `disk_charge` row. Rows whose directory cannot be
resolved to an account stay tier-1-only (disk_activity present,
disk_charge absent).

### Charging-interval semantics (identical to today)

The "dynamic accounting interval" used by `disk_charge_summary` today
is the per-row `reporting_interval` (column 7 of `acct.glade`,
typically `7` days). The importer feeds it into
`tib_years(bytes, reporting_interval)`
(`src/sam/summaries/disk_summaries.py`) at WRITE time — the resulting
`terabyte_years` / `charges` are persisted; the raw interval is not
carried on the summary row.

Layer 2 keeps that exactly:

- `disk_charge.terabyte_year` and `disk_charge.charge` are computed
  by the same `tib_years()` call, with the same per-row
  `reporting_interval` from the input file.
- `disk_charge_summary` continues to roll up via `_group_disk_entries`
  and is unaffected (Layer 1 stays).
- Bonus: the raw `reporting_interval` is **persisted on
  `disk_activity.reporting_interval`** — a column the summary table
  doesn't have. That makes per-row audits / recomputation feasible
  without re-reading the input snapshot, and is exactly how
  legacy SAM's `calculateDiskChargeSummaries` named query would
  recompute summaries from scratch.
- The `DISK_CHARGING_TIB_EPOCH` cutover guard (`src/sam/summaries/disk_summaries.py`)
  stays at the importer level; both tier-2 and tier-3 writes are
  gated by the same epoch check that's already in place
  (`commands.py:450–458`).

### Existing helpers to reuse

- `_resolve_for_row` (`src/cli/accounting/commands.py:516–535`) —
  already resolves directory_path → ProjectDirectory → Project →
  Account.
- `_resolve_user` (`src/sam/manage/summaries.py:36–50`) — username +
  unix_uid → User.
- `tib_years()` (`src/sam/summaries/disk_summaries.py`) — bytes ×
  reporting_interval → TiB-years.
- `_DISK_ROLLUP_USERNAMES` constant (`src/cli/accounting/commands.py:60`)
  — sentinel set including `'total'`.
- `mark_disk_snapshot_current()` (`src/sam/summaries/disk_summaries.py`)
  — already advances the current-snapshot pointer.
- `management_transaction` context manager.

## Design

### 1) New writer: `_write_disk_activity_and_charge(entries, ...)`

Add to `src/cli/accounting/commands.py`. Runs once per import, after
charging math, **before** the existing disk_charge_summary aggregation
+ upsert. Pseudocode:

```
for e in entries:
    # Skip synthetic gap rows (`<unidentified>`) — they don't bind to
    # a real directory.
    if e.user_override is not None:
        continue
    # Skip rollup-sentinel rows (Quasar 'total') — they're not
    # per-fileset by construction.
    if e.username in _DISK_ROLLUP_USERNAMES:
        continue

    activity = upsert_disk_activity(
        session,
        directory_name=e.directory_path,
        username=e.username,
        projcode=e.projcode,        # the input file's label (e.g. 'cesm')
        activity_date=e.activity_date,
        reporting_interval=e.reporting_interval,
        bytes=e.bytes,
        file_size_total=e.bytes,
        number_of_files=e.number_of_files,
        disk_cos_id=0,
        resource_name=resource_name,
        load_date=now,
        processing_status=True,    # flip to False / set error_comment if resolve fails
    )

    # Tier 2: only when resolve succeeds.
    ok, project, account = _resolve_for_row(e)
    if not ok:
        continue
    user = _resolve_user(session, e.username, None)
    upsert_disk_charge(
        session,
        disk_activity_id=activity.disk_activity_id,
        account_id=account.account_id,
        user_id=user.user_id,
        charge_date=now,
        activity_date=e.activity_date,
        terabyte_year=e.terabyte_years,
        charge=e.charges,
    )
```

**Idempotency**: before the loop, DELETE rows for
`(activity_date == snap_date, resource_name == X)` from `disk_activity`
(cascades to `disk_charge` via FK if configured; otherwise delete
explicitly first). Same pattern the disk_charge_summary path already
uses (`commands.py:546–580`). Re-running the same snapshot replaces
all tier-1/tier-2 rows for that day on that resource.

The unique constraint
`(directory_name, username, activity_date, projcode)` on
`disk_activity` is the upsert natural key. Place these helpers in
`src/sam/manage/summaries.py` next to `upsert_disk_charge_summary`:

- `upsert_disk_activity(session, *, directory_name, username, projcode, activity_date, ...) -> Tuple[DiskActivity, str]`
- `upsert_disk_charge(session, *, disk_activity_id, account_id, user_id, ...) -> Tuple[DiskCharge, str]`

Both follow the existing pattern: SELECT by natural key, INSERT-or-UPDATE,
return `(record, 'created' | 'updated')`.

### 2) Wire-up in `_run_disk()` (`src/cli/accounting/commands.py`)

Insert one block between the charging-math pass (currently line 491–493)
and the dry-run table check (currently line 537). Order:

1. Parse entries (existing).
2. Build gap rows (existing).
3. Charging math (existing).
4. **NEW: write tier-1 + tier-2** via `_write_disk_activity_and_charge()`.
5. Dry-run table (existing — show per-fileset rows).
6. Delete existing `disk_charge_summary` rows for snap (existing).
7. `_group_disk_entries()` Layer-1 aggregation (existing).
8. Chunked upsert to `disk_charge_summary` (existing).
9. Mark snapshot current (existing).

`--dry-run` skips both the new tier-1/tier-2 write AND the existing
tier-3 write — same semantics.

The new write needs its own `management_transaction` chunk (one chunk
per ~1000 entries) for parity with the existing `disk_charge_summary`
chunked upsert.

### 3) Per-directory query — `get_directory_usage_at(...)`

Add to `src/sam/queries/disk_usage.py`:

```
def get_directory_usage_at(
    session, *,
    project_id: int,
    resource_name: str,
    activity_date: date,
) -> List[Dict]:
    """Per-directory snapshot for one (project, resource, date)."""
    rows = session.query(
        DiskActivity.directory_name,
        func.sum(DiskActivity.bytes).label('bytes'),
        func.sum(DiskActivity.number_of_files).label('files'),
        func.count(DiskActivity.disk_activity_id).label('row_count'),
    ).join(DiskCharge, DiskCharge.disk_activity_id == DiskActivity.disk_activity_id) \
     .join(Account, Account.account_id == DiskCharge.account_id) \
     .filter(
        Account.project_id == project_id,
        DiskActivity.resource_name == resource_name,
        DiskActivity.activity_date == activity_date,
    ).group_by(DiskActivity.directory_name).all()
    return [...]
```

Bulk variant for many `(project_id, resource_name)` pairs at once
(mirrors `bulk_get_subtree_disk_capacity`'s pattern).

### 4) Dashboard wiring — `build_disk_subtree` (per-fileset display)

In `src/sam/queries/disk_usage.py`, when assembling each tree node's
payload, attach a `directories: [{'name', 'bytes', 'files'}]` list
when the project has >1 ProjectDirectory active on this resource.
For projects with exactly one (the common case), keep the current
single-line render path unchanged.

Single-fileset tree nodes: identical to today (one bytes badge).
Multi-fileset tree nodes: render the per-directory list under the
node header. Template: `src/webapp/templates/dashboards/user/resource_details_disk.html`.

### 5) Backfill

Re-run the existing CLI loop after deploy:

```
for rep in data/project_user_usage/acct.glade.2026-*; do
    sam-admin accounting --resource Campaign_Store --disk \
        --user-usage $rep --verbose --skip-errors
done
for rep in data/project_user_usage/acct.quasar.2026-*; do
    sam-admin accounting --resource Quasar --disk \
        --user-usage $rep --verbose --skip-errors
done
```

Idempotent under the per-resource per-date delete semantics.
Each snapshot now writes 3 tables: `disk_activity`, `disk_charge`,
`disk_charge_summary`. Old snapshots remain tier-3-only until they're
re-imported.

### 6) Production rollout

1. Deploy code.
2. Confirm prod's `disk_activity` unique index matches
   `(directory_name, username, activity_date, projcode)` (legacy
   schema; should be already present given prod has 5.3M rows under
   it, but verify via `SHOW INDEX FROM disk_activity` before flipping
   on the new writer).
3. Re-import the latest 1–2 snapshots on prod via the existing CLI
   loop. The new writer appends to `disk_activity` alongside legacy's
   continuing writes — coexistence is fine because of the unique
   key.
4. Spot-check `disk_activity` row counts before/after for a known
   multi-fileset project: e.g. P43713000 should gain ~4 csfs1 rows
   per snapshot.
5. Visit the disk Resource Details page for P43713000 and confirm
   the tree node now shows per-fileset bytes.

## Files

### Modified

- `src/sam/manage/summaries.py` — add `upsert_disk_activity()` and
  `upsert_disk_charge()` helpers next to `upsert_disk_charge_summary()`.
- `src/cli/accounting/commands.py` — add `_write_disk_activity_and_charge()`
  helper; wire into `_run_disk()` between charging math and dry-run.
- `src/sam/queries/disk_usage.py` — add `get_directory_usage_at()` and
  its bulk variant; extend `build_disk_subtree` to attach per-directory
  payloads when >1 fileset on the resource.
- `src/webapp/templates/dashboards/user/resource_details_disk.html` —
  per-fileset render under multi-fileset tree nodes.
- `tests/unit/test_accounting_disk_admin.py` — new tests:
  - `test_import_writes_disk_activity_per_directory`
    (multi-directory project: 4 input rows → 4 disk_activity rows
    + 4 disk_charge rows, each carrying its own bytes).
  - `test_disk_activity_re_import_replaces_per_resource_date`
    (idempotency: second run for same date deletes + re-inserts,
    not append).
  - `test_disk_activity_skipped_for_unidentified_and_total`
    (synthetic rows produce no disk_activity row).
- `tests/unit/test_disk_usage_queries.py` — new
  `TestPerDirectoryQuery` class:
  - `test_get_directory_usage_at_returns_per_directory_rows`
  - `test_bulk_per_directory_query_pairs`.
- `tests/unit/test_query_functions.py` — extend
  `TestDiskCapacityInDashboardData` with a multi-fileset assertion
  (the project's tree-node payload now carries `directories`).

### Reused (no change)

- `_group_disk_entries()` and the disk_charge_summary upsert path
  — Layer 1 stays exactly as it is.
- `Account.current_disk_usage()` — single-account read; unaffected.
- `Project.is_active`, `ProjectDirectory.is_active` hybrids.
- `bulk_get_subtree_disk_capacity()` — provides the project-level
  capacity used for the headline bar.

### Companion doc to update

- `docs/plans/DISK_PER_FILESET.md` — replace its "schema add of
  `directory_id`" sketch with a pointer to this plan and a note
  that we chose the no-schema-change path. Mark Layer 2 as DONE
  once it ships.

## Verification

### Unit / integration

```
source etc/config_env.sh
pytest tests/unit/test_accounting_disk_admin.py tests/unit/test_disk_usage_queries.py tests/unit/test_query_functions.py -v
pytest -m perf -n 0 -v
```

The new tests are the load-bearing ones. Existing perf budgets must
still hold; the new writer adds a fixed ~2 queries per chunk (delete
+ batched insert), independent of project count.

### Live sanity check

After local re-import of 2026-04-25:

```
mysql -h 127.0.0.1 -u root -proot sam -e "
  SELECT directory_name, SUM(bytes), SUM(number_of_files), COUNT(*)
  FROM disk_activity
  WHERE activity_date='2026-04-25'
    AND resource_name='Campaign_Store'
    AND projcode IN ('p43713000', 'P43713000', 'decs', 'cesm')
  GROUP BY directory_name
  ORDER BY 2 DESC;"
```

Expect 4 rows for P43713000's csfs1 filesets (data, decsdata,
work, transfer), bytes summing to ~19 PiB.

```
mysql -h 127.0.0.1 -u root -proot sam -e "
  SELECT COUNT(*) FROM disk_charge
  WHERE activity_date='2026-04-25';"
```

Expect ~equal to the disk_activity row count for that date (1:1
modulo unresolved rows).

Webapp Resource Details for P43713000 on Campaign_Store:

- Tree node lists 4 csfs1 filesets, each with its own bytes badge.
- Per-fileset bytes sum to the project-level bar value.
- Quasar / Stratus paths do **not** appear in the Campaign_Store
  view (filtered by `resource_name` on the disk_activity side).

### Production sanity check

Compare prod `disk_activity` row counts before / after the
post-deploy backfill of one snapshot:

```
SELECT COUNT(*) FROM disk_activity WHERE activity_date='YYYY-MM-DD';
```

Expect a step-up matching the per-(user, fileset) row count from
that day's `acct.glade` file.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Coexistence with legacy writer on prod | The unique key
`(directory_name, username, activity_date, projcode)` is shared. The
new writer's UPSERT matches existing rows and updates them with the
same data — no duplication. Verify via row-count delta before/after
the first prod backfill. |
| Row volume blow-up | ~2K rows/snapshot/resource × ~52 snapshots/year
≈ 100K disk_activity rows/year/resource. Prod already runs at this
scale (5.3M rows since 2012). Negligible. |
| disk_cos_id=0 missing on a fresh DB | Add an idempotent
`session.query(DiskCos).filter_by(disk_cos_id=0).first() or
session.add(DiskCos(disk_cos_id=0, description='No class of service'))`
guard at the top of `_write_disk_activity_and_charge()`. |
| Quasar 'total' rollup rows have no real directory | Skip them at
tier-1 (they remain tier-3-only via the existing rollup path). The
disk Resource Details view falls back to project-level capacity for
those projects, same as today. |
| `<unidentified>` gap rows have no real directory | Same — skip at
tier-1. |
| Test-DB schema drift on disk_activity columns | Run
`tests/integration/test_schema_validation.py` first; it will fail
if the test snapshot's `disk_activity` differs from prod or our ORM. |
| `_resolve_user` failures inside the new write loop | Tier-1 row
still gets written with `processing_status=False` and
`error_comment='User <X> not found'`; tier-2 row is skipped.
Audit trail preserved without aborting the import. |

## Out of scope

- Adding `directory_id` or any new column to `disk_charge_summary`.
- Adding `resource_id` to `ProjectDirectory`.
- Cross-resource fileset views (e.g. show all of P43713000's
  storage on Quasar+Stratus+Campaign_Store on one page).
- Rewriting historical snapshots' `disk_charge_summary` rollups
  on top of disk_activity (legacy-style `calculateDiskChargeSummaries`
  recomputation). Layer 1 already gives us the rollup; we don't need
  to recompute it.
- Per-fileset stacked-area chart (today's chart is per-user; the
  new infra makes per-fileset possible but no UX request for it
  yet).
- Renaming `terabyte_years` → `tebibyte_years`.

## When to pick this up

Trigger conditions, any of:

1. An operator hits a "which fileset is filling up?" question that
   today's project-level capacity bar can't answer.
2. The `--reconcile-quotas` CLI surface starts showing meaningful
   quota gaps that would benefit from per-fileset DB-side queries
   instead of re-reading `cs_usage.json` each time.
3. A multi-fileset project enters the dashboard's top-N attention
   and the lack of per-fileset detail is a visible UX wart.

Estimated effort: ~1 focused day (helpers + writer + dashboard
wiring + tests + backfill verification). Smaller than the
schema-add path because no DDL coordination with prod.
