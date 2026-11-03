-- ===========================================================================
-- unix_group_mask.batch_id — the head mask of the batch that opened a row.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/alter_unix_group_mask_batch.sql
--
-- Several members opened in one submit share one NUSD ticket, linked to the
-- head mask; the others reach it through batch_id. A row opened alone heads
-- itself. Existing rows stay NULL and read as their own head. Idempotent.
-- Apply BEFORE the code that selects the column rolls out.
--
-- ORM: src/sam/core/groups.py (UnixGroupMask). Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask'
                   AND COLUMN_NAME = 'batch_id');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE unix_group_mask ADD COLUMN batch_id INT NULL AFTER closed_by',
  'SELECT ''unix_group_mask.batch_id already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask'
                   AND INDEX_NAME = 'unix_group_mask_batch');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE unix_group_mask ADD KEY unix_group_mask_batch (batch_id)',
  'SELECT ''unix_group_mask_batch already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification: int, nullable, indexed; 13 columns, 4 indexes.
SELECT COLUMN_TYPE AS batch_id_expect_int, IS_NULLABLE AS expect_yes
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask'
   AND COLUMN_NAME = 'batch_id';

SELECT COUNT(*) AS columns_expect_13
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_4
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask';
