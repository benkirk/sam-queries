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
-- XRAS Remediations readiness modal.
--
-- Keyed on (request_id, kind): at most one active override of each kind per
-- request. Clearing sets active=0 and keeps the row as the trail. The table is
-- created empty; an override only takes effect on the next push of its request.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table.
--
-- request_id is the XRAS requestId, the stable per-request-line id on every
-- action payload — the consult key. request_number (NCAR4287) is a denormalized
-- display token, VARCHAR(128) to match xras_remediation_event.request_number
-- (the remediation cohort includes Submitted requests whose number is free text).
--
-- ONE foreign key, to mnemonic_code — the only identifier here that is a real
-- SAM row (mnemonic codes are retired via their active flag, never deleted, so
-- the FK is safe). request_id / request_number belong to XRAS and carry no FK,
-- like the sibling audit table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xras_request_override (
  request_id       INT UNSIGNED     NOT NULL,        -- XRAS requestId (consult key)
  kind             VARCHAR(24)      NOT NULL,        -- mnemonic|ignore_contract
  request_number   VARCHAR(128)         NULL,        -- display token (NCAR4287)
  mnemonic_code_id INT                  NULL,        -- set iff kind='mnemonic'
  source           VARCHAR(32)      NOT NULL DEFAULT 'manual',
  comment          VARCHAR(255)         NULL,        -- operator's reason
  created_by       VARCHAR(35)      NOT NULL,        -- the operator
  active           TINYINT(1)       NOT NULL DEFAULT 1,
  creation_time    DATETIME         NOT NULL,        -- app clock, naive-Mountain
  modified_time    TIMESTAMP        NULL DEFAULT CURRENT_TIMESTAMP
                                         ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (request_id, kind),
  KEY xras_request_override_number    (request_number),
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

SELECT COUNT(*) AS fks_expect_1
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'xras_request_override'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
