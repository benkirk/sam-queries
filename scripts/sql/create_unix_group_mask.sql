-- ===========================================================================
-- unix_group_mask — one row per operator add/remove over a mirrored unix group.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_unix_group_mask.sql
--
-- Keyed by gid so one shape covers an adhoc group (materialized into
-- adhoc_system_account_entry and protected inside sync_group) and a project
-- group (applied at read time). Design: docs/plans/UNIX_GROUP_MASK.md.
-- ORM: src/sam/core/groups.py (UnixGroupMask). Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. Created empty; rows appear when an operator opens a mask. A table created
-- before batch_id existed takes alter_unix_group_mask_batch.sql instead.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. NO FOREIGN KEYS (the external_ticket convention). Identifier
-- columns are utf8mb3; note is operator prose, so utf8mb4. Every datetime
-- comes from the app clock (naive-Mountain), never CURRENT_TIMESTAMP.
-- "One open row per (unix_gid, username)" is an application rule: MySQL has
-- no partial unique index.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unix_group_mask (
  mask_id        INT          NOT NULL AUTO_INCREMENT,
  unix_gid       INT          NOT NULL,
  kind           VARCHAR(8)   NOT NULL,   -- adhoc | project
  username       VARCHAR(35)  NOT NULL,   -- users.username as stored
  op             VARCHAR(8)   NOT NULL,   -- add | remove
  requested_by   VARCHAR(35)  NOT NULL,   -- operator username
  note           VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL,
  creation_time  DATETIME     NOT NULL,
  expires_at     DATETIME         NULL,   -- NULL: until upstream agrees
  closed_at      DATETIME         NULL,
  resolution     VARCHAR(16)      NULL,   -- confirmed | expired | withdrawn
  closed_by      VARCHAR(35)      NULL,   -- username or task:<name>
  batch_id       INT              NULL,   -- head mask of the batch (its own mask_id when alone)
  PRIMARY KEY (mask_id),
  KEY unix_group_mask_gid (unix_gid, closed_at),
  KEY unix_group_mask_user (username),
  KEY unix_group_mask_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM unix_group_mask;

SELECT COUNT(*) AS columns_expect_13
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_4
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'unix_group_mask'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
