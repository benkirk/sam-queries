-- ===========================================================================
-- account_request_event.listed -- opt-in public discoverability.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/alter_account_request_event_listed.sql
--
-- 1 shows the event on the public Upcoming Events card (/status/events);
-- 0 leaves it reachable by direct link only. Existing rows backfill to 0.
-- Apply BEFORE the code that selects the column rolls out. Idempotent.
--
-- ORM: src/sam/core/account_requests.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event'
                   AND COLUMN_NAME = 'listed');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE account_request_event ADD COLUMN listed TINYINT(1) NOT NULL DEFAULT 0 AFTER active',
  'SELECT ''account_request_event.listed already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification.
SELECT COLUMN_TYPE AS listed_expect_tinyint_1, IS_NULLABLE AS expect_no,
       COLUMN_DEFAULT AS expect_0
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event'
   AND COLUMN_NAME = 'listed';
