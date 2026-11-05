-- ===========================================================================
-- xras_request_note — the Postgres twin of create_xras_request_note.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_xras_request_note_pg.sql
--
-- Same columns, keys and rules as the MySQL script; a make refresh-dev builds
-- the same table from the ORM. Index names share Postgres's namespace with
-- tables, so none matches a table name. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS xras_request_note (
  request_number   VARCHAR(128) NOT NULL,
  source           VARCHAR(32)  NOT NULL,
  request_id       INTEGER          NULL,
  action_id        INTEGER          NULL,
  run_id           VARCHAR(64)  NOT NULL,
  generated_at     TIMESTAMP    NOT NULL,
  ruleset_version  VARCHAR(32)      NULL,
  decision         VARCHAR(16)  NOT NULL,
  button           VARCHAR(32)      NULL,
  end_date         DATE             NULL,
  values_json      TEXT             NULL,
  admin_comment    TEXT             NULL,
  rationale        TEXT             NULL,
  pi_history       VARCHAR(500)     NULL,
  findings_json    TEXT             NULL,
  created_by       VARCHAR(35)  NOT NULL,
  active           BOOLEAN      NOT NULL DEFAULT TRUE,
  creation_time    TIMESTAMP    NOT NULL,
  modified_time    TIMESTAMP        NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_xras_request_note PRIMARY KEY (request_number, source)
);

CREATE INDEX IF NOT EXISTS xras_request_note_reqid ON xras_request_note (request_id);

-- Applied as the superuser, the table would belong to postgres and the app's role could
-- not read it (permission denied, samuel-dev 2026-11-02). Every app table is sam_dev's.
ALTER TABLE xras_request_note OWNER TO sam_dev;

-- Verification: 0 rows, 19 columns, 2 indexes (the primary key included), owner sam_dev.
SELECT COUNT(*) AS rows_expect_0 FROM xras_request_note;
SELECT COUNT(*) AS columns_expect_19 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'xras_request_note';
SELECT COUNT(*) AS indexes_expect_2 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'xras_request_note';
SELECT tableowner AS owner_expect_sam_dev FROM pg_tables
 WHERE schemaname = current_schema() AND tablename = 'xras_request_note';
