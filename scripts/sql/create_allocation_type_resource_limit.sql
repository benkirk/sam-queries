-- ===========================================================================
-- allocation_type_resource_limit — request bounds per allocation type per
-- resource (MySQL).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_allocation_type_resource_limit.sql
--   then:        ... < scripts/sql/seed_allocation_type_resource_limit.sql
--   Postgres (sam_dev): the same two files with _pg.
--
-- A pair with no active row has no limit. Amounts are in the resource's own
-- unit. Design: docs/plans/XRAS_SUBMISSION.md § 4.5. ORM:
-- src/sam/accounting/allocation_type_limits.py. Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. NO FOREIGN KEYS. Every column is an id, an amount or a username,
-- so utf8mb3 throughout. Timestamps from the app clock (naive-Mountain).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS allocation_type_resource_limit (
  allocation_type_resource_limit_id  INT          NOT NULL AUTO_INCREMENT,
  allocation_type_id  INT          NOT NULL,
  resource_id         INT          NOT NULL,
  min_amount          DOUBLE           NULL,   -- NULL = no floor
  default_amount      DOUBLE           NULL,   -- NULL = no pre-fill
  max_amount          DOUBLE           NULL,   -- NULL = no ceiling
  active              TINYINT(1)   NOT NULL DEFAULT 1,
  created_by          VARCHAR(35)  NOT NULL,
  creation_time       DATETIME     NOT NULL,
  modified_time       DATETIME         NULL,
  PRIMARY KEY (allocation_type_resource_limit_id),
  UNIQUE KEY allocation_type_resource_limit_pair (allocation_type_id, resource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- Verification. All four must match, or the table is not as tested.
SELECT COUNT(*) AS rows_expect_0_before_seed FROM allocation_type_resource_limit;

SELECT COUNT(*) AS columns_expect_10
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_resource_limit';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_2
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_resource_limit';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_resource_limit'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
