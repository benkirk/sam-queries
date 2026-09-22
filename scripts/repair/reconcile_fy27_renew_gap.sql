-- reconcile_fy27_renew_gap.sql
--
-- Restore current allocation coverage for NCGD0073 and NMMM0082.
--
-- Cause: the FY26->FY27 renew was run with "overwrite/replace existing"
-- (replace_existing=True) on these FY-crossing projects. renew soft-deleted the
-- crossing allocation and created a full-FY27 replacement STARTING 2026-10-01, so
-- from now until 2026-09-30 the accounts have no active allocation (fstree
-- reports them "Waiting"; they cannot run/charge).
--
-- Fix: reactivate each soft-deleted crossing allocation (deleted 1->0) and
-- truncate its end_date to the FY26 boundary (2026-09-30 23:59:59) so it hands
-- off cleanly to the 2026-10-01 FY27 replacement. Amount is unchanged; the
-- corrective audit row carries transaction_amount=0.00 (a replay no-op), so the
-- replay(history)==amount invariant is preserved (verified in STEP 3).
--
-- Scope (prod allocation ids; confirm with STEP 0 before running):
--   NCGD0073 / Casper       aid 24660  (orig end 2027-05-31, amount 10000)
--   NCGD0073 / Casper GPU   aid 24658  (orig end 2027-05-31, amount 2840)
--   NMMM0082 / Derecho      aid 24559  (orig end 2027-04-30, amount 1000000)
--
-- Model: append-only, [REMEDIATION 2026-09-22]-tagged, transaction-wrapped,
-- self-verifying, UNDO documented -- mirrors reconcile_shared_link_ledger.sql.
-- Apply as a write user. Dry-run first by running only STEP 0 and STEP 3.

-- STEP 0 -- pre-check: expect exactly these 3 rows, deleted=1, future-dated end.
SELECT allocation_id, account_id, amount, start_date, end_date, deleted
FROM allocation WHERE allocation_id IN (24660, 24658, 24559);

START TRANSACTION;

-- STEP 1 -- reactivate + truncate to the FY26 boundary (expect 3 rows affected).
UPDATE allocation
   SET deleted = 0, end_date = '2026-09-30 23:59:59'
 WHERE allocation_id IN (24660, 24658, 24559) AND deleted = 1;

-- STEP 2 -- append the corrective audit row (amount 0 => replay no-op).
INSERT INTO allocation_transaction
    (allocation_id, user_id, requested_amount, transaction_amount,
     alloc_start_date, alloc_end_date, transaction_type, transaction_comment,
     propagated, creation_time)
SELECT allocation_id, 23971, amount, 0.00, start_date, end_date, 'ADJUSTMENT',
       '[REMEDIATION 2026-09-22] Reactivated (deleted 1->0) and truncated end_date to 2026-09-30 to restore FY26 coverage; gap left by renew replace_existing=True (FY27 replacement starts 2026-10-01).',
       0, NOW()
FROM allocation WHERE allocation_id IN (24660, 24658, 24559);

-- STEP 3 -- verify BEFORE committing: replay==amount and all 3 current-active.
--   replay here == SUM(transaction_amount) because each history is a single NEW
--   plus zero-amount ADJUSTMENTs (no multi-NEW reset); the authoritative check is
--   sam.accounting.allocations.replay_amount, exercised in the unit suite.
SELECT a.allocation_id, a.amount,
       (SELECT ROUND(SUM(t.transaction_amount), 2) FROM allocation_transaction t
         WHERE t.allocation_id = a.allocation_id) AS replay,
       (a.deleted = 0 AND a.start_date <= NOW() AND a.end_date >= NOW()) AS current_active
FROM allocation a WHERE a.allocation_id IN (24660, 24658, 24559);
-- expect: replay == amount for all 3; current_active = 1 for all 3.

-- STEP 4 -- commit. (Piping the whole file now APPLIES the change. To test
-- without persisting, run only STEP 0 + STEP 3, or replace COMMIT with ROLLBACK.)
COMMIT;

-- UNDO (after commit) -- restores the exact prior state:
--   UPDATE allocation SET deleted=1, end_date='2027-05-31 23:59:59' WHERE allocation_id IN (24660, 24658);
--   UPDATE allocation SET deleted=1, end_date='2027-04-30 23:59:59' WHERE allocation_id=24559;
--   DELETE FROM allocation_transaction
--    WHERE allocation_id IN (24660, 24658, 24559)
--      AND transaction_comment LIKE '[REMEDIATION 2026-09-22]%';
