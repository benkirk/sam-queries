-- ===========================================================================
-- account_request_event_enrollment -- the durable event<->user tie: one row
-- per user who enrolled in an event, however they got there (self-enroll,
-- sponsor invite, roster paste, or reconcile of a queued request).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_account_request_event_enrollment.sql
--
-- Design: docs/plans/ACCOUNT_REGISTRATION.md section 2.3. Apply this script
-- AFTER create_account_request_event.sql -- a row points at its event by id.
--
-- Why a table and not account_request.(event_id, user_id): add_user_to_project
-- links user<->project only, so the two direct-add paths (existing-user invite,
-- signed-in self-enroll) leave no event trace. This is the single source of
-- truth, written by every enrollment path.
--
-- NO DROP and no rollback script, the house rule for hand-applied tables.
-- No foreign keys: event_id and user_id are resolved at read time, and a row
-- stays readable as history after its event or project is retired.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS account_request_event_enrollment (
  enrollment_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_id       INT UNSIGNED NOT NULL,   -- account_request_event.account_request_event_id
  user_id        INT          NOT NULL,   -- users.user_id, the enrollee (always a known user)
  upid           INT              NULL,   -- users.upid, durable person key across a rename
  source         VARCHAR(16)  NOT NULL,   -- self | invite | roster | reconcile
  created_by     VARCHAR(35)  NOT NULL,   -- 'self' | sponsor username | 'task:...'
  enrolled_at    DATETIME     NOT NULL,   -- app clock, naive-Mountain
  creation_time  DATETIME     NOT NULL,
  modified_time  DATETIME     NOT NULL,
  PRIMARY KEY (enrollment_id),
  UNIQUE KEY account_request_event_enrollment_uk   (event_id, user_id),
  KEY        account_request_event_enrollment_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- No utf8mb4 columns: every value is an identifier or a short controlled token
-- (source, created_by username), never person-typed free text.

-- ---------------------------------------------------------------------------
-- Verification. All three must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM account_request_event_enrollment;

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_3
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event_enrollment';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event_enrollment'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
