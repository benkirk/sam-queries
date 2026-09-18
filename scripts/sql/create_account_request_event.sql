-- ===========================================================================
-- account_request_event -- a cohort of HPC account requests (a workshop, a
-- class, an onboarding wave) that share one project and one deadline.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_account_request_event.sql
--
-- Design: docs/plans/ACCOUNT_REGISTRATION.md section 2.1. Apply this script
-- BEFORE create_account_request.sql -- a request row points at its event by id.
--
-- NO DROP and no rollback script, the house rule for hand-applied tables.
-- No foreign keys: project_id and extra_sponsor_user_id are resolved at read
-- time, and an event stays readable as history after its project is retired.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS account_request_event (
  account_request_event_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_code             VARCHAR(32)   NOT NULL,   -- human-typed and shared, WRF-TUTORIAL-2026-10
  name                   VARCHAR(128)  NOT NULL,   -- shown on the form once the code is entered
  instructions           TEXT              NULL,   -- sponsor prose shown on the form under the name
  project_id             INT           NOT NULL,   -- every fulfilled account joins it
  extra_sponsor_user_id  INT               NULL,   -- one sponsor beyond the project lead and admin
  accounts_needed_by     DATE          NOT NULL,   -- the creation deadline NUSD works to
  opens_at               DATETIME          NULL,   -- when the public form accepts the code
  closes_at              DATETIME          NULL,
  active                 TINYINT(1)    NOT NULL DEFAULT 1,
  created_by             VARCHAR(35)   NOT NULL,   -- users.username
  creation_time          DATETIME      NOT NULL,   -- app clock, naive-Mountain
  modified_time          DATETIME      NOT NULL,
  PRIMARY KEY (account_request_event_id),
  UNIQUE KEY account_request_event_code     (event_code),
  KEY        account_request_event_project  (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- The charset split. `name` and `instructions` are free text a sponsor typed;
-- the identifiers stay utf8mb3 so event_code lookups are not mixed-charset
-- comparisons. tests/integration/test_schema_validation.py pins both halves.
-- ---------------------------------------------------------------------------
ALTER TABLE account_request_event
  MODIFY name         VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  MODIFY instructions TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;

-- ---------------------------------------------------------------------------
-- Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM account_request_event;

SELECT COUNT(*) AS utf8mb4_cols_expect_2
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_3
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request_event'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
