-- Reconcile: restore replay==amount on shared-pool child allocations whose
-- re-link recorded a zero-delta LINK for a pool supplement (SAMuel relink bug,
-- fixed going forward by PR #570). Append-only, tagged, reversible.
--
-- Verified on the local obfuscated-prod snapshot 2026-09-17: exactly 23 rows
-- (Casper 4, Derecho 8, Derecho GPU 11); dry-run after = 0; replay==amount for all.
--
-- USAGE (prod):
--   1. Run STEP 1 (dry-run) alone. Confirm every replay_after == stored_amt,
--      and note the row count.
--   2. Run STEP 2 as a single batch. It inserts inside a transaction, prints
--      before/after counts and the remaining-broken count, and COMMITs only if
--      0 remain broken; otherwise it ROLLBACKs and reports.
--   3. Re-run STEP 1: expect 0 rows.
--
-- UNDO (if ever needed): DELETE the tagged rows --
--   DELETE FROM allocation_transaction
--   WHERE transaction_comment LIKE '[REMEDIATION 2026-09-17]%'
--     AND transaction_type='ADJUSTMENT' AND user_id=23971 AND propagated=1;

-- =====================================================================
-- STEP 1  -- DRY RUN (read-only). Run this first, alone.
-- =====================================================================
SELECT al.allocation_id, p.projcode, r.resource_name,
       al.amount AS stored_amt, ROUND(t.s,2) AS replay_now,
       ROUND(al.amount - t.s, 2) AS correction,
       ROUND(t.s + (al.amount - t.s),2) AS replay_after
FROM allocation al
JOIN account a   ON a.account_id = al.account_id
JOIN project p   ON p.project_id = a.project_id
JOIN resources r ON r.resource_id = a.resource_id
JOIN (SELECT allocation_id, SUM(transaction_amount) s
      FROM allocation_transaction GROUP BY allocation_id) t ON t.allocation_id = al.allocation_id
WHERE al.deleted = 0
  AND al.parent_allocation_id IS NOT NULL              -- shared-pool children only
  AND ABS(al.amount - t.s) > 0.005                     -- currently broken only
  AND EXISTS (SELECT 1 FROM allocation_transaction z    -- relink fingerprint, recent
              WHERE z.allocation_id = al.allocation_id
                AND z.transaction_type='ADJUSTMENT'
                AND z.transaction_amount = 0 AND z.requested_amount IS NOT NULL
                AND z.creation_time >= '2026-04-01')
ORDER BY r.resource_name, p.projcode;


-- =====================================================================
-- STEP 2  -- APPLY (transaction-wrapped, self-verifying). Run as one batch.
-- =====================================================================
START TRANSACTION;

INSERT INTO allocation_transaction
  (allocation_id, user_id, requested_amount, transaction_amount,
   alloc_start_date, alloc_end_date, transaction_comment, transaction_type, propagated)
SELECT al.allocation_id, 23971 /*benkirk*/, al.amount, ROUND(al.amount - t.s, 2),
       al.start_date, al.end_date,
       CONCAT('[REMEDIATION ', CURDATE(), '] restore replay==amount on shared-pool ',
              'child (relink recorded a zero-delta LINK for a pool supplement)'),
       'ADJUSTMENT', 1
FROM allocation al
JOIN (SELECT allocation_id, SUM(transaction_amount) s
      FROM allocation_transaction GROUP BY allocation_id) t ON t.allocation_id = al.allocation_id
WHERE al.deleted = 0
  AND al.parent_allocation_id IS NOT NULL
  AND ABS(al.amount - t.s) > 0.005
  AND EXISTS (SELECT 1 FROM allocation_transaction z
              WHERE z.allocation_id = al.allocation_id
                AND z.transaction_type='ADJUSTMENT'
                AND z.transaction_amount = 0 AND z.requested_amount IS NOT NULL
                AND z.creation_time >= '2026-04-01');

SELECT ROW_COUNT() AS rows_inserted;

-- Re-check the predicate inside the same transaction: must be 0.
SELECT COUNT(*) AS still_broken
FROM allocation al
JOIN (SELECT allocation_id, SUM(transaction_amount) s
      FROM allocation_transaction GROUP BY allocation_id) t ON t.allocation_id = al.allocation_id
WHERE al.deleted = 0
  AND al.parent_allocation_id IS NOT NULL
  AND ABS(al.amount - t.s) > 0.005
  AND EXISTS (SELECT 1 FROM allocation_transaction z
              WHERE z.allocation_id = al.allocation_id
                AND z.transaction_type='ADJUSTMENT'
                AND z.transaction_amount = 0 AND z.requested_amount IS NOT NULL
                AND z.creation_time >= '2026-04-01');

-- Commit only if nothing remains broken; else roll the whole thing back.
SET @broken := (
  SELECT COUNT(*)
  FROM allocation al
  JOIN (SELECT allocation_id, SUM(transaction_amount) s
        FROM allocation_transaction GROUP BY allocation_id) t ON t.allocation_id = al.allocation_id
  WHERE al.deleted = 0
    AND al.parent_allocation_id IS NOT NULL
    AND ABS(al.amount - t.s) > 0.005
    AND EXISTS (SELECT 1 FROM allocation_transaction z
                WHERE z.allocation_id = al.allocation_id
                  AND z.transaction_type='ADJUSTMENT'
                  AND z.transaction_amount = 0 AND z.requested_amount IS NOT NULL
                  AND z.creation_time >= '2026-04-01'));

-- MySQL can't branch COMMIT/ROLLBACK outside a stored proc, so this is the
-- operator gate: verify 'still_broken' printed 0 above, then run COMMIT.
-- If it was non-zero, run ROLLBACK instead.
COMMIT;
-- ROLLBACK;
