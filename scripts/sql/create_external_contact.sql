-- ===========================================================================
-- external_contact — a contract PI or monitor who need not be a SAM user.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_external_contact.sql
--
-- A funding agency's program officer, or a PI with no SAM account, without a
-- users row. user_id links the contact when the person IS a SAM user.
-- Design: docs/plans/EXTERNAL_CONTACTS.md. ORM: src/sam/projects/contracts.py
-- (ExternalContact). Pinned by tests/integration/test_schema_validation.py.
-- Apply BEFORE alter_contract_external_contacts.sql. NO DROP; idempotent.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. NO FOREIGN KEYS (the external_ticket convention). name and
-- organization are prose (utf8mb4); email is stored lower-cased, so the plain
-- unique key is case-insensitive on every backend. MySQL's UNIQUE admits many
-- NULLs, so user_id is unique only where set. Datetimes come from the app clock.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS external_contact (
  external_contact_id INT          NOT NULL AUTO_INCREMENT,
  name                VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  email               VARCHAR(255)     NULL,   -- lower-cased
  organization        VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL,
  user_id             INT              NULL,   -- users.user_id when the person is a SAM user
  creation_time       DATETIME     NOT NULL,
  modified_time       DATETIME         NULL,
  PRIMARY KEY (external_contact_id),
  UNIQUE KEY external_contact_email (email),
  UNIQUE KEY external_contact_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM external_contact;

SELECT COUNT(*) AS columns_expect_7
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_contact';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_3
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_contact';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_contact'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
