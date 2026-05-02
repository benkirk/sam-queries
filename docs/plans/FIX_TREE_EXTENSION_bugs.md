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

## Stream A — Data backfill

### Strategy

For every (project, resource) in the parity report (start with the 19
fstree mismatches; the same flow likely affects more projects that
parity didn't flag because the `EDIT` happened before the renew or
without a follow-up edit):

1. **Find the corrupted rows** — `ADJUSTMENT` rows whose
   `transaction_comment` starts with `Amount: ` AND whose
   `transaction_amount` equals the allocation's current `amount` (a
   strong fingerprint of bug #2: stored total instead of delta).
2. **Parse the delta** from the comment (`Amount: X → Y` → delta = Y − X).
3. **Repair**: rewrite the row to `transaction_amount = Y − X`, keep
   `transaction_type = 'ADJUSTMENT'`. Don't change comment or
   creation_time. (Alternative: rewrite type to `NEW` and leave amount
   alone — works for the simple one-edit case but breaks if the
   allocation has >1 `EDIT` row, see "single-row UPDATE" caveat below.)
4. **Optionally delete the duplicate `NEW`** from renew (the one with
   `transaction_comment LIKE 'Renewed from%'` when an `Allocation
   created` row already exists for the same `allocation_id`). This
   cleans the audit trail; it's not strictly required to fix the
   replay sum because `NEW` is idempotent.
5. **Verify**: replay the surviving rows in Python (mirroring the legacy
   semantics) and confirm `replayed_amount == allocation.amount` within
   float tolerance. Then re-run `utils/parity/check_legacy_apis.py`.

### Tooling — write a script, don't hand-craft SQL

Build a Python utility (proposed location: `utils/remediation/fix_renew_audit_trail.py`)
with these phases:

```
phase 1: discover    → SELECT candidate rows for given projcode(s) or all
phase 2: dry-run     → print proposed UPDATEs and DELETEs, no DB writes
phase 3: replay-check → simulate legacy replay on each affected
                        allocation_id, confirm replayed == allocation.amount
                        BOTH before (should mismatch) AND after (should match)
phase 4: apply       → wrap UPDATEs/DELETEs in a transaction;
                        require --confirm flag and projcode allowlist
phase 5: verify      → re-run replay-check post-apply, then call the
                        legacy fstree API and diff vs allocation.amount
```

The replay simulator should be a port of `AllocationTransactionType.java`'s
modify functions (10–20 lines of Python) — that's the only way to
*prove* the repair before you commit.

### One-off SQL (for CESM0002/Derecho only)

Useful as a smoke test before investing in the script:

```sql
START TRANSACTION;

-- Verify candidate first
SELECT allocation_transaction_id, allocation_id, transaction_type,
       transaction_amount, transaction_comment
FROM allocation_transaction
WHERE allocation_transaction_id = 54552;

-- Repair (Option A: rewrite as a proper delta)
UPDATE allocation_transaction
SET transaction_amount = 4000000.00,
    transaction_comment = CONCAT('[remediation 2026-05-01] ',
                                 transaction_comment)
WHERE allocation_transaction_id = 54552;

-- Confirm via replay (manual check) before committing
-- COMMIT;  -- only if replay shows 465M
-- ROLLBACK;
```

**Caveat — single-row UPDATE doesn't generalize.** Rewriting type
`ADJUSTMENT` → `NEW` works for CESM0002 only because there's exactly
one `EDIT` row and its stored amount happens to equal the current
total. For allocations with >1 `EDIT` (intermediate `Y` values), only
the last EDIT-as-ADJUSTMENT stores the current total; rewriting all of
them to `NEW` makes last-NEW-wins land on an intermediate value.
**Always rewrite to a proper delta, not to `NEW`.**

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
