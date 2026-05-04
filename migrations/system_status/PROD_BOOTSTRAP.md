# Production bootstrap — `system_status`

For databases that **already** have the current `system_status` schema
(no `alembic_version` table yet) — production Postgres
(`csg-postgres.k8s.ucar.edu`), production-mirror staging, and any
long-running developer DB created by the legacy `setup_status_db.py`.

The procedure is to **stamp** the existing DB at revision `0001_baseline`,
which writes the `alembic_version` table without running any DDL. From
that point forward, normal `alembic upgrade head` works.

> **DO NOT** run `alembic upgrade head` blindly against a populated DB.
> The baseline migration emits `CREATE TABLE` for every table; on an
> existing DB those calls would fail. Always stamp first.

---

## Procedure

### 1. Backup

This is non-negotiable. Phase 2 normalization will run as the next
revision and Postgres can roll back DDL+DML in one transaction, but
**MySQL implicitly commits on DDL** — a partial-state recovery requires
the backup. Do it now even though stamping is non-destructive; you'll
need it for the next revision anyway.

```bash
# MySQL
mysqldump -u <user> -p -h <host> system_status > system_status.YYYY-MM-DD.sql

# Postgres
pg_dump -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
        --format=custom --file=system_status.YYYY-MM-DD.dump
```

### 2. Verify the schema is at the baseline shape

If the existing schema has drifted from the `0001_baseline` migration,
stamping will leave you in an inconsistent state. Use the test suite's
drift test as the canonical comparator — but run it against a
*throwaway* SQLite or against a copy of the prod schema, not against
prod itself.

```bash
# Quick visual cross-check (verify expected tables exist):

# MySQL
mysql -u <user> -p -h <host> system_status -e "
  SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'system_status' ORDER BY table_name;"

# Postgres
psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "
  SELECT tablename FROM pg_catalog.pg_tables
   WHERE schemaname = 'public' ORDER BY tablename;"
```

Expected tables (10 total):

```
casper_node_type_status
casper_status
derecho_status
filesystem_status
jupyterhub_status
login_node_status
queue_status
resource_reservations
system_outages
```

(plus `alembic_version` — which doesn't exist yet, that's the whole point.)

### 3. Dry-run the upgrade in `--sql` mode

`alembic upgrade head --sql` emits the DDL that *would* be issued. For
a populated DB the only thing it should emit is the `INSERT INTO
alembic_version` row written by `stamp` — i.e. nothing destructive.

```bash
source etc/config_env.sh   # ensure STATUS_DB_* env vars are set for prod

alembic -c migrations/system_status/alembic.ini upgrade head --sql > /tmp/dryrun.sql
less /tmp/dryrun.sql        # eyeball the output
```

If the dry-run shows `CREATE TABLE` statements, **stop** — the prod DB
is missing tables and stamping is the wrong action.

### 4. Stamp at `head`

```bash
make migrate-status-stamp-head
# or
alembic -c migrations/system_status/alembic.ini stamp head
```

### 5. Verify

```bash
make migrate-status-current
# Expected output: "0001_baseline (head)"
```

```bash
# MySQL
mysql -u <user> -p -h <host> system_status -e "
  SELECT version_num FROM alembic_version;"
# → 0001_baseline

# Postgres
psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "
  SELECT version_num FROM alembic_version;"
# → 0001_baseline
```

---

## Future revisions

Once stamped, regular Alembic operations apply:

```bash
make migrate-status-up       # apply all pending revisions
make migrate-status-history  # show revision graph
make migrate-status-down     # roll back one revision
```

Phase 2 (normalization to `systems` / `queues` / `filesystems` /
`login_nodes` lookup tables) ships as the next revision. See
`migrations/system_status/0002_NORMALIZATION_RUNBOOK.md` (added in
Commit 3) for its cut-over procedure with active collectors.
