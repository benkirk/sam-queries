# PostgreSQL Migration & Dual-Backend Plan

## Overview

SAM runs today in **maintain-compatibility mode**: the production MySQL/MariaDB
database is the schema source of truth, the ORM mirrors it faithfully, and the
legacy Java SAM still shares that schema. This document plans the move off MySQL in
two horizons:

1. **Near-term — dual existence.** Run **Postgres in development** and **MySQL in
   production** from the *same* ORM, selected by connection string. Dev's Postgres DB
   is derived from the MySQL clone by `containers/sam-sql-dev/load_postgres.py`
   (`make clone-pg` for the `sam_dev` database on the `csg-postgres` CNPG cluster,
   `make clone-pg-local` for the compose `postgres` service), so MySQL stays the sole
   schema source of truth and the only hand-authored Postgres DDL is the views. The
   CNPG copy is the seed for a k8s `sam-dev`; the dev-Postgres / prod-MySQL skew is
   accepted for the overlap period, and the test suite runs on both backends to keep
   it honest.
2. **Long-term — all-Postgres.** Cut production over to Postgres. **This is the point
   at which SAM-proper comes under Alembic** (see below) — only when the legacy Java
   SAM is retired are we in complete control of the schema and out of
   compatibility mode. Until then, SAM-side migrations stay off for the SAM DB.

Stages 1 and 2 (the views, the loader, both targets) are done. What remains for the
near term is the dual-backend test harness (Stage 3), the dialect switch plus a
bounded list of query-layer fixes the harness will confirm (Stage 4), and the
`sam-dev` deployment (Stage 5).

---

## Feasibility Verdict (dual existence)

**Feasible, and less work than a naïve reading suggests — but "only ORM tweaks" is
not sufficient.** Two facts make it tractable:

1. **The engine layer is already dialect-neutral, with a working in-repo precedent.**
   `sam/session/__init__.py`'s `create_engine()` carries no charset, isolation level,
   or MySQL `SET` listeners — it runs whatever URL it's handed. The only MySQL
   lock-in is `drivername='mysql+pymysql'` (line 47) and the pymysql-only SSL
   `connect_args` (line 90). **`system_status` already solves exactly this**:
   `system_status/session/__init__.py:48-52` switches dialect on `STATUS_DB_DRIVER`
   and branches the SSL args by driver (lines 104-107). `system_status` runs one ORM
   on three dialects today — Postgres in prod, MySQL in compose, SQLite in tests.
   `psycopg2-binary` is already a hard dependency (`pyproject.toml:45`).

2. **The loader builds the Postgres schema from the ORM, so DDL portability is
   proven on every refresh.** `load_postgres.py` calls `create_all()` on an FK-free
   copy of `Base.metadata` that skips `is_view` tables, so the "views get
   materialized as empty tables" trap cannot fire; FKs, sequences and the ported
   views go on after the data. Data-shape differences (gotchas 7–8) are load-time
   coercions. The first loads surfaced three DDL gotchas (10–12); two are fixed in
   the ORM and one is handled on the copy. `tests/unit/test_load_postgres.py`
   compiles every base table for the Postgres dialect, so the next DDL surprise
   cannot land unnoticed. Timestamp server-side auto-update remains a
   *prod-cutover* concern (Fix 5), not a dev one: ORM writes stamp `modified_time`
   fine on PG, and the only raw-SQL writes (NestedSet) don't touch it.

**The one genuine hazard of a permanent split:** dev stops reproducing prod's SQL
semantics (GROUP BY strictness, implicit coercion, and — unless ported — collation),
so a MySQL-only bug can hide in dev and surface in prod. The database may stand
(the `sam_dev` copy exists and is refreshed on demand); what stays gated is the
**webapp** on Postgres. The gate is Stage 3: the suite runs against a Postgres copy
of the test database in CI, and the `sam-dev` deployment (Stage 5) waits until the
Postgres expected-failures list for the default tier is empty.

### What "only ORM tweaks" covers — and the four things it doesn't

| Bucket | ORM tweak? | Effort | Status |
|---|---|---|---|
| Session driver switch (`SAM_DB_DRIVER`), SSL branch | Yes | Small | Fix 0, Stage 4. Near-copy of `system_status/session`. |
| Timestamps, NestedSet Core `update()`, fractional `TIMESTAMP` | Yes | Small | Fixes 2 and 4, Stage 4, prod-safe via `with_variant` / Core. |
| **A. The 7 database views** | **No** | — | **Done**: `containers/sam-sql-dev/postgres/views.sql`, applied by the loader. |
| **B. PG provisioning pipeline** | **No** | — | **Done**: `containers/sam-sql-dev/load_postgres.py`, two targets (compose 5433, CNPG). |
| **C. Query-layer dialect SQL** | Partly | Bounded, fully inventoried | Category C below: four `+ INTERVAL` sites, two `YEAR()`, one `GROUP_CONCAT` cluster, one untyped bind, four unguarded `VALUES ROW()` CTEs, one `information_schema` query, four backtick `UPDATE`s. `IFNULL` has no live use. |
| **D. Case-sensitivity / collation** | **No — no code fix** | Load-time | Mitigated by the loader: an ICU `sam_ci` collation on every column MySQL declares `_ci` (gotcha 6). Residual risk is code that *relies* on case-sensitivity; none known. |

---

## Schema Management Strategy (Alembic)

**system_status: DONE.** Alembic manages the separate `system_status` DB
(`migrations/system_status/`, cross-dialect — the same revisions apply to MySQL,
Postgres, and the SQLite tempfiles the test suite uses; `render_as_batch=True`,
`compare_type=True`). See `implemented/ADD_ALEMBRIC_and_SYSTEM_STATUS_REFACTOR.md`.

**SAM-proper: DEFERRED to the all-Postgres milestone — by design, not omission.**
Today the SAM DB is mirrored read-only from the legacy Java SAM's MySQL schema
("maintain compatibility mode"): the DB is the source of truth and the ORM follows
it. Authoring Alembic migrations for a schema we don't control would fight the Java
side. SAM moves under `migrations/sam/` (already reserved in
`migrations/system_status/alembic.ini:3-5`) **only when the legacy Java SAM is
retired at prod cutover** — that is the moment we take full schema ownership. Until
then, the dev-Postgres schema is *derived* from MySQL by the loader on each refresh
and is disposable; the only hand-authored PG schema is the ported view DDL (Stage 1).

Standing recommendation for the eventual SAM Alembic env: views marked
`info: {'is_view': True}` must stay excluded from autogenerate, with view DDL managed
via `op.execute()` in hand-written, dialect-branched migrations.

---

## MySQL -> PostgreSQL Data Type Gotchas

Line numbers refreshed against the current tree (2026-09-12). Counts are from a
full-tree audit; gotchas 10–14 were found by the first loads and the harness design.

### Critical — Will Break at Runtime

#### 1. `TIMESTAMP` + `text('CURRENT_TIMESTAMP')` — widespread (18 cols + mixin)

MySQL `TIMESTAMP` columns silently auto-update on row change via
`ON UPDATE CURRENT_TIMESTAMP`. PostgreSQL has no column-level equivalent.

**Affected:** `TimestampMixin` (`base.py:84,88`), `SoftDeleteMixin` (`base.py:91-100`),
and every table inheriting them, plus individual columns in `core/groups.py`,
`activity/hpc.py`, `activity/archive.py`, `resources/facilities.py`,
`resources/resources.py`, `projects/projects.py`, `accounting/accounts.py`, and the
newer `system_status` models. Fresh audit: 18 column defs + the 2-col mixin.

**Fix:** Fix 1 below. **For the dual-backend stepping stone this is lower severity
than it looks** — ORM writes stamp `modified_time` correctly on PG via `onupdate=`;
the server-side auto-update only matters for raw-SQL writes (none touch these
columns) and for the eventual prod cutover (Fix 5).

#### 2. Backtick quoting in `NestedSetMixin` raw SQL — `base.py:403-420`

Four raw `UPDATE` statements use MySQL backtick identifiers; PostgreSQL requires
double-quotes. They are the only backticks in `src/`.

```python
# Broken on PostgreSQL:
f"UPDATE `{table}` SET tree_left = tree_left + 2 WHERE tree_left >= :pr AND `{root_col}` = :root"
```

**Fix:** Fix 2 below (SQLAlchemy Core `update()`, dialect-neutral quoting).

#### 3. `TIMESTAMP(3)` is `timezone=3`, not fractional precision — `core/users.py:703`

`UserAlias.modified_time` is declared `Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))`.
Postgres accepts `CURRENT_TIMESTAMP(3)` as a default; the trap is the type. The
generic `TIMESTAMP`'s first positional argument is `timezone`, so on Postgres the
column comes out `timestamp WITH time zone` — the only such column among 208 on the
`sam_dev` copy — while MySQL ignores the flag and drops the fractional precision the
author meant. This is the only fractional case in the tree.

**Fix:** Fix 4 below — `DateTime().with_variant(mysql.TIMESTAMP(fsp=3), 'mysql')`,
which keeps MySQL byte-identical and gives Postgres a naive timestamp.

---

### High Priority — Semantic Differences

#### 4. `Float(precision)` — 9 amount columns

`Float(15/22/25)` use MySQL binary-precision semantics; PostgreSQL ignores the
argument and gives `DOUBLE PRECISION`. All nine live on **view-backed** models
(`integration/xras_views.py:86,111,112,133,134,135`, `activity/computational.py:316-318`),
so they shape result coercion, never DDL. `Numeric(p, s)` would be exact on both
dialects and is an improvement regardless of Postgres, but it is not required for
dual ops (Fix 3, optional).

#### 5. `GROUP BY` strictness and `VALUES ROW()` — bounded

PostgreSQL enforces SQL-standard `GROUP BY`; MySQL silently picks arbitrary values.
Dev and CI already run MySQL with `ONLY_FULL_GROUP_BY`, so the ORM query layer is
mostly clean. The live risks are raw SQL:

- `queries/xras_access.py:101` groups by an output alias (`firstName`) and leans on
  `ANY_VALUE()`; Postgres wants the table columns. The portable rewrite already
  exists — it is the same logic as `xras_user` in `containers/sam-sql-dev/postgres/views.sql`.
- `queries/rolling_usage.py:68-89,152-187` build four `VALUES ROW(...)` CTEs with
  **no capability probe**; Postgres spells the row constructor `(...)`.
  `projects/projects.py:33-58` has the probe and a Python fallback; `rolling_usage.py`
  raises. `fstree_access.py` calls into it.

Stage 3 runs them against Postgres; Category C below lists the fixes.

#### 6. Case sensitivity — ported at load time, not in code

MySQL `utf8mb3_general_ci` is case- and accent-insensitive; PostgreSQL's default
collation is neither. The schema is **mixed**: 121 columns are `_ci` (usernames,
projcodes, names; 118 of them varchar/char/text) and 186 are `_bin` (email
addresses, contract numbers), so 81 `==` filters outside `system_status` would change
meaning on a naive port.

**Mitigation (Stage 3):** the loader creates an ICU collation on the copy,
`CREATE COLLATION sam_ci (provider = icu, locale = 'und-u-ks-level1', deterministic = false)`,
and declares it on exactly the columns MySQL reports as `_ci` (it already reads
`information_schema.columns` for widths; collation rides along). Primary strength
folds case and accents the way `_general_ci` does. Postgres 18 — the version on
`csg-postgres` and in compose — supports `LIKE` on nondeterministic collations, so
equality, `LIKE`, `IN` and `ORDER BY` all match MySQL with no code change. B-tree
indexes work for equality and ordering; `text_pattern_ops` indexes do not apply,
which is fine for a dev copy. Code that *relies* on case-sensitive comparison of a
`_ci` column would be the residual risk; none is known, and the harness would show
it.

---

### Medium Priority — Data Migration Concerns (handled by the loader)

#### 7. `Boolean` storage
MySQL stores `Boolean` as `TINYINT(1)`. `load_postgres.py` writes `t`/`f` for every
ORM `Boolean` column at load time; SQLAlchemy's `Boolean` reads/writes correctly
thereafter. Pipeline concern, not ORM.

#### 8. `'0000-00-00'` sentinel dates — `queries/dashboard.py:180,554`
Both surviving uses are **Python-side sort keys** (`date_group_key`), never sent to
the DB — trivial. In data, pymysql already reads a zero-date as `None`;
`load_postgres.py` writes it as NULL, or as the column default (`COPY ... DEFAULT`)
when the column is NOT NULL with a server default — `phone_type.creation_time` (6
rows) and `user_organization` (4,143) are the cases in the clone.

#### 9. `String(16384)` — `operational.py`
PostgreSQL supports `VARCHAR` to 1 GB. Confirmed in the first load; no change.

#### 10. `Boolean` + `server_default=text('0')` — `summaries/allocation_state.py` (found by the first `create_all` on PG)
An integer literal as the default of a `boolean` column is a DDL error on Postgres
(`DatatypeMismatch`). **Fix (done):** `server_default=false()` / `true()`, which
SQLAlchemy renders as `false`/`true` on both dialects (MySQL accepts the keyword).
`tests/unit/test_load_postgres.py` compiles every base table for Postgres, so the
next one cannot land unnoticed.

#### 11. Index names are schema-global on Postgres (found by the first `create_all`)
MySQL scopes an index name to its table, so several ORM tables carry the same
`Index('allocation_account_fk', ...)`-style name; Postgres rejects the second
`CREATE INDEX` with `DuplicateTable`. The names mirror the live MySQL schema and
`make check-db-vs-orms` compares them, so the ORM keeps them; `load_postgres.py`
prefixes a shared name with its table name on the Postgres copy only.

#### 12. `varchar(n)` is enforced on Postgres (found by the first data load)
MySQL never checked the ORM's `String(n)` against the live column, so a narrow
declaration went unnoticed: `users.title` was `String(15)` over a `varchar(45)`
column (fixed). `load_postgres.py` sizes every varchar from the source's
`information_schema` and prints each ORM drift it had to widen, so the ORM stays
the thing to fix while the load never truncates.

#### 13. The test suite carries MySQL-isms of its own (Stage 3 work)
- `tests/integration/test_schema_validation.py` is built on MySQL
  `INFORMATION_SCHEMA` (`STATISTICS`, `SEQ_IN_INDEX`, `GROUP_CONCAT ... ORDER BY`,
  charset assertions). It is the MySQL drift gate by design and stays MySQL-only.
- `tests/unit/test_smoke.py` counts tables through `information_schema.tables` and
  queries the schema-qualified `sam.users`, which on Postgres names a schema that
  does not exist.
- Fixture SQL in `tests/conftest.py` (`_active_project_id`, `_multi_project_user_id`,
  `_subtree_project_id`, `_inheriting_project_lookup`) and `tests/perf/conftest.py`
  compares booleans to `1`/`0`; Postgres rejects `boolean = integer`. `= TRUE` /
  `= FALSE` is valid on both.
- `tests/conftest.py` boots the read-model table from
  `scripts/sql/create_account_allocation_state.sql` (`TINYINT(1)`, inline `KEY`,
  `ENGINE=InnoDB`); on Postgres the ORM table's `create()` does the same job.
- `tests/unit/test_gid_allocation.py` compiles against `mysql.dialect()` explicitly
  and needs no database, so it is fine.
- The target allowlist in `tests/conftest.py:29-33` and its mirror in
  `test_smoke.py` check host and port only, never the driver; a Postgres target
  needs its own entries.

#### 14. `NOW()` is the server's clock on MySQL and the session's zone on Postgres
SAM's columns are naive-Mountain (`CLAUDE.md`, DateTime Handling): MySQL `NOW()` is
the server's Mountain clock, Postgres `now()` is the session `TimeZone`. The CNPG
cluster runs `America/Denver`; the compose `postgres` and test services must set
`TZ`/`PGTZ` the same way so raw-SQL `NOW()` comparisons and the one
`timestamp with time zone` column keep their meaning. Nine raw-SQL sites use
`NOW()` (`directory_access.py`, `project_access.py`, `tree_audit.py`,
`fstree_access.py`, `xras_access.py`); none needs a code change once the zone
matches.

---

## Dialect-Agnostic ORM Design

Goal: connection string alone (`mysql+pymysql://` vs `postgresql+psycopg2://`)
selects the dialect. Where a change would alter *prod MySQL* DDL/behavior, prefer
`with_variant` or Core constructs so MySQL stays byte-identical and only Postgres
diverges — the dual-backend split means prod must not shift, and
`make check-db-vs-orms` is the proof.

### Fix 0: dialect switch in `sam.session` — mirror `system_status`

Add a `SAM_DB_DRIVER` env var (default `mysql`) and branch the driver + SSL args,
exactly as `system_status/session/__init__.py:48-52,104-107` already does:

```python
driver = os.getenv('SAM_DB_DRIVER', 'mysql').lower()
dialect = 'postgresql+psycopg2' if driver in ('postgresql', 'postgres') else 'mysql+pymysql'
...
if require_ssl:
    connect_args['sslmode'] = 'require' if driver in ('postgresql', 'postgres') else ...
    # else: connect_args['ssl'] = {'ssl_disabled': False}   # pymysql
```
Plumb through `src/config.py` (`SAMConfig`, including `reload()`), the SSL branch in
`webapp/run.py:~128` (add `application_name` on Postgres as the status engine does
at `webapp/run.py:150-158`), `SAM_DB_NAME` (already read, default `sam`; `sam_dev`
on CNPG), `.env.example`, `compose.yaml` pass-through, and helm `values*.yaml`
(default mysql everywhere). Unit tests: the URL built under each driver, and the
engine options branch.

### Fix 1: `TimestampMixin` / `SoftDeleteMixin` — no change for dual ops

`func.now()` compiles to `now()` on both dialects, but the current
`text('CURRENT_TIMESTAMP')` defaults already do, and `onupdate=text('CURRENT_TIMESTAMP')`
is emitted by the ORM on both. **Caveat:** `onupdate` fires through the ORM only;
raw-SQL updates won't auto-stamp on PG (they do on MySQL). See Fix 5 for optional
server-side parity at prod cutover.

### Fix 2: `NestedSetMixin` raw SQL -> Core `update()`

Replace the four backtick statements at `base.py:403-420` with SQLAlchemy Core
`update()` constructs (dialect-neutral quoting):

```python
from sqlalchemy import update, table as sa_table, column as sa_col, bindparam
def _ns_shift(session, table_name, root_col, pr, root=None):
    t = sa_table(table_name, sa_col('tree_left'), sa_col('tree_right'), sa_col(root_col))
    left = t.c.tree_left >= bindparam('pr'); right = t.c.tree_right >= bindparam('pr')
    if root is not None:
        left &= (t.c[root_col] == bindparam('root')); right &= (t.c[root_col] == bindparam('root'))
    params = {'pr': pr, 'root': root} if root is not None else {'pr': pr}
    session.execute(update(t).where(left).values(tree_left=t.c.tree_left + 2), params)
    session.execute(update(t).where(right).values(tree_right=t.c.tree_right + 2), params)
```
`tests/unit/test_no_fstring_sql.py` pins the per-file count of f-string SQL; it
drops by four with this fix.

### Fix 3 (optional): `Float(precision)` -> `Numeric` for financial columns

```python
from sqlalchemy import Numeric
amount     = Column(Numeric(15, 2))   # was Float(15)   -- float(15,2)
remaining  = Column(Numeric(25, 8))   # was Float(25)   -- double(25,8)
```
View models only (gotcha 4); worth doing for exactness, not needed for dual ops.

### Fix 4: fractional timestamp without the `timezone` trap — `core/users.py:703`

```python
from sqlalchemy.dialects.mysql import TIMESTAMP as MYSQL_TIMESTAMP
modified_time = Column(DateTime().with_variant(MYSQL_TIMESTAMP(fsp=3), 'mysql'),
                       server_default=text('CURRENT_TIMESTAMP(3)'))
```
MySQL DDL stays `timestamp(3)`; Postgres gets `timestamp without time zone` and
accepts the `(3)` default as is.

### Fix 5 (prod-cutover only): server-side trigger DDL for PostgreSQL

Only needed at the all-Postgres milestone, if raw-SQL writes must auto-update
`modified_time`. Attach a DDL event guarded to Postgres:

```python
from sqlalchemy import event, DDL
_pg_auto_modified_ddl = DDL("""
    CREATE OR REPLACE FUNCTION _sam_update_modified_time()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN NEW.modified_time = NOW(); RETURN NEW; END; $$;
    CREATE TRIGGER trg_%(table)s_modified BEFORE UPDATE ON %(table)s
    FOR EACH ROW EXECUTE FUNCTION _sam_update_modified_time();
""")
# event.listen(cls.__table__, 'after_create',
#              _pg_auto_modified_ddl.execute_if(dialect='postgresql'))
```
Not required for the dev stepping stone (writes go through the ORM). Belongs in the
future `migrations/sam/` Alembic baseline, not the compatibility-mode ORM.

### Category C — the exact query-layer list

Every raw or dialect-specific SQL site in `src/` (excluding `system_status`, which
already runs on Postgres in production):

| Site | Construct | Portable form |
|---|---|---|
| `queries/directory_access.py:61,77,135`, `queries/project_access.py:68` | `end_date + INTERVAL :n DAY > NOW()` | bind a Python-computed cutoff: `end_date > :cutoff` — index-friendly on both |
| `queries/tree_audit.py:117-118` | `YEAR(x)` | `EXTRACT(YEAR FROM x)`, valid on both |
| `queries/xras_access.py:61-75,101` | `IF()`, nested `ANY_VALUE()`, alias `GROUP BY` | the `xras_user` form in `containers/sam-sql-dev/postgres/views.sql`: `COALESCE`, `ARRAY_AGG ... FILTER` / `MIN`, group by `u.user_id` |
| `queries/xras_access.py:269` | `GROUP_CONCAT(...)` | one-line dialect branch: `GROUP_CONCAT` on MySQL, `STRING_AGG(x::text, ',')` on Postgres |
| `queries/fstree_access.py:181` | untyped `:resource` in a `UNION ALL` select list | `CAST(:resource AS VARCHAR(40))` |
| `queries/rolling_usage.py:53,68-89,137,152-187` | `VALUES ROW(...)` CTEs, no probe | row constructor `ROW(` on MySQL, `(` on Postgres via one helper keyed on `session.bind.dialect.name`, plus the `projects/projects.py:33-58` probe so an unknown engine falls back to Python |
| `webapp/utils/config_inspect.py:232-236` | `INFORMATION_SCHEMA.COLUMNS ... DATABASE()` | `sqlalchemy.inspect(engine).get_columns()` per table |
| `base.py:403-420` | backtick `UPDATE` ×4 | Fix 2 |

The legacy-compat API blueprints keep their byte-shape; their existing tests are the
gate for the `xras_access.py` rewrite. `projects/projects.py`'s probe-guarded
`VALUES ROW()` CTEs already fall back to Python and stay as they are.

---

## Stage 3 — the dual-backend harness

The suite runs against a Postgres copy of the test database, built by the loader from
the same obfuscated blob the `mysql-test` container restores. Nothing in `src/`
changes for this stage; the deliverable is a CI leg that is green *because its
expected-failures list is honest*.

**The Postgres test service.** A `postgres-test` service in `compose.yaml` under
`profiles: [test]`: `postgres:18`, fixed credentials `sam_test`/`sam_test`,
`POSTGRES_DB: sam`, host port **5434** (5432 belongs to the peer stack, 5433 to the
`pg` profile), `TZ`/`PGTZ: America/Denver` (gotcha 14), a `pg_isready` healthcheck,
its own volume. No initdb scripts: `make clone-pg-test` in `containers/sam-sql-dev`
runs `load_postgres.py --source mysql+pymysql://root:root@127.0.0.1:3307/sam
--pg-host 127.0.0.1 --pg-port 5434 --pg-db sam` with the fixed credentials exported
by the target (test credentials are constants like `root/root`, not `.env`). In CI
the same command runs inside the `webapp` container with the service names
(`mysql-test:3306`, `postgres-test:5432`). A load takes about 35 seconds.

**Loader additions.** `CREATE COLLATION sam_ci` in `<db>_next` before `create_all`,
and `collation='sam_ci'` on every `String`/`Text` column whose MySQL collation ends
in `_ci` (gotcha 6). Unit tests cover the column→collation map and that the DDL
compiles with `COLLATE "sam_ci"`; the live check is `users.username = 'BENKIRK'`
matching on Postgres.

**`tests/conftest.py`.** The allowlist gains `(127.0.0.1, 5434)`, `(localhost, 5434)`
and `(postgres-test, 5432)`, mirrored in `tests/unit/test_smoke.py`; a session-scoped
`dialect` fixture exposes `engine.dialect.name`; markers `mysql_only` and
`postgres_only` are registered in `pytest.ini` and skipped in
`pytest_collection_modifyitems`; the read-model bootstrap uses the ORM table's
`create()` on Postgres; fixture SQL compares booleans to `TRUE`/`FALSE`.
`test_schema_validation.py` and `test_schema_drift.py` are `mysql_only` on day one;
`test_smoke.py` moves to `inspect(engine).get_table_names()` and an unqualified
`users`.

**The expected-failures file.** A `postgres_expected_failures.txt` under `tests/`,
one node id per line with a `# reason`. On a Postgres target `conftest.py` applies
`xfail(strict=True)` to each, so a test that starts passing **fails the run** until
its line is removed. This is the burn-down list and the Stage 5 gate (empty for the
default tier). It is the same house pattern as `tests/perf/baselines.json` and
`tests/stress/scenarios.json`: a declaration file the tests check themselves against.
The first version is written from one full run with `--maxfail` lifted.

**Runners.** `make pytest-pg` (local:
`SAM_TEST_DB_URL=postgresql+psycopg2://sam_test:sam_test@127.0.0.1:5434/sam pytest`)
and `make docker-pytest-pg`; in CI a second job, `pytest-postgres`, in
`.github/workflows/sam-ci-docker.yaml`: `docker compose --profile test up`, a
wait script for `postgres-test` beside `scripts/ci/wait-for-mysql.sh` (a
`docker compose exec -T postgres-test pg_isready` loop), `clone-pg-test`, then the
default tier with no coverage upload. The perf and stress tiers stay MySQL-only —
their baselines are MySQL measurements — and `e2e/` is untouched.
`.github/workflows/ci-staging.yaml` gains the leg once the list is empty.
`docs/TESTING.md` documents the second target.

---

## Suggested Sequence

Ordered so the cheapest verification precedes the expensive commitment.

### Horizon 1 — Dual existence (Postgres dev, MySQL prod)

1. **Port the 7 views to portable, tracked DDL** — DONE (PR #550):
   `containers/sam-sql-dev/postgres/views.sql`, applied by the loader after the data;
   the loader skips `is_view` tables when it builds the schema.
2. **`make clone-pg` provisioning** — DONE (PR #550). pgloader cannot connect to
   either MySQL (its driver speaks only `mysql_native_password`; the local container
   is MySQL 9.x where that plugin is gone, prod uses `caching_sha2_password`), so the
   loader is in-house: `containers/sam-sql-dev/load_postgres.py` builds the schema
   from the ORM (`Base.metadata`, FKs deferred), streams every table through `COPY`,
   re-adds the FKs (`NOT VALID` for `config.yaml`'s `unvalidated_fks`), resets
   sequences, applies the views, and renames `<db>_next` over `<db>`. Two targets,
   one loader: the compose `postgres` service (`make clone-pg-local`, host port
   5433) for offline work, and the `sam_dev` database on the `csg-postgres` CNPG
   cluster (`make clone-pg`, `SAM_DEV_PG_*`). MySQL stays the schema source of
   truth; the PG schema is disposable.
3. **The harness** — everything under Stage 3 above, one PR. Ends with the first
   honest expected-failures list and a green `pytest-postgres` job.
4. **Dialect switch and burn-down** — Fix 0, Fix 2, Fix 4 and Category C, in the
   order the failures list dictates; every fix deletes lines from the file. One PR
   per file cluster if the list is long. `make check-db-vs-orms` stays clean:
   MySQL DDL is unchanged by construction (`with_variant`, Core `update()`, no type
   changes). Validate the full suite on `mysql-test` first for every change.
5. **`sam-dev` on Postgres** — a helm dev deployment with `SAM_DB_DRIVER=postgresql`,
   `SAM_DB_NAME=sam_dev` and `SAM_DB_REQUIRE_SSL` meaning `sslmode=require`, pointing
   at the CNPG copy (the chart today exposes only `SAM_DB_SERVER`/`_USERNAME`/
   `_PASSWORD`/`_REQUIRE_SSL`); the `sam_dev` role gets an OpenBao entry;
   `sam-admin cache --refresh` after deploy. **Gate:** the expected-failures list is
   empty for the default tier.

### Horizon 2 — All-Postgres production (endgame)

6. **Retire legacy Java SAM + cut prod to Postgres.** Only now do we leave
   compatibility mode. Steps: migrate prod data with the loader against a copy; audit
   any remaining GROUP BY; run the full suite on PG; apply Fix 5 triggers if raw-SQL
   auto-stamping is required; **bring SAM under `migrations/sam/` Alembic** (stamp a
   baseline against the migrated PG schema); flip prod's `SAM_DB_DRIVER`.
7. **Drop the objects nothing uses.** Once legacy SAM is gone we own the schema and
   can retire unused views (and later tables). The inventory of retirement candidates
   — starting with the 7 views SAM's own code no longer queries — lives in
   `SCHEMA_RETIREMENT.md`; port only the objects it does not list.

---

## Summary Table

| Issue | Severity (dual) | Fix | Location |
|---|---|---|---|
| Driver hardcoded `mysql+pymysql` | Blocker | Fix 0 (`SAM_DB_DRIVER`, mirror system_status) | `sam/session/__init__.py:47,90` |
| 7 views' DDL only in MySQL dump | Done | Stage 1: `postgres/views.sql` | `containers/sam-sql-dev/postgres/views.sql` |
| No PG provisioning path | Done | Stage 2: `make clone-pg` (`load_postgres.py`, not pgloader) | `containers/sam-sql-dev/`, `compose.yaml` |
| No second test target | Blocker for Stage 5 | Stage 3 harness + expected-failures file | `compose.yaml`, `tests/conftest.py`, CI |
| `TIMESTAMP` + `CURRENT_TIMESTAMP` | Low (dual) / High (cutover) | Fix 1 (none) / Fix 5 | `base.py:84,88`; ~15 tables |
| Backtick raw SQL | Critical | Fix 2 (Core `update()`) | `base.py:403-420` |
| `TIMESTAMP(3)` means `timezone=True` | Critical | Fix 4 (`with_variant`) | `core/users.py:703` |
| `Float(precision)` | Low (view models) | Fix 3, optional | 6x `xras_views.py`, 3x `computational.py` |
| `GROUP BY` alias, nested `ANY_VALUE` | High | Category C (the `views.sql` form) | `xras_access.py:61-75,101` |
| `VALUES ROW()` without a probe | High | Category C (dialect row constructor + probe) | `rolling_usage.py` ×4 |
| `+ INTERVAL`, `YEAR()`, untyped bind, `DATABASE()` | High | Category C | `directory_access.py`, `project_access.py`, `tree_audit.py`, `fstree_access.py`, `config_inspect.py` |
| Case sensitivity | Mitigated | loader `sam_ci` ICU collation (Stage 3) | 121 `_ci` columns, no code |
| `NOW()` timezone | Medium | `TZ`/`PGTZ=America/Denver` on every Postgres service | compose, CNPG (already) |
| `Boolean` / `'0000-00-00'` / widths / index names | Handled | `load_postgres.py` at load time | data-migration concern |
| Test-suite MySQL-isms | Stage 3 | markers, portable fixtures, `test_smoke.py` rewrite | `tests/` |
| `String(16384)` | Low | No change | `operational.py` |

---

*Created: 2026-04-19 (one-shot migration). Revised: 2026-09-01 (dual-backend plan;
Alembic-for-SAM placed at the all-Postgres milestone); 2026-09-12 (Stages 1–2 done
with an in-house loader; CNPG `sam_dev` live; dual-ops harness design; collation
port; gotchas 3, 5 and 6 corrected against the first loads).*
