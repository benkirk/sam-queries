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
    -- Deliberately NO `DEFAULT CURRENT_TIMESTAMP`. That default resolves in the
    -- MySQL *server's* timezone (UTC in these containers) while SAM's convention
    -- is naive-Mountain, so a server-defaulted received_time lands 6 hours ahead
    -- of the datetime.now() written to processed_time — making a processed row
    -- look like it completed before it arrived. The app always sets this column
    -- from its own clock (webapp/api/xras/actions.py::_record); dropping the
    -- default is what stops the bug being re-introduced by hand-written SQL.
    received_time       DATETIME     NOT NULL,
    remote_actor        VARCHAR(11)  NOT NULL,  -- api_credentials.username width.
                                                 -- On a replay row this stays the
                                                 -- ORIGINAL actor: the bytes still
                                                 -- came from XRAS. The human who
                                                 -- clicked goes in processed_by.
    action_type         VARCHAR(32),             -- NULL when the payload won't parse
    request_number      VARCHAR(30),             -- projcode for existing projects,
                                                 -- NCAR#### for New; project.projcode width
    -- The wire's `actionId`: the only identifier for the ACTION, and therefore the
    -- idempotency key. `requestId` is deliberately NOT stored — request_number
    -- already addresses the request in the form operators use, so it would be a
    -- second key for a thing that is already addressable.
    --
    -- Measured: three identical posts produce three rows identical in every column
    -- the dashboard can filter on. The cost of not noticing is asymmetric —
    -- Extension is 60% of traffic and a double post writes nothing (the
    -- equal-end-date skip), Supplement is 15% and a double post adds a full
    -- increment. XRAS owns the retry, so this is about DETECTION, which is the part
    -- no later code change can add without a second DBA ticket.
    action_id           INT UNSIGNED,
    -- Which legacy service handled it, from sam.xras.dispatch.SERVICES. VARCHAR
    -- rather than ENUM on purpose: the vocabulary lives in Python and an ENUM here
    -- would be a second copy that drifts.
    service             VARCHAR(16),
    -- Why it parked, in words, for whoever reads the row at 3am. FOUR causes produce
    -- byte-identical rows without it — nothing matched, the type is disabled by the
    -- XRAS_ACTIONS_ENABLED triage lever, no handler is registered, or Transfer
    -- parked by design — and only Transfer is distinguishable, and only because it
    -- owns a dedicated action_type.
    --
    -- NOT folded into error_messages, which means "the 422 body XRAS received" and
    -- is a wire contract. VARCHAR(255) rather than TEXT on purpose: this is a
    -- sentence, and a bounded column cannot reproduce the overflow that loses an
    -- audit row under STRICT_TRANS_TABLES.
    outcome_reason      VARCHAR(255),
    raw_payload         TEXT         NOT NULL,   -- the body, verbatim, before parsing
    status              VARCHAR(16)  NOT NULL,   -- received|processed|manual|failed|replayed
    http_status         SMALLINT UNSIGNED,       -- the code we answered: 200|400|422.
                                                 -- status='failed' covers BOTH a malformed
                                                 -- body (400) and a schema rejection (422),
                                                 -- and triage needs to tell them apart.
    error_messages      TEXT,                    -- the ordered list, one per line
    projcode_result     VARCHAR(30),             -- diverges from request_number on the New path
    processed_time      DATETIME,
    processed_by        VARCHAR(35),             -- users.username width
    replay_of_id        INT UNSIGNED,            -- self-FK, NULL for original posts
    PRIMARY KEY (xras_action_log_id),
    KEY xras_action_log_received (received_time),
    KEY xras_action_log_status   (status),
    -- The triage axis: "failed New actions" is the 55% failure cohort, and it is
    -- the table's default filter. The standalone status index above is kept for
    -- the status-only rollups (the summary strip, the CLI's --summary).
    KEY xras_action_log_triage   (status, action_type),
    KEY xras_action_log_request  (request_number),
    -- "have I seen this action before" is a point lookup, and the answer decides
    -- whether an operator treats a row as a duplicate or a second award.
    KEY xras_action_log_action   (action_id),
    KEY xras_action_log_replay_fk (replay_of_id),
    CONSTRAINT xras_action_log_replay_fk
        FOREIGN KEY (replay_of_id) REFERENCES xras_action_log (xras_action_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
