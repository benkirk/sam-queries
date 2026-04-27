# Disk Billing Refactor — Future Work

This is the deferred follow-up to the disk-charging cutover landed
in `docs/plans/DISK_CHARGING.md`. That plan delivered **Option 2**
(current-snapshot read path + epoch cutover to TiB-years for new
rows) and noted a deeper redesign of cumulative billing as Option 3.

After re-evaluation, **Option 3 collapses to a single targeted
change**: infer the reporting interval per row from the prior
successful snapshot for the same account, instead of using the
hard-coded `--reporting-interval=7` constant. The originally-proposed
`LAG()`-based read-time integration is dropped.

---

## Why we are not doing the read-time integration anymore

The original Option 3 draft proposed:

1. Stop pre-multiplying `terabyte_years` at ingest.
2. Replace every `SUM(charges)` call site with a `LAG(activity_date)`
   integration query.
3. Bump the DB minimum version.
4. Rename the `terabyte_years` column.

That design was motivated by three real edge cases — cadence
changes, missing snapshots, backfills — none of which the current
hard-coded-`7`-at-ingest path handles correctly.

But every one of those edge cases can be fixed at *write* time by
inferring the interval from the prior snapshot. If we do that:

- The six existing SUM call sites are correct without any change.
- No DB version bump is required.
- No column rename is required.
- No second epoch is required.
- No consumer-side defensive code is required.

The read-time integration's only remaining value-add is
**partial-period interrogation between snapshots** (today vs the
last snapshot, before the next one lands). That question is already
answered by Option 2's `Account.current_disk_usage()` and the
schema's `current_used_*` fields — in TiB rather than TiB-years,
which is what dashboards actually want.

**Decision**: drop the `LAG()` rewrite as a workstream. Keep the
integration logic in mind as an optional debug helper in
`sam.queries.charges` if a real use case shows up.

---

## What this plan does

### Where the magic constant lives today

```
src/cli/cmds/admin.py          --reporting-interval default 7
src/cli/accounting/commands.py reporting_interval: int = 7
                               tib_years(bytes, reporting_interval)  ← uniform across rows
```

Every row of a single import gets the same interval. The legacy
SAM doc itself flagged the fragility:

> *"If the snapshot frequency changes (e.g., to daily), this value
> must be updated to `1` to prevent 7× over-billing."*

A single hard-coded constant is the entire defense against under- or
over-billing.

### Write-time inferred interval

Replace the per-import constant with a per-row lookup at write time.
For each row in `_run_disk`:

```python
# Pre-load once per import (one query per chunk, populated lazily by account_id).
prior_date = (
    session.query(func.max(DiskChargeSummary.activity_date))
    .filter(
        DiskChargeSummary.account_id == acct_id,
        DiskChargeSummary.activity_date < snap_date,
    )
    .scalar()
)
interval_days = (snap_date - prior_date).days if prior_date else fallback_interval
terabyte_years = tib_years(bytes, interval_days)
charges        = terabyte_years
```

The `< snap_date` filter is critical: the existing `_run_disk`
deletes all `(resource, snap_date)` rows before the chunked insert,
so the lookup needs to find the snapshot *before* the one being
imported, not the row about to be overwritten.

### Properties under each cadence case

| case                                     | behavior                                                                                         |
|---                                       |---                                                                                               |
| Steady weekly cadence                    | Every interval = 7 → identical to today's hard-coded path.                                       |
| Steady daily cadence (operator forgot to flip flag) | Every interval = 1 → correct without operator action.                                  |
| Cadence change (weekly → daily)           | First daily snapshot's interval = 7 (covers the prior week); subsequent rows = 1.                |
| Missed snapshot day                      | Next snapshot's interval = (gap-with-missed-day) → bytes count for the missed window.            |
| Backfill out of order (Apr 11 imported after Apr 18) | The `< :snap_date` filter makes Apr 11's prior = Apr 04 (or earlier), not Apr 18.       |
| Bootstrap (first ever row for account)   | Falls back to `--reporting-interval` default (7). One-shot operator concern only.                |
| New account joining mid-stream           | First row uses bootstrap interval. Bytes-for-the-prior-period are genuinely unknown — bootstrap is honest. |
| Idempotent re-import (same date)         | `_run_disk` deletes existing rows for `(resource, snap_date)` before insert; the lookup then sees the actually-prior snapshot. |

---

## Implementation outline

### Files to touch

- `src/cli/accounting/commands.py` — modify `_run_disk` to pre-load
  `prior_date_by_account: dict[int, date]` from
  `disk_charge_summary` for the accounts the import will touch,
  then compute `interval_days` per row inside the chunk loop.
- `src/cli/cmds/admin.py` — update `--reporting-interval` help text
  to clarify it is a bootstrap-only fallback (not a per-row constant
  anymore).
- `src/sam/summaries/disk_summaries.py` — no signature change;
  `tib_years(bytes_, reporting_interval_days)` is unchanged. The
  caller selects the interval per row.

### Tests to add

- `tests/unit/test_disk_inferred_interval.py` — new:
  - Steady weekly cadence: imports across 4 weeks, all intervals = 7.
  - Steady daily cadence: imports across 4 days, all intervals = 1.
  - Cadence change: weekly snapshots followed by daily; first daily
    interval = 7, subsequent = 1.
  - Missed day: weekly snapshot, then a 14-day gap; the second
    snapshot's interval = 14.
  - Backfill out of order: import Apr 18, then Apr 11; Apr 11's
    interval is the gap to its actual predecessor (Apr 04 or
    bootstrap), NOT a negative number from Apr 18.
  - Bootstrap: first-ever row for a fresh account uses
    `--reporting-interval` default.
  - Re-import idempotency: re-running Apr 18 finds Apr 11 (not the
    deleted Apr 18 row) as prior, interval stays 7.
- `tests/unit/test_accounting_disk_admin.py` — extend with a
  multi-import scenario that verifies the second snapshot's rows
  use `(date2 - date1).days` as the interval.

### What is NOT changing

- The cumulative-billing query path (six SUM call sites surveyed
  in `DISK_CHARGING.md`) — no consumer touches required.
- The `disk_charge_summary.terabyte_years` column name — column
  rename to `tebibyte_years` is a separate, smaller, future PR.
- The `DISK_CHARGING_TIB_EPOCH = 2026-04-18` cutover — unchanged.
- The `Allocation.amount` ↔ `terabyte_years` unit alignment — unchanged.

---

## Verification

### Unit-level

```python
# Worked example: weekly cadence with one missed week
seed(account=A, date=2026-04-11, bytes=B)
seed(account=A, date=2026-04-25, bytes=B)   # 14 days later, missed Apr 18
import the second snapshot via _run_disk
assert row.terabyte_years == B * 14 / 365 / 1024**4
```

### Live cross-check

After landing this change, on the next `--disk` import:

```sql
SELECT activity_date,
       terabyte_years,
       ROUND(terabyte_years * 365 * POW(1024,4) / bytes) AS inferred_interval_days
FROM disk_charge_summary
WHERE activity_date >= '<post-feature-date>'
ORDER BY activity_date DESC, account_id LIMIT 20;
```

`inferred_interval_days` should equal the actual gap between
snapshot dates (typically 7; whatever the snapshot cadence is in
practice).

### Idempotency

Re-running the same date twice must produce identical
`terabyte_years` values across both runs (no off-by-one from
seeing the deleted-and-replaced row as "prior").

---

## Out of scope

- The pure `LAG()` read-time integration query. Demoted from
  primary recommendation to optional debug helper if a future use
  case demands it.
- The `terabyte_years` → `tebibyte_years` column rename. Separate
  PR, untouched here.
- Backfilling pre-epoch (decimal-TB-yr) rows. Per
  `DISK_CHARGING.md`, those stay in legacy units forever.
