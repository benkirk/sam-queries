-- ===========================================================================
-- allocation_type_guide.purpose / eligibility / requirements — the Postgres
-- twin of alter_allocation_type_guide_overview.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/alter_allocation_type_guide_overview_pg.sql
--   then:        ... -f scripts/sql/seed_allocation_type_guide_overview_pg.sql
--
-- Idempotent. Apply BEFORE the code rolls out: the ORM selects these columns.
-- ===========================================================================

ALTER TABLE allocation_type_guide ADD COLUMN IF NOT EXISTS purpose      VARCHAR(128) NULL;
ALTER TABLE allocation_type_guide ADD COLUMN IF NOT EXISTS eligibility  VARCHAR(128) NULL;
ALTER TABLE allocation_type_guide ADD COLUMN IF NOT EXISTS requirements VARCHAR(128) NULL;

-- Verification: three character varying(128) nullable rows; 17 columns.
SELECT column_name, data_type, character_maximum_length, is_nullable
  FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'allocation_type_guide'
   AND column_name IN ('purpose', 'eligibility', 'requirements');
SELECT COUNT(*) AS columns_expect_17 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'allocation_type_guide';
