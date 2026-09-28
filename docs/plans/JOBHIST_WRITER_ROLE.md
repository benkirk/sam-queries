# jobhist-sync writer role — narrowing the ingest off the Postgres superuser

**Status:** plan, 2026-09-28. No code yet; privileges below were checked read-only on
csg-postgres the same day. **See also:** `containers/ncar-hpc-deploy/README.md` (lanes, env
overlays), PR #645.
**Goal:** the routine `jobhist-sync` ingest runs as a DML-only role; schema work becomes an
explicit, rare step run by the DB owner.

## 1. Why the ingest has its own credential

There is no job_history write API: `jobhist-sync` (hpc-usage-queries) writes the per-machine
Postgres DBs `casper_jobs` / `derecho_jobs` directly. Everything else in SAM only reads
them (`accounting-comp`, the webapp jobs plugin), which is why the lane `env` carries the
SELECT-only `pguser` and only the `env.jobhist-sync` overlay carries a writer.

The ingest needs two different things:

| kind | what | when |
|---|---|---|
| DML | `jobs` INSERT ... ON CONFLICT DO NOTHING (+ bulk UPDATE under `--upsert`); `job_charges` INSERT ... ON CONFLICT DO UPDATE; `daily_summary` DELETE + INSERT; new `users`/`accounts`/`queues`/`job_qos` rows via `_get_or_create` | every run |
| DDL | `init_db(machine)`: CREATE DATABASE via the `postgres` maintenance DB if missing, `create_all`, `CREATE OR REPLACE FUNCTION fn_ensure_job_charge` + `DROP/CREATE TRIGGER trg_ensure_job_charge`, the jhublogin→uncharged rename, QoS seed rows | **every run, including `--dry-run`** (`job_history.sync.cli.sync` calls it unconditionally) |

Replacing the trigger and function requires **owning** them, and `postgres` owns every table
and the function. So today the overlay is not a scoped writer at all:

| role | casper_jobs / derecho_jobs | used by |
|---|---|---|
| `postgres` | superuser, owns everything | `env.jobhist-sync` (prod lane) |
| `pguser` | SELECT on all 10 tables; no INSERT/UPDATE/DELETE, no CREATE on `public` | lane `env`, webapp `jhDbCredentials` |
| `csg-pg`, `sam_dev`, `streaming_replica` | nothing on these DBs | — |

No reduced writer role exists yet. By precedent, roles beyond CNPG's app owner (`pguser`)
are created by hand as `postgres` (grant runbook in
`docs/plans/implemented/K8S_DEV_ENVIRONMENT.md`).

## 2. hpc-usage-queries change (peer repo, branch off `main`)

- `job_history.database.session`:
  - `init_db()` unchanged: the full setup.
  - New `check_db(machine)`: connects, confirms every `Base.metadata` table exists (via
    `sqlalchemy.inspect`) and that `trg_ensure_job_charge` exists (`pg_trigger` / `sqlite_master`).
    If anything is missing it raises `SchemaNotReady("... run jobhist-sync --init-db as the DB owner")`.
    It never writes.
- `job_history.sync.cli`:
  - New `--init-db` runs `init_db(machine)`; without dates or a log path it initializes and exits.
  - The normal path calls `check_db` instead of `init_db`. `SchemaNotReady` becomes a
    stderr line and exit 2, so cron mails.
  - SQLite: a missing DB file still falls through to `init_db`, so local and test flows are
    unchanged. Postgres never auto-creates.
- Tests: `check_db` passes on an initialized SQLite DB; it raises on a missing table and on a
  missing trigger; `--init-db` builds a fresh DB.
- README: a "Roles" note (owner runs `--init-db`, writer runs the sync) plus the GRANT
  block below.

## 3. csg-postgres role (DBA, as `postgres`, in each of casper_jobs and derecho_jobs)

```sql
CREATE ROLE jobhist_writer LOGIN PASSWORD '...';   -- once per cluster; secret in OpenBao
GRANT CONNECT ON DATABASE derecho_jobs TO jobhist_writer;
GRANT USAGE ON SCHEMA public TO jobhist_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jobhist_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jobhist_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jobhist_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO jobhist_writer;
```

`fn_ensure_job_charge` is not SECURITY DEFINER, so it runs as the inserting role, and its
`job_charges` insert is covered by the table grant. There is no TRUNCATE and no DDL.

## 4. sam-queries follow-ups (`containers/ncar-hpc-deploy/`)

- `etc/env.jobhist-sync.example`: name `jobhist_writer` (DML only), not the superuser.
- README: the overlay paragraph says "DML writer role". The gotcha "`jobhist-sync --dry-run`
  still runs `init_db()`" becomes: a schema change needs `jobhist-sync --init-db` run once by
  the owner, outside the lanes.
- `smoke/smoke.sh` is unchanged. Its jobhist-sync `--dry-run` now exercises `check_db`, so a
  schema that is behind fails the candidate with the "run --init-db" message.
- No pin bump: `containers/webapp/Dockerfile` installs hpc-usage-queries `@main` at build time.

## 5. Rollout order (the credential swap goes last)

1. Merge the hpc-usage-queries change. `postgres` still passes `check_db`.
2. A sam-queries image built after that reaches `:main`; the prod lane `update` blesses it.
3. The DBA creates `jobhist_writer` and applies §3 on both DBs.
4. Swap `lanes/prod/env.jobhist-sync` to `jobhist_writer`, then
   `ncar-hpc-deploy smoke --lane prod --image current` and one `tick --lane prod rapid`.

Swapping before step 2 breaks the ingest: the old code's `DROP TRIGGER` needs ownership.

## 6. Verification

- hpc-usage-queries test suite. On an initialized SQLite DB, `jobhist-sync --dry-run` does no
  schema writes; `--init-db` builds a fresh DB.
- Before the swap, as `jobhist_writer`, in a psql transaction that is rolled back:
  - INSERT into `jobs` (the trigger adds the `job_charges` row);
  - UPDATE `job_charges`;
  - DELETE + INSERT `daily_summary`;
  - `DROP TRIGGER` fails with "must be owner", as intended.
- After the swap:
  - prod smoke passes on casper and derecho;
  - the `tick rapid` jobhist step exits 0 and `max(jobs.id)` advances;
  - the webapp My Jobs card still reads through `pguser`.
