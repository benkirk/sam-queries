-- ===========================================================================
-- unix_group_mask — the Postgres twin of create_unix_group_mask.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_unix_group_mask_pg.sql
--
-- Same columns, keys and rules as the MySQL script; a make refresh-dev builds
-- the same table from the ORM. Index names share Postgres's namespace with
-- tables, so none matches a table name. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS unix_group_mask (
  mask_id        SERIAL       PRIMARY KEY,
  unix_gid       INTEGER      NOT NULL,
  kind           VARCHAR(8)   NOT NULL,
  username       VARCHAR(35)  NOT NULL,
  op             VARCHAR(8)   NOT NULL,
  requested_by   VARCHAR(35)  NOT NULL,
  note           VARCHAR(255)     NULL,
  creation_time  TIMESTAMP    NOT NULL,
  expires_at     TIMESTAMP        NULL,
  closed_at      TIMESTAMP        NULL,
  resolution     VARCHAR(16)      NULL,
  closed_by      VARCHAR(35)      NULL
);

CREATE INDEX IF NOT EXISTS unix_group_mask_gid  ON unix_group_mask (unix_gid, closed_at);
CREATE INDEX IF NOT EXISTS unix_group_mask_user ON unix_group_mask (username);

-- Applied as the superuser, the table would belong to postgres and the app's role could
-- not read it (permission denied, samuel-dev 2026-11-02). Every app table is sam_dev's.
ALTER TABLE unix_group_mask OWNER TO sam_dev;

-- Verification: 0 rows, 12 columns, 3 indexes (the primary key included), owner sam_dev.
SELECT COUNT(*) AS rows_expect_0 FROM unix_group_mask;
SELECT COUNT(*) AS columns_expect_12 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'unix_group_mask';
SELECT COUNT(*) AS indexes_expect_3 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'unix_group_mask';
SELECT tableowner AS owner_expect_sam_dev FROM pg_tables
 WHERE schemaname = current_schema() AND tablename = 'unix_group_mask';
