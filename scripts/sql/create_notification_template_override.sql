-- ===========================================================================
-- notification_template_override — an operator's edit of one shipped
-- notification template (Admin > Notifications > Templates).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/create_notification_template_override.sql
--
-- One row per template file name (`expiration-WNA.txt`). The file inside the
-- sam.notify package stays the baseline: the renderer prefers a row when one
-- exists, and deleting the row restores the shipped text. The table is
-- created empty; nothing changes until an operator saves an edit.
--
-- ORM: src/sam/notify/template_store.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. `name` is unique: at most one override per template file.
-- modified_time is stamped from the app clock (naive-Mountain), never from
-- CURRENT_TIMESTAMP, which resolves in the server's zone.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_template_override (
  notification_template_override_id INT NOT NULL AUTO_INCREMENT,
  name             VARCHAR(64)      NOT NULL,        -- template file name
  body             TEXT             NOT NULL,        -- utf8mb4, see below
  modified_by      VARCHAR(35)      NOT NULL,        -- users.username
  modified_time    DATETIME         NOT NULL,        -- app clock, naive-Mountain
  PRIMARY KEY (notification_template_override_id),
  UNIQUE KEY notification_template_override_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. The charset split. `body` is operator prose and may carry any character
-- a mail can; under STRICT_TRANS_TABLES a utf8mb3 column would refuse the
-- save. The identifiers stay utf8mb3 like their siblings.
-- ---------------------------------------------------------------------------
ALTER TABLE notification_template_override
  MODIFY body TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Verification.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM notification_template_override;

SELECT COUNT(*) AS utf8mb4_cols_expect_1
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification_template_override'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT COUNT(*) AS unique_keys_expect_1
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification_template_override'
   AND INDEX_NAME = 'notification_template_override_name' AND NON_UNIQUE = 0;
