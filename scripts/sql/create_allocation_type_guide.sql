-- ===========================================================================
-- allocation_type_guide — what a request page says about each allocation
-- type before anyone starts a request (MySQL).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_allocation_type_guide.sql
--   then:        ... < scripts/sql/seed_allocation_type_guide.sql
--   Postgres (sam_dev): the same two files with _pg.
--
-- One row per XRAS allocation type (allocationTypeInfo.allocationTypeId).
-- allocation_type names the SAM type whose limits the card shows; audience
-- 'ncar' keeps a type off the public page (NULL = everyone). Design:
-- docs/plans/implemented/XRAS_SUBMISSION_SPRINT2.md. ORM:
-- src/sam/accounting/allocation_type_guide.py. Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. NO FOREIGN KEYS. utf8mb4 on the prose columns. Timestamps from
-- the app clock (naive-Mountain).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS allocation_type_guide (
  allocation_type_guide_id  INT          NOT NULL AUTO_INCREMENT,
  xras_allocation_type_id   INT          NOT NULL,
  allocation_type     VARCHAR(20)          NULL,   -- SAM allocation_type name, for the limits
  title               VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  summary             TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  funding             VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  cadence             VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  notes               TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  audience            VARCHAR(16)          NULL,   -- 'university', 'ncar', NULL = everyone
  display_order       INT          NOT NULL DEFAULT 0,
  active              TINYINT(1)   NOT NULL DEFAULT 1,
  created_by          VARCHAR(35)  NOT NULL,
  creation_time       DATETIME     NOT NULL,
  modified_time       DATETIME         NULL,
  PRIMARY KEY (allocation_type_guide_id),
  UNIQUE KEY allocation_type_guide_xras_type (xras_allocation_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- Verification. All three must match, or the table is not as tested.
SELECT COUNT(*) AS columns_expect_14
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_2
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_type_guide'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
