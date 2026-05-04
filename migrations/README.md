# Database migrations

Each logical database has its own Alembic environment under `migrations/<bind>/`.

| Bind | Status | Location |
|---|---|---|
| `system_status` | **active** | `migrations/system_status/` |
| `sam` (legacy) | not yet | will land at `migrations/sam/` once the legacy SAM database retirement begins |

### Why per-bind directories?

`system_status` and `sam` are two physically separate databases:
- `system_status` runs on `csg-postgres.k8s.ucar.edu` (Postgres) in prod and a
  local MySQL/MariaDB in dev.
- `sam` is the production MySQL cluster.

They never share a transaction, never reference each other via FK, and will
be stamped/upgraded on completely different schedules. Two Alembic envs in
sibling directories keep the histories cleanly separated and prevent
`alembic upgrade head` from accidentally targeting the wrong database.

## Running migrations — `system_status`

The project Makefile wraps the common operations:

```bash
make migrate-status-current             # show current revision
make migrate-status-up                  # upgrade head
make migrate-status-down                # downgrade -1
make migrate-status-revision MSG="…"    # autogenerate a new revision
make migrate-status-history             # show revision history
```

Or invoke alembic directly:

```bash
alembic -c migrations/system_status/alembic.ini current
alembic -c migrations/system_status/alembic.ini upgrade head
```

### Connection-URL resolution

`migrations/system_status/env.py` resolves the database URL in this order:

1. `ALEMBIC_SYSTEM_STATUS_URL` (test / one-off override)
2. `system_status.session.connection_string` — built from `STATUS_DB_*`
   env vars (driver, server, credentials, SSL). Same logic the runtime uses.

For production use, ensure `.env` is sourced (`source etc/config_env.sh`)
before invoking alembic.

### Tests and migrations

The pytest suite intentionally **does not** run migrations per test — the
per-worker SQLite fixture continues to use `db.create_all(bind_key='system_status')`
for speed. A single integration test (`tests/integration/test_alembic_migrations.py`)
runs `alembic upgrade head` against an empty SQLite and asserts the resulting
schema matches `StatusBase.metadata` exactly. That one test catches drift in
either direction (model added without migration, or vice versa).

### Production bootstrap

For databases that already exist with the current schema (no `alembic_version`
table), follow `migrations/system_status/PROD_BOOTSTRAP.md`.
