-- ===========================================================================
-- xras_submission — the Postgres twin of create_xras_submission.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_xras_submission_pg.sql
--
-- Same columns, keys and rules as the MySQL script; a make refresh-dev builds
-- the same table from the ORM. Index names share Postgres's namespace with
-- tables, so none matches a table name. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS xras_submission (
  xras_submission_id  SERIAL       PRIMARY KEY,
  action_id           INTEGER          NULL,
  request_id          INTEGER          NULL,
  request_number      VARCHAR(128)     NULL,
  project_id          INTEGER          NULL,
  action_type         VARCHAR(24)  NOT NULL,
  request_type        VARCHAR(16)      NULL,
  opportunity_id      INTEGER          NULL,
  user_id             INTEGER          NULL,
  admin_user_id       INTEGER          NULL,
  facility_id         INTEGER          NULL,
  mnemonic_code_id    INTEGER          NULL,
  allocation_type_id  INTEGER          NULL,
  contract_id         INTEGER          NULL,
  registration_id     INTEGER          NULL,
  eula_sha            VARCHAR(40)  NOT NULL,
  eula_accepted_at    TIMESTAMP    NOT NULL,
  state               VARCHAR(16)  NOT NULL,
  created_by          VARCHAR(35)  NOT NULL,
  xa_user             VARCHAR(64)  NOT NULL,
  creation_time       TIMESTAMP    NOT NULL,
  modified_time       TIMESTAMP        NULL,
  submitted_time      TIMESTAMP        NULL,
  applied_time        TIMESTAMP        NULL,
  aborted_time        TIMESTAMP        NULL,
  last_error          TEXT             NULL,
  payload             TEXT             NULL,
  xras_snapshot       TEXT             NULL,
  step_events         TEXT             NULL,
  CONSTRAINT xras_submission_action UNIQUE (action_id)
);

CREATE INDEX IF NOT EXISTS xras_submission_line    ON xras_submission (request_id);
CREATE INDEX IF NOT EXISTS xras_submission_project ON xras_submission (project_id);
CREATE INDEX IF NOT EXISTS xras_submission_pi      ON xras_submission (user_id, state);
CREATE INDEX IF NOT EXISTS xras_submission_by      ON xras_submission (created_by, state);

-- Verification: 0 rows, 29 columns, 6 indexes (the primary key and the unique included).
SELECT COUNT(*) AS rows_expect_0 FROM xras_submission;
SELECT COUNT(*) AS columns_expect_29 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'xras_submission';
SELECT COUNT(*) AS indexes_expect_6 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'xras_submission';
