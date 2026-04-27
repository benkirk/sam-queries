# Plan: `sam-admin accounting --disk` for `disk_charge_summary` updates

## Context

The legacy Java/Hibernate path that populated `disk_charge_summary`
from per-user-per-project disk snapshots is gone. We have:

- A working `--comp --machine ...` path that imports per-job HPC usage
  and upserts into `comp_charge_summary` via `upsert_comp_charge_summary()`.
- A working `--reconcile-quotas <cs_usage.json>` path that compares
  GPFS fileset quotas against SAM allocations (read-only on
  allocations; never touches `disk_charge_summary`).
- A complete, **already-implemented** `upsert_disk_charge_summary()` in
  `src/sam/manage/summaries.py:377` (delegates to
  `_upsert_storage_summary`, lines 298–374). This handles user/project/
  resource/account resolution, facility derivation, and PUT-style upsert.
- A `--disk` flag in the CLI that today just prints
  *"not yet implemented"* (`src/cli/accounting/commands.py:165–167`).

We need to wire **two complementary inputs** into a daily disk-charge
import for `Campaign_Store`:

1. **`acct.glade.YYYY-MM-DD`** — CSV of per-user-per-project usage
   (the only source of `(user, project, bytes, files)` tuples).
2. **`cs_usage.json`** — GPFS quota dump with **per-fileset totals
   (FILESET)** and **per-user totals (USR)**, but **no user→project
   mapping**.

The acct file alone is sufficient for *most* projects, but FILESET
totals don't always equal the sum of per-user rows (some bytes are
owned by service accounts or by users absent from the per-user
report). The reconciliation strategy is to attribute any unaccounted
project-level bytes to a synthetic `<unidentified>` user so SAM books
match GPFS truth.

### Investigations performed (verified in this plan session)

**Schema & data**

- `disk_charge_summary` schema confirmed against MySQL — matches the
  ORM in `src/sam/summaries/disk_summaries.py` (PK
  `disk_charge_summary_id`, natural key
  `(activity_date, act_username, act_projcode, account_id)`, columns
  include `number_of_files`, `bytes`, `terabyte_years`, `charges`,
  with `act_*` columns NULL for disk per the legacy doc).
  Latest `activity_date` in DB is **2026-04-18** (104,541 rows total).
  This exact `acct.glade.2026-04-18` import is already present, so
  the new command will UPDATE those rows on idempotent re-runs.

**Units (carefully verified)**

The legacy Java doc was correct about `KiB`; my earlier byte-vs-KiB
analysis was wrong. Verified by exact match against an existing DB row:

```
acct row:   wchapman / cgd/amp / number_of_files=1361584 / col6=142,788,904,480
DB row:     wchapman / NCGD0009 / number_of_files=1361584 / bytes=146,215,838,187,520
ratio:      146,215,838,187,520 / 142,788,904,480 = 1024.000000  ← exact
ty check:   146,215,838,187,520 × 7 / 365 / 1e12 = 2.8041  ← matches DB 2.80413938
```

So the corrected unit map is:

| Source                                  | Unit       |
|---                                       |---|
| `acct.glade` column 6 (`fileSizeTotal`) | **KiB**    |
| `cs_usage.json` `FILESET[*].usage`/`USR[*].usage` | **KiB** |
| `disk_activity.bytes`                   | bytes (= col6 × 1024) |
| `disk_charge_summary.bytes`             | bytes (sum of activity bytes) |
| `disk_charge_summary.terabyte_years`    | **TB-years (decimal, 10¹²)** ← legacy convention |
| `Allocation.amount` (disk)              | **TiB (binary, 1024⁴)** |

The legacy `GpfsQuotaReader._KIB = 1024` and the legacy
`DiskActivity.Factory` × 1024 multiply are **both correct** — no
unit bug there.

**Legacy formula confirmed** (from
`legacy_sam/.../TByteYearStorageChargeFormula.java:24-26`,
`Constants.java:43`, `FileSizeUtil.java:8,15`):

```
terabyte_year = (bytes * reportingInterval) / DAYS_IN_YEAR / BYTES_PER_TB
              = (bytes * 7)                / 365         / 1e12
```

The legacy doc's "365.25" claim is **wrong** — the constant in code
is exactly `365`. The legacy "TB" is **decimal** (1000⁴), not TiB.

**The unit-mismatch problem the user wants to fix**

`disk_charge_summary.terabyte_years` is decimal TB-years.
`Allocation.amount` (for disk) is **TiB**. When the
allocation-balance calculation rolls up
`Σ terabyte_years` and compares to `Allocation.amount`, the two
values differ by ~9.95% (`1024⁴ / 10¹² = 1.099511627776`).

→ This plan switches the new command (and a one-shot backfill of
existing rows) to **TiB-years (1024⁴)** so the column units
match the allocation column units. Formula becomes:

```
terabyte_years_TiB = (bytes * reporting_interval) / 365 / (1024**4)
```

We keep `365` (not `365.25`) for legacy parity — this is a billing
convention, and changing it has no effect on allocation comparisons
as long as both sides use the same value.

**Reconciliation gap is small but real**

Reading FILESET vs Σ(per-user acct rows):

- Total FILESET usage: 130.74 PB (= 127.68 TB × 1024 KiB → KiB-units
  match, no scale comparison meaningful here without recomputing in
  bytes; gap percentage holds either way).
- Corpus-wide gap: ~1.5%. 295 filesets in FILESET have **no** acct
  rows at all (`jntp`, `vast`, `cesmdata`, `fedata`, parent `cgd`).
  Top per-project measurable gaps: `acom` ~53 GB, `uokl0045` ~12 GB,
  `utam0020` ~6 GB.
- The acct file and FILESET use *different* keys for the project axis:
  acct's column 3 is the **SAM projcode** (e.g. `p48503002`); FILESET
  is keyed by **fileset name** (e.g. `jntp`). The mapping
  `fileset_name → projcode` is recoverable from the acct file itself
  (column 2 path → column 3 projcode) for any fileset that has at
  least one acct row, and 295 FILESET-only filesets cannot be mapped
  this way. For those we fall back to *path → projcode* derivation
  via SAM's `ProjectDirectory` table (already used by
  `_run_reconcile_quotas`).

---

## Approach

Add a new `_run_disk()` method to `AccountingAdminCommand` plus a small,
pluggable parser package modeled on `quota_readers/`. The command
reuses `upsert_disk_charge_summary()` exactly — no new ORM writes.

### CLI surface (entry point `src/cli/cmds/admin.py`)

Extend the existing `accounting` Click command (no new top-level command):

```
sam-admin accounting --disk \
    --resource Campaign_Store \
    --user-usage ./acct.glade.2026-04-18 \
    --quotas    ./cs_usage.json \
    [--reporting-interval 7] \
    [--unidentified-user <unidentified>] \
    [--reconcile-quota-gap] \
    [--date 2026-04-18 | --today | --last 7d] \
    [--dry-run] [--skip-errors] [--chunk-size 500] [-v]
```

Notes:
- `--user-usage` (new) is the per-user-per-project file.
  Same-shape positional input as `--reconcile-quotas` — Click `Path`,
  must exist.
- `--quotas` (new, optional) is the same `cs_usage.json` file already
  used by `--reconcile-quotas`. Required only if
  `--reconcile-quota-gap` is set.
- `--reporting-interval` default `7` (as documented in the legacy
  spec); the value goes straight into the terabyte-years formula.
- `--unidentified-label` default literal `<unidentified>`. **This is a
  free-text audit label written to the `act_username` column only.**
  It is **NOT** added to the `users` table. The resolved
  `user_id`/`username` for gap rows is the project lead (see
  "Unidentified attribution" below).
- The existing `--resource` flag is reused; `--machine` is ignored for
  disk (and we error if both `--disk` and `--machine` are passed).
- Date selection (`--date`/`--today`/`--last`) is honored for
  *filtering / safety*: the parsed file's snapshot date must fall in
  the requested window, otherwise we error out (prevents the wrong
  file getting fed to "yesterday").

### Files to add / modify (concrete paths)

#### NEW — disk usage parsers (object-oriented, pluggable)

`src/cli/accounting/disk_usage/__init__.py`
- Exports `get_disk_usage_reader(resource_name, path)` and the dataclass
  `DiskUsageEntry(activity_date, projcode, username, number_of_files, bytes, directory_path, reporting_interval, cos)`.
- Mirror `quota_readers/__init__.py`'s registry pattern.

`src/cli/accounting/disk_usage/base.py`
- `DiskUsageReader` ABC with `path: str`, `snapshot_date: date | None`,
  `read() -> list[DiskUsageEntry]`.
- `DiskUsageEntry` dataclass.

`src/cli/accounting/disk_usage/glade_csv.py`
- `GladeCsvReader(DiskUsageReader)` — parses `acct.glade.YYYY-MM-DD`.
- Columns: `(date, path, projcode, username, nfiles, fsize_bytes, reporting_interval, cos)` per
  legacy doc §Phase 1, but **`fsize_bytes` is taken as bytes directly**
  (no ×1024 — confirm with current data, see Investigations above).
- Skips `gpfsnobody` and any row where `username` is purely numeric
  (legacy "uid was never resolved" rows like row 10 of the sample
  file).
- Honors a strict mode where unknown projcodes/usernames raise — the
  CLI's `--skip-errors` decides whether to abort or continue.

`src/cli/accounting/disk_usage/registry.py` *(or fold into __init__)*
- `Campaign_Store` → `GladeCsvReader`. Add others (Stratus,
  Lustre/Destor) as we get sample inputs. The reader chosen by
  `(resource_name, file extension/shape)` keeps the surface
  filesystem-agnostic.

#### MODIFY

`src/cli/cmds/admin.py` (the `accounting` Click command)
- Add the three new options (`--user-usage`, `--quotas`,
  `--reporting-interval`, `--unidentified-user`,
  `--reconcile-quota-gap`).
- Forward them to `AccountingAdminCommand.execute(...)` when `--disk`
  is set.
- Validate mutual exclusion (`--machine` is HPC-only;
  `--user-usage` is required for `--disk`; `--reconcile-quota-gap`
  requires `--quotas`).

`src/cli/accounting/commands.py`
- Replace the `--disk` stub at lines 165–167 with a real `_run_disk()`
  modeled on `_run_comp` (lines 177–302). Pseudocode:

  ```python
  def _run_disk(self, resource_name, user_usage_path, quotas_path,
                reporting_interval, unidentified_user, reconcile_gap,
                date_window, *, dry_run, skip_errors, chunk_size, verbose):
      # 1. Pick a reader, parse user-usage file
      reader = get_disk_usage_reader(resource_name, user_usage_path)
      entries = reader.read()
      if not entries: return 0

      # 2. Validate snapshot date against requested window
      self._assert_snapshot_in_window(reader.snapshot_date, date_window)

      # 3. Verbose dump (reuse display_dry_run_table pattern)
      if verbose: display_disk_dry_run_table(self.ctx, entries, ...)

      # 4. Compute terabyte_years / charges per row
      #    ty = (bytes * reporting_interval) / 365.25 / 1e12
      #    charges = ty   (1:1 per legacy doc §Phase 2 / 3)

      # 5. Optional: reconcile per-project gap from cs_usage.json
      if reconcile_gap and quotas_path:
          gap_rows = self._build_unidentified_rows(
              user_usage=entries, quotas_path=quotas_path,
              unidentified_user=unidentified_user,
              reporting_interval=reporting_interval)
          entries.extend(gap_rows)

      if dry_run: return 0

      # 6. Chunk and upsert via upsert_disk_charge_summary()
      with management_transaction(self.session):
          for row in chunk:
              upsert_disk_charge_summary(
                  self.session,
                  activity_date=row.activity_date,
                  act_username=row.username,
                  act_projcode=row.projcode,
                  act_unix_uid=None,
                  resource_name=resource_name,
                  charges=row.charges,
                  number_of_files=row.number_of_files,
                  bytes=row.bytes,
                  terabyte_years=row.terabyte_years,
                  include_deleted_accounts=...,
              )

      # 7. display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)
  ```

`src/cli/accounting/display.py`
- Add `display_disk_dry_run_table(ctx, entries, ...)` —
  mirror the existing `display_dry_run_table` (it's already
  parameterized over an adapter, so we may be able to reuse it
  with a disk-specific adapter; if not, a small parallel helper
  is fine).

### Unidentified attribution (no synthetic User row)

**No `<unidentified>` row is added to the `users` table.** Synthetic
rows in core lookup tables leak into webapp listings, RBAC, schemas,
and access-control checks. Instead, the gap is carried entirely in
the audit columns of `disk_charge_summary`:

| Column        | Value for a normal row  | Value for a gap row                |
| ---           | ---                     | ---                                |
| `act_username`| NULL (per legacy doc)   | `'<unidentified>'`  ← audit label  |
| `act_projcode`| NULL                    | NULL                               |
| `act_unix_uid`| NULL                    | NULL                               |
| `username`    | the actual user         | **the project lead's username**    |
| `user_id`     | the actual user.id      | **the project lead's user_id**     |
| `projcode`    | resolved projcode       | resolved projcode                  |
| `account_id`  | resolved account.id     | resolved account.id                |

Every project has a guaranteed `lead` (`Project.lead` property), so
the FK side is always satisfiable. The `act_username='<unidentified>'`
literal is the only signal that the row is a synthetic
attribution — easy to grep for, never collides with a real
username, and never escapes into joins on the `users` table.

This requires a small extension to the upsert API. Today
`_upsert_storage_summary()` calls `_resolve_user(act_username,
act_unix_uid)`, which would fail when `act_username='<unidentified>'`
and there is no such user. Two options:

1. **Add a pre-resolved `user` parameter to
   `upsert_disk_charge_summary()`** — when supplied, the upsert
   skips `_resolve_user` and uses the caller-provided User
   directly. This is the cleanest extension and keeps the resolver
   side-effect-free. Apply the same pattern symmetrically to the
   archive upsert if/when needed. This is the recommended option.

2. Have the caller pass `act_username=lead.username` and live with
   losing the "this row is synthetic" signal in the audit column.
   Rejected — defeats the purpose.

→ Implement option 1: thread an optional
`user: Optional[User] = None` (and possibly
`account: Optional[Account] = None`, since the gap-row caller has
already resolved both) through `_upsert_storage_summary` and
`upsert_disk_charge_summary`. When supplied, skip the resolver
calls; otherwise behave as today. No DB schema change needed.

### Charging math (single source of truth — **deviates from legacy**)

The legacy code computed `terabyte_years` using **decimal TB
(1000⁴)** and `DAYS_IN_YEAR = 365`. That choice creates a ~10% drift
when rolling `Σ terabyte_years` up and comparing against
`Allocation.amount` (which is in **TiB**, 1024⁴).

The new command writes `terabyte_years` in **TiB-years (binary,
1024⁴)** so it composes cleanly with allocation amounts:

```
TIB = 1024 ** 4               # 1,099,511,627,776
DAYS_IN_YEAR = 365            # legacy parity (not 365.25)

terabyte_years = (bytes * reporting_interval) / DAYS_IN_YEAR / TIB
charges        = terabyte_years         # 1:1 until a Factor row says otherwise
```

Implemented once in a small `disk_usage/charging.py` helper (or
`base.py`) so the user-rows path and the `<unidentified>` gap path
share the same code.

> **Despite the column name `terabyte_years`, the values stored will
> represent tebibyte-years going forward.** Renaming the column is a
> separate change (touches the ORM, schema validation tests, and any
> consumers — left for follow-up).

#### One-shot backfill of existing rows

There are **104,541 rows** in `disk_charge_summary` (back to
2025-03-21) computed under the legacy decimal-TB convention. Mixing
legacy-TB and new-TiB rows in the same column would silently drift
allocation-balance reports.

Add a one-shot migration script
`scripts/backfill_disk_charge_summary_to_tib.py` (or an
`alembic`-style data migration if the project uses one) that
recomputes `terabyte_years` and `charges` from each row's existing
`bytes` value:

```python
# inside management_transaction(session)
for row in session.query(DiskChargeSummary).yield_per(2000):
    row.terabyte_years = row.bytes * 7 / 365 / TIB
    row.charges        = row.terabyte_years
```

The `bytes` column is unambiguous and unchanged. The `7` (reporting
interval) is hard-coded for Campaign Store today; if other disk
resources ever use a different interval the script must read it from
the source `disk_activity` rows. This is documented in the script.

Run order: ship the backfill **before** the first live `--disk` run
so all rows in the column are in the same units. The new
`upsert_disk_charge_summary` writes use TiB-years from the start.

**Impact on existing consumers**:

- `Project.get_detailed_allocation_usage()` (`src/sam/projects/projects.py`,
  per CLAUDE.md) and the matching `AllocationWithUsageSchema`: today
  they sum `terabyte_years` and compare against `Allocation.amount`
  (TiB). After the migration these are unit-consistent for the
  first time. Add a test that asserts the comparison — a known-good
  project + allocation should report `percent_used` matching the
  ratio computed directly from `bytes`.
- API endpoints `/api/v1/projects/<projcode>/charges` and
  `/api/v1/accounts/<account_id>/balance`: numbers will shift by
  ~9.95% versus pre-migration values for disk resources. Note in
  release notes; warn anyone consuming these.

### Reconciliation — `--reconcile-quota-gap` flow

For each project that appears in **both** the user-usage parse and
`cs_usage.json` FILESET, **and** for any project that appears only
in FILESET:

1. Compute `Σ user_bytes` from acct rows for that projcode.
2. Compute `quota_bytes` from FILESET (joining via fileset name →
   projcode). Mapping precedence:
   a. If any acct row has `path` matching a FILESET path → trust that
      `path → projcode`.
   b. Else look up `ProjectDirectory.path` matches in SAM.
   c. Else log a warning row and skip (do NOT silently mis-attribute).
3. If `quota_bytes − Σ user_bytes > tolerance`, emit a gap row:
   load the project's lead via `Project.lead`, then upsert with
   `act_username='<unidentified>'`,
   `user=<lead User object>` (passed as the new pre-resolved
   parameter), `account=<resolved account>`,
   `bytes=gap`, `number_of_files=0`, `terabyte_years` and `charges`
   computed from the same formula as user rows. Skip the project
   entirely (with a logged warning) if the lead cannot be resolved.
   Tolerance: 1 GiB **or** 1% of quota, whichever is larger;
   tunable via `--gap-tolerance` if needed.
4. The activity_date for these rows is the snapshot date from the
   acct file (must equal the cs_usage.json `date` field — error if
   they disagree by more than a day).

### Critical files to touch

- `src/cli/cmds/admin.py` — flag wiring (lines around 100–155, the
  `accounting` Click command def).
- `src/cli/accounting/commands.py` — replace `--disk` stub (165–167)
  with `_run_disk`; add helpers for snapshot-window validation and
  unidentified-row construction.
- `src/cli/accounting/disk_usage/{__init__,base,glade_csv}.py` — new
  parser package.
- `src/cli/accounting/display.py` — add disk dry-run table.
- `src/sam/manage/summaries.py:298–384` — extend
  `_upsert_storage_summary` and `upsert_disk_charge_summary` with
  optional pre-resolved `user`/`account` parameters (see
  "Unidentified attribution" above). Backward-compatible — existing
  callers keep working unchanged.
- *(read-only reuse)* `src/cli/accounting/quota_readers/gpfs.py` — for
  the `--reconcile-quota-gap` path we re-read the JSON, but go
  through the dict directly to avoid the suspected `_KIB` bug; we do
  NOT depend on `GpfsQuotaReader.read()` returning bytes correctly.
  (See "Out of scope" below.)

### Existing helpers to reuse (paths confirmed)

- `sam.manage.summaries.upsert_disk_charge_summary` — full
  user/project/resource/account/facility resolution and PUT-style
  upsert. (`src/sam/manage/summaries.py:377`)
- `sam.manage.transaction.management_transaction` — chunk-level
  commit/rollback. (used identically to `_run_comp`)
- `cli.accounting.display.display_import_summary` — created/updated/
  errors/skipped totals. (`src/cli/accounting/display.py`)
- `cli.core.base.BaseCommand` — provides `self.session`,
  `self.console`, `self.ctx`, `self.require_plugin`.
- `Resource.get_by_name`, `Account.get_by_project_and_resource`,
  `User.get_by_username`, `Project.get_by_projcode` — already wired
  into the upsert path; do not duplicate.

### Out of scope (for this PR)

- **Renaming `disk_charge_summary.terabyte_years` to a TiB-correct
  name** (e.g. `tebibyte_years`). The unit content changes here but
  the column name stays — rename in a follow-up that updates the
  ORM, schema validation tests, schemas, and any consumers.
- **Path-based reconciliation against `ProjectDirectory`.** Phase 2
  only applies when the simple `acct path → projcode` mapping is
  unavailable; if needed it can be added as a fallback in a
  follow-up.
- **Charging factor ≠ 1.0** (the doc hints "currently 1:1 with
  TB-Years"). If/when a `Factor` row controls disk charging, drop in
  a multiplier without changing the parser layer.
- **Reading the legacy Spring upload endpoint
  `/protected/admin/dasg/glade/upload`** in the *current* webapp.
  The Java endpoint
  (`legacy_sam/.../GladeDataRRH.java`) is documented for context;
  this PR is CLI-only and does not port the upload endpoint.

---

## Verification

### Manual smoke

```bash
source etc/config_env.sh

# Dry-run on the sample inputs sitting in the repo:
sam-admin accounting --disk \
    --resource Campaign_Store \
    --user-usage ./acct.glade.2026-04-18 \
    --quotas    ./cs_usage.json \
    --reconcile-quota-gap \
    --date 2026-04-18 \
    --dry-run -v

# Compare the dry-run table against the existing DB rows:
mysql -h 127.0.0.1 -u root -proot sam --table -e "
  SELECT projcode, username, number_of_files, bytes,
         ROUND(terabyte_years,4) AS ty, ROUND(charges,4) AS ch
  FROM disk_charge_summary
  WHERE activity_date = '2026-04-18'
  ORDER BY bytes DESC LIMIT 30"

# Live run (idempotent; existing 104,541 rows for 2026-04-18 will UPDATE in place):
sam-admin accounting --disk \
    --resource Campaign_Store \
    --user-usage ./acct.glade.2026-04-18 \
    --quotas    ./cs_usage.json \
    --reconcile-quota-gap

# Spot check a known-tricky case (cgd → cgd/amp, cgd/oce subdirs):
mysql ... -e "SELECT username, bytes FROM disk_charge_summary
              WHERE projcode IN ('CGD','NCGD0009') AND activity_date='2026-04-18'"
```

### Tests

- `tests/unit/test_disk_usage_reader.py` — new. Use a small inline
  CSV fixture; assert dataclass values, `gpfsnobody`/numeric-username
  filtering, snapshot-date parsing.
- `tests/unit/test_accounting_disk_admin.py` — new. Use the existing
  Layer-2 factories (`make_user`, `make_project`, `make_allocation`)
  to build a tiny project graph (with an explicit lead user); feed a
  5-row temp acct file + 3-key cs_usage.json; run the admin command
  via `CliRunner`; assert rows in `disk_charge_summary` (created vs
  updated counts, and the gap row has
  `act_username='<unidentified>'` AND `user_id == lead.user_id`).
- `tests/unit/test_upsert_disk_pre_resolved.py` — new. Targeted unit
  test for the `user=`/`account=` override paths in
  `upsert_disk_charge_summary` — confirms `_resolve_user` is not
  called and `act_username` is stored verbatim.
- `tests/integration/test_admin_disk_smoke.py` — subprocess `sam-admin
  accounting --disk … --dry-run` against the test DB.
- Re-run full suite: `pytest` (~65s, parallel).

### Charging-math sanity check (TiB-years)

For the `wchapman / cisl/aiml / naml0001` row in
`acct.glade.2026-04-18`:

```
col6 (KiB)              = 235,938,728,304
bytes                   = 235,938,728,304 × 1024 = 241,601,257,783,296
terabyte_years (TiB-yr) = 241,601,257,783,296 × 7 / 365 / 1024**4
                        = 4.21500…   (vs legacy decimal-TB value 4.63345)
```

Confirm after a live run that the DB row for `(2026-04-18, NAML0001,
wchapman)` shows:
- `bytes = 241,601,257,783,296` (unchanged from legacy import)
- `terabyte_years ≈ 4.21500` (was `4.63345` before the backfill)
- `charges = terabyte_years` to four decimal places

### Backfill spot-check

After running `scripts/backfill_disk_charge_summary_to_tib.py`:

```sql
-- Pick any pre-existing row, eyeball ratio:
SELECT bytes, terabyte_years,
       terabyte_years / (bytes * 7 / 365 / POW(1024,4)) AS unity
FROM disk_charge_summary
WHERE activity_date = '2026-04-18' AND username = 'wchapman'
LIMIT 5;
-- 'unity' should be ≈ 1.0 for every row.
```
