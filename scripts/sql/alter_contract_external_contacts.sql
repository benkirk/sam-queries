-- ===========================================================================
-- contract: a PI or monitor may be an external_contact; the PI user is optional.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/alter_contract_external_contacts.sql
--
-- A SAM-user PI or monitor stays on the user FK; one who is not goes on the
-- contact column (docs/plans/implemented/EXTERNAL_CONTACTS.md). Every contract still has a
-- PI, by one column or the other (an application rule).
-- WARNING: legacy SAM's contract editor (/app/contract/edit) fails once an active
-- contract has a NULL PI user; Ben accepted that 2026-11-05. Legacy never writes
-- the new columns. Apply AFTER create_external_contact.sql and BEFORE the code
-- that selects the columns rolls out. Idempotent; the new columns carry no FK
-- constraint (the external_ticket convention).
-- ===========================================================================

SET @nullable := (SELECT IS_NULLABLE FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract'
                     AND COLUMN_NAME = 'principal_investigator_user_id');
SET @ddl := IF(@nullable = 'NO',
  'ALTER TABLE contract MODIFY principal_investigator_user_id INT NULL COMMENT ''user_id of the principal investigator who applied for the contract and oversees the project; NULL when pi_contact_id names an external contact''',
  'SELECT ''contract.principal_investigator_user_id already nullable'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract'
                   AND COLUMN_NAME = 'pi_contact_id');
SET @ddl := IF(@exists = 0,
  'ALTER TABLE contract ADD COLUMN pi_contact_id INT NULL COMMENT ''external_contact of a PI who is not a SAM user'' AFTER contract_monitor_user_id, ADD COLUMN monitor_contact_id INT NULL COMMENT ''external_contact of a monitor who is not a SAM user'' AFTER pi_contact_id, ADD KEY contract_pi_contact (pi_contact_id), ADD KEY contract_monitor_contact (monitor_contact_id)',
  'SELECT ''contract.pi_contact_id already exists'' AS note');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Verification: PI user nullable; both contact columns int, nullable, indexed; 14 columns.
SELECT IS_NULLABLE AS pi_user_nullable_expect_yes
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract'
   AND COLUMN_NAME = 'principal_investigator_user_id';

SELECT COLUMN_NAME, COLUMN_TYPE AS expect_int, IS_NULLABLE AS expect_yes
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract'
   AND COLUMN_NAME IN ('pi_contact_id', 'monitor_contact_id');

SELECT COUNT(*) AS columns_expect_14
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract';

SELECT COUNT(DISTINCT INDEX_NAME) AS contact_indexes_expect_2
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contract'
   AND INDEX_NAME IN ('contract_pi_contact', 'contract_monitor_contact');
