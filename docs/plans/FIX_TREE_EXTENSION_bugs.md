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

## Decisions locked in

- **Single PR, multiple commits.** All work lands in one PR; commits are
  ordered so each is independently reviewable and revertable. Stream B
  (source fixes) commits land before Stream A (backfill) so the prod
  data fix can't be undone by the next renew + edit.
- **B3a — emit only legacy strings.** Our write path will only ever
  insert `transaction_type` ∈ `{NEW, ADJUSTMENT, SUPPLEMENT, EXTENSION,
  TRANSFER}`. The richer Python-side intent (CREATE/RENEW/EDIT/DELETE/
  DETACH/LINK) is preserved via a `[TAG]` prefix in
  `transaction_comment`. Coexists peacefully with legacy SAM's enum
  validator; no Java change required.
- **Scope: CESM0002 tree only.** The user confirms the only project
  touched by the new "Extend Project Tree" flow + amount-edit cycle was
  CESM0002. Stream A targets that tree exclusively; the broader
  inventory queries are kept as a *post-fix sanity check* (expected
  empty), not as a remediation target.
- **Prod write access available, dry-run first.** The user has prod DB
  write credentials. Every Stream A apply step is preceded by a
  Python-replay dry-run that *proves* the corrective row produces
  `replayed_amount == allocation.amount` before any `INSERT`.

## Two streams

- **Stream B — fix the source code** (prevents recurrence). Lands first.
- **Stream A — backfill the CESM0002 tree** (corrects already-written
  rows). Lands after B is reviewed.

## Stream A — Data backfill (CESM0002 tree, append-only corrective transactions)

### Scope

Per the user: the only project touched by the new "Extend Project Tree"
flow + amount-edit cycle was **CESM0002**. Stream A targets that tree
exclusively (root project + all inheriting children, all resources).
The 19 fstree mismatches in the parity report are all (CESM0002 or
descendant project) × resource pairs.

The broader inventory queries (suspicious EDIT-as-ADJUSTMENT count,
duplicate-NEW count) are run as a *post-fix sanity check* (expected to
return zero outside the CESM0002 tree); if they don't, that's a
separate finding to escalate, not in scope for this PR.

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

Build a Python utility at `utils/remediation/fix_cesm0002_audit_trail.py`
(name reflects its scope) with these phases:

```
phase 1: discover     → for projcode='CESM0002' (and all inheriting
                        descendants via project tree), SELECT candidate
                        rows (suspicious EDIT-as-ADJ + duplicate NEWs)
phase 2: replay-check → simulate legacy replay on each affected
                        allocation_id; confirm replayed != allocation.amount
                        (proves the row needs repair); compute proposed
                        correction = -X parsed from "Amount: X → Y"
phase 3: dry-run      → print proposed INSERT(s) per allocation; show
                        before/after replay sums; flag any allocation
                        whose post-correction replay still ≠ amount
                        (manual review)
phase 4: apply        → wrap INSERTs in a transaction; require
                        --confirm flag and an explicit
                        --projcode=CESM0002 (no default — refuses to
                        run without it); tag every corrective row with
                        a uniform comment prefix "[REMEDIATION
                        YYYY-MM-DD]" for easy rollback
phase 5: verify       → re-run replay-check post-apply (must match);
                        call the legacy fstree API and diff vs
                        allocation.amount; re-run check_legacy_apis.py
                        (mismatch count should drop to 0 for CESM0002
                        rows); run inventory queries against the rest
                        of the DB (expected to return zero)
```

The replay simulator is a 10–20-line port of
`AllocationTransactionType.java`'s `execute()` methods — `NEW` resets,
`SUPPLEMENT`/`ADJUSTMENT`/`TRANSFER` add, `EXTENSION` no-ops the
amount. That's the only way to *prove* the repair before commit.

The replay simulator is the same code that ships with B3 (the legacy
helper used by the new history-view), so it lives in
`src/sam/accounting/allocations.py` (alongside the enum), not in the
remediation script — the script imports it. This keeps the canonical
replay logic in one place and unit-tests it once.

Rollback is trivial: `DELETE FROM allocation_transaction WHERE
transaction_comment LIKE '[REMEDIATION YYYY-MM-DD]%'`.

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

Three commits, all in `src/sam`, ordered so each is independently
reviewable. The whole stream lands before Stream A.

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

### B3. Emit only legacy strings; preserve intent via comment tags

**Decision: B3a.** The `transaction_type` column will only ever contain
the five legacy strings (`NEW`, `ADJUSTMENT`, `SUPPLEMENT`, `EXTENSION`,
`TRANSFER`). The Python-side `AllocationTransactionType` enum continues
to express richer intent (CREATE, RENEW, EDIT, DELETE, DETACH, LINK,
EXPIRE) at the call site, but `log_allocation_transaction` translates
to the legacy vocabulary on write and prepends a `[TAG]` to
`transaction_comment` so the high-level intent is recoverable.

#### Mapping table

Verified against current call-sites (`grep AllocationTransactionType\.`
returns these 9 active types; ADJUSTMENT/SUPPLEMENT/NEW have no direct
Python callers — they're produced exclusively via this mapping).

| Python intent | Legacy `transaction_type` | Comment tag | Replay effect | Notes |
|---|---|---|---|---|
| `CREATE` | `NEW` | (none) | `setAmount`, `setDates` | natural fit; default for `create_allocation()` |
| `RENEW` | `NEW` | `[RENEW]` | `setAmount`, `setDates` | semantically a creation; tag carries provenance ("Renewed from #X") |
| `EDIT` | `ADJUSTMENT` | (none) | `addAmount(delta)` | **requires B1 first** — `transaction_amount` must be `(new − old)` |
| `EXPIRE` | `EXTENSION` | (none) | `setEndDate` | semantically "set end_date to earlier"; no Python callers today, but defined for future |
| `DELETE` | `ADJUSTMENT` | `[DELETE]` | `addAmount(0)` (no-op) | `allocation.deleted=1` is the source of truth; this row is purely audit; `transaction_amount=0` |
| `DETACH` | `ADJUSTMENT` | `[DETACH]` | `addAmount(0)` (no-op) | parent_allocation_id change; doesn't affect amount; `transaction_amount=0` |
| `LINK` | `ADJUSTMENT` | `[LINK]` | `addAmount(0)` (no-op) | parent_allocation_id change; doesn't affect amount; `transaction_amount=0` |
| `TRANSFER` | `TRANSFER` | (none) | `addAmount(delta)` | natural fit; both sides of the exchange |
| `EXTENSION` | `EXTENSION` | (none) | `setEndDate` | natural fit (already used by `extend.py`) |
| `ADJUSTMENT` | `ADJUSTMENT` | (none) | `addAmount(delta)` | natural fit |
| `SUPPLEMENT` | `SUPPLEMENT` | (none) | `addAmount(delta)` | natural fit |

The `[TAG]` prefix uses square brackets so it's unambiguous to parse
back out and visually distinct from human-written comments (which often
start with capitalized words like `"Renewed from..."`,
`"End date extended..."`, etc.).

#### Implementation

In `src/sam/manage/allocations.py`'s `log_allocation_transaction`, add
a translation step before the `AllocationTransaction(...)` constructor:

```python
# Map Python intent → legacy DB string + optional comment tag
_LEGACY_TYPE_MAP = {
    AllocationTransactionType.CREATE:    ("NEW",        None),
    AllocationTransactionType.RENEW:     ("NEW",        "RENEW"),
    AllocationTransactionType.EDIT:      ("ADJUSTMENT", None),
    AllocationTransactionType.EXPIRE:    ("EXTENSION",  None),
    AllocationTransactionType.DELETE:    ("ADJUSTMENT", "DELETE"),
    AllocationTransactionType.DETACH:    ("ADJUSTMENT", "DETACH"),
    AllocationTransactionType.LINK:      ("ADJUSTMENT", "LINK"),
    AllocationTransactionType.TRANSFER:  ("TRANSFER",   None),
    AllocationTransactionType.EXTENSION: ("EXTENSION",  None),
    AllocationTransactionType.ADJUSTMENT:("ADJUSTMENT", None),
    AllocationTransactionType.SUPPLEMENT:("SUPPLEMENT", None),
    AllocationTransactionType.NEW:       ("NEW",        None),
}

db_type, tag = _LEGACY_TYPE_MAP[transaction_type]
if tag:
    final_comment = f"[{tag}] {final_comment}" if final_comment else f"[{tag}]"
```

For `DELETE`/`DETACH`/`LINK` — types that don't change `amount` but get
mapped to `ADJUSTMENT` — the writer must also force
`transaction_amount = 0.0` so legacy replay's `addAmount` is a no-op.
This is naturally consistent with B1 (deltas), since the actual amount
delta for these operations is zero.

The Python-side enum stays as-is (call sites unchanged); only the
write-translation is new. The enum keeps acting as the canonical
vocabulary for *intent*; the DB column is the legacy *storage format*.

#### Reading back

For new readers that want to recover intent (e.g. allocation history
view in the new web UI), parse `transaction_comment` for `^\[(\w+)\]`
to recover the tag. Untagged rows are exactly the legacy semantics.
A small helper `parse_intent(txn) -> AllocationTransactionType` in
`accounting/allocations.py` keeps this in one place.

#### Validation

A new test asserts the invariant: `SELECT DISTINCT transaction_type
FROM allocation_transaction` should remain a subset of the five legacy
strings after our code writes any transaction. Add this as both:

1. A unit test that exercises every `AllocationTransactionType` value
   through `log_allocation_transaction` and asserts the resulting row's
   `transaction_type` is in the legacy set.
2. A migration-time integrity check (one-shot SQL) that confirms no
   non-legacy values exist in the table at the moment B3 lands.

**B3 only works if B1 lands first** — otherwise renaming `EDIT →
ADJUSTMENT` continues to corrupt sum-of-transactions semantics. Commit
ordering enforces this.

## Commit sequence (single PR)

All commits land in one PR on a feature branch (suggested name:
`fix-renew-audit-trail`). Each commit is independently reviewable;
the PR is **not** merged until prod backfill is verified green.

| # | Commit | Files | Rationale |
|---|---|---|---|
| 1 | **B1**: `log_allocation_transaction` writes signed deltas for `EDIT` | `src/sam/manage/allocations.py`, tests | Fix bug #2 first — everything downstream assumes deltas are correct |
| 2 | **B2**: `renew.py` stops double-logging | `src/sam/manage/renew.py`, tests | Fix bug #1; one `NEW` per allocation going forward |
| 3 | **B3**: legacy-string mapping + `[TAG]` comment scheme | `src/sam/manage/allocations.py`, `src/sam/accounting/allocations.py`, tests | After B1+B2; introduces `_LEGACY_TYPE_MAP` and the `parse_intent()` reader; adds replay simulator co-located with the enum |
| 4 | **A1**: backfill script (dry-run only) | `utils/remediation/fix_cesm0002_audit_trail.py`, tests | Discover + replay-check + dry-run modes; no `--confirm` path yet, or `--confirm` raises if not explicitly enabled |
| 5 | **A2**: enable `--confirm` apply path | same file | Gate behind `--projcode=CESM0002` allowlist; uniform `[REMEDIATION YYYY-MM-DD]` tag |
| 6 | **A3**: prod apply log + verification artifacts | docs only (e.g. `docs/remediation/CESM0002_2026-05-XX.md`) | Capture the actual run output: before/after replay, fstree API diffs, `check_legacy_apis.py` results |

### Pre-prod gate (between commits 5 and 6)

The user runs the backfill script against prod with these phases, in
order, with explicit user confirmation between each:

1. **Discover (read-only)** — `python -m utils.remediation.fix_cesm0002_audit_trail --projcode CESM0002 --discover`
   Lists all candidate rows under the CESM0002 tree. User reviews.
2. **Dry-run (read-only)** — `… --dry-run` runs the full replay-check
   pipeline and prints proposed `INSERT`s with before/after replay
   sums. User reviews; must confirm every flagged row.
3. **Apply (writes prod)** — `… --confirm --projcode CESM0002` runs
   inside a single DB transaction; refuses to run without explicit
   `--projcode` and `--confirm`. On any post-apply replay mismatch,
   `ROLLBACK` automatically.
4. **Verify** — re-run discover + replay-check (should report nothing
   to fix), hit legacy fstree API for CESM0002 manually, run
   `python utils/parity/check_legacy_apis.py` (CESM0002 rows should be
   gone from the mismatch list).
5. **Inventory sweep (read-only)** — run the global inventory queries
   from "Inventory the affected rows before scripting" against the
   rest of the DB; both should return zero. If non-zero, escalate as
   a separate finding.

Only after all five steps pass does commit A3 (verification artifacts)
land and the PR is approved for merge.

## Verification

End-to-end check after each apply step:

- `python utils/parity/check_legacy_apis.py` — fstree mismatch count
  should drop monotonically; should reach 0 after A is fully applied.
- Project Dashboard and Account Statement views in legacy SAM agree
  on `allocationAmount` for CESM0002/Derecho (both 465M).
- For each repaired allocation, Python replay of its
  `allocation_transaction` rows equals `allocation.amount` within float
  tolerance.

## Resolved decisions

- **Single PR, multiple commits.** Commits 1–6 above; PR not merged
  until prod backfill verifies green.
- **B3a (legacy strings + `[TAG]` comments) is the long-term answer.**
  No need to widen the legacy Java enum; the two implementations
  coexist on shared storage, with our column values constrained to the
  legacy vocabulary. If/when legacy SAM is decommissioned, the mapping
  layer can be retired and the Python enum stored directly.
- **Scope is CESM0002 tree.** No broader backfill in this PR. Inventory
  queries are post-fix sanity checks only.

## Residual open questions

(Resolve during PR review, not blockers for starting work.)

- Should the `parse_intent()` reader be exposed on the `AllocationTransaction`
  model (e.g. `txn.intent` hybrid property) or live as a free function?
  Lean toward hybrid property — fits existing model patterns
  (`is_active`, etc.) and gives templates one-line access.
- For commit ordering: do B1 + B2 + B3 land as three commits with
  separate review, or fold into one "rewrite of `log_allocation_transaction`"
  commit? The table above splits them; reviewer preference may collapse.
- Cost of commit A3 (a markdown doc capturing the prod run): worth
  keeping for audit purposes, or just reference the PR description?
  Leaning keep — gives future investigators a concrete trail back to
  the corrective rows by date.
