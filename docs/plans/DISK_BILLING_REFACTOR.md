# Disk Billing Refactor — Execution Plan

## Context

`docs/plans/DISK_BILLING_REFACTOR.md` already lays out the *what* and the
*why* for the deferred Option-3 follow-up to the disk-charging cutover.
Today every row of a `sam-admin accounting --disk` import is multiplied
by the same hard-coded `--reporting-interval=7`. That is the entire
defense against under- or over-billing on cadence changes, missed
snapshots, and out-of-order backfills.

This plan turns that doc into an execution-ready PR: replace the
per-import constant with a per-row interval inferred at write time
from the prior `disk_charge_summary.activity_date` for the same
account, falling back to `--reporting-interval` only when no prior
row exists (bootstrap).

The goal of this document is to specify the implementation precisely
enough to execute (file:line landings, function shapes, test cases)
without further design discussion. On approval, port the plan back
to `docs/plans/DISK_BILLING_REFACTOR.md`, replace the implementation
outline in §"What this plan does" with the concrete steps below, and
proceed to coding.

---

## Design

### Where the math moves

Today, charging math runs **once before the chunk loop** at
`src/cli/accounting/commands.py:482–485`:

```python
# ---- 6. Charging math ------------------------------------------
for e in entries:
    e.terabyte_years = tib_years(e.bytes, reporting_interval)
    e.charges = e.terabyte_years
```

For normal rows, `account_id` is **not** known at that point — it is
resolved lazily inside the chunk loop via `_resolve_for_row()` at
line 620. The interval depends on `account_id` (each account has its
own prior snapshot), so the math has to move.

**Move the per-row math into the chunk loop, immediately after
account resolution.** The pre-loop block becomes a no-op and is
deleted; the chunk loop computes `terabyte_years` per row using a
lazy `prior_date_by_account` cache.

### Lookup query

```python
prior_date = (
    self.session.query(func.max(DiskChargeSummary.activity_date))
    .filter(
        DiskChargeSummary.account_id == acct_id,
        DiskChargeSummary.activity_date < snap_date,   # critical: <, not <=
    )
    .scalar()
)
interval_days = (snap_date - prior_date).days if prior_date else fallback_interval
```

The `< snap_date` filter is the safety bar against the idempotency
delete: `_run_disk` block 7b at lines 538–572 removes all
`(resource, snap_date)` rows **before** the chunk loop runs, so a
re-import sees the actually-prior snapshot, not the row about to be
overwritten. `<` (strict) instead of `<=` is also what protects
backfill out of order — importing Apr 11 *after* Apr 18 has been
imported finds Apr 11's prior as Apr 04, not Apr 18.

### Cache shape

```python
prior_date_by_account: dict[int, Optional[date]] = {}
# Sentinel: presence in the dict means "we asked"; value None means
# "no prior" (use fallback).

def _interval_for(account_id: int) -> int:
    if account_id not in prior_date_by_account:
        prior_date_by_account[account_id] = (
            self.session.query(func.max(DiskChargeSummary.activity_date))
            .filter(
                DiskChargeSummary.account_id == account_id,
                DiskChargeSummary.activity_date < snap_date,
            )
            .scalar()
        )
    prior = prior_date_by_account[account_id]
    return (snap_date - prior).days if prior else reporting_interval
```

Lazy because account_ids for normal rows are not known up-front.
Multiple users on the same project share an account, so the cache
deduplicates queries within an import — typical Campaign_Store
imports (~2000 rows, ~150 distinct accounts) will issue ~150 lookups
instead of 2000.

### Gap rows

`_build_unidentified_disk_rows` at `commands.py:678–823` constructs
`DiskUsageEntry` instances with `account_override=account` already
set (line 812). It currently passes `reporting_interval` through to
the entry (line 808) but the value is overwritten by the now-deleted
block 6 anyway — so the field is effectively dead.

Gap rows flow through the same chunk loop as normal rows. They take
the `account_override` path at `commands.py:609–613` which sets
`account_for_upsert = row.account_override` before the upsert. The
new per-row math runs after that branch, using
`account_for_upsert.account_id` to populate the same cache. No
special-case logic.

**Cleanup**: drop the unused `reporting_interval` argument from
`_build_unidentified_disk_rows` (the kwarg at line 686, the call site
at line 469, and the constructor field on `DiskUsageEntry`). One
fewer dead parameter to confuse future readers.

### Flag semantics

`--reporting-interval` (admin.py:204–206) goes from "uniform per-row
interval" to "bootstrap fallback used only on the first-ever row for
an account." Keep the flag and default of `7`; rewrite the help text:

```
'[disk] Bootstrap interval in days, used only when no prior '
'snapshot exists for an account. Steady-state imports infer the '
'per-row interval from the gap to the previous snapshot.'
```

---

## Files to change

### `src/cli/accounting/commands.py`

1. **Delete** block 6 at lines 482–485 (the pre-loop `for e in entries`).
2. **Inside `_run_disk`**, before the chunk loop (around line 574),
   declare `prior_date_by_account: dict[int, Optional[date]] = {}` and
   define a small `_interval_for(account_id)` closure that reads /
   populates the cache. `from sqlalchemy import func` if not already.
3. **Inside the chunk loop**, after `_resolve_for_row()` succeeds (or
   `account_override` is taken — i.e., right after the
   `account_for_upsert` assignment at lines 613 and 620), compute:
   ```python
   interval_days = _interval_for(account_for_upsert.account_id)
   row.terabyte_years = tib_years(row.bytes, interval_days)
   row.charges = row.terabyte_years
   ```
   Then call `upsert_disk_charge_summary(...)` as today.
4. **Drop the `reporting_interval` argument** to
   `_build_unidentified_disk_rows` (parameter at line 686, call site at
   line 469, field on `DiskUsageEntry`).

### `src/cli/cmds/admin.py`

Update the help text on the `--reporting-interval` Click option at
lines 204–206 to the bootstrap-fallback wording above.

### `src/sam/summaries/disk_summaries.py`

No change. `tib_years(bytes_, reporting_interval_days)` keeps its
signature; the caller selects the interval per row.

### `src/cli/accounting/disk_usage/base.py`

Remove the `reporting_interval` field from `DiskUsageEntry` (the
field is set at `commands.py:808` when constructing gap rows; once
the math moves into the chunk loop, the field is dead).

---

## Tests

### New: `tests/unit/test_disk_inferred_interval.py`

End-to-end tests against `_run_disk` via `CliRunner`. Reuse
`_build_campaign_store_graph`, `_write_acct`, `_write_quotas`,
`runner`, and `mock_db_session` from
`tests/unit/test_accounting_disk_admin.py:36–103`. Each case seeds
prior `DiskChargeSummary` rows directly via `session.add()` (not via
the CLI) so the test pins one variable at a time, then runs one CLI
invocation and asserts the resulting `terabyte_years`.

| case | seed | import | expected `terabyte_years` |
|---|---|---|---|
| Steady weekly | row at `snap-7` | snap | `bytes × 7 / 365 / 1024⁴` |
| Steady daily | row at `snap-1` | snap | `bytes × 1 / 365 / 1024⁴` |
| Cadence change weekly→daily | row at `snap-7` | snap | `bytes × 7 / 365 / 1024⁴` (first daily after weekly covers prior week) |
| Missed week | row at `snap-14` | snap | `bytes × 14 / 365 / 1024⁴` |
| Backfill out of order | row at `snap+7` (later snapshot already imported), row at `snap-7` | snap | `bytes × 7 / 365 / 1024⁴` (must NOT pick up `snap+7`) |
| Bootstrap (no prior) | none | snap | `bytes × 7 / 365 / 1024⁴` (fallback to flag default) |
| Bootstrap with non-default flag | none | snap, `--reporting-interval=3` | `bytes × 3 / 365 / 1024⁴` |
| Re-import idempotency | row at `snap-7`, row at `snap` | snap (re-import) | `bytes × 7 / 365 / 1024⁴` (the `snap` row is deleted before the lookup runs) |

The "backfill out of order" case is the load-bearing one — it proves
the strict `<` filter. The "re-import idempotency" case proves the
delete-then-lookup ordering at lines 538–572 is preserved.

Use `pytest.approx(expected, abs=1e-7)` for the `terabyte_years`
comparison (DB column is FLOAT, ~7 decimal digits) — match the
existing tolerance at `test_accounting_disk_admin.py:148`.

### Extend: `tests/unit/test_accounting_disk_admin.py`

Add one multi-import test that runs the CLI twice with different
snapshot dates 8 days apart and asserts the second import's row uses
interval = 8, not 7. Exercises the full CLI path twice rather than
mixing seeded rows with one CLI run.

### `tests/unit/test_disk_charging_math.py`

No change. Pure formula tests for `tib_years()` are already pinned;
this refactor does not touch `tib_years`.

---

## Verification

### Unit-level

```
pytest tests/unit/test_disk_inferred_interval.py \
       tests/unit/test_accounting_disk_admin.py \
       tests/unit/test_disk_charging_math.py -v
```

All eight new cases plus the extended multi-import case pass.

### Live cross-check

After landing, on the next two consecutive `--disk` imports:

```sql
SELECT activity_date,
       account_id,
       terabyte_years,
       bytes,
       ROUND(terabyte_years * 365 * POW(1024,4) / bytes) AS inferred_days
FROM disk_charge_summary
WHERE activity_date >= '<post-feature-date>'
ORDER BY activity_date DESC, account_id
LIMIT 40;
```

`inferred_days` must equal the actual gap between consecutive
snapshot dates for that account (typically 7).

### Idempotency

Re-run the same `--disk` import twice in a row. Diff the
`terabyte_years` column across both runs — must be byte-identical.
This is the regression test that the strict `<` filter and the
delete-before-lookup ordering survive.

### Bootstrap honesty

After landing, run `--disk` against a freshly-created account that
has no prior `disk_charge_summary` rows. The first-ever row uses the
flag default (7). This is the only operator-tunable case and is
preserved.

---

## What is NOT in this PR

- `LAG()` read-time integration helper. Demoted to optional debug
  helper in `sam.queries.charges` if a use case ever shows up;
  currently no consumer needs it.
- `terabyte_years` → `tebibyte_years` column rename. Separate, smaller
  PR; mechanical sed across the six SUM call sites.
- Backfilling pre-`DISK_CHARGING_TIB_EPOCH` rows. Per
  `DISK_CHARGING.md`, those stay in legacy decimal-TB-yr forever.
- Touching the six cumulative-billing SUM call sites
  (`sam.queries.charges:349`, `sam.queries.dashboard:909`/`:996`,
  `sam.accounting.calculator:54`, `sam.schemas.allocation:213`,
  `sam.projects.projects:766`). They remain correct because the
  per-row `terabyte_years` is now correct under all cadence cases.

---

## Rollout

One PR off `disk_charging`. Commits, in order:

1. **refactor**: move `_run_disk` charging math into the chunk loop
   (no behavior change yet — `_interval_for` always returns the
   flag value). Existing tests still pass.
2. **feat**: implement the `prior_date_by_account` lookup inside
   `_interval_for`. Tests in `test_disk_inferred_interval.py` go from
   skipped to passing.
3. **chore**: drop the dead `reporting_interval` field from
   `DiskUsageEntry` and the kwarg from `_build_unidentified_disk_rows`.
4. **docs**: update help text on `--reporting-interval`; rewrite the
   "Implementation outline" section of
   `docs/plans/DISK_BILLING_REFACTOR.md` to reflect the landed
   design (drop the "future work" framing).

Each commit independently passes `pytest`. Operator action on next
deploy is zero — the flag still exists, the default is unchanged,
the column is unchanged. The only operator-visible diff is the help
text and the fact that operator-forgot-to-flip-the-flag bugs are no
longer possible.
