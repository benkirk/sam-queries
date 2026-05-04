# Phase A — `user_proj_queue_status` cut-over runbook

**Migration:** `0003_user_proj_queue_status`
**Adds tables:** `users`, `project_codes`, `user_proj_queue_status`
**Modifies:** none — this migration is **purely additive**.

## What this migration does

Adds two new lookup tables (`users`, `project_codes`) and one new snapshot
table (`user_proj_queue_status`) for per-user / per-project / per-queue
rollups parsed from PBS qstat output. The schema for `queue_status`
itself is unchanged; the ORM was refactored to share the rollup metric
columns via a mixin, but the underlying column names and types are
identical.

## Hazards

This migration creates three new tables, populates nothing, and
touches no existing rows. Risk profile:

- **Postgres** runs the migration in one transaction; failure rolls
  back cleanly. Backup recommended but not strictly required.
- **MySQL** implicitly commits on DDL — a partial failure leaves the
  three new tables in an inconsistent state. Backup recommended.
- **Active collectors** pose no problem during the migration itself
  (no destructive DDL on the tables they POST to). However, after the
  webapp is deployed with the new code, collectors will start sending
  `user_project_queues[]` arrays — make sure the migration has been
  applied first or those arrays will fail validation.

## Procedure

### 1. Backup (recommended)

```bash
# Postgres (production)
pg_dump -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
        --format=custom --file=system_status.YYYY-MM-DD.dump

# MySQL (any host)
mysqldump -u <user> -p -h <host> system_status > system_status.YYYY-MM-DD.sql
```

### 2. Dry-run

```bash
source etc/config_env.sh   # ensures STATUS_DB_* point at the target DB
alembic -c migrations/system_status/alembic.ini upgrade head --sql > /tmp/0003.sql
less /tmp/0003.sql
```

Expect: three `CREATE TABLE` statements (`users`, `project_codes`,
`user_proj_queue_status`), six `CREATE INDEX` on `user_proj_queue_status`,
and the unique-constraint creation. **No `DROP`, no `UPDATE`, no
`ALTER` to existing tables.**

### 3. Apply

```bash
make migrate-status-up
# or:
alembic -c migrations/system_status/alembic.ini upgrade head
```

Expected runtime: subsecond — no data migration.

### 4. Verify

```bash
make migrate-status-current
# Expected: "0003_user_proj_queue_status (head)"
```

```sql
-- Postgres
\dt
-- New tables visible: users, project_codes, user_proj_queue_status

-- MySQL
SHOW TABLES LIKE 'users';
SHOW TABLES LIKE 'project_codes';
SHOW TABLES LIKE 'user_proj_queue_status';
```

### 5. Deploy webapp

The new POST schema (`UserProjQueueSchema` nested in
`DerechoStatusSchema` / `CasperStatusSchema`) and the extended
`before_flush` listener ship in the same commit as the migration.
Deploy the webapp before the next collector tick or the new
`user_project_queues[]` payload key will be silently dropped (it's
optional in the schema, so existing payloads keep working).

### 6. Confirm next 5-min tick

Wait for the next collector cycle:

```sql
-- Should grow by ~50–200 rows per tick on Derecho.
SELECT MAX(timestamp), COUNT(*) FROM user_proj_queue_status;

-- Lookup tables grow as new users / projects appear.
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM project_codes;
```

## Rollback

```bash
alembic -c migrations/system_status/alembic.ini downgrade -1
# or:
make migrate-status-down
```

Drops `user_proj_queue_status`, then `project_codes`, then `users`.
The webapp must be reverted at the same time so it stops sending
`user_project_queues[]` payloads.

Outage window estimate: ~30 seconds for the migration plus ~2 minutes
per `kubectl scale` operation if collectors are restarted explicitly.
Total: under 5 minutes.
