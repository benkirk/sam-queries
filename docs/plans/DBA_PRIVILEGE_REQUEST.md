# DBA request — DDL privileges for `hpc-writer` on `sam`

**Status:** drafted 2026-08-10, not yet filed. Account and current grants
confirmed against production the same day — see § *Confirmed on production*.

**What this replaces:** the per-table DBA ticket described in
[`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md) § 2. That ticket is still
needed *this round* (§ *File both in one ticket*); the privilege grant is what
stops there being a next one.

---

## The ask

```sql
GRANT CREATE, ALTER, INDEX, REFERENCES ON `sam`.* TO 'hpc-writer'@'%';
```

Deliberately **no `DROP`**, and nothing at the global level. See
§ *What is not being asked for*.

⚠️ One thing for the DBA to check before applying: `'hpc-writer'@'%'` is the row
an operator on the UCAR VPN matches. If a **more specific** row also exists — for
the k8s pod subnet, say — MySQL matches that one instead, and a grant applied
only to `@'%'` would never reach the running webapp. We cannot see the other rows
(that needs `SELECT` on the `mysql` schema); the DBA can.

The strictly-minimal subset — enough for the three tables below and nothing
more — is `CREATE, REFERENCES`. `ALTER` and `INDEX` are requested alongside so
that a later column or index change is not a second ticket.

---

## Why

Three new tables land in `sam` with the XRAS reimplementation (PR #424) and the
notification framework (PR #428):

| Table | ORM model | DDL |
|---|---|---|
| `xras_action_log` | `src/sam/integration/xras.py:95` | `containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql` |
| `xras_activation_event` | `src/sam/integration/xras.py:244` | `.../zz-91-xras_activation_event.sql` |
| `notification_log` | `src/sam/notify/models.py:62` | `.../zz-92-notification_log.sql` |

Two facts make every such table an external ticket today:

- `migrations/README.md` records `sam` as Alembic-unmanaged — only
  `system_status` has a migration environment, so there is no automated path.
- `hpc-writer` holds `SELECT, INSERT, UPDATE, DELETE ON sam.*` and no DDL
  (`scripts/repair/RUNBOOK-missing-projects.md:38`).

The cost is documented rather than hypothetical. `XRAS_SPRINT_B.md:26` is titled
*"The decision that reorders everything: hold the DBA ticket"* — a sprint was
sequenced around keeping one ticket open long enough to add a second table to
it. `NOTIFICATION_FRAMEWORK.md:449` cut the `notification_subscription` table
partly because a follow-up would cost another full round of lead time.

---

## Evidence — measured, not inferred

Run 2026-08-10 on a throwaway `mysql:8.0.41` container (production's exact
version: `sam-sql.ucar.edu`, 8.0.41-32), with a user carrying **exactly**
`hpc-writer`'s production grant set, running the three DDL files verbatim.

### Rung 0 — current production grants

`GRANT SELECT, INSERT, UPDATE, DELETE ON sam.*` — the harness user was given
exactly this, and § *Confirmed on production* shows it is byte-for-byte what
`hpc-writer` holds today.

```
zz-90: ERROR 1142 (42000) at line 71: CREATE command denied to user 'hpc-writer'@'…' for table 'xras_action_log'
zz-91: ERROR 1142 (42000) at line 59: CREATE command denied to user 'hpc-writer'@'…' for table 'xras_activation_event'
zz-92: ERROR 1142 (42000) at line 92: CREATE command denied to user 'hpc-writer'@'…' for table 'notification_log'
```

### Rung 1 — `+ CREATE` (what a ticket asking for "CREATE TABLE" would get)

```
zz-90: ERROR 1142 (42000) at line 71: REFERENCES command denied to user 'hpc-writer'@'…' for table 'sam.xras_action_log'
zz-91: ERROR 1142 (42000) at line 59: REFERENCES command denied to user 'hpc-writer'@'…' for table 'sam.project'
zz-92: OK
```

**`CREATE` alone yields one of the three tables.** This is the whole point of
running the harness. MySQL 8.0 requires the `REFERENCES` privilege on the
*parent* table of every foreign key, and:

- `zz-90` has a **self-referential** FK (`replay_of_id` → `xras_action_log`), so
  it needs `REFERENCES` on the table it is itself creating.
- `zz-91` has FKs to `project` — a table `hpc-writer` does not create — and to
  `xras_action_log`.

A ticket written from "we need CREATE TABLE" fails here, after the lead time has
already been spent.

### Rung 2 — `+ REFERENCES` — the proven-minimal set

```
zz-90: OK    zz-91: OK    zz-92: OK
```

Post-conditions verified on the result:

- All three FK constraints present (`xras_action_log_replay_fk`,
  `xras_activation_event_project_fk`, `xras_activation_event_action_fk`).
- 27 indexes created — inline `KEY …` definitions work under `CREATE` alone; the
  `INDEX` privilege is not required for a create. (MySQL 8.0 manual: *"If you
  have the CREATE privilege for a table, you can include index definitions in
  the CREATE TABLE statement."*)
- The utf8mb3/utf8mb4 split survives a restricted-user create — exactly 7
  utf8mb4 columns (`raw_payload`, `error_messages`, `comment`, `notified_to`,
  `recipient_name`, `subject`, `error`), everything else utf8mb3, including the
  columns joined against `project.projcode`. This is the check at
  `XRAS_CUTOVER_RUNBOOK.md:30`.

---

## What is not being asked for

Stated explicitly, because it is what makes the request minimal:

| Not requested | Why |
|---|---|
| **`DROP`** | The blast-radius item. Without it the worst case is *additive*, and additive damage is recoverable; a dropped table holding an audit trail is not. (`DROP` is also what `TRUNCATE TABLE` requires.) |
| `CREATE VIEW` / `SHOW VIEW` beyond current | The six `xras_*` objects are pre-existing views; `src/sam/integration/xras_views.py` is unchanged by this work. |
| `CREATE ROUTINE`, `TRIGGER`, `EVENT` | None of the DDL uses them. |
| `CREATE TABLESPACE`, `FILE`, `RELOAD`, `SUPER`, any global-level grant | Not needed for anything SAM does. |

Verified on the harness: with the requested set and no `DROP`, `DROP TABLE` and
`ALTER TABLE` are both still denied to the account.

---

## Blast radius, pre-answered

`hpc-writer` is the credential the **running webapp** holds
(`src/sam/session/__init__.py` builds `SQLALCHEMY_DATABASE_URI` from
`SAM_DB_USERNAME`/`SAM_DB_PASSWORD`). Widening it widens what an application-level
bug could reach. Stated honestly:

- **Mitigation that is real:** no `DROP` in the ask, so the worst case is a
  spurious table or column, not data loss.
- **Mitigation that is a convention, not an enforcement:** SAM's write path is
  SQLAlchemy ORM plus `text()`-wrapped statements, and `CLAUDE.md` carries a
  standing ban on raw SQL strings. That is a code-review norm; it is not
  enforced by the database.

If that trade is unacceptable, take one of the two fallbacks below rather than
declining outright — both leave SAM better off than the status quo.

### Fallback A — a separate DDL account (recommended if the ask is refused)

```sql
CREATE USER 'hpc-ddl'@'<operator-subnet>' IDENTIFIED BY '<secret>';
GRANT CREATE, ALTER, INDEX, REFERENCES ON `sam`.* TO 'hpc-ddl'@'<operator-subnet>';
```

Used interactively by an operator only, and **never** placed in a k8s secret,
a `.env`, or any deployed config. Strictly better on security than widening
`hpc-writer`, marginally worse on convenience, and it still removes the
per-table ticket.

Note the host pattern here should be the operator VPN range, **not** `@'%'` —
this account is for humans at a terminal, so there is no reason for it to be
reachable from anywhere `hpc-writer` is. DBA's call on the exact range.

### Fallback B — table-scoped grants (narrowest; one-shot only)

Verified working on the harness. MySQL 8.0.41 **does** accept table-level grants
for tables that do not exist yet, so this can be issued ahead of the create:

```sql
GRANT CREATE, REFERENCES ON `sam`.`xras_action_log`        TO 'hpc-writer'@'%';
GRANT CREATE              ON `sam`.`xras_activation_event` TO 'hpc-writer'@'%';
GRANT CREATE              ON `sam`.`notification_log`      TO 'hpc-writer'@'%';
GRANT REFERENCES          ON `sam`.`project`               TO 'hpc-writer'@'%';
```

Measured with exactly this set: all three files run clean, and the account still
cannot create any *other* table (`CREATE command denied … for table 'evil'`),
cannot `DROP`, and cannot `ALTER`.

⚠️ This solves the current three tables and **nothing else** — the next table is
a new ticket again. It is the narrowest option, not the one that removes the
recurring cost.

---

## File both in one ticket

The grant is itself a DBA action with its own lead time. Requesting it *instead
of* the three tables trades one wait for another and leaves the XRAS cutover
blocked. So the ticket should carry **both**:

1. The `GRANT` above.
2. The three `CREATE TABLE` statements, as a transcription of
   `zz-90` / `zz-91` / `zz-92`.

If the grant is approved, future tables are self-serve. If it is refused, the
three tables still land this round. This is the same rule the runbook already
applies to the tables themselves — *"One ticket carries both tables. A second
costs another round of lead time"* (`XRAS_CUTOVER_RUNBOOK.md:69`).

⚠️ **Attach the current files.** `zz-90` gained `action_id`, `service` and
`outcome_reason` in Sprint C.1b, and the utf8mb3/utf8mb4 split is free only
while the tables do not exist — afterwards it becomes an `ALTER` on a table with
an audit trail in it.

---

## Confirmed on production

Run 2026-08-10 against `sam-sql.ucar.edu` from the UCAR VPN. Read-only; writes
nothing:

```
mysql -h sam-sql.ucar.edu -u hpc-writer -p sam -e "SHOW GRANTS FOR CURRENT_USER()"

+---------------------------------------------------------------------+
| Grants for hpc-writer@%                                             |
+---------------------------------------------------------------------+
| GRANT USAGE ON *.* TO `hpc-writer`@`%`                              |
| GRANT SELECT, INSERT, UPDATE, DELETE ON `sam`.* TO `hpc-writer`@`%` |
+---------------------------------------------------------------------+
```

Two things this settles:

1. **The account is `'hpc-writer'@'%'`** — that is the host pattern in the ask
   above, subject to the more-specific-row check flagged there.
2. **The rung-0 baseline was faithful.** The harness user was granted
   `SELECT, INSERT, UPDATE, DELETE ON sam.*` and nothing else, which is exactly
   what production holds — so the `ERROR 1142` evidence is what `hpc-writer`
   would produce, not an approximation of it.

Use bare `-p` (interactive prompt), not inline `-p'<pass>'`. Command-line options
outrank `~/.my.cnf`, so the prompt still wins — `RUNBOOK-missing-projects.md:33`'s
warning is specifically about `MYSQL_PWD`, the *environment variable*, losing to
option files. The prompt keeps the secret out of `ps` and shell history.

Not re-checked this session, and not load-bearing for the request:
`STRICT_TRANS_TABLES` and the server version. Both are already recorded in
`zz-90`'s header as measured on production (MySQL 8.0.41-32), which is the
version the harness ran.

**Scope:** production only. The Test instance and the staging RDS (creds via
`scripts/infra/db-creds-staging.sh`) are out of scope here; staging still needs
all three files run by hand, since `infrastructure/scripts/init-rds.sh` restores
the raw `.xz` with no initdb hook.

## After the grant lands

There is no privilege self-check anywhere in the repo — nothing runs
`SHOW GRANTS` / `CURRENT_USER()`. A few lines in `scripts/check_db_drift.py`
(already the read-only prod-introspection tool) would turn *"did the DBA
actually apply it"* into a command rather than a memory.
