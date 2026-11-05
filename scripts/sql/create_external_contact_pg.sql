-- ===========================================================================
-- external_contact — the Postgres twin of create_external_contact.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_external_contact_pg.sql
--
-- Same columns, keys and rules as the MySQL script; a make refresh-dev builds
-- the same table from the ORM. Index names share Postgres's namespace with
-- tables, so none matches a table name. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS external_contact (
  external_contact_id SERIAL       PRIMARY KEY,
  name                VARCHAR(255) NOT NULL,
  email               VARCHAR(255)     NULL,
  organization        VARCHAR(255)     NULL,
  user_id             INTEGER          NULL,
  creation_time       TIMESTAMP    NOT NULL,
  modified_time       TIMESTAMP        NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS external_contact_email ON external_contact (email);
CREATE UNIQUE INDEX IF NOT EXISTS external_contact_user  ON external_contact (user_id);

-- Applied as the superuser, the table would belong to postgres and the app's role could
-- not read it (permission denied, samuel-dev 2026-11-02). Every app table is sam_dev's.
ALTER TABLE external_contact OWNER TO sam_dev;

-- Verification: 0 rows, 7 columns, 3 indexes (the primary key included), owner sam_dev.
SELECT COUNT(*) AS rows_expect_0 FROM external_contact;
SELECT COUNT(*) AS columns_expect_7 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'external_contact';
SELECT COUNT(*) AS indexes_expect_3 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'external_contact';
SELECT tableowner AS owner_expect_sam_dev FROM pg_tables
 WHERE schemaname = current_schema() AND tablename = 'external_contact';
