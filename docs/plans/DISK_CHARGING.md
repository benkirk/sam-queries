# Plan: `sam-admin accounting --disk` for `disk_charge_summary` updates

## Context

The legacy Java/Hibernate path that populated `disk_charge_summary`
from per-user-per-project disk snapshots has been retired. We have:

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
- A `DiskChargeSummaryStatus` table
  (`src/sam/summaries/disk_summaries.py:48–59`) — defined with a
  `current` boolean keyed by `activity_date`, but **never written or
  read by anything in the current codebase**. This plan resurrects it.

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
project-level bytes to the project lead, labeled with
`act_username='<unidentified>'`, so SAM books match GPFS truth.

This plan also addresses two long-standing issues in how
`disk_charge_summary` is *consumed*:

- A unit mismatch (decimal TB-years stored vs binary TiB allocations)
  that produced silent ~10% drift in allocation balance reports.
- A missing "current usage right now" query path. Today every
  consumer SUMs `terabyte_years` (or `charges`) over the allocation
  window — correct for cumulative billing, but not what dashboards
  want when they answer "you're 47/50 TiB full *today*."

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

The legacy Java doc was correct about `KiB`. Verified by exact match
against an existing DB row:

```
acct row:   wchapman / cgd/amp / number_of_files=1361584 / col6=142,788,904,480
DB row:     wchapman / NCGD0009 / number_of_files=1361584 / bytes=146,215,838,187,520
ratio:      146,215,838,187,520 / 142,788,904,480 = 1024.000000  ← exact
ty check:   146,215,838,187,520 × 7 / 365 / 1e12 = 2.8041  ← matches DB 2.80413938
```

So the unit map is:

| Source                                  | Unit       |
|---                                       |---|
| `acct.glade` column 6 (`fileSizeTotal`) | **KiB**    |
| `cs_usage.json` `FILESET[*].usage`/`USR[*].usage` | **KiB** |
| `disk_activity.bytes`                   | bytes (= col6 × 1024) |
| `disk_charge_summary.bytes`             | bytes (sum of activity bytes) |
| `disk_charge_summary.terabyte_years` (pre-epoch rows)  | **TB-years (decimal, 10¹²)** ← legacy convention |
| `disk_charge_summary.terabyte_years` (post-epoch rows) | **TiB-years (binary, 1024⁴)** ← new convention |
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

**Reconciliation gap is small but real**

Reading FILESET vs Σ(per-user acct rows):

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

**Consumption survey of `DiskChargeSummary`**

Six call sites aggregate this table; **all six SUM `.charges` over a
date range** (cumulative billing semantics). No call site uses
`MAX(activity_date)` or otherwise reads "the latest snapshot only":

| File:line                                          | Function                                | Intent  |
|---                                                 |---                                      |---|
| `src/sam/accounting/calculator.py:54`              | `calculate_charges()`                   | billing |
| `src/sam/schemas/allocation.py:213`                | `AllocationWithUsageSchema._get_charges_by_resource_type()` | billing |
| `src/sam/queries/charges.py:349`                   | `get_daily_charge_trends_for_accounts()`| billing |
| `src/sam/queries/dashboard.py:909`                 | `get_resource_charges_by_date()` (subtree) | billing |
| `src/sam/queries/dashboard.py:996`                 | `get_resource_charges_by_date()` (single account) | billing |
| `src/sam/projects/projects.py:766`                 | `Project.get_subtree_charges()`         | billing |

`DiskChargeSummaryStatus.current` is defined but never written and
never read. It's the obvious mechanism for marking the latest
snapshot — this plan finally wires it.

---

## Approach (Option 2)

This plan delivers a clean **post-cutover** disk-charging pipeline
without touching any pre-cutover row, and adds a missing **current-usage**
read path while leaving cumulative-billing math alone.

Three deliverables:

1. A new `_run_disk()` import that produces TiB-year rows from
   `acct.glade` + `cs_usage.json`, with `<unidentified>` reconciliation.
2. A **cutover epoch** (`DISK_CHARGING_TIB_EPOCH`) — a single date
   constant pinning when the unit convention switches. Pre-epoch rows
   stay in decimal TB-years (left untouched); post-epoch rows are
   TiB-years.
3. A new "current usage" path: `_run_disk()` updates
   `DiskChargeSummaryStatus.current`; new `Account.current_disk_usage()`
   / `Project.current_disk_usage()` methods read it; the disk side of
   `AllocationWithUsageSchema` exposes new `current_used_*` fields
   alongside the existing cumulative `used`.

The cumulative-billing query path is **unchanged in this PR.**
A redesign that integrates `bytes` over time (Option 3) is summarized
below as future work.

### CLI surface (entry point `src/cli/cmds/admin.py`)

Extend the existing `accounting` Click command (no new top-level command):

```
sam-admin accounting --disk \
    --resource Campaign_Store \
    --user-usage ./acct.glade.2026-04-18 \
    --quotas    ./cs_usage.json \
    [--reporting-interval 7] \
    [--unidentified-label <unidentified>] \
    [--reconcile-quota-gap] \
    [--gap-tolerance "1GiB|1%"] \
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
- `--reporting-interval` default `7` (legacy spec).
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
- The CLI **refuses to write rows with `activity_date <
  DISK_CHARGING_TIB_EPOCH`** with a clear error pointing at the epoch
  constant. We will not retroactively rewrite legacy data via this
  command.

### Files to add / modify (concrete paths)

#### NEW — disk usage parsers (object-oriented, pluggable)

`src/cli/accounting/disk_usage/__init__.py`
- Exports `get_disk_usage_reader(resource_name, path)` and the
  dataclass `DiskUsageEntry(activity_date, projcode, username,
  number_of_files, bytes, directory_path, reporting_interval, cos)`.
- Mirror `quota_readers/__init__.py`'s registry pattern.

`src/cli/accounting/disk_usage/base.py`
- `DiskUsageReader` ABC with `path: str`, `snapshot_date: date | None`,
  `read() -> list[DiskUsageEntry]`.
- `DiskUsageEntry` dataclass.

`src/cli/accounting/disk_usage/glade_csv.py`
- `GladeCsvReader(DiskUsageReader)` — parses `acct.glade.YYYY-MM-DD`.
- Columns: `(date, path, projcode, username, nfiles, fsize_kib, reporting_interval, cos)`.
- **`bytes = fsize_kib × 1024`** (KiB → bytes), per legacy convention.
- Skips `gpfsnobody` and any row where `username` is purely numeric
  (legacy "uid was never resolved" rows).
- Honors a strict mode where unknown projcodes/usernames raise — the
  CLI's `--skip-errors` decides whether to abort or continue.

`src/cli/accounting/disk_usage/charging.py`
- Holds the single charging-math source of truth (see
  "Charging math" below). Exposes
  `tib_years(bytes_, reporting_interval) -> float`.

#### NEW — cutover epoch and constants

`src/sam/summaries/disk_constants.py` (or fold into
`disk_summaries.py`)
- `DISK_CHARGING_TIB_EPOCH: date` — the first `activity_date` whose
  row is written in TiB-year units. Initially set to the date of the
  first `--disk` run; once committed, treat as immutable.
- `BYTES_PER_TIB = 1024 ** 4`
- `DAYS_IN_YEAR = 365`

#### MODIFY

`src/cli/cmds/admin.py` (the `accounting` Click command)
- Add the new options (`--user-usage`, `--quotas`,
  `--reporting-interval`, `--unidentified-label`,
  `--reconcile-quota-gap`, `--gap-tolerance`).
- Forward them to `AccountingAdminCommand.execute(...)` when `--disk`
  is set.
- Validate mutual exclusion (`--machine` is HPC-only;
  `--user-usage` is required for `--disk`; `--reconcile-quota-gap`
  requires `--quotas`).

`src/cli/accounting/commands.py`
- Replace the `--disk` stub at lines 165–167 with `_run_disk()`,
  modeled on `_run_comp` (lines 177–302). Pseudocode:

  ```python
  def _run_disk(self, resource_name, user_usage_path, quotas_path,
                reporting_interval, unidentified_label, reconcile_gap,
                gap_tolerance, date_window,
                *, dry_run, skip_errors, chunk_size, verbose):
      reader = get_disk_usage_reader(resource_name, user_usage_path)
      entries = reader.read()
      if not entries: return 0

      self._assert_snapshot_in_window(reader.snapshot_date, date_window)
      self._assert_post_epoch(reader.snapshot_date)   # refuse pre-epoch dates

      if reconcile_gap and quotas_path:
          gap_rows = self._build_unidentified_rows(
              user_usage=entries, quotas_path=quotas_path,
              unidentified_label=unidentified_label,
              gap_tolerance=gap_tolerance,
              reporting_interval=reporting_interval)
          entries.extend(gap_rows)

      # Compute terabyte_years / charges per row (TiB-year math)
      for e in entries:
          e.terabyte_years = tib_years(e.bytes, reporting_interval)
          e.charges        = e.terabyte_years

      if verbose: display_disk_dry_run_table(self.ctx, entries)
      if dry_run: return 0

      # Chunked upsert via upsert_disk_charge_summary()
      with management_transaction(self.session):
          for chunk in chunked(entries, chunk_size):
              for row in chunk:
                  upsert_disk_charge_summary(
                      self.session,
                      activity_date=row.activity_date,
                      act_username=row.act_username,    # may be the unidentified label
                      act_projcode=None,
                      act_unix_uid=None,
                      resource_name=resource_name,
                      charges=row.charges,
                      number_of_files=row.number_of_files,
                      bytes=row.bytes,
                      terabyte_years=row.terabyte_years,
                      user=row.user_override,           # set for gap rows only
                      account=row.account_override,     # set for gap rows only
                  )
          # Mark this snapshot as "current" — clears the prior current flag
          mark_disk_snapshot_current(self.session, reader.snapshot_date)

      display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)
  ```

`src/cli/accounting/display.py`
- Add `display_disk_dry_run_table(ctx, entries, ...)` — mirror the
  existing `display_dry_run_table`.

`src/sam/manage/summaries.py:298–384`
- Extend `_upsert_storage_summary` and
  `upsert_disk_charge_summary` with optional pre-resolved
  `user: Optional[User]` and `account: Optional[Account]`
  parameters. When supplied, skip `_resolve_user`/`_resolve_account`.
  Backward-compatible — existing callers keep working unchanged.

`src/sam/summaries/disk_summaries.py`
- Add a small helper `mark_disk_snapshot_current(session,
  activity_date)`:
  - Set `DiskChargeSummaryStatus.current = False` on any prior row
    where `current = True`.
  - Upsert `(activity_date, current=True)` for the new snapshot.
  - Same `management_transaction` as the data writes.

`src/sam/accounting/accounts.py`
- Add `Account.current_disk_usage(session) ->
  Optional[CurrentDiskUsage]` returning a small dataclass
  `(activity_date, bytes, terabyte_years, number_of_files)`. Joins
  on `DiskChargeSummaryStatus.current = True` first; falls back to
  `MAX(activity_date)` only if the status table has no `current` row
  (defensive — should not happen post-cutover). Returns `None` for
  accounts whose resource is not a disk resource.

`src/sam/projects/projects.py`
- Add `Project.current_disk_usage(session, resource_name=None)`
  returning a dict keyed by resource name (parallel to
  `get_detailed_allocation_usage`). Internally iterates each disk
  account and calls `Account.current_disk_usage`. Result schema:
  `{"Campaign_Store": {"activity_date": …, "bytes": …,
  "current_used_tib": …, "number_of_files": …}}`.

`src/sam/schemas/allocation.py` — `AllocationWithUsageSchema`
- For disk allocations only, add fields:
  - `current_used_bytes` (int)
  - `current_used_tib` (float, derived = bytes / 1024⁴)
  - `current_snapshot_date` (date)
  - `current_pct_used` (float, derived = current_used_tib /
    allocation.amount × 100)
  These are `null` for non-disk resources. The cumulative `used` /
  `remaining` / `percent_used` fields are unchanged.

Webapp consumers (templates / dashboards) **are not modified in this
PR.** The new schema fields are additive; consuming them is a
follow-up.

### Charging math (single source of truth — **deviates from legacy**)

```
BYTES_PER_TIB = 1024 ** 4       # 1,099,511,627,776
DAYS_IN_YEAR  = 365             # legacy parity (not 365.25)

terabyte_years = (bytes * reporting_interval) / DAYS_IN_YEAR / BYTES_PER_TIB
charges        = terabyte_years          # 1:1 until a Factor row says otherwise
```

Implemented once in `src/cli/accounting/disk_usage/charging.py` so the
user-rows path and the `<unidentified>` gap path share identical code.

> **Despite the column name `terabyte_years`, post-epoch values
> represent tebibyte-years.** Renaming the column is a separate
> change (touches the ORM, schema validation tests, and any
> consumers — left for follow-up).

### Cutover epoch — no backfill

There are 104,541 pre-existing rows (back to 2025-03-21) computed
under the legacy decimal-TB convention. **We deliberately leave them
alone.**

- A single constant `DISK_CHARGING_TIB_EPOCH` (a `date`) marks the
  cutover. Every row with `activity_date >= EPOCH` is in TiB-years;
  every row with `activity_date < EPOCH` is in legacy decimal
  TB-years.
- `_run_disk()` refuses to write rows with `activity_date < EPOCH`
  (clear error, no surprises).
- The first `--disk` run sets `EPOCH` to the snapshot date being
  imported. Pick the first date whose import we want in TiB; commit
  the constant; treat it as immutable thereafter.

**Accepted consequences of leaving legacy data alone:**

- **Cumulative-billing rollups that span the epoch will mix units.**
  An allocation whose window straddles `EPOCH` will read pre-epoch
  rows in decimal TB-years and post-epoch rows in TiB-years, and SUM
  them as if they were the same unit. The error per-row is ~9.95%
  on the pre-epoch portion only.
- For most allocations this is acceptable because:
  - Allocations whose window is entirely pre- or post-epoch are
    self-consistent.
  - The new "current usage" path (which is the dashboard-facing
    answer for most users) is post-epoch by construction and
    unambiguous.
  - The drift is bounded and known — easy to call out in release
    notes.
- An optional helper view / query that converts pre-epoch rows
  on-the-fly may be added later if the mixed-window case proves
  noisy in practice. Not in this PR.

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

This requires the small upsert-API extension described above
(pre-resolved `user`/`account` parameters), so the resolver does not
fail on the audit label.

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
   `user=<lead User object>`, `account=<resolved account>`,
   `bytes=gap`, `number_of_files=0`, `terabyte_years` and `charges`
   computed from the same formula as user rows. Skip the project
   entirely (with a logged warning) if the lead cannot be resolved.
   Default tolerance: 1 GiB **or** 1% of quota, whichever is larger;
   tunable via `--gap-tolerance`.
4. The activity_date for these rows is the snapshot date from the
   acct file (must equal the cs_usage.json `date` field — error if
   they disagree by more than a day).

### Critical files to touch

- `src/cli/cmds/admin.py` — flag wiring on the `accounting` Click
  command.
- `src/cli/accounting/commands.py` — replace `--disk` stub (165–167)
  with `_run_disk`; add helpers for snapshot-window validation,
  epoch enforcement, and unidentified-row construction.
- `src/cli/accounting/disk_usage/{__init__,base,glade_csv,charging}.py`
  — new parser package + charging-math helper.
- `src/cli/accounting/display.py` — add disk dry-run table.
- `src/sam/summaries/disk_summaries.py` — add
  `mark_disk_snapshot_current()` helper; add the
  `DISK_CHARGING_TIB_EPOCH` constant (or split into a
  `disk_constants.py`).
- `src/sam/manage/summaries.py:298–384` — extend
  `_upsert_storage_summary` and `upsert_disk_charge_summary` with
  optional pre-resolved `user`/`account` parameters.
- `src/sam/accounting/accounts.py` — add
  `Account.current_disk_usage()`.
- `src/sam/projects/projects.py` — add `Project.current_disk_usage()`.
- `src/sam/schemas/allocation.py` — add
  `current_used_bytes`/`current_used_tib`/`current_snapshot_date`/
  `current_pct_used` to `AllocationWithUsageSchema` (disk-only,
  null otherwise).

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
- `cli.accounting.quota_readers.gpfs.GpfsQuotaReader` — reuse for
  the FILESET parse on the `--reconcile-quota-gap` path. Its
  `_KIB = 1024` is correct.

### Out of scope (for this PR)

- **Backfilling pre-epoch rows.** Deliberate — see "Cutover epoch"
  above. Old rows stay in decimal TB-years, untouched.
- **Renaming `disk_charge_summary.terabyte_years` to a TiB-correct
  name** (e.g. `tebibyte_years`). Unit content changes here but the
  column name stays — rename in a follow-up.
- **Updating webapp templates / dashboards** to consume the new
  `current_used_*` schema fields. The schema gains the fields; UI
  follow-up is separate.
- **Path-based reconciliation against `ProjectDirectory`** as the
  primary mapping. We use it only as a fallback when the acct
  `path → projcode` mapping is unavailable.
- **Charging factor ≠ 1.0.** If/when a `Factor` row controls disk
  charging, drop in a multiplier without changing the parser layer.
- **Reading the legacy Spring upload endpoint
  `/protected/admin/dasg/glade/upload`** in the *current* webapp.
  The Java endpoint (`legacy_sam/.../GladeDataRRH.java`) is documented
  for context; this PR is CLI-only.
- **Option 3 (integrate-bytes-over-time billing).** Summarized below
  as future work.

---

## Future work — Option 3: integrate-bytes-over-time billing

This PR delivers **Option 2** (current-snapshot path + epoch cutover
for new rows). The deeper redesign of *cumulative billing* is
deferred to a follow-up. Capturing the design intent here so we
don't lose context.

### Why Option 3 exists

Comp/DAV/Archive activity is *flow*: a row records new charges
accruing on a given day. Summing flow over a window gives the
correct total.

Disk activity is *stock*: each daily/weekly row is a full inventory
snapshot. Pre-multiplying by `reporting_interval / 365` at ingest
bakes the time dimension into the row, so summing works **only if**
every snapshot's interval exactly tiles the window with no gaps and
no overlaps. The legacy doc itself flags this fragility:

> *"If the snapshot frequency changes (e.g., to daily), this value
> must be updated to `1` to prevent 7× over-billing."*

A single hard-coded `7` in the import path is the entire defense
against under- or over-billing. There is no consumer-side check.

### What Option 3 changes

Stop pre-multiplying at ingest. Store **bytes per snapshot only**;
let the billing query integrate over time using the actual gap
between consecutive snapshots:

```
billing_TiB_years
  = Σ over snapshots i in window:
        (bytes_i / 1024⁴)
        × (date(snapshot_{i+1}) − date(snapshot_i)) / 365
```

The last snapshot in the window uses `min(window_end, today)` as its
right edge. Missing days are absorbed into the next observed
snapshot (or warned about). Mixed cadences (weekly archive +
daily current) compose correctly without a magic constant.

### What needs to change for Option 3

1. **Stop writing the pre-multiplied column at ingest.** Either
   leave `terabyte_years` NULL post-Option-3-epoch, or repurpose it
   to mean "TiB at snapshot" (= `bytes / 1024⁴`) and rename.
2. **Replace the six SUM call sites** (see "Consumption survey"
   above) with the integration query. Likely one helper in
   `src/sam/queries/charges.py` that takes
   `(account_ids, start, end)` and returns TiB-years computed from
   `bytes` + `LAG(activity_date)` window function. MySQL/MariaDB
   support `LAG` (MariaDB ≥ 10.2 / MySQL ≥ 8.0).
3. **Update `AllocationWithUsageSchema._get_charges_by_resource_type`**
   to call the new helper for disk resources.
4. **Update `Project.get_detailed_allocation_usage` /
   `get_subtree_charges`** to route disk through the integration
   helper.
5. **Update `get_daily_charge_trends_for_accounts`** to either
   keep returning per-day occupancy (already correct — it doesn't
   sum bytes, it groups by date) or expose a parallel "TiB-year per
   day" series computed from interval-deltas.
6. **Decide the fate of `disk_activity` / `disk_charge`** —
   they currently store the per-snapshot numbers we'd be reading
   from. Either keep them as the source of truth and stop deriving
   `disk_charge_summary` at all, or keep `disk_charge_summary` as a
   denormalized view but with `terabyte_years` derived at read time.
7. **Tests**: add fixtures with deliberately uneven cadences (a
   missing Wednesday, a daily-then-weekly transition, a snapshot
   straddling allocation boundaries) and assert billing math
   matches a hand-computed integral.
8. **Migration story** for the existing data: same cutover-epoch
   approach probably makes sense, with a second epoch
   `DISK_CHARGING_INTEGRATION_EPOCH`. Pre-epoch rows are read by
   the legacy SUM; post-epoch by the integration helper. A wrapper
   query handles allocations that straddle.

### Why we are not doing it now

- It touches every disk consumer in the codebase (six SUM call
  sites, two schemas, dashboards).
- Combining it with the unit switch (TB → TiB) and the
  `<unidentified>` reconciliation in the same PR makes any
  regression hard to bisect.
- Option 2 already gives webapp dashboards the answer they
  actually want for the common "how full are we right now" question.

A separate plan (`docs/plans/DISK_BILLING_INTEGRATION.md`) should be
written when work begins. The current plan deliberately keeps
billing math *bug-for-bug compatible* with the legacy approach
post-epoch, modulo the unit switch.

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

# Live run — date must be >= DISK_CHARGING_TIB_EPOCH or we refuse:
sam-admin accounting --disk \
    --resource Campaign_Store \
    --user-usage ./acct.glade.2026-04-18 \
    --quotas    ./cs_usage.json \
    --reconcile-quota-gap

# Confirm the snapshot was marked current:
mysql -h 127.0.0.1 -u root -proot sam --table -e "
  SELECT * FROM disk_charge_summary_status WHERE current = 1"

# Confirm a known row is now in TiB-years:
mysql -h 127.0.0.1 -u root -proot sam --table -e "
  SELECT projcode, username, bytes,
         ROUND(terabyte_years,5)                               AS ty_stored,
         ROUND(bytes * 7 / 365 / POW(1024,4), 5)               AS ty_expected_tib,
         ROUND(terabyte_years / (bytes * 7 / 365 / POW(1024,4)), 5) AS unity
  FROM disk_charge_summary
  WHERE activity_date = '2026-04-18' AND username = 'wchapman'"
# 'unity' should be ≈ 1.0 for every post-epoch row.

# Spot check the gap row:
mysql ... -e "
  SELECT projcode, username, act_username, bytes
  FROM disk_charge_summary
  WHERE activity_date = '2026-04-18' AND act_username = '<unidentified>'
  LIMIT 5"
```

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
- `bytes = 241,601,257,783,296`
- `terabyte_years ≈ 4.21500`
- `charges = terabyte_years` to four decimal places

### Tests

- `tests/unit/test_disk_usage_reader.py` — new. Inline CSV fixture;
  assert dataclass values, `gpfsnobody`/numeric-username filtering,
  snapshot-date parsing, KiB→bytes conversion.
- `tests/unit/test_disk_charging_math.py` — new. Single-purpose unit
  test for `tib_years()`. Pin the formula constants and the
  `wchapman` worked example.
- `tests/unit/test_accounting_disk_admin.py` — new. Use Layer-2
  factories (`make_user`, `make_project`, `make_allocation`) to
  build a project with an explicit lead; feed a 5-row temp acct
  file + 3-key cs_usage.json; run the admin command via
  `CliRunner`; assert:
  - rows in `disk_charge_summary` have correct created/updated counts
  - the gap row has `act_username='<unidentified>'` AND
    `user_id == lead.user_id`
  - `disk_charge_summary_status` has exactly one row with
    `current=True` matching the snapshot date.
- `tests/unit/test_upsert_disk_pre_resolved.py` — new. Targeted
  unit test for the `user=`/`account=` override paths in
  `upsert_disk_charge_summary` — confirms `_resolve_user` is not
  called and `act_username` is stored verbatim.
- `tests/unit/test_current_disk_usage.py` — new. Seed two
  snapshots (different dates) for the same account; mark only the
  later as `current=True`; assert
  `Account.current_disk_usage()` returns the later row's bytes,
  not a sum.
- `tests/unit/test_allocation_schema_disk_current.py` — new. Pin
  the new `current_used_*` fields on `AllocationWithUsageSchema`
  for a disk allocation.
- `tests/unit/test_disk_epoch_enforcement.py` — new. CLI run with
  a snapshot date < EPOCH must error cleanly without writing.
- `tests/integration/test_admin_disk_smoke.py` — subprocess
  `sam-admin accounting --disk … --dry-run` against the test DB.
- Re-run full suite: `pytest` (~65s, parallel).
