-- ===========================================================================
-- samuel_role, samuel_role_permission, samuel_role_grant — the webapp's
-- role catalog (Admin > Configuration > Roles & access).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_samuel_roles.sql
--   Then seed:   sam-admin rbac --seed && sam-admin rbac --seed-keys
--
-- A role is a named permission bundle that may extend one parent. A grant
-- hands a role or one permission to a user, a POSIX group or an API key,
-- for every facility or for one. Revocation is a stamp, never a delete.
-- The legacy `role` / `role_user` tables are untouched. Read only when
-- RBAC_SOURCE=db. ORM: src/sam/security/samuel_roles.py. Design:
-- docs/plans/RBAC_DB_ROLES.md. Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. Created empty; nothing changes until seeded and RBAC_SOURCE flips.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The tables. NO FOREIGN KEYS, per the app-owned-table convention. Every
-- column is an identifier, so utf8mb3 throughout. All times come from the
-- app clock (naive-Mountain), never CURRENT_TIMESTAMP.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS samuel_role (
  samuel_role_id   INT          NOT NULL AUTO_INCREMENT,
  name             VARCHAR(40)  NOT NULL,
  description      VARCHAR(255)     NULL,
  extends_role_id  INT              NULL,   -- one parent; folded in at resolve time
  active           TINYINT(1)   NOT NULL,
  created_by       VARCHAR(35)  NOT NULL,   -- users.username or cli:seed
  creation_time    DATETIME     NOT NULL,
  modified_by      VARCHAR(35)  NOT NULL,
  modified_time    DATETIME     NOT NULL,
  PRIMARY KEY (samuel_role_id),
  UNIQUE KEY samuel_role_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

CREATE TABLE IF NOT EXISTS samuel_role_permission (
  samuel_role_id   INT          NOT NULL,
  permission       VARCHAR(64)  NOT NULL,   -- Permission.value, e.g. view_projects
  PRIMARY KEY (samuel_role_id, permission)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

CREATE TABLE IF NOT EXISTS samuel_role_grant (
  samuel_role_grant_id INT      NOT NULL AUTO_INCREMENT,
  subject_type     VARCHAR(8)   NOT NULL,   -- user | group | apikey
  subject_name     VARCHAR(35)  NOT NULL,   -- username | adhoc_group.group_name | key name
  samuel_role_id   INT              NULL,   -- exactly one of these two is set
  permission       VARCHAR(64)      NULL,
  facility_name    VARCHAR(40)      NULL,   -- NULL = every facility
  note             VARCHAR(255)     NULL,
  created_by       VARCHAR(35)  NOT NULL,
  creation_time    DATETIME     NOT NULL,
  revoked_by       VARCHAR(35)      NULL,
  revoked_at       DATETIME         NULL,
  PRIMARY KEY (samuel_role_grant_id),
  KEY samuel_role_grant_subject (subject_type, subject_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification. All must match, or the tables are not as tested.
-- ---------------------------------------------------------------------------
SELECT (SELECT COUNT(*) FROM samuel_role) AS roles_expect_0,
       (SELECT COUNT(*) FROM samuel_role_permission) AS permissions_expect_0,
       (SELECT COUNT(*) FROM samuel_role_grant) AS grants_expect_0;

SELECT TABLE_NAME, COUNT(*) AS columns_expect_9_2_11
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'samuel_role%'
 GROUP BY TABLE_NAME ORDER BY TABLE_NAME;

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'samuel_role%'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
