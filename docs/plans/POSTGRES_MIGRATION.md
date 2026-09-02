# PostgreSQL Migration & Dual-Backend Plan

## Overview

SAM runs today in **maintain-compatibility mode**: the production MySQL/MariaDB
database is the schema source of truth, the ORM mirrors it faithfully, and the
legacy Java SAM still shares that schema. This document plans the move off MySQL in
two horizons:

1. **Near-term — dual existence.** Run **Postgres in development** and **MySQL in
   production** from the *same* ORM, selected by connection string. Dev's Postgres DB
   is derived from the existing obfuscated MySQL clone via pgloader (bounded in size
   like `make clone`), so MySQL stays the sole schema source of truth and no
   Postgres schema is authored by hand except the views. This is a low-risk stepping
   stone that shakes out dialect issues continuously.
2. **Long-term — all-Postgres.** Cut production over to Postgres. **This is the point
   at which SAM-proper comes under Alembic** (see below) — only when the legacy Java
   SAM is retired are we in complete control of the schema and out of
   compatibility mode. Until then, SAM-side migrations stay off for the SAM DB.

The near-term work is mostly a superset of "make the ORM dialect-agnostic," plus one
genuinely new artifact (portable view DDL) and one new pipeline (`make clone-pg`).

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

2. **Deriving dev-Postgres via pgloader defuses most Critical items**, because they
   were framed around `create_all()` and a one-shot cutover:
   - `create_all()` is **never** called against the SAM Base (dev and test both load
     the obfuscated MySQL dump). Since pgloader builds the PG schema, the "views get
     materialized as empty tables" trap cannot fire.
   - pgloader handles TINYINT->bool, `'0000-00-00'` coercion, and charset conversion
     at load time — pipeline config, not code.
   - Timestamp server-side auto-update becomes a *prod-cutover* concern (Fix 5), not
     a dev one: ORM writes stamp `modified_time` fine on PG, and the only raw-SQL
     writes (NestedSet) don't touch it.

**The one genuine hazard of a permanent split:** dev stops reproducing prod's SQL
semantics (case sensitivity, GROUP BY strictness, implicit coercion), so a
MySQL-only bug can hide in dev and surface in prod. The mitigation that makes it
sound rather than a footgun is **running the test suite on both backends in CI**
(Stage 3). Do not stand up a standing PG dev instance (Stage 4) until the suite is
green on PG.

### What "only ORM tweaks" covers — and the four things it doesn't

| Bucket | ORM tweak? | Effort | Why |
|---|---|---|---|
| Session driver switch (`SAM_DB_DRIVER`), SSL branch | Yes | Small | Near-copy of `system_status/session`. |
| Timestamps, `Float`->`Numeric`, NestedSet Core `update()` | Yes | Small–moderate | Fixes 1–4 below, prod-safe via `with_variant`. |
| **A. The 7 database views** | **No** | Moderate, net-new maintenance | SELECT DDL lives *only* as MySQL mysqldump (git-ignored `containers/sam-sql-dev/dump/views.sql` + the LFS blob) — `any_value()`, `group_concat(... separator)`, backticks, `union all`. Must be hand-ported to PG and tracked in-repo. |
| **B. PG provisioning pipeline** | **No** | Moderate, new infra | No `mysqldump\|psql` path. Need `make clone-pg`: pgload the obfuscated MySQL into PG, then apply the ported view DDL. New compose `postgres` service. |
| **C. Query-layer dialect SQL** | Partly | Bounded sweep | Backtick UPDATEs, `IFNULL`/`CONCAT`/`+ INTERVAL n DAY`/`YEAR()`/`GROUP_CONCAT`, `DATABASE()`, ~15 GROUP-BY candidates. |
| **D. Case-sensitivity / collation** | **No — no code fix** | Permanent risk | ~114 `==` filters are case-insensitive on MySQL (`_general_ci`), case-sensitive on PG. Shaken out by Stage 3, mitigated per-column (`citext`/`func.lower()`/pgloader COLLATE cast). |

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
then, the dev-Postgres schema is *derived* from MySQL by pgloader on each refresh and
is disposable; the only hand-authored PG schema is the ported view DDL (Stage 1).

Standing recommendation for the eventual SAM Alembic env: views marked
`info: {'is_view': True}` must stay excluded from autogenerate, with view DDL managed
via `op.execute()` in hand-written, dialect-branched migrations.

---

## MySQL -> PostgreSQL Data Type Gotchas

Line numbers refreshed against the current tree (2026-09-01). Counts are from a
full-tree audit.

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
double-quotes.

```python
# Broken on PostgreSQL:
f"UPDATE `{table}` SET tree_left = tree_left + 2 WHERE tree_left >= :pr AND `{root_col}` = :root"
```

**Fix:** Fix 2 below (SQLAlchemy Core `update()`, dialect-neutral quoting).

#### 3. `TIMESTAMP(3)` / `CURRENT_TIMESTAMP(3)` — `core/users.py:703`

`UserAlias.modified_time` uses MySQL fractional-second syntax:
```python
Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))
```
PostgreSQL accepts `TIMESTAMP(3)` as a type but not `CURRENT_TIMESTAMP(3)` as a
server_default. This is the only fractional case in the tree.

**Fix:** `with_variant()` — keep MySQL behavior, fall back to plain `DateTime` on PG.

---

### High Priority — Semantic Differences

#### 4. `Float(precision)` — 9 amount columns

`Float(15/22/25)` use MySQL binary-precision semantics; PostgreSQL ignores the
argument and gives `DOUBLE PRECISION`. Since these are allocation/charge amounts
(financial), they should be `Numeric(p, s)` for exact decimal semantics on both
dialects — an improvement regardless of Postgres.

Affected: `integration/xras_views.py:86,111,112,133,134,135`
(`amount`, `allocatedAmount`, `remainingAmount`, `allocated`, `used`, `remaining`)
and `activity/computational.py:316,317,318`
(`external_charge`, `core_hours`, `charge`). The `# float(22,8)` trailing comments
mark the true scale that `Float(N)` fails to express.

#### 5. `GROUP BY` strictness — ~15 candidates

PostgreSQL enforces SQL-standard `GROUP BY`; MySQL silently picks arbitrary values.
The old CTE line refs in `projects.py` are now pure-Python metric code — the risk
moved into the raw `VALUES ROW()` CTEs (`projects.py:1180,1203,1362,1380`,
`rolling_usage.py:69,82,152,172`; all `GROUP BY <single key>`, safe as written but
pattern-sensitive) and, highest-risk, `_SQL_UNIX_ACCOUNTS`
(`queries/directory_access.py:175`, which mixes aggregates with many bare columns).
Run them against Postgres (Stage 3) and fix any `column must appear in GROUP BY`.

#### 6. Case sensitivity — ~114 comparisons, no code fix

MySQL `utf8mb3_general_ci` is case-insensitive; PostgreSQL is case-sensitive by
default. ~114 `==` equality filters on username/projcode/name/email span ~36 files.
Collation is **mixed**: `_general_ci` columns (usernames, projcodes) change behavior;
`_bin` columns (email_address, contract_number) are already case-sensitive and match
PG. ~40 sites already normalize (`func.lower()`/`func.upper()`/`.ilike()`).

Options (choose per column against Stage-3 failures): `citext` extension,
`func.lower()` on both sides, or a pgloader `COLLATE` cast rule.

---

### Medium Priority — Data Migration Concerns (handled by pgloader)

#### 7. `Boolean` storage
MySQL stores `Boolean` as `TINYINT(1)`. pgloader maps `1/0` -> `true/false` at load
time; SQLAlchemy's `Boolean` reads/writes correctly thereafter. Pipeline concern, not
ORM.

#### 8. `'0000-00-00'` sentinel dates — `queries/dashboard.py:180,554`
Both surviving uses are **Python-side sort keys** (`date_group_key`), never sent to
the DB — trivial. pgloader coerces any real zero-dates in data to `NULL`.

#### 9. `String(16384)` — `operational.py`
PostgreSQL supports `VARCHAR` to 1 GB. No change needed; confirm in a test load.

---

## Dialect-Agnostic ORM Design

Goal: connection string alone (`mysql+pymysql://` vs `postgresql+psycopg2://`)
selects the dialect. **Refinement over the original plan:** where a change would
alter *prod MySQL* DDL/behavior, prefer `with_variant` so MySQL stays byte-identical
and only Postgres diverges — the dual-backend split means prod must not shift.

### Fix 0 (new): dialect switch in `sam.session` — mirror `system_status`

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
Plumb through `src/config.py` (`SAMConfig`), the SSL branch in `webapp/run.py:~128`,
`.env.example`, `compose.yaml`, and helm `values*.yaml` (default mysql everywhere).

### Fix 1: `TimestampMixin` / `SoftDeleteMixin`

```python
from sqlalchemy import DateTime, func
# with_variant keeps MySQL's TIMESTAMP + server-side CURRENT_TIMESTAMP; PG uses func.now()
class TimestampMixin:
    modified_time = Column(DateTime, server_default=func.now(), onupdate=func.now())
```
`func.now()` compiles to `now()` on both dialects. **Caveat:** `onupdate` fires
through the ORM only; raw-SQL updates won't auto-stamp on PG (they do on MySQL). See
Fix 5 for optional server-side parity at prod cutover.

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

### Fix 3: `Float(precision)` -> `Numeric` for financial columns

```python
from sqlalchemy import Numeric
amount     = Column(Numeric(15, 2))   # was Float(15)   -- float(15,2)
remaining  = Column(Numeric(25, 8))   # was Float(25)   -- double(25,8)
```
Apply to all 9 columns in the Float gotcha above.

### Fix 4: dialect-agnostic fractional timestamp — `core/users.py:703`

```python
from sqlalchemy.dialects.mysql import TIMESTAMP as MYSQL_TIMESTAMP
def dialect_timestamp(frac=False):
    base = DateTime(timezone=False)
    return base.with_variant(MYSQL_TIMESTAMP(fsp=3), 'mysql') if frac else base
modified_time = Column(dialect_timestamp(frac=True), server_default=func.now())
```

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

### Also in scope (query-layer, category C)

`IFNULL`->`func.coalesce`; `CONCAT`->`||`/`func.concat`; `+ INTERVAL :n DAY`->
dialect-neutral date math; `YEAR()`->`func.extract`; `GROUP_CONCAT`
(`xras_access.py:269`)->branch (`group_concat` vs `string_agg`) or Python aggregation;
`DATABASE()`->`current_database()` branch in `webapp/utils/config_inspect.py:235`.
Leave the probe-guarded `VALUES ROW()` CTEs alone — they already fall back to Python.

---

## Suggested Sequence

Ordered so the cheapest verification precedes the expensive commitment. Stages 0 and
part of C are net wins even if Postgres never ships.

### Horizon 1 — Dual existence (Postgres dev, MySQL prod)

0. **Dialect-agnostic ORM (Fixes 0–4 + category C)** — commit to main; validate the
   full suite on the mysql-test container *first*, proving prod MySQL is unchanged.
   `make check-db-vs-orms` stays clean.
1. **Port the 7 views to portable, tracked DDL** — extract from
   `containers/sam-sql-dev/dump/views.sql`, translate the MySQL-isms, store in-repo as
   a dialect-aware `.sql` the pipeline applies after load; add an `is_view` exclusion
   guard so a future `create_all()` can't materialize them as tables.
2. **`make clone-pg` provisioning** — reuse `bootstrap_clone.py` (FK-aware
   downsample) -> `anonymize_sam_db.py` -> obfuscated MySQL, then **pgloader** into a
   local Postgres, then apply the Stage-1 views. Add a `postgres` service to
   `compose.yaml` (+ a `postgres-test` profile). MySQL stays the schema source of
   truth; the PG schema is disposable.
3. **Dual-backend CI (the gate)** — parametrize `SAM_TEST_DB_URL` to also run against
   `postgres-test`; relax the `tests/conftest.py` host allowlist. Fix GROUP BY
   (gotcha 5) and case-sensitivity (gotcha 6) surprises here, empirically. **Green
   PG suite is the prerequisite for Stage 4.**
4. **`samuel-dev.k8s.ucar.edu` on Postgres** — a helm dev deployment with
   `SAM_DB_DRIVER=postgresql` pointing at a PG DB from Stage 2 (plumb the new value
   into the chart, which today exposes only `SAM_DB_SERVER`/`_USERNAME`/`_PASSWORD`/
   `_REQUIRE_SSL`). The end goal of this horizon — behind the CI gate.

### Horizon 2 — All-Postgres production (endgame)

5. **Retire legacy Java SAM + cut prod to Postgres.** Only now do we leave
   compatibility mode. Steps: migrate prod data with pgloader against a copy; audit
   any remaining GROUP BY; run the full suite on PG; apply Fix 5 triggers if raw-SQL
   auto-stamping is required; **bring SAM under `migrations/sam/` Alembic** (stamp a
   baseline against the migrated PG schema); flip prod's `SAM_DB_DRIVER`.

---

## Summary Table

| Issue | Severity (dual) | Fix | Location |
|---|---|---|---|
| Driver hardcoded `mysql+pymysql` | Blocker | Fix 0 (`SAM_DB_DRIVER`, mirror system_status) | `sam/session/__init__.py:47,90` |
| 7 views' DDL only in MySQL dump | Blocker (net-new) | Stage 1: port + track portable DDL | `integration/xras_views.py`, `activity/computational.py` |
| No PG provisioning path | Blocker (new infra) | Stage 2: `make clone-pg` (pgloader) | `containers/sam-sql-dev/`, `compose.yaml` |
| `TIMESTAMP` + `CURRENT_TIMESTAMP` | Low (dual) / High (cutover) | Fix 1 / Fix 5 | `base.py:84,88`; ~15 tables |
| Backtick raw SQL | Critical | Fix 2 (Core `update()`) | `base.py:403-420` |
| `TIMESTAMP(3)`/`CURRENT_TIMESTAMP(3)` | Critical | Fix 4 (`with_variant`) | `core/users.py:703` |
| `Float(precision)` | High | Fix 3 (`Numeric(p,s)`) | 6x `xras_views.py`, 3x `computational.py` |
| `GROUP BY` strictness | High | Audit on PG (Stage 3) | `directory_access.py:175`, CTEs |
| Case sensitivity | High (silent) | `citext`/`func.lower()`/pgloader COLLATE | ~114 sites, query layer |
| `IFNULL`/`CONCAT`/`INTERVAL`/`YEAR`/`GROUP_CONCAT`/`DATABASE()` | High | Category-C sweep | `sam/queries/*`, `config_inspect.py:235` |
| `Boolean` / `'0000-00-00'` / charset | Medium | pgloader at load time | data-migration concern |
| `String(16384)` | Low | No change | `operational.py` |

---

*Created: 2026-04-19 (one-shot migration). Revised: 2026-09-01 (dual-backend plan;
Alembic-for-SAM placed at the all-Postgres milestone).*
