# Investigation + Remediation: `renew → edit` audit-trail corruption

Tracking under session **renew-enum-collision**.

## Context

`utils/parity/check_legacy_apis.py` reports 19 `fstree / allocationAmount`
mismatches between legacy SAM and our new implementation. CESM0002/Derecho
is the most diagnostic row:

```
Derecho/CESM0002/Derecho: legacy=926000000, new=465000000
```

Same DB, two views in legacy SAM disagree:

| Legacy view | Source | CESM0002/Derecho |
|---|---|---|
| Project Dashboard | `Allocation.amount` column (live) | 465M |
| Account Statement → Overall Usage | `DateBoundedAllocationAmount` — replays `allocation_transaction` rows | 926M |

The fstree API consumed by the parity script uses the replay path. Our new
impl reads `allocation.amount` directly. The `allocation_transaction` table
is internally inconsistent with the `allocation.amount` column for these
19 (project, resource) pairs — that's the bug.

The active 2026-05-01 → 2027-04-30 allocation tree on these projects was
created via the new "Extend Project Tree" button (our renew code in
`src/sam/manage/renew.py`), then amount-edited shortly after.

## Root cause

For CESM0002/Derecho the live row is `allocation_id=24511, amount=465M`.
Its three audit rows:

| txid  | type         | transaction_amount | comment |
|-------|--------------|--------------------|---------|
| 54513 | `NEW`        | 461,000,000        | "Allocation created" |
| 54514 | `NEW`        | 461,000,000        | "Renewed from #21664 … scaled ×0.67" |
| 54552 | `ADJUSTMENT` | **465,000,000**    | "Amount: 461000000.0 → 465000000.0" |

Legacy `DateBoundedAllocationAmount(allocation, targetDate)` (legacy_sam
`src/main/java/edu/ucar/cisl/sam/domain/allocation/DateBoundedAllocationAmount.java:40`)
replays in `creation_time` order using
`AllocationTransactionType.java`:

- `NEW` → `setAmount(transaction_amount)` (resets — idempotent)
- `ADJUSTMENT` → `addAmount(transaction_amount)` (delta)
- `SUPPLEMENT` → `addAmount(transaction_amount)` (delta)
- `EXTENSION` → end_date only
- `TRANSFER` → `addAmount(transaction_amount)` (delta, possibly negative)

Replay for 24511:
1. `NEW 461M` → 461M
2. `NEW 461M` → 461M (NEW resets, doesn't accumulate)
3. `ADJUSTMENT 465M` → 461M + 465M = **926M** ← matches the legacy fstree value

### Three interacting bugs in our write path

1. **Duplicate `NEW` row on renewal.** `src/sam/manage/renew.py:344-366`
   calls `create_allocation()`, which already logs a `CREATE` (txid 54513).
   `renew.py:354` then logs a *second* transaction of type `RENEW`
   (txid 54514). Cosmetically benign under legacy replay (`NEW` is
   idempotent), but it muddies the audit trail and breaks any consumer
   that assumes one `NEW` per allocation.

2. **`EDIT` rows store the post-edit total, not the delta.**
   `src/sam/manage/allocations.py:143` unconditionally writes
   `transaction_amount=allocation.amount` for *every* transaction type,
   including `EDIT`. Legacy semantics for `ADJUSTMENT.transaction_amount`
   are a **signed delta**. The txid 54552 comment confirms the human
   intent was +4M (`461M → 465M`). This is the bug that turns "duplicate
   NEW" from cosmetic into a 2× overstatement.

3. **Manual DB remediation by a legacy SAM admin.** George Williams,
   2026-04-23 10:03 AM:
   > "the `transaction_type` is a string in the database, but there is a
   > corresponding enum in the SAM code, and it was seeing values that
   > were not valid (CREATE, RENEW, and EDIT). I modified the
   > allocation_transaction table with appropriate values
   > (NEW, NEW, ADJUSTMENT), and the exception went away."
   This isn't a shim in our code — it's a one-off `UPDATE` to unblock
   legacy SAM. It's also the trigger that activated bug #2: rewriting our
   `EDIT` rows to `ADJUSTMENT` told legacy replay to call
   `addAmount(transaction_amount)` on a value we'd written as the
   post-edit total. Without this rewrite, the `EDIT` rows would have
   stayed unrecognized and the double-count wouldn't surface.

## Why two legacy views disagree

The DB is internally inconsistent: stored `Allocation.amount` no longer
matches the sum-of-transactions for these allocations. Project Dashboard
reads the column (correct). Account Statement → Overall Usage replays the
audit trail (corrupt → wrong). Each is self-consistent with its own data
source.

## Useful invariants for future parity checks

- `allocation.amount` should equal the legacy replay of its
  `allocation_transaction` rows (modulo `FLOAT(15,2)` precision noise of
  ~10² for sums in the 10⁸ range). Where it doesn't, the audit trail is
  malformed.
- `ADJUSTMENT.transaction_amount` is a **signed delta**. Any row where
  `|transaction_amount|` is comparable to `allocation.amount` is suspect.
- An allocation should have **exactly one** `NEW` row. Multiple `NEW`
  rows on the same `allocation_id` indicate a write-path bug.

## Files referenced

- `utils/parity/check_legacy_apis.py` — the parity driver
- `src/sam/manage/renew.py:344-366` — duplicate NEW on renew
- `src/sam/manage/allocations.py:107-152` — `log_allocation_transaction`,
  the offending line is `transaction_amount=allocation.amount` at :143
- `src/sam/accounting/allocations.py:274-289` —
  `AllocationTransactionType` enum (CREATE/EDIT/RENEW are defined but
  legacy SAM rejects them; admin rewrites them to NEW/ADJUSTMENT)
- `legacy_sam/.../domain/allocation/DateBoundedAllocationAmount.java`
- `legacy_sam/.../domain/allocation/AllocationTransactionType.java` —
  authoritative replay semantics

---

# Remediation Plan

Two streams of work, **must do both**:

- **Stream A — backfill prod data** (idempotent, repairs the 19 rows).
- **Stream B — fix the source code** (prevents recurrence).

Stream A without Stream B will be undone by the next "Extend Project
Tree" + amount-edit cycle.

## Stream A — Data backfill (append-only corrective transactions)

### Strategy: append, don't rewrite

Rather than `UPDATE` or `DELETE` historical `allocation_transaction`
rows, **append a corrective `ADJUSTMENT` row per affected allocation**
that brings the legacy replay sum back into agreement with
`allocation.amount`. The corrective row's `transaction_amount` is a
proper signed delta (which is what `ADJUSTMENT` semantics demand), so
the repair itself is well-formed even though it's compensating for a
malformed prior row.

Why append-only is preferable to rewrite-in-place:

- **Audit trail intact** — historical rows untouched; reviewer can trace
  exactly what was wrong and what was done about it via the corrective
  row's `transaction_comment`.
- **Correct semantics** — `ADJUSTMENT` of `−Δ` *is* a delta, which is
  the contract `ADJUSTMENT` is supposed to honor. We're not bending
  rules to remediate; we're using the type as designed.
- **Reversible** — a mistake can be undone by `DELETE`-ing the
  corrective row by its `creation_time`. Rewrites lose the original
  data.
- **Doesn't pollute usage views** — corrective rows live in
  `allocation_transaction`, not `charge_adjustment`. They never appear
  as fake charges on user dashboards. (The `charge_adjustment` table
  feeds `adjustedUsage`, not `allocationAmount`, so it cannot be used
  to fix this class of bug — see "Why not charge_adjustment" below.)

### Per-allocation procedure

For each affected `allocation_id`:

1. **Identify the bogus row(s).** Look for `ADJUSTMENT` rows whose
   `transaction_comment` starts with `Amount: ` AND whose
   `|transaction_amount|` is comparable to `allocation.amount` (the
   fingerprint of bug #2: stored total instead of delta). Parse
   `X → Y` from the comment to recover the intended delta.
2. **Compute the correction.**
   `correction = intended_delta − stored_transaction_amount`
   = `(Y − X) − Y`
   = `−X`
   For CESM0002/Derecho txid 54552: `−461,000,000`.
3. **Compute expected post-replay amount.** Run a Python replay over
   all existing `allocation_transaction` rows for this `allocation_id`
   plus the proposed corrective row. Confirm
   `replayed == allocation.amount` within float tolerance. If not,
   the allocation has additional anomalies (e.g. multiple EDITs at
   different intermediate values) — flag for manual review, don't
   auto-apply.
4. **Append the corrective `ADJUSTMENT`.**
   ```sql
   INSERT INTO allocation_transaction
     (allocation_id, user_id, transaction_type,
      transaction_amount, requested_amount,
      alloc_start_date, alloc_end_date,
      transaction_comment, propagated, creation_time)
   VALUES
     (:allocation_id, :admin_user_id, 'ADJUSTMENT',
      :correction, :correction,
      :alloc_start_date, :alloc_end_date,
      CONCAT('[remediation YYYY-MM-DD] correct double-counted ',
             'EDIT-as-ADJUSTMENT (txid ', :bad_txid,
             ' stored post-edit total ', :stored_amount,
             ' instead of delta ', :intended_delta, ')'),
      0, NOW());
   ```
5. **(Optional, separate phase)** clean up the duplicate `NEW` rows
   from renew. Strictly cosmetic for the replay sum (NEW is
   idempotent); skip if you want to keep this remediation maximally
   conservative. If you do clean up, prefer `UPDATE
   transaction_comment` to mark them as superseded rather than
   `DELETE`.

### Why not `charge_adjustment`?

`charge_adjustment` rows feed `adjustedUsage`, not `allocationAmount`.
A +461M `charge_adjustment` against CESM0002/Derecho would:

| Metric | Today (legacy) | After `+461M charge_adj` (legacy) |
|---|---|---|
| `allocationAmount` | 926M | **926M (unchanged)** |
| `adjustedUsage`    | ~0   | 461M (phantom usage) |
| `balance`          | 926M | 465M |

`balance` would match the new side, but `allocationAmount` (the field
the parity script checks) would still mismatch, *and* `adjustedUsage`
(currently passing) would start failing. Worse, our new side reads
`charge_adjustment` for its user-facing dashboards too, so users would
see 461M of fake "charges" on a brand-new project that has not run a
single job. Discarded.

### One-off SQL (CESM0002/Derecho only — smoke test)

```sql
START TRANSACTION;

-- Confirm the bogus row
SELECT allocation_transaction_id, allocation_id, transaction_type,
       transaction_amount, transaction_comment, creation_time
FROM allocation_transaction
WHERE allocation_transaction_id = 54552;

-- Append corrective ADJUSTMENT
INSERT INTO allocation_transaction
  (allocation_id, user_id, transaction_type,
   transaction_amount, requested_amount,
   alloc_start_date, alloc_end_date,
   transaction_comment, propagated, creation_time)
VALUES
  (24511, <admin_user_id>, 'ADJUSTMENT',
   -461000000.00, -461000000.00,
   '2026-05-01 00:00:00', '2027-04-30 23:59:59',
   '[remediation 2026-05-01] correct double-counted EDIT-as-ADJUSTMENT (txid 54552 stored post-edit total 465M instead of delta +4M)',
   0, NOW());

-- Verify replay (Python, before commit)
-- Expected sum: 461M (NEW) + 461M (NEW idempotent) + 465M (ADJ)
--             − 461M (corrective ADJ) = 465M ✓

-- COMMIT;   -- only after verification
-- ROLLBACK;
```

After commit, both legacy views (Project Dashboard reading
`allocation.amount` AND Account Statement → Overall Usage replaying
`allocation_transaction`) report 465M.

### Tooling — write a script, don't hand-craft SQL

Build a Python utility (proposed location:
`utils/remediation/fix_renew_audit_trail.py`) with these phases:

```
phase 1: discover     → SELECT candidate rows (suspicious EDIT-as-ADJ)
                        for given projcode(s) or all
phase 2: replay-check → simulate legacy replay on each affected
                        allocation_id; confirm replayed != allocation.amount
                        (proves the row needs repair); compute proposed
                        correction = -X parsed from "Amount: X → Y"
phase 3: dry-run      → print proposed INSERT(s) per allocation; show
                        before/after replay sums; flag any allocation
                        whose post-correction replay still ≠ amount
                        (manual review)
phase 4: apply        → wrap INSERTs in a transaction; require --confirm
                        flag and explicit projcode allowlist; tag every
                        corrective row with a uniform comment prefix
                        "[remediation YYYY-MM-DD]" for easy rollback
phase 5: verify       → re-run replay-check post-apply (must match);
                        call the legacy fstree API and diff vs
                        allocation.amount; re-run check_legacy_apis.py
                        (mismatch count should drop to 0 for the
                        scoped projects)
```

The replay simulator is a 10–20-line port of
`AllocationTransactionType.java`'s `execute()` methods — `NEW` resets,
`SUPPLEMENT`/`ADJUSTMENT`/`TRANSFER` add, `EXTENSION` no-ops the
amount. That's the only way to *prove* the repair before commit.

Rollback is trivial: `DELETE FROM allocation_transaction WHERE
transaction_comment LIKE '[remediation YYYY-MM-DD]%'`.

### Inventory the affected rows before scripting

```sql
-- Count of suspicious EDIT-as-ADJUSTMENT rows
SELECT COUNT(*)
FROM allocation_transaction at
JOIN allocation a ON a.allocation_id = at.allocation_id
WHERE at.transaction_type = 'ADJUSTMENT'
  AND at.transaction_comment LIKE 'Amount:%'
  AND ABS(at.transaction_amount - a.amount) < 1.0;

-- Allocations with >1 NEW row (duplicate-NEW from renew)
SELECT allocation_id, COUNT(*) AS new_count
FROM allocation_transaction
WHERE transaction_type = 'NEW'
GROUP BY allocation_id
HAVING COUNT(*) > 1;
```

Both should be empty after Stream A is complete.

## Stream B — Source-code fixes

Three changes, all in `src/sam`. Each gets its own PR / commit so the
backfill script can land in parallel.

### B1. `log_allocation_transaction` writes deltas for `EDIT`

`src/sam/manage/allocations.py:107-152`. Today (line 143):

```python
transaction_amount=allocation.amount,
requested_amount=allocation.amount,
```

For `EDIT` (and any future delta-typed transaction), `transaction_amount`
must be `new_amount - old_amount`. The function already receives
`old_values`, so:

```python
if transaction_type == AllocationTransactionType.EDIT:
    delta = (allocation.amount - old_values.get('amount', allocation.amount)
             if 'amount' in (old_values or {}) else 0.0)
    txn_amount = delta
else:
    txn_amount = allocation.amount

transaction = AllocationTransaction(
    ...,
    transaction_amount=txn_amount,
    requested_amount=allocation.amount,  # OK — that's "what was requested"
    ...
)
```

Tests: add a unit test in `tests/unit/test_query_functions.py` (or wherever
`log_allocation_transaction` is tested today) that runs an `EDIT` and
asserts `transaction_amount == new - old`. Add a replay-equivalence
test that builds a small in-memory transaction history and confirms
`replay(history) == allocation.amount`.

### B2. `renew.py` stops double-logging

`src/sam/manage/renew.py:344-366`, and the analogous block at :405-431
for children. Two options:

- **B2a (smaller diff):** drop the redundant `log_allocation_transaction(
  ..., RENEW, ...)` call after `create_allocation()` returns. Lose the
  "Renewed from #X scaled ×Y" comment in the audit trail.
- **B2b (preserve provenance):** stop calling `create_allocation()` (the
  one in `manage/`); call `Allocation.create()` (the model classmethod)
  directly, then write a single transaction of type `RENEW` with the
  full provenance comment.

Prefer **B2b** — it preserves the renewal context in the audit trail,
which is the whole reason that second log call exists.

### B3. Decide on the type-string contract with legacy SAM

This is the one that needs a product decision before code change.

- **B3a — emit only legacy strings.** Map our enum on the way out:
  `CREATE → NEW`, `RENEW → NEW`, `EDIT → ADJUSTMENT`, `EXPIRE/DELETE
  /DETACH/LINK` → ??? (no legacy equivalent — these would still throw).
  This matches what George did manually, makes the legacy fstree API
  happy for the cases it understands, but loses information at the type
  level (you can no longer distinguish a `RENEW` from a fresh `CREATE`,
  or an `EDIT` from a `SUPPLEMENT`).
- **B3b — widen legacy enum.** Get the Java side to ignore unknown
  types instead of throwing (e.g. add a `UNKNOWN` fallthrough to
  `AllocationTransactionType.fromString`). Replay then skips our
  type-extension rows. Keeps full type fidelity in our DB; needs a
  legacy SAM deploy.
- **B3c — accept the divergence.** Keep both string vocabularies in the
  same column; document that legacy SAM's replay path is not
  authoritative for allocations created post-cutover; teach the parity
  script to ignore mismatches on allocations whose history contains
  unknown-to-legacy types.

**Recommendation:** B3a in the short term (unblocks the parity script
and the legacy UI without a Java deploy), B3b once we have the appetite
for a legacy SAM change. **B3a only works if B1 lands first** —
otherwise renaming `EDIT → ADJUSTMENT` continues to corrupt the
sum-of-transactions semantics.

## Suggested ordering

1. **B1** — fix `log_allocation_transaction` to write deltas. Land
   first; everything else depends on this being correct.
2. **A** — write the backfill script. Run dry-run for the 19 fstree
   rows, then for the broader inventory queries above.
3. **B2** — drop the duplicate `NEW` log in renew. Land alongside or
   after A so the script's "delete duplicate NEWs" logic doesn't fight
   ongoing writes.
4. **A apply** — run the backfill on prod for CESM0002 first (smoke
   test), then the rest of the 19, then the broader inventory.
5. **B3** — make the type-emission decision and ship it.

## Verification

End-to-end check after each apply step:

- `python utils/parity/check_legacy_apis.py` — fstree mismatch count
  should drop monotonically; should reach 0 after A is fully applied.
- Project Dashboard and Account Statement views in legacy SAM agree
  on `allocationAmount` for CESM0002/Derecho (both 465M).
- For each repaired allocation, Python replay of its
  `allocation_transaction` rows equals `allocation.amount` within float
  tolerance.

## Open questions

(Resolve before implementation, not now.)

- For Stream B3, does CISL want the legacy SAM Java enum widened, or do
  we accept the lossy type mapping forever?
- Are there allocations corrupted by this flow that the parity script
  *doesn't* flag (e.g. the renew happened but no follow-up amount edit)?
  The inventory queries above will tell us.
- Do we need to backfill historical `SUPPLEMENT`/`TRANSFER` audit rows
  written by our code, or is the bug confined to `EDIT` and the
  duplicate-NEW from renew?
