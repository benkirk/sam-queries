-- ===========================================================================
-- external_ticket — one row per help-desk ticket SAM filed or learned.
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/sql/create_external_ticket.sql
--
-- Polymorphic over the SAM entity (entity_type/entity_id, like
-- notification_log), so project- and allocation-level requests can use it
-- later without another DDL handoff. No URL is stored: it is derived from the
-- provider at render time. Design: docs/plans/implemented/TICKET_PROVIDER.md § 8.3.
-- ORM: src/sam/integration/tickets/models.py. Pinned by
-- tests/integration/test_schema_validation.py.
--
-- NO DROP. Created empty; rows appear once JIRA_ENABLED is on.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. NO FOREIGN KEYS, per the notification_log/account_request
-- convention. Every column is an identifier or a tracker's status name, so
-- utf8mb3 throughout: there is no human prose to widen. creation_time,
-- closed_at and synced_at come from the app clock (naive-Mountain), never
-- CURRENT_TIMESTAMP, which resolves in the server's zone.
--
-- One ticket per request is the ledger's dedup_key rule, not a constraint
-- here: a second row for one entity is legal and is the cutover-duplicate
-- signal. (provider, ticket_key) is unique.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS external_ticket (
  external_ticket_id  INT          NOT NULL AUTO_INCREMENT,
  provider            VARCHAR(32)  NOT NULL,   -- registry name: jira-servicedesk
  ticket_key          VARCHAR(32)  NOT NULL,   -- RC-40274
  entity_type         VARCHAR(32)  NOT NULL,   -- account_request
  entity_id           INT          NOT NULL,
  origin              VARCHAR(16)  NOT NULL,   -- created | learned
  requested_by        VARCHAR(35)  NOT NULL,   -- username, self, task:<name>
  status              VARCHAR(32)      NULL,   -- tracker status name, display only
  closed_at           DATETIME         NULL,   -- first sync that saw category done
  synced_at           DATETIME         NULL,   -- last successful read
  creation_time       DATETIME     NOT NULL,
  PRIMARY KEY (external_ticket_id),
  UNIQUE KEY external_ticket_provider_key (provider, ticket_key),
  KEY external_ticket_entity (entity_type, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification. All four must match, or the table is not as tested.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM external_ticket;

SELECT COUNT(*) AS columns_expect_11
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_ticket';

SELECT COUNT(DISTINCT INDEX_NAME) AS indexes_expect_3
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_ticket';

SELECT COUNT(*) AS fks_expect_0
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'external_ticket'
   AND REFERENCED_TABLE_NAME IS NOT NULL;
