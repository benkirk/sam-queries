-- fy27_renew_child_only_resources.sql
--
-- Create the FY27 allocations that renew skipped for three sub-project-only
-- resources.
--
-- Cause: renew anchors each resource on the tree root. A resource held only by
-- a sub-project has no root source, so the NRAL0002 and NCIS0001 FY27 renews
-- skipped it as no_source, and each account below has no allocation after
-- 2026-09-30. See docs/plans/FY27_RENEWAL_GAPS.md.
--
-- Fix: clone each source allocation into 2026-10-01 -> 2027-09-30 exactly as
-- renew would have (same amount and description, standalone, one NEW row whose
-- comment starts with "[RENEW] " so parse_intent() reads it as a renewal).
--
-- Scope (prod ids, verified 2026-09-23; confirm with STEP 0 before running):
--   NTMA0002 (child of NRAL0002) / Destor       aid 25222  acct 18565  amount 300
--   NRAL0031 (child of NRAL0002) / GLADE user   aid 23947  acct 17384  amount 2
--   NCIS0014 (child of NCIS0001) / Data_Access  aid 23522  acct 17198  amount 1
--
-- Model: append-only, [REMEDIATION 2026-09-23]-tagged, transaction-wrapped,
-- self-verifying, UNDO documented; mirrors reconcile_fy27_renew_gap.sql.
-- Apply as a write user. Dry-run first by replacing COMMIT with ROLLBACK.
-- A direct write does not flush the webapp cache: run `sam-admin cache --refresh`.

-- STEP 0 -- pre-check: expect exactly these 3 rows, deleted=0, parent NULL,
--   end 2026-09-30 23:59:59, fy27_rows=0.
SELECT a.allocation_id, a.account_id, a.amount, a.description, a.start_date,
       a.end_date, a.deleted, a.parent_allocation_id,
       (SELECT COUNT(*) FROM allocation b WHERE b.account_id = a.account_id
          AND b.deleted = 0 AND b.end_date > '2026-09-30 23:59:59') AS fy27_rows
FROM allocation a WHERE a.allocation_id IN (25222, 23947, 23522);

START TRANSACTION;

-- STEP 1 -- create the FY27 rows (expect 3). The NOT EXISTS guard makes a rerun,
--   or a row someone added in the UI meanwhile, a no-op for that account.
INSERT INTO allocation (account_id, amount, description, start_date, end_date,
                        deleted, creation_time, parent_allocation_id)
SELECT s.account_id, s.amount, s.description,
       '2026-10-01 00:00:00', '2027-09-30 23:59:59', 0, NOW(), NULL
FROM allocation s
WHERE s.allocation_id IN (25222, 23947, 23522) AND s.deleted = 0
  AND NOT EXISTS (SELECT 1 FROM allocation b
                   WHERE b.account_id = s.account_id AND b.deleted = 0
                     AND b.start_date <= '2027-09-30 23:59:59'
                     AND (b.end_date IS NULL OR b.end_date >= '2026-10-01'));
SELECT ROW_COUNT() AS step1_rows;

-- STEP 2 -- one NEW/[RENEW] audit row per new allocation (expect 3).
--   user_id 23971 = benkirk; change it if someone else applies this.
INSERT INTO allocation_transaction
    (allocation_id, user_id, requested_amount, transaction_amount,
     alloc_start_date, alloc_end_date, transaction_type, transaction_comment,
     propagated, creation_time)
SELECT n.allocation_id, 23971, n.amount, n.amount, n.start_date, n.end_date, 'NEW',
       CONCAT('[RENEW] Renewed from allocation #', s.allocation_id, ' (',
              DATE(s.start_date), ' → ', DATE(s.end_date), ')',
              ' [REMEDIATION 2026-09-23] sub-project-only resource skipped by root-anchored renew'),
       0, NOW()
FROM allocation n
JOIN allocation s ON s.account_id = n.account_id
                 AND s.allocation_id IN (25222, 23947, 23522)
WHERE n.deleted = 0 AND n.start_date = '2026-10-01 00:00:00'
  AND n.end_date = '2027-09-30 23:59:59'
  AND NOT EXISTS (SELECT 1 FROM allocation_transaction t
                   WHERE t.allocation_id = n.allocation_id);
SELECT ROW_COUNT() AS step2_rows;

-- STEP 3 -- verify BEFORE committing: 3 rows, replay == amount, n_txn = 1.
--   replay == SUM(transaction_amount) here because each history is one NEW row.
SELECT n.allocation_id, n.account_id, n.amount, n.description, n.start_date,
       n.end_date, n.parent_allocation_id,
       (SELECT ROUND(SUM(t.transaction_amount), 2) FROM allocation_transaction t
         WHERE t.allocation_id = n.allocation_id) AS replay,
       (SELECT COUNT(*) FROM allocation_transaction t
         WHERE t.allocation_id = n.allocation_id) AS n_txn
FROM allocation n
WHERE n.account_id IN (18565, 17384, 17198) AND n.deleted = 0
  AND n.start_date = '2026-10-01 00:00:00';

-- STEP 4 -- commit. (Piping the whole file APPLIES the change; swap in ROLLBACK
--   to test without persisting, then verify the persisted state after the real run.)
COMMIT;

-- UNDO (after commit). Before any FY27 charges accrue, hard-delete (audit rows
-- first, for the FK); from 2026-10-01 on, soft-delete instead
-- (SET deleted = 1, deletion_time = NOW()) and leave the audit rows.
--   DELETE t FROM allocation_transaction t
--     JOIN allocation n ON n.allocation_id = t.allocation_id
--    WHERE n.account_id IN (18565, 17384, 17198)
--      AND n.start_date = '2026-10-01 00:00:00'
--      AND t.transaction_comment LIKE '%[REMEDIATION 2026-09-23]%';
--   DELETE FROM allocation
--    WHERE account_id IN (18565, 17384, 17198)
--      AND start_date = '2026-10-01 00:00:00' AND creation_time >= '2026-09-23';
