-- ===========================================================================
-- Backfill account_request_event_enrollment from the request rows that already
-- carry both an event and a fulfilled user. Recovers the enrollments that
-- predate the ledger: queued invites and anonymous self-registrations that
-- reconcile fulfilled. (The two direct-add paths -- existing-user invite and
-- signed-in self-enroll -- never wrote a request row, so nothing here can
-- recover them; going forward enroll_user_in_event records every path.)
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/backfill_event_enrollment.sql
--
-- Apply AFTER create_account_request_event_enrollment.sql. A no-op in a fresh
-- deployment (the feature ships dark, so no such rows exist yet). Idempotent:
-- INSERT IGNORE skips a (event_id, user_id) that already has a ledger row, so
-- re-running after the helper has written live rows is safe.
--
-- source = 'reconcile': these rows fulfilled through the reconcile path, which
-- is the value that path stamps live. NO DROP, no rollback -- house rule.
-- ===========================================================================

INSERT IGNORE INTO account_request_event_enrollment
    (event_id, user_id, upid, source, created_by, enrolled_at,
     creation_time, modified_time)
SELECT r.event_id,
       r.user_id,
       r.upid,
       'reconcile',
       r.created_by,
       COALESCE(r.fulfilled_at, r.creation_time),
       NOW(),
       NOW()
  FROM account_request r
 WHERE r.event_id IS NOT NULL
   AND r.user_id  IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Verification: every fulfilled request that names an event now has a ledger
-- row, so these two counts match.
-- ---------------------------------------------------------------------------
SELECT
  (SELECT COUNT(DISTINCT event_id, user_id) FROM account_request
    WHERE event_id IS NOT NULL AND user_id IS NOT NULL) AS fulfilled_event_rows,
  (SELECT COUNT(*) FROM account_request_event_enrollment
    WHERE source = 'reconcile') AS ledger_reconcile_rows;
