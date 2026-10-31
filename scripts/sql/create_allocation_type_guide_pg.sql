-- ===========================================================================
-- allocation_type_guide — the Postgres twin (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_allocation_type_guide_pg.sql
--   then:        ... -f scripts/sql/seed_allocation_type_guide_pg.sql
--
-- Same columns and keys as the MySQL script. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS allocation_type_guide (
  allocation_type_guide_id  SERIAL  PRIMARY KEY,
  xras_allocation_type_id   INTEGER       NOT NULL,
  allocation_type     VARCHAR(20)           NULL,
  title               VARCHAR(64)       NOT NULL,
  summary             TEXT                  NULL,
  funding             VARCHAR(128)          NULL,
  cadence             VARCHAR(64)           NULL,
  notes               TEXT                  NULL,
  audience            VARCHAR(16)           NULL,
  display_order       INTEGER           NOT NULL DEFAULT 0,
  active              BOOLEAN           NOT NULL DEFAULT TRUE,
  created_by          VARCHAR(35)       NOT NULL,
  creation_time       TIMESTAMP         NOT NULL,
  modified_time       TIMESTAMP             NULL,
  CONSTRAINT allocation_type_guide_xras_type UNIQUE (xras_allocation_type_id)
);

-- Verification: 14 columns, 2 indexes (the primary key and the XRAS type).
SELECT COUNT(*) AS columns_expect_14 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'allocation_type_guide';
SELECT COUNT(*) AS indexes_expect_2 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'allocation_type_guide';
