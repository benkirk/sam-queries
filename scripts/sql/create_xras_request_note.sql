-- ===========================================================================
-- xras_request_note — the latest recommendation a tool posted on ONE XRAS request.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_xras_request_note.sql
--
-- Written by POST /api/v1/xras/notes (the ARC Allocation Recommender, one batch
-- per run) and read by the request dialog and the Remediations card. Keyed on
-- (request_number, source) like xras_request_override; each run overwrites the
-- row. Design: docs/plans/ARC_RECOMMENDER_NOTES.md. ORM: src/sam/integration/xras.py
-- (XrasRequestNote). Pinned by tests/integration/test_schema_validation.py.
--
-- NO DROP. Created empty; apply BEFORE the image that serves the endpoint rolls.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. NO FOREIGN KEYS: every id is XRAS-owned. request_number is the
-- NCAR####/projcode token, never the volatile request_id. Every datetime comes
-- from the app clock (naive-Mountain).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xras_request_note (
  request_number   VARCHAR(128)     NOT NULL,        -- NCAR####/projcode (key)
  source           VARCHAR(32)      NOT NULL,        -- arc-allocation-recommender
  request_id       INT                  NULL,        -- XRAS requestId, informational
  action_id        INT                  NULL,        -- the Process-page link's target
  run_id           VARCHAR(64)      NOT NULL,
  generated_at     DATETIME         NOT NULL,
  ruleset_version  VARCHAR(32)          NULL,
  decision         VARCHAR(16)      NOT NULL,        -- approve|reject|route_to_human
  button           VARCHAR(32)          NULL,
  end_date         DATE                 NULL,
  values_json      TEXT                 NULL,        -- {hours_by_resource, requested_hours, links}
  admin_comment    TEXT                 NULL,
  rationale        TEXT                 NULL,
  pi_history       VARCHAR(500)         NULL,
  findings_json    MEDIUMTEXT           NULL,        -- [{id, severity, message, sources}]
  created_by       VARCHAR(35)      NOT NULL,        -- the API key's username
  active           TINYINT(1)       NOT NULL DEFAULT 1,
  creation_time    DATETIME         NOT NULL,
  modified_time    TIMESTAMP        NULL DEFAULT CURRENT_TIMESTAMP
                                         ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (request_number, source),
  KEY xras_request_note_reqid (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. The charset split. The prose and the two JSON columns carry model-written
-- text and real names: utf8mb4. The identifiers stay utf8mb3 so a lookup
-- against the action log is not a mixed-charset comparison.
-- ---------------------------------------------------------------------------
ALTER TABLE xras_request_note
  MODIFY values_json   TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY admin_comment TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY rationale     TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY pi_history    VARCHAR(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY findings_json MEDIUMTEXT   CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;

-- ---------------------------------------------------------------------------
-- 3. Verification. All five must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM xras_request_note;

SELECT COUNT(*) AS columns_expect_19
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_note';

SELECT COUNT(*) AS utf8mb4_cols_expect_5
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_note'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS pk_expect_request_number_source
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_note'
   AND CONSTRAINT_NAME = 'PRIMARY';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_note'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
