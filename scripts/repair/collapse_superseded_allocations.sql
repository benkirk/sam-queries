-- collapse_superseded_allocations.sql
--
-- STATUS: captured 2026-10-02, NOT APPLIED. Deferred by Ben: legacy reporting
-- is being retired fast enough that the discrepancy may be accepted instead.
-- Re-run STEP 0 against prod before any apply; the scope may have changed.
--
-- End every allocation soft-deleted by "Superseded by renew" where its live
-- replacement begins, so legacy SAM stops serving it.
--
-- Cause: legacy ignores allocation.deleted in sysacct, directory access and the
-- fairShareTree, so each superseded row (all from renew's pre-#598
-- replace_existing=True path) stayed live beside its replacement. UFSU0023's
-- dead rows run to 2033-07-31, the live ones to 2027-09-30. An end before the
-- start is not an option: legacy's DateRange.validateStartEnd throws.
-- Audit + census: docs/plans/HARD_DELETE_AUDIT.md section 1.
--
-- Rule: new end_date = later of (end of the row's start day, twin start - 1 s).
--   76 rows share their twin's start -> collapse to their start day.
--   NCGD0071-73 Data_Access (aid 24388, 24552, 24659) started months before
--   their 2026-10-01 twins -> end 2026-09-30 23:59:59, as #598 truncates now.
-- deleted stays 1; amount is unchanged and the audit row carries 0.00, so
-- replay(history) == amount holds.
--
-- Scope on prod 2026-10-02: 79 rows (every deleted=1 allocation), each with
-- exactly one live overlapping twin. Confirm with STEP 0 before running.
-- Apply as a write user. Dry-run: STEP 0 + STEP 3 only, or ROLLBACK at STEP 4.

-- STEP 0 -- pre-check: expect 79 rows, twins = 1 each, proposed_end >= start_date.
SELECT d.allocation_id, d.start_date, d.end_date, l.allocation_id AS twin_id,
       GREATEST(DATE(d.start_date) + INTERVAL 86399 SECOND,
                l.start_date - INTERVAL 1 SECOND) AS proposed_end
FROM allocation d
JOIN allocation l ON l.account_id = d.account_id AND l.deleted = 0
                 AND l.start_date <= d.end_date AND l.end_date >= d.start_date
WHERE d.deleted = 1
  AND EXISTS (SELECT 1 FROM allocation_transaction t
               WHERE t.allocation_id = d.allocation_id
                 AND t.transaction_comment LIKE '[DELETE] Superseded by renew%');

START TRANSACTION;

-- STEP 1 -- collapse (expect 79 rows affected).
UPDATE allocation d
JOIN allocation l ON l.account_id = d.account_id AND l.deleted = 0
                 AND l.start_date <= d.end_date AND l.end_date >= d.start_date
   SET d.end_date = GREATEST(DATE(d.start_date) + INTERVAL 86399 SECOND,
                             l.start_date - INTERVAL 1 SECOND)
WHERE d.deleted = 1
  AND EXISTS (SELECT 1 FROM allocation_transaction t
               WHERE t.allocation_id = d.allocation_id
                 AND t.transaction_comment LIKE '[DELETE] Superseded by renew%');

-- STEP 2 -- append the audit row (amount 0 => replay no-op).
INSERT INTO allocation_transaction
    (allocation_id, user_id, requested_amount, transaction_amount,
     alloc_start_date, alloc_end_date, transaction_type, transaction_comment,
     propagated, creation_time)
SELECT d.allocation_id, 23971, d.amount, 0.00, d.start_date, d.end_date, 'ADJUSTMENT',
       '[REMEDIATION 2026-10-02] Superseded row ended where its replacement begins; legacy SAM ignores deleted=1 (docs/plans/HARD_DELETE_AUDIT.md).',
       0, NOW()
FROM allocation d
WHERE d.deleted = 1
  AND EXISTS (SELECT 1 FROM allocation_transaction t
               WHERE t.allocation_id = d.allocation_id
                 AND t.transaction_comment LIKE '[DELETE] Superseded by renew%')
  AND NOT EXISTS (SELECT 1 FROM allocation_transaction t
                   WHERE t.allocation_id = d.allocation_id
                     AND t.transaction_comment LIKE '[REMEDIATION 2026-10-02]%');

-- STEP 3 -- verify BEFORE committing.
--   a) no superseded row is live to legacy at NOW() (expect 0)
SELECT COUNT(*) AS still_live_to_legacy
FROM allocation d
WHERE d.deleted = 1 AND d.start_date <= NOW() AND d.end_date >= NOW();
--   b) no dead row overlaps its twin any more, apart from a shared start day (expect 0)
SELECT COUNT(*) AS overlaps_past_start_day
FROM allocation d
JOIN allocation l ON l.account_id = d.account_id AND l.deleted = 0
                 AND l.start_date <= d.end_date AND l.end_date >= d.start_date
WHERE d.deleted = 1 AND d.end_date > DATE(d.start_date) + INTERVAL 86399 SECOND;
--   c) end >= start everywhere (expect 0) and one audit row per collapsed row (expect 79)
SELECT SUM(end_date < start_date) AS end_before_start FROM allocation WHERE deleted = 1;
SELECT COUNT(*) AS audit_rows FROM allocation_transaction
 WHERE transaction_comment LIKE '[REMEDIATION 2026-10-02]%';

-- STEP 4 -- commit. (Piping the whole file APPLIES the change.)
COMMIT;

-- UNDO (after commit): every collapsed row's prior end_date is the alloc_end_date
-- of its "[DELETE] Superseded by renew" transaction.
--   UPDATE allocation d
--   JOIN allocation_transaction t ON t.allocation_id = d.allocation_id
--        AND t.transaction_comment LIKE '[DELETE] Superseded by renew%'
--      SET d.end_date = t.alloc_end_date
--    WHERE d.deleted = 1;
--   DELETE FROM allocation_transaction
--    WHERE transaction_comment LIKE '[REMEDIATION 2026-10-02]%';
