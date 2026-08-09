-- notification_log — one row per delivery ATTEMPT, for every channel
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Same self-retiring arrangement as zz-90-xras_action_log.sql and
-- zz-91-xras_activation_event.sql, for the same reason: dev and CI get the table
-- here because it does not exist in production yet and the prod writer account
-- holds SELECT/INSERT/UPDATE/DELETE and no DDL (see
-- scripts/repair/RUNBOOK-missing-projects.md). Alembic is not an option —
-- migrations/README.md records `sam` as unmanaged; only `system_status` has an
-- Alembic environment.
--
-- ⚠️  THIS FILE JOINS THE SAME DBA TICKET AS zz-90 AND zz-91.
--     Creating a table in production is a request with external lead time, and a
--     SECOND request costs another full round of it. Filing zz-90/zz-91 without
--     this one is the mistake the ⚠️ in docs/plans/XRAS_SPRINT_B.md § Schema
--     deltas exists to prevent. Three files, one ticket.
--
-- WHY ONE TABLE AND NOT TWO
-- -------------------------
-- An earlier design shipped `notification_subscription` alongside this, DORMANT
-- — DDL, ORM and tests, no reader — on the argument that it should ride the
-- ticket already open. That argument does not survive being stated: it weighs
-- one ticket against zero, when the real comparison is one against TWO, because
-- a dormant table has no consumer to validate its shape and altering a wrong
-- shape is the same DDL, the same ticket, the same lead time. Preferences get
-- their own table when somebody has a use case.
-- Rationale in full: docs/plans/NOTIFICATION_FRAMEWORK.md § 6.
--
-- APPEND-ONLY, WITH EXACTLY ONE PERMITTED TRANSITION
-- --------------------------------------------------
-- One row per delivery ATTEMPT. A retry is a NEW row sharing the same
-- dedup_key, never an edit — the same discipline xras_activation_event keeps.
-- The sole exception is `queued` -> `sent` | `failed` (plus sent_time, error),
-- which is that row's own outcome rather than a state overwrite. That is what
-- makes the table outbox-ready: a drain can be added later with no DDL.
--
-- A process that dies between the two writes leaves the row `queued`, which
-- reads as an honest "we do not know" rather than a silent loss.
--
-- ⚠️  A `queued` row therefore participates in suppression (a process that died
--     AFTER handing the message to the relay must not re-send) — but the
--     application qualifies that arm with NOTIFY_QUEUED_STALE_SECONDS.
--     Without the horizon, one crash BEFORE the relay suppresses that recipient
--     permanently, since the two crashes leave indistinguishable rows.
--
-- WHY A GENERIC entity_type/entity_id AND NO FOREIGN KEY
-- -----------------------------------------------------
-- A notification is about whatever prompted it: a project today, an allocation
-- or a user tomorrow, and for an unmapped XRAS path nothing at all. A column
-- per entity is a forest of nullable FKs that grows with every new kind, and
-- EACH ADDITION IS A DBA TICKET — precisely the cost this design exists to
-- avoid. The trade is no referential integrity, which is correct for an
-- append-only historical record: a deleted parent must not cascade the evidence
-- away. `projcode` is denormalized beside it because "did we mail anyone about
-- SCSG0001" is the one query that matters, and because the ledger has to stay
-- readable after a project is renamed.
--
-- CHARSET SPLIT — NOT COSMETIC, IN BOTH DIRECTIONS
-- -----------------------------------------------
-- Table default utf8mb3, with utf8mb4 ONLY on columns holding human text
-- (recipient_name, subject, error). utf8mb3 cannot hold a 4-byte character at
-- all: under STRICT_TRANS_TABLES one emoji in a subject line raises error 1366
-- and the ledger row is LOST — the same failure zz-90 records for raw_payload,
-- and losing the record of a mail you sent is worse than sending none.
--
-- The reverse matters just as much. `projcode` MUST stay utf8mb3 because it is
-- compared against project.projcode (utf8mb3_general_ci); commit 5aef6bb
-- measured a utf8mb4 value there turning a `const` index seek into a 4,650-row
-- index scan, and called the split a cutover precondition. Addresses stay
-- utf8mb3 too: they are ASCII by RFC and they are indexed.
--
-- The stock mysql:9 entrypoint runs /docker-entrypoint-initdb.d/* in LC_ALL=C
-- sort order. This runs after init-db.sh, zz-90- and zz-91-; it references none
-- of them, so the ordering is convention rather than a requirement.
--
-- SELF-RETIRING: once the DBA creates the table in production and the snapshot is
-- next regenerated, the restore already contains notification_log and the
-- IF NOT EXISTS makes this a harmless no-op. Delete it whenever. Nothing to undo.
--
-- ⚠️  `make docker-down` is `docker compose --profile test down` with NO -v
--     (Makefile), so it will not re-run init scripts. Picking up this table needs:
--         docker compose --profile test down -v && make docker-build && make docker-up
--
-- Column widths are matched to the live schema, not guessed:
--   project.projcode   varchar(30)  utf8mb3
--   users.username     varchar(35)  utf8mb3
--   email addresses    varchar(255) — RFC 5321 maximum path length

USE `sam`;

CREATE TABLE IF NOT EXISTS notification_log (
    notification_log_id INT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- A sam.notify.kinds.NOTIFICATION_KINDS key ('expiration',
    -- 'xras_activation'). Deliberately no ENUM and no CHECK, matching
    -- xras_activation_event.event_type: the rest of this schema uses varchar
    -- for such columns, an ENUM change is a DBA ticket where a string is not,
    -- and NOTIFICATION_KINDS (enforced in NotificationLog.create) is the single
    -- enforcement point.
    kind                VARCHAR(32)  NOT NULL,

    -- 'email' today; 'slack' is declared in the application enum with no
    -- transport behind it.
    channel             VARCHAR(16)  NOT NULL,

    -- Which transport actually handled it: smtp | null | console. Recorded
    -- rather than derived from config, because config changes and the row is
    -- evidence about the past.
    transport           VARCHAR(16)  NOT NULL,

    -- queued | sent | failed | suppressed | redirected
    --
    -- `redirected` is deliberately distinct from `sent`: under
    -- NOTIFY_REDIRECT_TO a message really was delivered, but NOT to its
    -- subject, and a ledger that called that `sent` would be lying about the
    -- one fact it exists to record.
    status              VARCHAR(16)  NOT NULL,

    -- The address actually handed to the transport (post-redirect).
    recipient           VARCHAR(255) NOT NULL,

    -- Set ONLY when a redirect happened — who it was really for. NULL is the
    -- normal case and means "recipient is the subject".
    intended_recipient  VARCHAR(255),

    -- Human text: a display name can carry anything a name can carry.
    recipient_name      VARCHAR(255) CHARACTER SET utf8mb4
                                     COLLATE utf8mb4_general_ci,

    -- lead | admin | user | operator — the caller's domain vocabulary,
    -- recorded and not interpreted.
    recipient_role      VARCHAR(16),

    -- Human text, and the single most likely place a 4-byte character appears.
    subject             VARCHAR(255) CHARACTER SET utf8mb4
                                     COLLATE utf8mb4_general_ci,

    -- The TEXT template actually chosen, e.g. 'expiration-WNA.txt'. This is
    -- what makes the facility fallback auditable after the fact: "which letter
    -- did this PI actually get" has no other answer, since bodies are not
    -- stored.
    template            VARCHAR(64),

    -- What the notification was about. No FK, deliberately — see the header.
    entity_type         VARCHAR(32),
    entity_id           INT,

    -- Denormalized. utf8mb3 is load-bearing: it is compared against
    -- project.projcode. See the CHARSET note in the header.
    projcode            VARCHAR(30),

    -- The suppression key, built by the caller from the INTENDED recipient
    -- (never the redirect target — otherwise a whole staging run collapses
    -- onto one key). NULL means "never suppress this one".
    dedup_key           VARCHAR(128),

    -- Human-ish text: a relay's rejection message is free-form and may be
    -- anything. Truncated defensively in Python before it gets here.
    error               TEXT         CHARACTER SET utf8mb4
                                     COLLATE utf8mb4_general_ci,

    -- users.username of the human who asked, or 'cli' / 'system' when
    -- unattended. users.username width.
    requested_by        VARCHAR(35)  NOT NULL,

    -- No DEFAULT CURRENT_TIMESTAMP, for the reason zz-90 records at length: the
    -- default resolves in the MySQL *server's* timezone (UTC in the containers)
    -- while SAM's convention is naive-Mountain, and MySQL ROUNDS fractional
    -- seconds rather than truncating. Stamp from the app clock.
    creation_time       DATETIME     NOT NULL,

    -- When the outcome was learned. NULL while `queued`.
    sent_time           DATETIME     NULL,

    PRIMARY KEY (notification_log_id),

    -- The suppression query: "has this key been used", newest first.
    KEY notification_log_dedup (dedup_key, creation_time),
    -- The facet chips and the admin card's per-kind / per-status counts.
    KEY notification_log_kind (kind, creation_time),
    KEY notification_log_status (status, creation_time),
    -- "What have we sent this person", and the free-text recipient filter.
    KEY notification_log_recipient (recipient, creation_time),
    -- "Everything about this project" — the query the denormalized projcode
    -- exists for.
    KEY notification_log_projcode (projcode, creation_time),
    KEY notification_log_entity (entity_type, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
