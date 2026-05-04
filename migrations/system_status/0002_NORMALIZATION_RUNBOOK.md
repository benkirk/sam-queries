# Phase 2 normalization — cut-over runbook

**Migration:** `0002_normalize_lookups`
**Affects:** `queue_status`, `filesystem_status`, `login_node_status`,
`system_outages`, `resource_reservations`
**Adds tables:** `systems`, `queues`, `filesystems`, `login_nodes`

## What this migration does

Replaces the denormalized text columns
`system_name` / `queue_name` / `filesystem_name` / `node_name` /
`node_type` on the snapshot tables with FK references against four
new lookup tables. Backfills the lookups from distinct text values,
populates FKs via correlated UPDATE, and then **drops the legacy text
columns**. The new ORM (in the same commit) accesses them via
relationships and a `before_flush` event handler that resolves
`_pending_*_name` strings staged by the property setters.

## Hazards

- **MySQL implicit-commit-on-DDL.** Migration runs ~10 DDL statements;
  if any of them fails after one has succeeded, MySQL leaves the
  schema torn (no rollback for prior DDL). Backup-before-migrate is
  mandatory.
- **Postgres** runs the entire migration in one transaction (DDL +
  DML), so a failure rolls back cleanly. Backup is still recommended.
- **Active collectors** post every 5 minutes. If a collector's POST
  hits the webapp during the window where the schema is partially
  changed, the request will fail. Stop collectors first.

## Procedure

### 1. Stop collectors

Production collectors:

```bash
# (k8s example — adjust to deployment)
kubectl scale deployment derecho-collector --replicas=0
kubectl scale deployment casper-collector  --replicas=0
kubectl scale deployment jupyterhub-collector --replicas=0
```

Verify no further POSTs are landing (check webapp logs).

### 2. Backup the database

```bash
# MySQL
mysqldump -u <user> -p -h <host> system_status > system_status.YYYY-MM-DD.sql

# Postgres
pg_dump -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
        --format=custom --file=system_status.YYYY-MM-DD.dump
```

### 3. Pre-flight: count rows by text identifier

Run **before** the migration so you can compare totals afterwards.

```sql
-- MySQL or Postgres
SELECT system_name, COUNT(*) FROM queue_status      GROUP BY system_name;
SELECT system_name, COUNT(*) FROM filesystem_status GROUP BY system_name;
SELECT system_name, COUNT(*) FROM login_node_status GROUP BY system_name;
SELECT system_name, COUNT(*) FROM system_outages    GROUP BY system_name;
SELECT system_name, COUNT(*) FROM resource_reservations GROUP BY system_name;
```

Capture the output — you'll diff it against post-migration counts in step 5.

### 4. Run the migration

```bash
source etc/config_env.sh   # ensure STATUS_DB_* point at prod
make migrate-status-up
# or:
alembic -c migrations/system_status/alembic.ini upgrade head
```

The migration's sanity-check raises `RuntimeError` if any row fails
to map to a lookup id, aborting the migration before any destructive
DDL runs.

### 5. Post-flight: row-count parity

Run the equivalent post-migration query and confirm totals match.

```sql
-- MySQL or Postgres
SELECT s.name AS system_name, COUNT(*) FROM queue_status qs
  JOIN systems s ON s.system_id = qs.system_id GROUP BY s.name;
SELECT s.name AS system_name, COUNT(*) FROM filesystem_status fs
  JOIN systems s ON s.system_id = fs.system_id GROUP BY s.name;
SELECT s.name AS system_name, COUNT(*) FROM login_node_status lns
  JOIN systems s ON s.system_id = lns.system_id GROUP BY s.name;
SELECT s.name AS system_name, COUNT(*) FROM system_outages so
  JOIN systems s ON s.system_id = so.system_id GROUP BY s.name;
SELECT s.name AS system_name, COUNT(*) FROM resource_reservations rr
  JOIN systems s ON s.system_id = rr.system_id GROUP BY s.name;
```

Counts must match step 3 exactly.

### 6. Deploy webapp with new code

Schemas, ingest path, queries module, and ORM models all ship in
the same commit as the migration. Deploy the webapp before restarting
collectors.

### 7. Restart collectors

```bash
kubectl scale deployment derecho-collector --replicas=1
kubectl scale deployment casper-collector  --replicas=1
kubectl scale deployment jupyterhub-collector --replicas=1
```

### 8. Verify next 5-min tick lands cleanly

Wait for the next collector cycle (~5 minutes), then:

```sql
-- New rows should land with FKs populated.
SELECT MAX(timestamp) FROM queue_status;
SELECT MAX(timestamp) FROM filesystem_status;
SELECT MAX(timestamp) FROM login_node_status;

-- Lookup tables should have grown if any new system/queue/etc was
-- introduced (rare on the first tick, more likely later as queues
-- come and go).
SELECT COUNT(*) FROM systems;
SELECT COUNT(*) FROM queues;
SELECT COUNT(*) FROM filesystems;
SELECT COUNT(*) FROM login_nodes;
```

## Rollback

`alembic -c migrations/system_status/alembic.ini downgrade -1`

The downgrade re-creates the legacy text columns and backfills them
from the lookup tables, then drops the FK columns and lookup tables.
All Phase 2 ORM/code changes must be reverted at the same time
(redeploy the previous webapp build).

Outage window estimate: ~5–10 minutes for the migration itself plus
~2 minutes per kubectl scale operation. Total: ~10–15 minutes.
Collector POSTs missed during this window are not recoverable, but
the next tick fully repopulates the moving 5-minute snapshot.
