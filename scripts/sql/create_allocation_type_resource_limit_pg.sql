-- ===========================================================================
-- allocation_type_resource_limit — the Postgres twin (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/create_allocation_type_resource_limit_pg.sql
--   then:        ... -f scripts/sql/seed_allocation_type_resource_limit_pg.sql
--
-- Same columns and keys as the MySQL script. NO DROP; idempotent.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS allocation_type_resource_limit (
  allocation_type_resource_limit_id  SERIAL  PRIMARY KEY,
  allocation_type_id  INTEGER           NOT NULL,
  resource_id         INTEGER           NOT NULL,
  min_amount          DOUBLE PRECISION      NULL,
  default_amount      DOUBLE PRECISION      NULL,
  max_amount          DOUBLE PRECISION      NULL,
  active              BOOLEAN           NOT NULL DEFAULT TRUE,
  created_by          VARCHAR(35)       NOT NULL,
  creation_time       TIMESTAMP         NOT NULL,
  modified_time       TIMESTAMP             NULL,
  CONSTRAINT allocation_type_resource_limit_pair UNIQUE (allocation_type_id, resource_id)
);

-- Verification: 10 columns, 2 indexes (the primary key and the pair).
SELECT COUNT(*) AS columns_expect_10 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'allocation_type_resource_limit';
SELECT COUNT(*) AS indexes_expect_2 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'allocation_type_resource_limit';
