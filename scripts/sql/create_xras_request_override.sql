-- ===========================================================================
-- xras_request_override — operator decisions that unblock ONE XRAS request.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/create_xras_request_override.sql
--
-- A local table populated out-of-band and READ AT INGEST by sam.xras
-- (resolve_mnemonic_code / plan_contracts), never written by anything that
-- talks to the XRAS API — the same shape as xras_opportunity_allocation_type.
-- Backs the "Set mnemonic code" / "Ignore contract blocker" controls in the
-- XRAS Remediations modals.
--
-- Keyed on (request_number, kind): at most one active override of each kind per
-- request. Clearing sets active=0 and keeps the row as the trail. The table is
-- created empty; an override only takes effect on the next push of its request.
--
-- NOTE: if an earlier build of this table was applied keyed on request_id, see
-- scripts/alter_xras_request_override_rekey.sql for the in-place re-key (the
-- table is empty and inert, so it is a trivial ALTER, no data migration).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table.
--
-- request_number is the NCAR####/projcode token — the STABLE key the whole
-- subsystem uses (the card, the reports and the recheck all key on it). The
-- XRAS requestId is deliberately NOT the key: it is per-request-line and
-- volatile (a re-issue mints a new one, a family exposes several), so it is
-- kept only as an informational column. ONE foreign key, to mnemonic_code —
-- the only identifier here that is a real SAM row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xras_request_override (
  request_number   VARCHAR(128)     NOT NULL,        -- NCAR####/projcode (key)
  kind             VARCHAR(24)      NOT NULL,        -- mnemonic|ignore_contract
  request_id       INT                  NULL,        -- XRAS requestId, informational
  mnemonic_code_id INT                  NULL,        -- set iff kind='mnemonic'
  source           VARCHAR(32)      NOT NULL DEFAULT 'manual',
  comment          VARCHAR(255)         NULL,        -- operator's reason
  created_by       VARCHAR(35)      NOT NULL,        -- the operator
  active           TINYINT(1)       NOT NULL DEFAULT 1,
  creation_time    DATETIME         NOT NULL,        -- app clock, naive-Mountain
  modified_time    TIMESTAMP        NULL DEFAULT CURRENT_TIMESTAMP
                                         ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (request_number, kind),
  KEY xras_request_override_reqid     (request_id),
  KEY xras_request_override_mnemonic  (mnemonic_code_id),
  CONSTRAINT fk_xras_request_override_mnemonic
    FOREIGN KEY (mnemonic_code_id) REFERENCES mnemonic_code (mnemonic_code_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. The charset split. `comment` is unconstrained operator prose (real names,
-- accents) — utf8mb4. The identifiers stay utf8mb3 so an equality lookup
-- against the action log / remediation event is not a mixed-charset comparison.
-- tests/integration/test_schema_validation.py pins both halves.
-- ---------------------------------------------------------------------------
ALTER TABLE xras_request_override
  MODIFY comment VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;

-- ---------------------------------------------------------------------------
-- 3. Verification.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM xras_request_override;

SELECT COUNT(*) AS utf8mb4_cols_expect_1
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_override'
   AND CHARACTER_SET_NAME = 'utf8mb4';

SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS pk_expect_request_number_kind
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_override'
   AND CONSTRAINT_NAME = 'PRIMARY';

SELECT COUNT(*) AS fks_expect_1
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_override'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
