-- ===========================================================================
-- allocation_type_guide.purpose / eligibility / requirements — the comparison
-- table's three columns on /request and in the New dialog (MySQL, prod).
--
--   Apply with:  mysql -u <ddl-user> -h <host> -p sam \
--                      < scripts/sql/alter_allocation_type_guide_overview.sql
--   then:        ... < scripts/sql/seed_allocation_type_guide_overview.sql
--   Postgres (sam_dev): the same two files with _pg.
--
-- One short phrase each; any may stay NULL (the page falls back to the
-- audience label and the funding line plus required documents). Idempotent.
-- Apply BEFORE the code rolls out: the ORM selects these columns.
--
-- ORM: src/sam/accounting/allocation_type_guide.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide'
                   AND COLUMN_NAME = 'purpose');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE allocation_type_guide ADD COLUMN purpose VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL AFTER summary',
  'SELECT ''allocation_type_guide.purpose already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide'
                   AND COLUMN_NAME = 'eligibility');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE allocation_type_guide ADD COLUMN eligibility VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL AFTER purpose',
  'SELECT ''allocation_type_guide.eligibility already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide'
                   AND COLUMN_NAME = 'requirements');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE allocation_type_guide ADD COLUMN requirements VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL AFTER eligibility',
  'SELECT ''allocation_type_guide.requirements already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification: three varchar(128) utf8mb4 nullable rows; 17 columns.
SELECT COLUMN_NAME, COLUMN_TYPE, CHARACTER_SET_NAME, IS_NULLABLE
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide'
   AND COLUMN_NAME IN ('purpose', 'eligibility', 'requirements')
 ORDER BY ORDINAL_POSITION;

SELECT COUNT(*) AS columns_expect_17
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide';
