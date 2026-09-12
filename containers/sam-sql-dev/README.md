# SAM Database Development Environment

Tools for the local MySQL copy of SAM: clone a subset of production, anonymize
it, and dump the obfuscated blob the dev and test containers restore from.

## Scripts

| Script | Purpose |
|--------|---------|
| `bootstrap_clone.py` | Subset production into the local container (SELECT-only on prod; FK-aware sampling) |
| `cleanup_orphans.py` | Prune rows whose parents the sampler did not bring over |
| `anonymize_sam_db.py [--yes]` | Rewrite PII in place (deterministic, seeded); `--yes` skips its confirmation |
| `preview_anonymization.py` / `verify_anonymization.py` / `test_username_anonymization.py` | Preview and verify a run |
| `check_username_leak.py` | Exit non-zero if any username column still holds a real name |
| `run_anonymization_workflow.sh [--yes]` | Preview, dry-run, anonymize, verify; non-zero on any failure |

Configuration is `config.yaml`: remote and local connections, the sampling
strategies, `unvalidated_fks`, and the anonymization preserve list.

## Make targets

Run from this directory with the project conda environment active
(`source ../../etc/config_env.sh`). Production credentials come from
`PROD_SAM_DB_USERNAME` / `PROD_SAM_DB_PASSWORD` / `PROD_SAM_DB_SERVER` in the
`.env` file, the read-only `hpc-reader` account.

| Target | What it does |
|---|---|
| `make db-up` / `db-down` / `db-restart` | Start (and wait for), stop, or stop-and-start the `mysql` compose service. Data survives all three. |
| `make db-reset` | Drop the `mysql` container **and its volume** (only that volume); the next `db-up` restores `backups/sam-obfuscated.sql.xz`. |
| `make clone` | Subset production into the running container. **The result holds real PII.** 10–20 minutes, ~0.5 GB. Exits non-zero if any table, the views, or the orphan sweep failed. `SAM_CLONE_DAYS=1500 make clone` (or `--days`) deepens every `recent` window. |
| `make verify` | Leak check against the container; non-zero on a leak. |
| `make backups/sam-obfuscated.sql.xz` | Dump the committed blob. Depends on `verify`, so it cannot be built from an unanonymized database. |
| `make bootstrap` | `clean`, remove `backups/*` → `clone` → raw backup → `run_anonymization_workflow.sh --yes` → verified blob. Stops at the first failure. |
| `make db-test` | `verify` plus the username-consistency test. |

How the clone loads: schema without FK constraints, then each table in
parent-before-child order (full copy, recent-window, sampled, or emptied per
`config.yaml`), then views, then the FKs from production, then the orphan
sweep. Only a *sampled* parent restricts its children; a fully copied parent
has every row. FKs listed under `unvalidated_fks` are re-applied but not
enforced by the sweep (`disk_activity` is emptied by policy while
`disk_charge` is kept).

Other dumps (`make backups/<name>.sql.xz`) skip the verify gate and must never
be committed; `.gitattributes` routes every `*.sql.xz` through Git LFS.

## Postgres copy — `make clone-pg`

`load_postgres.py` turns the local MySQL clone into a Postgres database: the
schema comes from the ORM (`sam.base.Base.metadata`, views excluded, FKs
deferred), every table streams through `COPY` into `<db>_next`, then the FKs
(`NOT VALID` for `unvalidated_fks`), sequences and the ported views in
`containers/sam-sql-dev/postgres/views.sql` go on, and `<db>_next` is renamed over `<db>`. A row-count
mismatch or a session another role holds on `<db>` stops the swap and leaves
`<db>_next` for inspection (`--no-swap` does the same on purpose). A source
that still holds real usernames loads with a warning: PII is a public-repo
concern, and neither target is the repo.

| Target | Command | Where |
|---|---|---|
| Local container (offline work) | `make pg-up && make clone-pg-local` | compose `postgres` service on `127.0.0.1:5433`, initialized from the same `SAM_DEV_PG_USER` / `_PASSWORD` / `_DB` (changing them: `make pg-reset` first, never `down -v`, which also removes the MySQL volume) |
| `csg-postgres` CNPG cluster | `make clone-pg` | `SAM_DEV_PG_HOST/PORT/USER/PASSWORD/DB/REQUIRE_SSL` from `.env` |
| Test copy for `make pytest-pg` | `make pg-test-up && make clone-pg-test` | compose `postgres-test` service on `127.0.0.1:5434`, `sam` as `sam_test`/`sam_test`, built from `mysql-test` (3307) |

The first two targets read `.env`; nothing about the role is written into the
repo. The test target never does: its credentials are constants like
`root/root`, and CI overrides only the hosts (`PG_TEST_SOURCE_URL`,
`PG_TEST_HOST`, `PG_TEST_PORT`) to run it inside the stack by service name.

| Service targets | |
|---|---|
| `make pg-up` / `pg-down` | Start (and wait for) or stop the `postgres` service. |
| `make pg-reset` | Drop the `postgres` container and its volume only (a credential change needs a fresh initdb). |
| `make pg-test-up` / `pg-test-reset` | The same pair for `postgres-test`. |

Extra loader flags go through `CLONE_PG_ARGS`, e.g.
`make clone-pg-local CLONE_PG_ARGS=--no-swap`. The rhythm is `make bootstrap`
(or a blob restore) → `make clone-pg-local`; a raw `make clone` also loads,
with the warning. The CNPG copy is the same run with the `.env` target and is
the seed for a k8s `sam-dev`.

One-time CNPG setup, run by the operator in the primary pod
(`kubectl -n pg-testing exec csg-postgres-1 -c postgres -- psql -U postgres`):

```sql
CREATE ROLE <SAM_DEV_PG_USER> LOGIN PASSWORD '<SAM_DEV_PG_PASSWORD>' CREATEDB;
CREATE DATABASE sam_dev OWNER <SAM_DEV_PG_USER>;
```

`CREATEDB` is what lets the loader build `sam_dev_next` and swap; the role is
deliberately not a superuser. The role name and password live only in `.env`.

## Documentation

- [ANONYMIZATION_PROCESS.md](ANONYMIZATION_PROCESS.md) — what is rewritten, what is preserved, troubleshooting.
- [DOCKER_COMPOSE_CI.md](DOCKER_COMPOSE_CI.md) — how CI restores the blob.
- `docs/LOCAL_SETUP.md` — the developer setup that consumes the blob.

## Security note

The obfuscated blob is committed to a public repository. Preserved usernames
(`preserve_usernames` in `config.yaml`) keep their real data, and anything
the anonymizer cannot reach (JSON payloads) is emptied by a `mode: empty`
strategy instead. Before committing a regenerated blob, read the `verify`
output: a table reading clean may simply be empty.
