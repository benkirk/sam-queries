-- ===========================================================================
-- account_allocation_state — the allocation/usage read-model.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_account_allocation_state.sql
--
-- One row per allocation the dashboards would show (active now, or ended
-- within the last 90 days), holding the rolled-up usage the live paths
-- otherwise recompute on every cold cache miss. Written ONLY by the hourly
-- `refresh_allocation_state` task; readers consult it when fresh and fall
-- back to the live computation otherwise. Design: docs/plans/READ_MODEL.md.
--
-- No foreign keys on purpose: this is a rebuilt-hourly projection, and a
-- dangling row is harmless (the freshness gate self-heals). Constraining it
-- would only make an allocation hard-delete wait for the next refresh.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS account_allocation_state (
  allocation_id        INT           NOT NULL,        -- allocation.allocation_id
  account_id           INT           NOT NULL,
  project_id           INT           NOT NULL,
  projcode             VARCHAR(30)   NOT NULL,
  resource_id          INT           NOT NULL,
  resource_name        VARCHAR(40)   NOT NULL,
  resource_type        VARCHAR(35)   NOT NULL,        -- HPC|DAV|DISK|ARCHIVE|...
  facility_name        VARCHAR(30)       NULL,
  allocation_type      VARCHAR(20)       NULL,
  parent_allocation_id INT               NULL,
  is_inheriting        TINYINT(1)    NOT NULL DEFAULT 0,
  root_projcode        VARCHAR(30)       NULL,        -- set iff is_inheriting
  allocated            DOUBLE        NOT NULL,
  self_used            DOUBLE        NOT NULL,        -- own subtree, incl. adjustments
  used                 DOUBLE        NOT NULL,        -- pool subtree when inheriting
  remaining            DOUBLE        NOT NULL,
  percent_used         DOUBLE        NOT NULL,
  self_percent_used    DOUBLE            NULL,        -- set iff is_inheriting
  charges_by_type      JSON          NOT NULL,        -- {comp,dav,disk,archive}
  adjustments          DOUBLE        NOT NULL DEFAULT 0,
  activity_date        DATE              NULL,        -- disk snapshot date
  rolling_windows      JSON              NULL,        -- {30: {...}, 90: {...}}
  start_date           DATETIME      NOT NULL,
  end_date             DATETIME          NULL,
  is_current           TINYINT(1)    NOT NULL DEFAULT 1,
  refreshed_at         DATETIME      NOT NULL,        -- database clock (SELECT NOW()); compared with ON UPDATE stamps
  PRIMARY KEY (allocation_id),
  KEY account_allocation_state_account  (account_id),
  KEY account_allocation_state_resource (resource_id, is_current),
  KEY account_allocation_state_projcode (projcode),
  KEY account_allocation_state_facility (facility_name, allocation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- Verification.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM account_allocation_state;

SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS pk_expect_allocation_id
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_allocation_state'
   AND CONSTRAINT_NAME = 'PRIMARY';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'account_allocation_state'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
