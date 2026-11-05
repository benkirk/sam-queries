-- ===========================================================================
-- contract external contacts — the Postgres twin of
-- alter_contract_external_contacts.sql (sam_dev).
--
--   Apply with:  psql -h csg-postgres.k8s.ucar.edu -d sam_dev \
--                     -f scripts/sql/alter_contract_external_contacts_pg.sql
--
-- Apply AFTER create_external_contact_pg.sql and BEFORE the code that selects
-- the columns rolls out. Idempotent.
-- ===========================================================================

ALTER TABLE contract ALTER COLUMN principal_investigator_user_id DROP NOT NULL;
ALTER TABLE contract ADD COLUMN IF NOT EXISTS pi_contact_id INTEGER NULL;
ALTER TABLE contract ADD COLUMN IF NOT EXISTS monitor_contact_id INTEGER NULL;
CREATE INDEX IF NOT EXISTS contract_pi_contact ON contract (pi_contact_id);
CREATE INDEX IF NOT EXISTS contract_monitor_contact ON contract (monitor_contact_id);

-- Verification: PI user nullable, 14 columns, both contact indexes.
SELECT is_nullable AS pi_user_nullable_expect_yes FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'contract'
   AND column_name = 'principal_investigator_user_id';
SELECT COUNT(*) AS columns_expect_14 FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'contract';
SELECT COUNT(*) AS contact_indexes_expect_2 FROM pg_indexes
 WHERE schemaname = current_schema() AND tablename = 'contract'
   AND indexname IN ('contract_pi_contact', 'contract_monitor_contact');
