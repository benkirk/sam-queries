-- ===========================================================================
-- Invitation links and invitation-only events.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/alter_account_request_invites.sql
--
-- account_request_event.invite_only: 1 closes every self-service path (the
-- code, the picker, signed-in self-enroll); only rostered people join.
-- Existing rows backfill to 0.
-- account_request: invite_sent_at (also the link's binding, so a resend
-- invalidates older links), completed_at (the invitee finished the form),
-- eula_sha + eula_accepted_at (the agreement accepted, as a git blob SHA).
-- Apply BEFORE the code that selects the columns rolls out. Idempotent.
--
-- ORM: src/sam/core/account_requests.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event'
                   AND COLUMN_NAME = 'invite_only');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request_event ADD COLUMN invite_only TINYINT(1) NOT NULL DEFAULT 0 AFTER listed',
  'SELECT ''account_request_event.invite_only already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
                   AND COLUMN_NAME = 'invite_sent_at');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request ADD COLUMN invite_sent_at DATETIME NULL AFTER merged_at',
  'SELECT ''account_request.invite_sent_at already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
                   AND COLUMN_NAME = 'completed_at');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request ADD COLUMN completed_at DATETIME NULL AFTER invite_sent_at',
  'SELECT ''account_request.completed_at already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
                   AND COLUMN_NAME = 'eula_sha');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request ADD COLUMN eula_sha VARCHAR(40) NULL AFTER completed_at',
  'SELECT ''account_request.eula_sha already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
                   AND COLUMN_NAME = 'eula_accepted_at');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request ADD COLUMN eula_accepted_at DATETIME NULL AFTER eula_sha',
  'SELECT ''account_request.eula_accepted_at already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification: five rows, invite_only TINYINT(1) NOT NULL DEFAULT 0, the rest nullable.
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND ((TABLE_NAME = 'account_request_event' AND COLUMN_NAME = 'invite_only')
        OR (TABLE_NAME = 'account_request'
            AND COLUMN_NAME IN ('invite_sent_at', 'completed_at', 'eula_sha',
                                'eula_accepted_at')))
 ORDER BY TABLE_NAME, ORDINAL_POSITION;
