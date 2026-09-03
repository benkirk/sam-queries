-- ===========================================================================
-- xras_remediation_event — SAM's record of operator WRITES out to XRAS.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/create_xras_remediation_event.sql
--
-- Backs the XRAS Remediations card (docs/plans/implemented/XRAS_REMEDIATIONS.md).
-- DDL of record + verification: docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md § 2d
--
-- ⚠️ NO DROP, and no rollback script. This table is an audit trail of
-- irreversible acts against a system SAM does not own — a merge DELETES an
-- XRAS account — so its rows cannot be reconstructed from anywhere else. Get
-- it right the first time. It is created empty and stays empty until the
-- XRAS_WRITE_ENABLED lever is armed, so applying it early costs nothing.
--
-- Verified 2026-08-21: this exact script, applied to an empty schema, produces
-- a table byte-identical to the one the test suite (6,988 passing) runs
-- against, and the ORM validates against it with no column drift.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table.
--
-- NO FOREIGN KEYS, deliberately. Every identifier here belongs to XRAS —
-- both usernames, request_id, action_id, role_id — and the merge operation a
-- row records DELETES the username it names, so an FK to `users` would either
-- fail or, worse, prevent recording the very operation that removed it.
--
-- Two identity columns that are NOT the same person:
--   created_by   the SAM operator who clicked. users.username width (35).
--   xa_user      who SAM impersonated to authorize the call, because every
--                request-scoped XRAS write authorizes on "XA-USER holds a role
--                on that request". NULL for merge, which is user-agnostic.
--
-- request_number is VARCHAR(128), NOT 30 like xras_action_log.request_number.
-- That divergence is deliberate and measured: the action log only ever sees
-- requests being *pushed*, which always carry a real projcode, while this
-- table sees the whole remediation cohort — including Submitted requests whose
-- number is still free text a PI typed. Live example, 55 characters:
--     'New University Large Request - Fall 2017 UCUD0005 Zhong'
-- It renders on the card with a Withdraw button, so an audit row for it is
-- reachable; at 30 the insert would truncate, or error under strict mode.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xras_remediation_event (
  xras_remediation_event_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  operation        VARCHAR(24)      NOT NULL,   -- merge_person|withdraw_action|
                                                -- submit_action|add_role|remove_role
  status           VARCHAR(16)      NOT NULL,   -- attempted|verified|unverified|
                                                -- rejected|error
  username         VARCHAR(64)          NULL,   -- the XRAS user acted on
  target_username  VARCHAR(64)          NULL,   -- merge only: the identity kept
  request_number   VARCHAR(128)         NULL,   -- see the note above
  request_id       INT UNSIGNED         NULL,   -- what the WRITE routes key on
  action_id        INT UNSIGNED         NULL,
  role_id          INT UNSIGNED         NULL,   -- what role removal keys on
  role_type        VARCHAR(24)          NULL,   -- PI|Allocation Manager|User
  xa_user          VARCHAR(64)          NULL,   -- impersonated; NULL = user-agnostic
  created_by       VARCHAR(35)      NOT NULL,   -- the operator. never 'task:*'
  creation_time    DATETIME         NOT NULL,   -- app clock, no DB default:
  completed_time   DATETIME             NULL,   --   naive-Mountain by convention
  http_status      SMALLINT UNSIGNED    NULL,
  outcome_reason   VARCHAR(255)         NULL,   -- one line an operator can read
  comment          TEXT                 NULL,   -- operator's reason
  before_state     TEXT                 NULL,   -- JSON captures; see § 2 below
  after_state      TEXT                 NULL,
  PRIMARY KEY (xras_remediation_event_id),
  KEY xras_remediation_event_op_time  (operation, creation_time),
  KEY xras_remediation_event_user     (username),
  KEY xras_remediation_event_request  (request_number),
  KEY xras_remediation_event_operator (created_by, creation_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. The charset split. NOT optional and NOT cosmetic.
--
-- `before_state` captures the pre-merge person detail sheet — including
-- residenceCountry, which the inbound wire never carries — precisely because
-- the merge then deletes the source and that sheet exists nowhere else SAM can
-- reach. `after_state` carries XRAS's own validation errors on a rejection,
-- and `comment` is unconstrained operator prose. At utf8mb3 all three truncate
-- at the first 4-byte character.
--
-- The identifier columns stay utf8mb3 so an equality lookup against
-- xras_action_log is not a mixed-charset comparison (which would drop it to a
-- full scan). tests/integration/test_schema_validation.py pins both halves.
--
-- Doing this later is an ALTER on a table with an audit trail in it. Doing it
-- now is a property of an empty table.
-- ---------------------------------------------------------------------------
ALTER TABLE xras_remediation_event
  MODIFY comment      TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY before_state TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY after_state  TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;

-- ---------------------------------------------------------------------------
-- 3. Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM xras_remediation_event;

SELECT COUNT(*) AS utf8mb4_cols_expect_3
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_remediation_event'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_5
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_remediation_event';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_remediation_event'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
