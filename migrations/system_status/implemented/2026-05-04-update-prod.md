# `update-prod.md` — CIRRUS `system_status` DB upgrade session brief

**Purpose:** standalone, self-contained instructions for a fresh Claude
session to perform the **DB-side** of the Phase 2 normalization upgrade
on CIRRUS production Postgres (`csg-postgres.k8s.ucar.edu`). All
collector/webapp/k8s operations remain manual and are handled by Ben.

This is a one-shot operational doc; once the upgrade has happened, it
can be deleted.

## Confidence checkpoint (what we already know works)

The migration was exercised end-to-end against the **local MySQL DB**
with **populated data** before this runbook was finalized:

| Source table         | Rows backfilled | NULL FKs after |
|----------------------|----------------:|---------------:|
| `queue_status`       | 54,412          | 0              |
| `filesystem_status`  | 40,158          | 0              |
| `login_node_status`  | 46,957          | 0              |
| `system_outages` + `resource_reservations` | (small) | 0 |

Lookup tables populated correctly: 3 systems (derecho, casper, an
`all` row from outage records), 34 queues, 5 filesystems, 14 login-node
defs. Legacy text columns dropped. Collector POSTs returned **201**
on the next 5-minute tick.

A **MySQL-specific collation fix** was discovered during this exercise
(`utf8mb4_0900_ai_ci` on new lookup tables vs `utf8mb4_unicode_ci` on
existing tables) and folded into the migration. The fix is gated on
`op.get_bind().dialect.name == "mysql"`, so it is a **no-op on
Postgres** — but it means CIRRUS prod (Postgres) is taking a path that
was not directly exercised on populated data. Mitigations: the
migration runs in a single Postgres transaction (clean rollback on
any error), and the optional pre-flight below lets us dry-run against
a Postgres snapshot before touching prod.

## Optional pre-flight: dry-run against a local Postgres snapshot

Recommended but skippable. Adds ~10 minutes; eliminates Postgres-side
surprises before we touch prod.

```bash
# 1. Snapshot prod (Step 2 of the main procedure does this anyway).
pg_dump -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
        --format=custom \
        --file=/tmp/cirrus_backup/system_status.$(date -I).dump

# 2. Spin up a throwaway local Postgres.
docker run --rm -d --name pg-dryrun \
  -e POSTGRES_PASSWORD=dryrun \
  -p 5433:5432 postgres:18

# 3. Restore the snapshot.
sleep 5
PGPASSWORD=dryrun psql -h 127.0.0.1 -p 5433 -U postgres \
  -c "CREATE DATABASE system_status"
PGPASSWORD=dryrun pg_restore -h 127.0.0.1 -p 5433 -U postgres \
  -d system_status /tmp/cirrus_backup/system_status.$(date -I).dump

# 4. Run the same stamp + upgrade against the throwaway Postgres.
ALEMBIC_SYSTEM_STATUS_URL='postgresql+psycopg2://postgres:dryrun@127.0.0.1:5433/system_status' \
  alembic -c migrations/system_status/alembic.ini stamp 0001_baseline

ALEMBIC_SYSTEM_STATUS_URL='postgresql+psycopg2://postgres:dryrun@127.0.0.1:5433/system_status' \
  alembic -c migrations/system_status/alembic.ini upgrade head

# 5. Tear down.
docker stop pg-dryrun
```

If step 4 succeeds against the prod snapshot, prod is safe.

---

## Pre-restart checklist (Ben does these BEFORE restarting Claude)

1. **Branch + repo state**
   ```bash
   cd /Users/benkirk/codes/project_samuel/devel
   git checkout alembric_system_status
   git status            # working tree should be clean except untracked data/, docs/presentations/*
   git log --oneline -5  # top should be `Document Alembic in README + link to migrations/README.md`
   ```

2. **Conda env activated:**
   ```bash
   source etc/config_env.sh
   alembic --version
   psql --version
   pg_dump --version
   ```

3. **`.env` switched to CIRRUS** — uncomment all five CIRRUS lines (the
   bottom block plus `STATUS_DB_DRIVER=postgresql`):
   ```bash
   STATUS_DB_DRIVER=postgresql
   STATUS_DB_USERNAME=${PROD_STATUS_DB_USERNAME}
   STATUS_DB_PASSWORD=${PROD_STATUS_DB_PASSWORD}
   STATUS_DB_SERVER=${PROD_STATUS_DB_SERVER}
   ```
   Re-source `etc/config_env.sh` after editing.

4. **VPN up** to `csg-postgres.k8s.ucar.edu`. Verify:
   ```bash
   psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "SELECT 1"
   ```
   (Password should be picked up from `.env` via the user's psql config,
   or use `PGPASSWORD=…` for a one-shot connection.)

5. **Backup directory** decided. Suggested:
   ```bash
   mkdir -p /tmp/cirrus_backup
   ```

6. **Restart Claude** with prompt like:
   > go — `.env` points at prod, VPN up, on branch `alembric_system_status`.
   > Use `update-prod.md` as your runbook.

---

## What Claude will do (Phase 1 — non-destructive, no pause needed)

### Step 1: Sanity-check `.env` resolution

```bash
echo "driver=$STATUS_DB_DRIVER server=$STATUS_DB_SERVER db=${STATUS_DB_NAME:-system_status}"
# Expected: driver=postgresql server=csg-postgres.k8s.ucar.edu db=system_status
```

If anything is wrong, **stop and report**.

### Step 2: Backup prod DB (mandatory)

```bash
pg_dump -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
        --format=custom \
        --file=/tmp/cirrus_backup/system_status.$(date -I).dump
ls -lh /tmp/cirrus_backup/system_status.$(date -I).dump
```

Report the file size. Anything < 100 KB is suspect for a populated DB.

### Step 3: Verify schema shape matches `0001_baseline`

```bash
psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "
  SELECT tablename FROM pg_catalog.pg_tables
   WHERE schemaname = 'public' ORDER BY tablename;"
```

Expected exactly these 9 tables (no `alembic_version` yet, no
`systems`/`queues`/`filesystems`/`login_nodes` yet):

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

If any are missing or any extra tables exist, **stop and report**.

### Step 4: Pre-flight row counts

```bash
psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
     -A -F$'\t' -t -c "
  SELECT 'queue_status'        AS tbl, system_name, COUNT(*)
    FROM queue_status      GROUP BY system_name
  UNION ALL SELECT 'filesystem_status', system_name, COUNT(*)
    FROM filesystem_status GROUP BY system_name
  UNION ALL SELECT 'login_node_status', system_name, COUNT(*)
    FROM login_node_status GROUP BY system_name
  UNION ALL SELECT 'system_outages',    system_name, COUNT(*)
    FROM system_outages    GROUP BY system_name
  UNION ALL SELECT 'resource_reservations', system_name, COUNT(*)
    FROM resource_reservations GROUP BY system_name
  ORDER BY tbl, system_name;
" | tee /tmp/cirrus_backup/preflight_counts.txt
```

Show Ben the full output.

### Step 5: Confirm `alembic_version` is missing

```bash
make migrate-status-current
# Expected: blank or "(none)" — no version row yet
```

If it shows a revision already, the DB has been stamped previously —
**stop and ask Ben** before proceeding.

### Step 6: Stamp at `0001_baseline` (NOT head)

> **CRITICAL:** Use the explicit revision form. `make migrate-status-stamp-head`
> would stamp at the *current head* (`0002_normalize_lookups`),
> which would skip the destructive migration entirely. We need
> `0002` to remain pending.

```bash
alembic -c migrations/system_status/alembic.ini stamp 0001_baseline
```

Then verify:

```bash
make migrate-status-current
# Expected: 0001_baseline

psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "
  SELECT version_num FROM alembic_version;"
# Expected: 0001_baseline
```

Show Ben both outputs.

### Step 7: PAUSE — wait for Ben's go-ahead

Report:

> Phase 1 complete. Stamped at `0001_baseline`. The `0002_normalize_lookups`
> migration is pending and is destructive (drops legacy text columns).
> **Do not proceed until Ben confirms collectors AND webapp are scaled to 0.**

Wait for explicit go-ahead like "ok, scaled down, proceed" before Step 8.

---

## What Claude will do (Phase 2 — destructive, AFTER Ben confirms scale-down)

### Step 8: Apply `0002_normalize_lookups`

```bash
make migrate-status-up
# Postgres runs DDL + DML in one transaction.
# The migration's sanity-check raises RuntimeError before any
# destructive DDL if backfill leaves any FK NULL — in that case
# the entire transaction rolls back automatically.
```

If migration fails: report stderr verbatim, **stop**, do not retry.
The DB is back at `0001_baseline` shape (Postgres rolled back).

### Step 9: Verify head

```bash
make migrate-status-current
# Expected: 0002_normalize_lookups (head)

psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status -c "
  SELECT version_num FROM alembic_version;
  SELECT COUNT(*) FROM systems;
  SELECT COUNT(*) FROM queues;
  SELECT COUNT(*) FROM filesystems;
  SELECT COUNT(*) FROM login_nodes;"
```

`systems` should have ~3 rows (derecho/casper/jupyterhub).
`queues`/`filesystems`/`login_nodes` should each have a handful of
distinct names. Show Ben the output.

### Step 10: Post-flight row-count parity

```bash
psql -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
     -A -F$'\t' -t -c "
  SELECT 'queue_status'        AS tbl, s.name, COUNT(*)
    FROM queue_status qs
    JOIN systems s ON s.system_id = qs.system_id GROUP BY s.name
  UNION ALL SELECT 'filesystem_status', s.name, COUNT(*)
    FROM filesystem_status fs
    JOIN systems s ON s.system_id = fs.system_id GROUP BY s.name
  UNION ALL SELECT 'login_node_status', s.name, COUNT(*)
    FROM login_node_status lns
    JOIN systems s ON s.system_id = lns.system_id GROUP BY s.name
  UNION ALL SELECT 'system_outages',    s.name, COUNT(*)
    FROM system_outages so
    JOIN systems s ON s.system_id = so.system_id GROUP BY s.name
  UNION ALL SELECT 'resource_reservations', s.name, COUNT(*)
    FROM resource_reservations rr
    JOIN systems s ON s.system_id = rr.system_id GROUP BY s.name
  ORDER BY tbl, name;
" | tee /tmp/cirrus_backup/postflight_counts.txt

diff /tmp/cirrus_backup/preflight_counts.txt /tmp/cirrus_backup/postflight_counts.txt
```

The diff should be **empty** — per-system counts must match exactly.
If they don't, report the diff and stop. (Backup is at
`/tmp/cirrus_backup/system_status.<date>.dump`.)

### Step 11: Hand back to Ben

Report:

> Phase 2 complete. DB at `0002_normalize_lookups (head)`. Pre/post
> row-count diff is clean. Ready for Ben to:
> 1. Merge PR #223 to staging.
> 2. Build & deploy the new webapp image.
> 3. Scale webapp back up.
> 4. Restart collectors.
>
> Backup retained at `/tmp/cirrus_backup/system_status.<date>.dump`.

---

## Failure handling

### Migration fails mid-flight (Step 8)

Postgres rolls back the entire transaction. DB is back at
`0001_baseline` shape. Report stderr to Ben verbatim. Common causes:

- **`RuntimeError: backfill: N rows in <tbl>.<col> could not be mapped`**
  — a row had a `system_name` (or similar) that doesn't appear in the
  union of distinct values. Should be impossible in practice; investigate
  data before retry.
- **Connection drop** — VPN or network. Re-establish, retry from Step 8
  (the stamp at `0001_baseline` is durable; only Step 8 needs to repeat).
- **Permission error** — postgres user lacks DDL rights. Stop, escalate
  to Ben.

### Recovery from a partial-state DB (MySQL only — included for reference)

This won't apply on CIRRUS (Postgres rolls back cleanly), but for any
future MySQL `system_status` DB stuck mid-migration: MySQL implicitly
commits each DDL, so a failure mid-upgrade leaves lookup tables and FK
columns lying around with `alembic_version` still at `0001_baseline`.
We exercised this exact recovery on local MySQL before finalizing the
runbook. Recipe:

```sql
SET FOREIGN_KEY_CHECKS=0;

-- Drop FK columns from snapshot tables.
ALTER TABLE queue_status      DROP FOREIGN KEY fk_queue_status_system_id_systems,
                              DROP FOREIGN KEY fk_queue_status_queue_id_queues,
                              DROP COLUMN system_id, DROP COLUMN queue_id;
ALTER TABLE filesystem_status DROP FOREIGN KEY fk_filesystem_status_system_id_systems,
                              DROP FOREIGN KEY fk_filesystem_status_filesystem_id_filesystems,
                              DROP COLUMN system_id, DROP COLUMN filesystem_id;
ALTER TABLE login_node_status DROP FOREIGN KEY fk_login_node_status_system_id_systems,
                              DROP FOREIGN KEY fk_login_node_status_login_node_def_id_login_nodes,
                              DROP COLUMN system_id, DROP COLUMN login_node_def_id;
ALTER TABLE system_outages    DROP FOREIGN KEY fk_system_outages_system_id_systems,
                              DROP COLUMN system_id;
ALTER TABLE resource_reservations DROP FOREIGN KEY fk_resource_reservations_system_id_systems,
                                  DROP COLUMN system_id;

-- Drop the lookup tables (in FK-dependency order).
DROP TABLE login_nodes;
DROP TABLE filesystems;
DROP TABLE queues;
DROP TABLE systems;

SET FOREIGN_KEY_CHECKS=1;
```

After this, `alembic current` should still report `0001_baseline` and
the existing snapshot data is intact. Re-run `make migrate-status-up`
to apply the (fixed) Phase 2 migration.

### Phase 2 succeeded but Ben spots a problem after deploy

Ben handles webapp/collector rollback. For DB rollback Claude can do:

```bash
alembic -c migrations/system_status/alembic.ini downgrade -1
```

But **only after** Ben has scaled webapp + collectors back to 0. The
downgrade re-creates legacy text columns and backfills them from the
lookup tables before dropping the FKs.

### Catastrophic — restore from backup

```bash
# Drop & recreate schema (PSEUDOCODE — Ben confirms first):
psql -U postgres -h csg-postgres.k8s.ucar.edu -d postgres -c "
  DROP DATABASE system_status;
  CREATE DATABASE system_status;"

pg_restore -U postgres -h csg-postgres.k8s.ucar.edu -d system_status \
           --clean --if-exists \
           /tmp/cirrus_backup/system_status.<date>.dump
```

Claude should **never** initiate restore without explicit Ben go-ahead
for each command.

---

## Safety guardrails Claude observes

- **No `kubectl`.** All k8s ops are Ben's.
- **No `git push` / merge / branch deletion.** All git ops are Ben's.
- **No edits to `.env`.** Ben manages the toggle in/out of prod.
- **Hard pause at Step 7.** Wait for explicit confirmation that
  collectors AND webapp are at 0 replicas before destructive Step 8.
- **`alembic stamp head` is forbidden.** Always `stamp <explicit-revision>`.
- **Failures are read-only events.** On any error, stop and report —
  do not retry without Ben.
- **All commands must be shown** in Claude's response so Ben can audit
  before/after.

---

## Post-upgrade cleanup (Ben's checklist, AFTER everything is verified)

1. Re-comment the CIRRUS toggle block in `.env` so local dev tooling
   no longer points at prod.
2. `git push origin alembric_system_status` if any local fixups
   happened during the session.
3. Merge PR #223 (staging → main per normal flow).
4. Delete this `update-prod.md` (or move to `docs/runbooks/`) once
   the upgrade is complete and stable.
