-- ===========================================================================
-- unix_group_mask.batch_id — the Postgres twin of alter_unix_group_mask_batch.sql
-- (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/alter_unix_group_mask_batch_pg.sql
--
-- Idempotent; existing rows stay NULL and read as their own head. Apply BEFORE
-- the code that selects the column rolls out.
-- ===========================================================================

ALTER TABLE unix_group_mask ADD COLUMN IF NOT EXISTS batch_id INTEGER NULL;
CREATE INDEX IF NOT EXISTS unix_group_mask_batch ON unix_group_mask (batch_id);

-- Verification: 13 columns, 4 indexes (the primary key included).
SELECT COUNT(*) AS columns_expect_13 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'unix_group_mask';
SELECT COUNT(*) AS indexes_expect_4 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'unix_group_mask';
