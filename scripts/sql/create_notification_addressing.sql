-- ===========================================================================
-- notification_addressing — an operator-added cc/bcc for one notification
-- scope (Admin > Notifications > Addressing).
--
--   Apply with:  mysql -u <hpc-writer> -h <host> -p sam \
--                      < scripts/create_notification_addressing.sql
--
-- One address per row. `scope` is a family key (`xras`), a kind key
-- (`xras_supplement`) or a facility variant stem (`expiration-WNA`); the
-- vocabulary is sam.notify.kinds.addressing_scopes(). Rows ADD to the
-- NOTIFY_<FAMILY>_{CC,BCC} deployment defaults; deleting a row removes the
-- copy. The table is created empty; nothing changes until an operator adds
-- an address.
--
-- ORM: src/sam/notify/addressing_store.py. Pinned by
-- tests/integration/test_schema_validation.py.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. The table. (scope, field, address) is unique. Addresses are ASCII, so
-- utf8mb3 throughout. creation_time is stamped from the app clock
-- (naive-Mountain), never CURRENT_TIMESTAMP.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_addressing (
  notification_addressing_id INT NOT NULL AUTO_INCREMENT,
  scope            VARCHAR(48)      NOT NULL,        -- family | kind | kind-FACILITY
  field            VARCHAR(8)       NOT NULL,        -- cc | bcc
  address          VARCHAR(255)     NOT NULL,        -- one mailbox
  created_by       VARCHAR(35)      NOT NULL,        -- users.username
  creation_time    DATETIME         NOT NULL,        -- app clock, naive-Mountain
  PRIMARY KEY (notification_addressing_id),
  UNIQUE KEY notification_addressing_entry (scope, field, address)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

-- ---------------------------------------------------------------------------
-- 2. Verification.
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS rows_expect_0 FROM notification_addressing;

SELECT COUNT(DISTINCT INDEX_NAME) AS unique_keys_expect_1
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'notification_addressing'
   AND INDEX_NAME = 'notification_addressing_entry' AND NON_UNIQUE = 0;
