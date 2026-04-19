# PostgreSQL Migration Planning

## Overview

SAM currently treats the production MySQL/MariaDB database as the schema source of truth,
with ORM models written to mirror it faithfully. This document captures considerations for:

1. Migrating the production database from MySQL → PostgreSQL
2. Making the ORM models dialect-agnostic so they work with either backend
3. Introducing Alembic for forward schema management

---

## Schema Management Strategy (Alembic)

Before any migration, the right tool is **Alembic** (maintained by the SQLAlchemy team).

**How it fits this project:**
- Auto-generates migration scripts from ORM model diffs (`alembic revision --autogenerate`)
- Tracks schema history in an `alembic_version` table
- Supports upgrade/downgrade, making schema changes reversible
- Works with SQLAlchemy 2.0 natively
- Existing `tests/integration/test_schema_validation.py` drift-detection tests shift from
  primary safety net → secondary sanity check once Alembic owns the schema

**Bootstrapping from current state:**
```bash
alembic init alembic
# Edit alembic/env.py to point at SAM Base metadata
alembic stamp head    # Mark current DB as baseline without running migrations
# Future model changes:
alembic revision --autogenerate -m "add_foo_column"
alembic upgrade head
```

**Views:** tables marked `info: {'is_view': True}` require special handling —
Alembic's autogenerate will try to manage them as tables. Add them to
`exclude_tables` in `env.py` and manage view DDL via `op.execute()` in hand-written
migrations.

---

## MySQL → PostgreSQL Data Type Gotchas

### Critical — Will Break at Runtime

#### 1. `TIMESTAMP` + `text('CURRENT_TIMESTAMP')` — widespread

MySQL `TIMESTAMP` columns silently auto-update on row change via `ON UPDATE CURRENT_TIMESTAMP`.
PostgreSQL has no equivalent column-level behavior.

**Affected:** `TimestampMixin` (`base.py:82`), `SoftDeleteMixin` (`base.py:94`), and all
~15+ tables that inherit them, plus individual columns in:
- `operational.py` — `Synchronizer.last_run`
- `projects/projects.py` — `DefaultProjectSelection.modified_time`, `ProjectLead.membership_change_time`
- `resources/facilities.py` — `FacilityResource.creation_time/modified_time`
- `accounting/accounts.py` — `Account.creation_time/modified_time`
- `core/users.py` — `User.pdb_modified_time`
- `core/groups.py` — `AdhocGroup`, `AdhocGroupTag`, `GroupEntry` timestamps
- `activity/dav.py`, `activity/hpc.py`, `activity/archive.py` — `modified_time`

**Fix:** Replace `TIMESTAMP` + `text('CURRENT_TIMESTAMP')` with `DateTime` + `func.now()`
(see §Dialect-Agnostic ORM Design below).

#### 2. Backtick quoting in `NestedSetMixin` raw SQL — `base.py:381-397`

MySQL uses backtick identifiers; PostgreSQL requires double-quotes. The raw SQL strings
in `_ns_place_in_tree()` will fail on PostgreSQL.

```python
# Broken on PostgreSQL:
f"UPDATE `{table}` SET tree_left = tree_left + 2 WHERE tree_left >= :pr AND `{root_col}` = :root"
```

**Fix:** Replace with SQLAlchemy Core `update()` constructs (see §Dialect-Agnostic ORM Design).

#### 3. `TIMESTAMP(3)` / `CURRENT_TIMESTAMP(3)` — `core/users.py:594`

Fractional-second timestamp syntax is MySQL-specific. `UserAlias.modified_time` uses:
```python
Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))
```
PostgreSQL spells fractional precision as `TIMESTAMP(3)` but does not accept
`CURRENT_TIMESTAMP(3)` as a `server_default` expression.

**Fix:** Use `with_variant()` to keep MySQL behavior, fall back to plain `DateTime` on PG.

---

### High Priority — Semantic Differences

#### 4. `Float(precision)` — `integration/xras_views.py`

`Float(15)`, `Float(22)`, `Float(25)` use MySQL's binary-precision semantics. PostgreSQL
ignores the precision argument entirely, giving `DOUBLE PRECISION` regardless.

Affected columns in `xras_views.py`:
- `XrasActionView.amount` → `Float(15)` (DB comment: `float(15,2)`)
- `XrasAllocationView.allocatedAmount` → `Float(15)` (DB comment: `float(15,2)`)
- `XrasAllocationView.remainingAmount` → `Float(25)` (DB comment: `double(25,8)`)
- `XrasHpcAllocationAmountView.allocated/used/remaining` → `Float(15/22)`

Since these represent allocation amounts (financial data), they should use `Numeric(p, s)`
for exact decimal semantics on both dialects.

#### 5. `GROUP BY` strictness — `projects/projects.py`

PostgreSQL enforces SQL-standard `GROUP BY`: every non-aggregate SELECT column must appear
in the `GROUP BY` clause. MySQL silently picks arbitrary values for un-grouped columns.

The CTE-based charge calculations (~lines 853, 876, 1030, 1048) need auditing.
Run them against a PostgreSQL instance and fix any `ERROR: column must appear in GROUP BY`
failures before cutover.

#### 6. Case sensitivity

MySQL with default `utf8mb4_general_ci` collation is case-insensitive for string
comparisons. PostgreSQL is case-sensitive by default. Queries doing string equality
on usernames, projcodes, or resource names may silently return different result sets.

Options:
- `ILIKE` instead of `LIKE` in PostgreSQL queries
- `citext` extension on PostgreSQL for transparent case-insensitive columns
- Use `func.lower()` on both sides of comparisons

---

### Medium Priority — Data Migration Concerns

#### 7. `Boolean` storage

MySQL stores `Boolean` as `TINYINT(1)` or `BIT(1)`. Data migration tools
(pgloader, etc.) need to map `1/0` → `true/false`. SQLAlchemy's `Boolean` type
handles reads/writes correctly once data is in PostgreSQL — this is purely a
migration-time concern, not an ORM concern.

#### 8. `'0000-00-00'` sentinel dates — `queries/dashboard.py:200,515`

MySQL accepts zero-dates; PostgreSQL rejects them with a cast error.
Verify no actual `0000-00-00` values exist in the data before migration.
Replace the string sentinels in dashboard.py with `None`/`NULL` comparisons.

#### 9. `String(16384)` — `operational.py`

PostgreSQL supports `VARCHAR` up to 1 GB, so this is technically fine.
Worth confirming the column can hold the actual data during a test migration.

---

## Dialect-Agnostic ORM Design

The goal: connection string alone (`mysql+pymysql://` vs `postgresql+psycopg2://`)
determines which dialect runs. No model changes needed at runtime.

### Fix 1: `TimestampMixin` and `SoftDeleteMixin`

```python
# base.py — replace TIMESTAMP + text() with DateTime + func.now()
from sqlalchemy import DateTime, func

class TimestampMixin:
    modified_time = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()       # SQLAlchemy injects SET modified_time=now() in UPDATEs
    )

class SoftDeleteMixin:
    deletion_time = Column(DateTime, nullable=True)
```

`func.now()` compiles to `now()` on both MySQL and PostgreSQL.

**Caveat:** `onupdate=func.now()` fires through the ORM only. Raw SQL updates that
bypass SQLAlchemy will not auto-update `modified_time` on PostgreSQL (they do on MySQL
via the server-side trigger). See Fix 4 for optional server-side parity.

### Fix 2: `NestedSetMixin` raw SQL

Replace the backtick raw SQL with SQLAlchemy Core `update()` constructs:

```python
# base.py — replace raw SQL in _ns_place_in_tree()
from sqlalchemy import update, table as sa_table, column as sa_col, bindparam

def _ns_shift(session, table_name, root_col, pr, root=None):
    t = sa_table(table_name, sa_col('tree_left'), sa_col('tree_right'), sa_col(root_col))
    left_clause = t.c.tree_left >= bindparam('pr')
    right_clause = t.c.tree_right >= bindparam('pr')
    if root is not None:
        left_clause = left_clause & (t.c[root_col] == bindparam('root'))
        right_clause = right_clause & (t.c[root_col] == bindparam('root'))
    params = {'pr': pr, 'root': root} if root is not None else {'pr': pr}
    session.execute(update(t).where(left_clause).values(tree_left=t.c.tree_left + 2), params)
    session.execute(update(t).where(right_clause).values(tree_right=t.c.tree_right + 2), params)
```

### Fix 3: `Float(precision)` → `Numeric` for financial columns

```python
# xras_views.py
from sqlalchemy import Numeric

# Before:
amount = Column(Float(15))          # float(15,2) in DB

# After:
amount = Column(Numeric(15, 2))     # exact decimal, dialect-agnostic
remaining = Column(Numeric(25, 8))  # double(25,8) in DB
```

### Fix 4: Dialect-agnostic fractional timestamps

```python
# base.py — helper for TIMESTAMP(3) behavior
from sqlalchemy import DateTime
from sqlalchemy.dialects.mysql import TIMESTAMP as MYSQL_TIMESTAMP

def dialect_timestamp(frac=False):
    """TIMESTAMP(3) on MySQL, plain DateTime on PostgreSQL."""
    base = DateTime(timezone=False)
    if frac:
        return base.with_variant(MYSQL_TIMESTAMP(frac=True), 'mysql')
    return base

# core/users.py — UserAlias.modified_time
modified_time = Column(dialect_timestamp(frac=True), server_default=func.now())
```

### Fix 5 (Optional): Server-side trigger DDL for PostgreSQL

If server-side `modified_time` auto-update is required (fires even on raw SQL
bypassing the ORM), attach a DDL event that only executes on PostgreSQL:

```python
# base.py
from sqlalchemy import event, DDL

_pg_auto_modified_ddl = DDL("""
    CREATE OR REPLACE FUNCTION _sam_update_modified_time()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN NEW.modified_time = NOW(); RETURN NEW; END;
    $$;

    CREATE TRIGGER trg_%(table)s_modified
    BEFORE UPDATE ON %(table)s
    FOR EACH ROW EXECUTE FUNCTION _sam_update_modified_time();
""")

class TimestampMixin:
    ...
    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, '__table__'):
            event.listen(
                cls.__table__,
                'after_create',
                _pg_auto_modified_ddl.execute_if(dialect='postgresql')
            )
```

The `.execute_if(dialect='postgresql')` ensures MySQL never sees this DDL.

---

## Suggested Migration Sequence

1. **Fix ORM models** (Fixes 1–4 above) — make them dialect-agnostic, commit to main
2. **Spin up a PostgreSQL instance** — run `create_all()` against it, fix any DDL errors
3. **Audit GROUP BY queries** — run charge calculation queries against PostgreSQL, fix
   any strict GROUP BY violations
4. **Test migration with pgloader** against a copy of production data:
   ```
   pgloader mysql://root@127.0.0.1/sam postgresql://user@localhost/sam
   ```
   pgloader handles TINYINT→boolean, zero-date coercion, charset conversion automatically
5. **Run full test suite** against PostgreSQL — `SAM_TEST_DB_URL=postgresql://...`
6. **Stamp Alembic baseline** against the migrated PostgreSQL database
7. **Cutover** — update `SAM_DB_URL` in production `.env`

---

## Summary Table

| Issue | Severity | Fix | Location |
|---|---|---|---|
| `TIMESTAMP` + `text('CURRENT_TIMESTAMP')` | Critical | `DateTime` + `func.now()` | `base.py`, ~15 tables |
| Backtick raw SQL | Critical | SQLAlchemy Core `update()` | `base.py` `NestedSetMixin` |
| `TIMESTAMP(3)` / `CURRENT_TIMESTAMP(3)` | Critical | `with_variant()` helper | `core/users.py:594` |
| `Float(precision)` | High | `Numeric(p, s)` | `integration/xras_views.py` |
| `GROUP BY` strictness | High | Audit + fix queries | `projects/projects.py` |
| Case sensitivity | High | `ILIKE` / `citext` / `func.lower()` | Query layer |
| `Boolean` storage | Medium | Data migration concern only | All boolean columns |
| `'0000-00-00'` sentinel dates | Medium | Replace with `NULL` | `queries/dashboard.py` |
| `String(16384)` | Low | No change needed | `operational.py` |

---

*Created: 2026-04-19*
