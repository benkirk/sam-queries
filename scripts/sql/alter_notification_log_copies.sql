-- ===========================================================================
-- notification_log.copies — the cc/bcc that left with each delivery.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/alter_notification_log_copies.sql
--
-- `cc:a@x,b@y;bcc:c@z`, written by the ledger on the `queued` row from
-- Message.copies_summary(); NULL when nothing was copied, which includes
-- every redirected row. Existing rows stay NULL: nothing recorded copies
-- before this column existed. Idempotent.
--
-- ORM: src/sam/notify/models.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification_log'
                   AND COLUMN_NAME = 'copies');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE notification_log ADD COLUMN copies VARCHAR(512) NULL AFTER template',
  'SELECT ''notification_log.copies already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification.
SELECT COLUMN_TYPE AS copies_expect_varchar_512, IS_NULLABLE AS expect_yes
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification_log'
   AND COLUMN_NAME = 'copies';
