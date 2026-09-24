-- ===========================================================================
-- account_request -- one person who needs an NCAR HPC account.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_account_request.sql
--
-- Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md section 2. Apply AFTER
-- create_account_request_event.sql.
--
-- SAM never creates users; NUSD does. This row holds only what SAM cannot
-- re-derive from the users mirror -- who asked, who vouched, whether NUSD was
-- told, who set the row aside and why. Whether the account now exists is
-- derived at read time and stamped by the reconcile pass (user_id, upid).
--
-- NO DROP and no rollback script. No foreign keys: project_id, sponsor and
-- event ids are resolved at read time, and xras_username names an XRAS-side
-- placeholder that is not a SAM account.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS account_request (
  account_request_id   INT UNSIGNED NOT NULL AUTO_INCREMENT,
  -- Identity claim. The XRAS person field set, so a row can feed an upstream
  -- form or POST /v1/people without re-derivation.
  email                VARCHAR(255)  NOT NULL,   -- lower-cased ASCII match key, resolved in Python (never joined)
  first_name           VARCHAR(64)   NOT NULL,
  middle_name          VARCHAR(64)       NULL,
  last_name            VARCHAR(64)   NOT NULL,
  organization         VARCHAR(128)      NULL,
  academic_status      VARCHAR(64)       NULL,
  residence_country    VARCHAR(64)       NULL,
  orcid                VARCHAR(19)       NULL,   -- 0000-0000-0000-000X
  phone                VARCHAR(32)       NULL,
  desired_username     VARCHAR(64)       NULL,   -- a hint for NUSD, NEVER a match key
  -- Intent.
  purpose              VARCHAR(16)   NOT NULL,   -- standalone|enrollment|submission
  project_id           INT               NULL,   -- the project a fulfilled account joins
  sponsor_user_id      INT               NULL,   -- the user who invited them
  event_id             INT UNSIGNED      NULL,   -- account_request_event.account_request_event_id
  xras_username        VARCHAR(64)       NULL,   -- the ARC or SAM placeholder, lower-cased match key
  -- Queue state. Fulfilled is not a state (see the header).
  state                VARCHAR(16)   NOT NULL,   -- submitted|claimed|rejected|dismissed
  assignee             VARCHAR(35)       NULL,   -- users.username of the operator who claimed it
  requested_at         DATETIME          NULL,   -- included in a digest to NUSD on
  closed_by            VARCHAR(35)       NULL,   -- who dismissed or rejected it
  closed_at            DATETIME          NULL,
  closed_reason        VARCHAR(255)      NULL,
  comment              TEXT              NULL,   -- the sponsor's note, operator prose
  purpose_note         VARCHAR(500)      NULL,   -- the public form's "why"
  -- Provenance.
  created_by           VARCHAR(35)   NOT NULL,   -- 'self' | users.username | 'task:xras_sweep'
  verified_at          DATETIME          NULL,   -- NULL = invisible to the queue
  verified_by          VARCHAR(35)       NULL,   -- 'self' (mail) or the vouching operator
  verify_code_hash     CHAR(64)          NULL,   -- HMAC-SHA256 hex of the emailed code
  verify_expires_at    DATETIME          NULL,
  verify_sent_count    SMALLINT UNSIGNED NOT NULL DEFAULT 0,  -- verification mails issued
  source_ip            VARCHAR(45)       NULL,   -- where the public form was submitted from
  creation_time        DATETIME      NOT NULL,   -- app clock, naive-Mountain
  modified_time        DATETIME      NOT NULL,
  -- Fulfillment, stamped by reconcile once the mirrored users row appears.
  user_id              INT               NULL,
  upid                 INT               NULL,   -- users.upid, the durable person key; outlives a username change
  fulfilled_at         DATETIME          NULL,
  fulfill_error        VARCHAR(255)      NULL,   -- the account exists but the enrollment failed
  closure_notified_at  DATETIME          NULL,   -- when the requester was told of a rejection
  merged_at            DATETIME          NULL,   -- when the XRAS placeholder was merged into the real person
  -- Invitation link and agreement acceptance.
  invite_sent_at       DATETIME          NULL,   -- last invite mail; also the link binding (a resend invalidates older links)
  completed_at         DATETIME          NULL,   -- the invitee finished the form
  eula_sha             VARCHAR(40)       NULL,   -- git blob SHA of the accepted agreement (src/webapp/register/eula.md)
  eula_accepted_at     DATETIME          NULL,
  PRIMARY KEY (account_request_id),
  -- Index names live in one namespace with table names on Postgres (the
  -- dual-backend clone builds from the ORM), so none may equal a table name.
  KEY account_request_state     (state, verified_at),
  KEY account_request_email     (email),
  KEY account_request_xras      (xras_username),
  KEY account_request_event_ref (event_id),
  KEY account_request_project   (project_id),
  KEY account_request_origin    (created_by, creation_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- The charset split. Every column a person types (or that echoes one, such as
-- fulfill_error's interpolated names) must survive a 4-byte character; under
-- STRICT_TRANS_TABLES a utf8mb3 column would refuse the whole row. The ASCII
-- identifiers (email, usernames, orcid, phone) stay utf8mb3.
-- tests/integration/test_schema_validation.py pins both halves.
-- ---------------------------------------------------------------------------
ALTER TABLE account_request
  MODIFY first_name        VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  MODIFY middle_name       VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY last_name         VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  MODIFY organization      VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY academic_status   VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY residence_country VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY closed_reason     VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY comment           TEXT         CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY purpose_note      VARCHAR(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY fulfill_error     VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;

-- ---------------------------------------------------------------------------
-- Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM account_request;

SELECT COUNT(*) AS utf8mb4_cols_expect_10
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_7
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_request'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
