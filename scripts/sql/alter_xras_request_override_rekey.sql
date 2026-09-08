-- ===========================================================================
-- Re-key xras_request_override from (request_id, kind) to (request_number, kind).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/alter_xras_request_override_rekey.sql
--
-- IN-PLACE, no data migration: the table is created empty and stays inert until
-- an operator sets an override, so this is a trivial PK swap. Run ONLY if an
-- earlier build applied the table keyed on request_id; a fresh create from
-- scripts/create_xras_request_override.sql already has the right shape.
--
-- Why: the XRAS requestId is per-request-line and volatile (a re-issue mints a
-- new one; a request family exposes several ids for one request_number), so an
-- id-keyed override is orphaned by the next sweep. request_number (NCAR####/
-- projcode) is the stable key the rest of the subsystem already uses.
-- ===========================================================================

ALTER TABLE xras_request_override
  DROP PRIMARY KEY,
  MODIFY request_number VARCHAR(128) NOT NULL,
  MODIFY request_id     INT NULL,
  DROP INDEX xras_request_override_number,
  ADD PRIMARY KEY (request_number, kind),
  ADD INDEX xras_request_override_reqid (request_id);

-- Verification: PK is now (request_number, kind).
SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS pk_expect_request_number_kind
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_override'
   AND CONSTRAINT_NAME = 'PRIMARY';
