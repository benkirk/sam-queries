-- ncgd0006_extend_overlap.sql
--
-- Undo the 2026-09-30 Extend that laid the FY26 allocations of the NCGD0006
-- tree over the FY27 renewals.
--
-- Cause: an operator ran Admin -> Extend allocations from NCGD0073 (2026-09-30
-- 15:56 MDT, user 19513). Extend always walks from the tree root, so it pushed
-- every FY26 allocation in the NCGD0006 tree from 2026-09-30 to 2027-05-31:
-- 70 EXTENSION rows, txn 57837-57906, 15 projects, 6 resources. Each of those
-- accounts already held its FY27 allocation (2026-10-01 -> 2027-09-30, renewed
-- 2026-09-22), so two allocations were live on every account from 2026-10-01.
-- Extend had no check for an existing later allocation.
--
-- Fix: put the 70 end dates back to 2026-09-30 23:59:59, with one EXTENSION
-- audit row each (legacy replay takes the end date from EXTENSION rows;
-- amounts are untouched, so replay == amount holds). The FY27 rows are not
-- touched; NCGD0073's FY27 allocations already end 2027-09-30.
--
-- Model: append-only, [REMEDIATION 2026-10-01]-tagged, transaction-wrapped,
-- self-verifying, UNDO documented. Keyed on the 70 txn ids AND the current
-- end dates, so a rerun is a no-op. Needs SELECT/INSERT/UPDATE only (the write
-- user has no CREATE TEMPORARY TABLES): the audit rows go first and the UPDATE
-- follows them.
-- Never commits itself: `source` it interactively, check STEP 3, then type COMMIT.
-- A direct write does not flush the webapp cache: run `sam-admin cache --refresh`.

-- STEP 0 -- pre-check: expect 70 rows, all ending 2027-05-31 23:59:59, each
--   with fy27_rows = 1.
SELECT a.allocation_id, a.account_id, a.start_date, a.end_date, a.parent_allocation_id,
       (SELECT COUNT(*) FROM allocation n WHERE n.account_id = a.account_id
          AND n.deleted = 0 AND n.start_date = '2026-10-01 00:00:00'
          AND n.end_date = '2027-09-30 23:59:59') AS fy27_rows
FROM allocation_transaction t
JOIN allocation a ON a.allocation_id = t.allocation_id
WHERE t.allocation_transaction_id BETWEEN 57837 AND 57906
ORDER BY a.account_id;

START TRANSACTION;

-- STEP 1 -- one EXTENSION audit row per allocation being reverted (expect 70).
--   user_id 23971 = benkirk; change it if someone else applies this.
INSERT INTO allocation_transaction
  (allocation_id, user_id, transaction_type, transaction_amount,
   alloc_start_date, alloc_end_date, propagated, transaction_comment)
SELECT a.allocation_id, 23971, 'EXTENSION', a.amount,
       a.start_date, '2026-09-30 23:59:59', t.propagated,
       CONCAT('End date reverted 2027-05-31 -> 2026-09-30, undoing txn ',
              t.allocation_transaction_id,
              ' [REMEDIATION 2026-10-01] Extend from NCGD0073 overlapped the FY27 renewals')
FROM allocation_transaction t
JOIN allocation a ON a.allocation_id = t.allocation_id
WHERE t.allocation_transaction_id BETWEEN 57837 AND 57906
  AND t.transaction_type = 'EXTENSION' AND t.user_id = 19513
  AND t.alloc_end_date = '2027-05-31 23:59:59'
  AND a.end_date = '2027-05-31 23:59:59' AND a.deleted = 0
  AND EXISTS (SELECT 1 FROM allocation n
              WHERE n.account_id = a.account_id AND n.deleted = 0
                AND n.start_date = '2026-10-01 00:00:00'
                AND n.end_date   = '2027-09-30 23:59:59');
SELECT ROW_COUNT() AS step1_rows;

-- STEP 2 -- revert the end dates the STEP 1 rows name (expect 70).
UPDATE allocation a
JOIN allocation_transaction r ON r.allocation_id = a.allocation_id
SET a.end_date = '2026-09-30 23:59:59', a.modified_time = NOW()
WHERE r.transaction_comment LIKE '%[REMEDIATION 2026-10-01] Extend from NCGD0073%'
  AND a.end_date = '2027-05-31 23:59:59';
SELECT ROW_COUNT() AS step2_rows;

-- STEP 3 -- verify BEFORE committing: reverted = 70, still_extended = 0, and no
--   account in the tree holds two live allocations on 2026-10-01.
SELECT SUM(a.end_date = '2026-09-30 23:59:59') AS reverted,
       SUM(a.end_date = '2027-05-31 23:59:59') AS still_extended
FROM allocation_transaction t
JOIN allocation a ON a.allocation_id = t.allocation_id
WHERE t.allocation_transaction_id BETWEEN 57837 AND 57906;

SELECT COUNT(*) AS overlapping_accounts FROM (
  SELECT a.account_id FROM allocation a
  JOIN account ac ON ac.account_id = a.account_id
  JOIN project p ON p.project_id = ac.project_id
  JOIN project r ON r.projcode = 'NCGD0006' AND p.tree_root = r.tree_root
  WHERE a.deleted = 0 AND a.start_date <= '2026-10-01' AND a.end_date >= '2026-10-01'
  GROUP BY a.account_id HAVING COUNT(*) > 1) x;

-- STEP 4 -- the transaction is left OPEN. Type COMMIT once STEP 3 reads 70 / 0 / 0,
--   else ROLLBACK. (Piped with `<`, the session ends uncommitted and rolls back.)
-- COMMIT;

-- UNDO (after commit): re-extend the same 70 and record it the same way.
--   UPDATE allocation a
--     JOIN allocation_transaction r ON r.allocation_id = a.allocation_id
--      SET a.end_date = '2027-05-31 23:59:59', a.modified_time = NOW()
--    WHERE r.transaction_comment LIKE '%[REMEDIATION 2026-10-01] Extend from NCGD0073%';
--   then one EXTENSION row per allocation with alloc_end_date '2027-05-31 23:59:59'.
