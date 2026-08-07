-- xras_activation_event — operator actions on the XRAS pending-activation card
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Same self-retiring arrangement as zz-90-xras_action_log.sql, for the same
-- reason: dev and CI get the table here because it does not exist in production
-- yet and the prod writer account holds SELECT/INSERT/UPDATE/DELETE and no DDL
-- (see scripts/repair/RUNBOOK-missing-projects.md). Alembic is not an option —
-- migrations/README.md records `sam` as unmanaged; only `system_status` has an
-- Alembic environment.
--
-- WHY IT LANDS BEFORE THE FEATURE THAT USES IT
-- --------------------------------------------
-- Creating a table in production is a DBA request with external lead time, and a
-- SECOND request costs another round of it. The pending-activation card is
-- read-only and fully derived today; giving it Notify / Activate / Dismiss /
-- Comments needs state SAM records nowhere. So the schema was settled while the
-- design was fresh, ahead of the feature, and this file joins the SAME ticket as
-- zz-90-xras_action_log.sql. Filing only the first is the mistake the ⚠️ in
-- docs/plans/XRAS_SPRINT_B.md § Schema deltas exists to prevent.
--
-- WHY THERE ARE NO STATE COLUMNS
-- ------------------------------
-- This is an APPEND-ONLY event log; current state is DERIVED, never stored, so
-- it cannot drift from its own history. In particular there is no `notified`
-- boolean and no UNIQUE(project_id). The card compares each event against the
-- most recent XRAS action naming the project:
--
--     hidden from the card  iff  latest('dismissed')
--                                    > MAX(latest_action, latest('restored'))
--     "marked notified"     iff  latest('notified')  > latest_action
--
-- That single rule is both the anti-spam mechanism (nobody is mailed twice about
-- the same thing) and the re-open mechanism (a dismissed project reappears when a
-- new Extension arrives), with no episode table and no scheduled cleanup. A
-- boolean gets both wrong. "Notified 3 times, last by benkirk" comes free.
-- Rationale in full: docs/plans/XRAS_SPRINT_B_FOLLOWUP.md.
--
-- The stock mysql:9 entrypoint runs /docker-entrypoint-initdb.d/* in LC_ALL=C
-- sort order, and the only other entries are init-db.sh (the `xzcat | mysql`
-- restore) and zz-90-. 'i' < 'z' and '0' < '1', so this runs after both — which
-- matters, because the FK below references xras_action_log.
--
-- SELF-RETIRING: once the DBA creates the table in production and the snapshot is
-- next regenerated, the restore already contains xras_activation_event and the
-- IF NOT EXISTS makes this a harmless no-op. Delete it whenever. Nothing to undo.
--
-- ⚠️  `make docker-down` is `docker compose --profile test down` with NO -v
--     (Makefile), so it will not re-run init scripts. Picking up this table needs:
--         docker compose --profile test down -v && make docker-build && make docker-up
--
-- Column widths and charset are matched to the live schema, not guessed:
--   project.project_id        int          (SIGNED — see the column comment)
--   users.username            varchar(35)  utf8mb3
--   project / allocation_transaction / manual_task  ENGINE=InnoDB, utf8mb3_general_ci

USE `sam`;

CREATE TABLE IF NOT EXISTS xras_activation_event (
    xras_activation_event_id INT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- SIGNED int, deliberately: project.project_id is `int` in the live schema,
    -- and MySQL rejects a foreign key whose type does not match exactly. The
    -- surrounding xras_* tables use INT UNSIGNED for their OWN keys, so this
    -- asymmetry looks like a typo and is not.
    project_id          INT          NOT NULL,

    -- notified | dismissed | activated | comment | restored
    --
    -- Deliberately no ENUM and no CHECK: the rest of this schema uses varchar for
    -- such columns, an ENUM change is a DBA ticket where a string is not, and the
    -- application constant (sam.integration.xras.XRAS_ACTIVATION_EVENT_TYPES,
    -- enforced in XrasActivationEvent.create) is the single enforcement point.
    event_type          VARCHAR(16)  NOT NULL,

    -- Required for 'comment' and 'dismissed'; unused by the one-click actions.
    comment             TEXT,

    -- Who was actually told. Recorded rather than derived because the project
    -- lead can change: "the current lead" and "who we notified" are different
    -- questions, and only the second one is an audit answer.
    notified_to         TEXT,

    -- PROVENANCE ONLY — which action prompted this. project_id is the key; the
    -- card is project-scoped so it survives the action log's blind spots.
    xras_action_log_id  INT UNSIGNED,

    created_by          VARCHAR(35)  NOT NULL,   -- users.username width

    -- No DEFAULT CURRENT_TIMESTAMP, for the reason zz-90 records at length: the
    -- default resolves in the MySQL *server's* timezone (UTC in the containers)
    -- while SAM's convention is naive-Mountain, and MySQL ROUNDS fractional
    -- seconds rather than truncating. Stamp from the app clock.
    creation_time       DATETIME     NOT NULL,

    PRIMARY KEY (xras_activation_event_id),
    -- Serves every "latest event for this project" read, which is every read the
    -- derive rule makes.
    KEY xras_activation_event_project (project_id, creation_time),
    KEY xras_activation_event_type (event_type, creation_time),
    KEY xras_activation_event_action_fk (xras_action_log_id),
    CONSTRAINT xras_activation_event_project_fk
        FOREIGN KEY (project_id) REFERENCES project (project_id),
    CONSTRAINT xras_activation_event_action_fk
        FOREIGN KEY (xras_action_log_id)
        REFERENCES xras_action_log (xras_action_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
