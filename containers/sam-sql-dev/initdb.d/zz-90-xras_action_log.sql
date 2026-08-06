-- xras_action_log — audit trail for POST /api/xras/v1/actions
--
-- WHY THIS FILE EXISTS
-- --------------------
-- The obfuscated snapshot both `mysql` and `mysql-test` restore from is dumped
-- from production, and this table does not exist in production yet: the prod
-- writer account holds SELECT/INSERT/UPDATE/DELETE and no DDL, so creating it
-- there is a DBA request with its own lead time (see
-- scripts/repair/RUNBOOK-missing-projects.md). Alembic is not an option either —
-- migrations/README.md records `sam` as unmanaged; only `system_status` has an
-- Alembic environment.
--
-- Meanwhile, adding the ORM model without the table fails two schema-validation
-- tests (tests/integration/test_schema_validation.py — test_all_tables_exist_in_database
-- and test_all_models_have_tables), and there is no allowlist for "model exists,
-- table pending". So dev and CI get the table here instead, which lets schema
-- validation pass *honestly*: the table really exists, and its indexes and column
-- types get checked like every other table's.
--
-- The stock mysql:9 entrypoint runs /docker-entrypoint-initdb.d/* in LC_ALL=C
-- sort order, and the only other entry is init-db.sh (which performs the
-- `xzcat | mysql` restore). 'i' < 'z', so this runs AFTER the restore.
--
-- SELF-RETIRING: once the DBA creates the table in production and the snapshot is
-- next regenerated, the restore already contains xras_action_log and the
-- IF NOT EXISTS makes this a harmless no-op. Delete it whenever. Nothing to undo.
--
-- ⚠️  `make docker-down` is `docker compose --profile test down` with NO -v
--     (Makefile), so it will not re-run init scripts. Picking up this table needs:
--         docker compose --profile test down -v && make docker-build && make docker-up
--
-- Column widths and charset are matched to the live schema, not guessed:
--   api_credentials.username  varchar(11)  utf8mb3
--   project.projcode          varchar(30)  utf8mb3
--   users.username            varchar(35)  utf8mb3
--   project / allocation_transaction / manual_task  ENGINE=InnoDB, utf8mb3_general_ci

USE `sam`;

CREATE TABLE IF NOT EXISTS xras_action_log (
    xras_action_log_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    received_time       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    remote_actor        VARCHAR(11)  NOT NULL,  -- api_credentials.username width
    action_type         VARCHAR(32),             -- NULL when the payload won't parse
    request_number      VARCHAR(30),             -- projcode for existing projects,
                                                 -- NCAR#### for New; project.projcode width
    raw_payload         TEXT         NOT NULL,   -- the body, verbatim, before parsing
    status              VARCHAR(16)  NOT NULL,   -- received|processed|manual|failed|replayed
    error_messages      TEXT,                    -- the ordered list, one per line
    projcode_result     VARCHAR(30),             -- diverges from request_number on the New path
    processed_time      DATETIME,
    processed_by        VARCHAR(35),             -- users.username width
    replay_of_id        INT UNSIGNED,            -- self-FK, NULL for original posts
    PRIMARY KEY (xras_action_log_id),
    KEY xras_action_log_received (received_time),
    KEY xras_action_log_status   (status),
    KEY xras_action_log_request  (request_number),
    KEY xras_action_log_replay_fk (replay_of_id),
    CONSTRAINT xras_action_log_replay_fk
        FOREIGN KEY (replay_of_id) REFERENCES xras_action_log (xras_action_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
