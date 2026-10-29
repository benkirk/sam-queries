-- ===========================================================================
-- xras_submission — one row per XRAS action SAM files for a user (MySQL).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_xras_submission.sql
--   Postgres (sam_dev): scripts/sql/create_xras_submission_pg.sql
--
-- A draft exists before XRAS assigns anything, so the key is a surrogate;
-- action_id is unique once known, request_id is the line it hangs on. Design:
-- docs/plans/XRAS_SUBMISSION.md § 4.2; build plan XRAS_SUBMISSION_SPRINT1.md § 4.
-- ORM: src/sam/integration/xras_submission.py. Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. Created empty; rows appear once XRAS_SUBMIT_ENABLED is on.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. NO FOREIGN KEYS (the external_ticket convention). state is a
-- bare VARCHAR checked in code (draft created populated submitted aborted
-- applied), never an ENUM: the table ships in two dialects. Timestamps come
-- from the app clock (naive-Mountain), never CURRENT_TIMESTAMP. utf8mb4 on the
-- prose and echoed columns: the payload carries the user's title and abstract,
-- the snapshot and last_error echo XRAS.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xras_submission (
  xras_submission_id  INT          NOT NULL AUTO_INCREMENT,
  action_id           INT              NULL,   -- unique once XRAS mints it
  request_id          INT              NULL,   -- the line; the only key before submit
  request_number      VARCHAR(128)     NULL,   -- the family token, null until submit
  project_id          INT              NULL,   -- set from the start, or when a New applies
  action_type         VARCHAR(24)  NOT NULL,   -- Extension|Supplement|Renewal|New
  request_type        VARCHAR(16)      NULL,   -- New|Renewal for a line SAM created
  opportunity_id      INT              NULL,
  user_id             INT              NULL,   -- the PI, users.user_id
  admin_user_id       INT              NULL,
  facility_id         INT              NULL,
  mnemonic_code_id    INT              NULL,
  allocation_type_id  INT              NULL,
  contract_id         INT              NULL,
  registration_id     INT              NULL,   -- account_request, sprint 2
  eula_sha            VARCHAR(40)  NOT NULL,   -- git blob SHA of the vendored agreement
  eula_accepted_at    DATETIME     NOT NULL,
  state               VARCHAR(16)  NOT NULL,
  created_by          VARCHAR(35)  NOT NULL,   -- who clicked
  xa_user             VARCHAR(64)  NOT NULL,   -- who SAM impersonated
  creation_time       DATETIME     NOT NULL,
  modified_time       DATETIME         NULL,
  submitted_time      DATETIME         NULL,
  applied_time        DATETIME         NULL,
  aborted_time        DATETIME         NULL,
  last_error          TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  payload             TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  xras_snapshot       TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  step_events         TEXT             NULL,   -- JSON: step -> xras_remediation_event id
  PRIMARY KEY (xras_submission_id),
  UNIQUE KEY xras_submission_action (action_id),
  KEY xras_submission_line    (request_id),
  KEY xras_submission_project (project_id),
  KEY xras_submission_pi      (user_id, state),
  KEY xras_submission_by      (created_by, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM xras_submission;

SELECT COUNT(*) AS columns_expect_29
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_submission';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_6
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_submission';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_submission'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
