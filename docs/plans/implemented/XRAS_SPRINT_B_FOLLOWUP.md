# XRAS Sprint B follow-up — the activation worklist

**Handoff doc.** Written for a cold start. Sprint B's as-built record is
[`XRAS_SPRINT_B.md`](../XRAS_SPRINT_B.md); the wire contract and production data live
in [`XRAS_REIMPLEMENTATION.md`](../XRAS_REIMPLEMENTATION.md).

> **Status: BUILT.** Both halves shipped together on PR #424 — the table *and*
> the feature, so the schema was proven by something that renders before
> production commits to it. What actually landed, and the six deliberate
> departures from this document, are recorded in
> [`XRAS_SPRINT_B.md`](../XRAS_SPRINT_B.md) § *Deviations* item 10 and
> § *Schema deltas*. **Read those first** — three sections below are now
> superseded:
>
> | Section here | What actually happened |
> |---|---|
> | § *Notify, with SMTP still deferred* (recommends `mailto:`) | **Record-only + a "not implemented" dialog.** No `mailto:`. SMTP is a separate follow-on PR. |
> | § *The schema* (four event types) | **Five** — `restored` was added as the undo for a dismissal. Free: the column has no `ENUM`/`CHECK`. |
> | § *The card* trap 3 (`inactivate_time`) | Solved with a narrow `Project.reactivate()`, **not** by widening `Project.update`. |
>
> The § *The rule that does the real work* paragraph survives intact and is still
> the thing most worth preserving — with one extra term for `restored`.

This document exists because one decision could not wait — see below — and the
rest deliberately could.

---

## Why this document exists at all

Sprint B shipped a **pending-activation card**: XRAS projects arrive `active = 0`
by design and a human activates them, so the card lists projects an XRAS action
named that are still inactive. It stands in for the success email legacy sends and
SAM has no mailer for, which is what keeps SMTP deferred rather than a
prerequisite for the `POST /actions` cutover.

It is **read-only and fully derived** — it holds no state of its own.

The next round wants three things on it: **Notify**, **Activate / Dismiss**, and
**Comments**. All three are state SAM does not record anywhere today. And that
collides with the one deadline that matters:

> `xras_action_log` does not exist in production. The prod writer holds
> `SELECT, INSERT, UPDATE, DELETE` and **no DDL**, and Alembic manages only
> `system_status`, so creating it is a DBA request with external lead time.
> **A second request costs another round of it.**

So the schema had to be settled while the design was fresh, even though the
feature is built later. That is the whole content of this document: the DDL joins
the *current* ticket, empty and unused, and the application code follows whenever
there is room for it.

---

## Decisions

Taken with Ben, 2026-08-07:

| Question | Decision |
|---|---|
| What is the second action? | **Activate *and* Dismiss.** Activate does the real job in one click; Dismiss handles requests that should never be activated. |
| How are comments stored? | **Append-only**, not a mutable field — the model-audit log is ephemeral in k8s, so an edited-in-place note would leave no durable trace of who changed what. |
| Ship the DDL before the UI? | **Yes.** An unused table costs nothing; a second DBA ticket costs weeks. |
| Table shape? | **One append-only event table.** State is derived. |
| Scope? | **XRAS only** — not a general notification ledger (see § *The adjacent bug*). |

---

## Why a new table — the alternatives, and why they lost

Recorded so nobody re-litigates it.

**`ManualTask` (`src/sam/operational.py:25`) cannot host this.** It looks like a
generic operator-task queue and is not one: its natural key is
`(client, transaction_id, job_key, name)` — a task identified by *someone else's*
job — it carries no FK to any SAM entity, its payload is an opaque `data` blob
with an EAV child table, and it is written by an external client. Nothing in this
codebase reads or writes it. Sprint A cited it narrowly as a *column-shape*
precedent for `xras_action_log` ("this is how this schema spells an audit row"),
never as a table to reuse.

**There is nothing to extend, because nothing records notifications.** No table,
no column, no state file — verified against the full production `DESC` dump and
by grepping `notified` / `last_notified` / `notification_sent` / `email_log`
across `src/`, `tests/`, `migrations/` and `sql/`. `Synchronizer`
(`operational.py:9`) is the right *shape* for a job watermark but is per-job, not
per-recipient, so it cannot answer "did we already tell SCSG0001".

**Columns on `xras_action_log` would be actively wrong.** The card is keyed on
**project**, and several actions can name the same project — the existing query
already dedupes to the most recent. Notify state parked on "whichever action was
latest when the operator clicked" *disappears* the moment a new action arrives,
and the card re-notifies. That is precisely the spam this design exists to
prevent.

**What we do copy:** `XrasActionLog`'s rule that an operator action is recorded as
a **new linked row, never an edit of the original** (`webapp/api/xras/replay.py`
§2) — "has this been replayed" is derived from the relationship. And
`processed_by`, `varchar(35)`, `users.username` width, meaning "the human who
clicked".

---

## The schema

New init script `containers/sam-sql-dev/initdb.d/zz-91-xras_activation_event.sql`,
alongside the existing `zz-90-`. **The DBA ticket carries both files.**

```sql
-- xras_activation_event — operator actions on the XRAS pending-activation card.
--
-- Same self-retiring arrangement as zz-90-xras_action_log.sql: dev and CI get the
-- table here because it does not exist in production yet and the prod writer holds
-- no DDL. Once the DBA creates it and the snapshot is next regenerated, IF NOT
-- EXISTS makes this a harmless no-op.
--
-- ⚠️  `make docker-down` has no -v, so it will not re-run init scripts. Picking
--     this up needs:
--         docker compose --profile test down -v && make docker-build && make docker-up

USE `sam`;

CREATE TABLE IF NOT EXISTS xras_activation_event (
    xras_activation_event_id INT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- SIGNED int, deliberately: project.project_id is `int` in the live schema,
    -- and MySQL rejects a foreign key whose type does not match exactly. The
    -- surrounding xras_* tables use INT UNSIGNED for their OWN keys, so this
    -- asymmetry looks like a typo and is not.
    project_id          INT          NOT NULL,

    -- notified | dismissed | activated | comment
    event_type          VARCHAR(16)  NOT NULL,

    -- Required for 'comment'; an optional annotation on the other three.
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
    KEY xras_activation_event_project (project_id, creation_time),
    KEY xras_activation_event_type (event_type, creation_time),
    CONSTRAINT xras_activation_event_project_fk
        FOREIGN KEY (project_id) REFERENCES project (project_id),
    CONSTRAINT xras_activation_event_action_fk
        FOREIGN KEY (xras_action_log_id)
        REFERENCES xras_action_log (xras_action_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
```

**No state columns, and no `UNIQUE(project_id)`.** Current state is derived from
the events, so it can never drift from its own history, and "notified 3 times,
last by benkirk" comes free. The composite index on `(project_id, creation_time)`
serves every "latest event for this project" read.

The ORM model goes in `src/sam/integration/xras.py` beside `XrasActionLog`,
exported from `src/sam/__init__.py`:

```python
class XrasActivationEvent(Base, SessionMixin):
    """One operator action on the XRAS pending-activation card.

    Append-only: state is DERIVED, never stored. See the module docs and
    docs/plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md for why, and for the timestamp rule
    that makes this both the anti-spam and the re-open mechanism.
    """
    __tablename__ = 'xras_activation_event'
    ...
```

Use `SessionMixin` for the `update()`/`create()` convention, but **hand-roll
`creation_time` rather than `TimestampMixin`** — the mixin's
`server_default=CURRENT_TIMESTAMP` is exactly the thing the DDL comment above
rejects. `XrasActionLog` makes the same choice for the same reason.

Exporting the model auto-registers a Flask-Admin view at
`/database/default_views/xras_activation_event`, which is the entire operator
surface until the card lands — precisely how `xras_action_log` was handled
between Sprint A and Sprint B.

---

## The rule that does the real work

Do **not** store a `notified` boolean. Compare each event against the most recent
XRAS action naming that project:

```
latest_action = MAX(xras_action_log.received_time) over rows naming the project
                (projcode_result OR request_number — the join
                 get_xras_pending_activation already does)

hidden from the card     iff  latest('dismissed') > latest_action
already-notified badge   iff  latest('notified')  > latest_action
```

That single rule is both the anti-spam mechanism and the re-open mechanism, with
no episode table and no scheduled cleanup:

| Situation | Behaviour | Why it is right |
|---|---|---|
| Dismissed, then a new Extension arrives | **Reappears** | New information — the operator should look again |
| Notified, nothing has changed | Button reads "Notified 3 days ago" | Nobody is mailed twice about the same thing |
| Notified, then a new action arrives | Button offers "Notify again" | The situation changed; telling them again is appropriate |
| Dismissed, project later goes inactive by other means | Stays hidden | The operator said "not via XRAS"; accepted |

A boolean gets the first three wrong. **This is the paragraph most worth
preserving from this document.**

---

## The work, when there is room for it

### Query layer

Extend `get_xras_pending_activation` (`src/sam/queries/xras_actions.py`) to return
`dismissed`, `notified_time`, `notified_by` and `comment_count` per row, and to
apply the hide rule. One extra grouped query over `xras_activation_event`, joined
in memory against the rows already fetched — not N+1, and not a correlated
subquery per row.

The docstring's existing caveat still applies and should be extended, not
replaced: there is no provenance marker on `Project`, so the card only ever sees
projects this log knows about, and an empty card is not proof that nothing is
pending.

### The card

`templates/dashboards/allocations/partials/xras_pending_card.html` gains an
actions column and a comment affordance. Gating:

| Action | Permission |
|---|---|
| Notify / Dismiss / Comment | `MANAGE_XRAS` — exists, already gates replay |
| Activate | `MANAGE_XRAS` **and** project governance |

⚠️ **Three traps.**

1. **`active` is a `GOVERNANCE_FIELD`.** `htmx_project_update`
   (`webapp/dashboards/admin/projects_routes.py`) strips every governance key
   unless `can_edit_project_governance(current_user, project)` passes — a project
   lead with `EDIT_PROJECTS` cannot flip it. `MANAGE_XRAS` alone must not be
   enough either.
2. **Do not reuse `EditProjectForm`.** Its `active = f.Bool(load_default=False)`
   is correct for a full form — an unchecked checkbox sends no key — and
   catastrophic for a one-click activate: a partial POST that omits `active`
   *deactivates* the project. Write a narrow schema and call
   `Project.update(active=True)` directly.
3. **Decide about `Project.inactivate_time`.** The CLI sets it on deactivate
   (`cli/project/commands.py`) and nothing anywhere clears it on reactivation. An
   activate-from-card path should clear it, deliberately.

### Notify, with SMTP still deferred

**Recommended; confirm before building.** The button opens a `mailto:` with a
prefilled subject and body **and** records the `notified` event. That is a real,
working operator flow today with zero mail infrastructure, and when SMTP lands the
same button sends server-side with the schema unchanged.

The honesty caveat to design around: the event records that an operator
*initiated* a notification, not that mail was delivered. Label it "Last notified
by X" rather than "Notified" — the operator is accountable for actually sending
it. That is also why `notified_to` is stored: it is what the operator was handed.

---

## The adjacent bug — recorded here, ✅ FIXED in Sprint D

> **Update (2026-08-09).** This section was written to record the bug and
> name the moment to fix it: *"if a notification ledger is ever wanted, that
> is the moment to fold both in."* That moment arrived — see
> [`../NOTIFICATION_FRAMEWORK.md`](../NOTIFICATION_FRAMEWORK.md).
> `notification_log` now records every attempt and
> `expiration:{projcode}:{latest_end_date}:{recipient}` suppresses the
> re-send. Verified end to end against the obfuscated snapshot: 602 sent,
> then 0 sent / 602 skipped on an immediate re-run. `--force` overrides.
>
> The analysis below stands as the record of what the bug was.



`sam-admin project --upcoming-expirations --notify` **persists nothing at all.**
It recomputes its recipient list from a pure date-window query on every run
(`get_projects_by_allocation_end_date(start=now, end=now+32d)`), sends via
`EmailNotificationService.send_batch_notifications`, and discards the result after
rendering it to the terminal. No `session.add`, no commit, no state file.

So every invocation inside the 32-day window re-emails the entire roster, admin
and lead of every matching project. The only guards are `--dry-run` and the fact
that a human runs it.

It is the identical spam problem this design solves for XRAS, and it has no
ledger to prevent it. **Deliberately out of scope** — a general notification
ledger serving both would be designing for a second consumer nobody has specified,
and it is its own DBA ticket. But it is a real bug, and if a notification ledger
is ever wanted, *that* is the moment to fold both in.

---

## Verification

Nothing to verify until the DDL lands. When it does:

```bash
docker compose --profile test down -v && make docker-build && make docker-up
pytest tests/integration/test_schema_validation.py
```

Confirm the table exists in **both** databases — `mysql` *and* `mysql-test`.
`make docker-build` only rebuilds the profile-gated `mysql-test` because of the
`--profile test` fix Sprint B made to the `Makefile`; before that, a new init
script landed in `mysql` and nowhere pytest could see it:

```bash
docker compose exec mysql-test mysql -uroot -proot sam -e "DESC xras_activation_event"
```

Then look at `/database/default_views/xras_activation_event` — the Flask-Admin
view is the whole operator surface until the card is built.

The feature work verifies as Sprint B did: query-layer unit tests, HTTP-tier
auth/render smoke, a route-map snapshot regen in the same commit as the routes,
and a browser pass for the click paths (which is what caught the collapse-toggle
and contrast defects last time).
