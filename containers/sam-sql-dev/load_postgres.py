#!/usr/bin/env python3
"""Build a Postgres copy of the local MySQL SAM clone and swap it into place.

Schema comes from the ORM (`sam.base.Base.metadata`, views excluded, FKs
deferred); data streams table by table through COPY into `<db>_next`; FKs,
sequences and the ported views go on last; then `<db>_next` is renamed over
`<db>`. Target from SAM_DEV_PG_* (CNPG defaults) or the CLI; source is the
`local:` block of config.yaml. A source that still holds real usernames loads
with a warning (PII is a public-repo concern, not a cluster one). Exits
non-zero on any count mismatch or a failed view.
"""
import argparse
import copy
import datetime
import decimal
import io
import json
import os
import sys
from urllib.parse import urlparse

import psycopg2
import pymysql
import yaml
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import (URL, Boolean, Column, Enum, ForeignKeyConstraint, Index, Integer, MetaData,
                        String, Table, UniqueConstraint, create_engine)

from check_username_leak import leak_query, preserved_usernames

ENV_DEFAULTS = {
    "host": ("SAM_DEV_PG_HOST", "csg-postgres.k8s.ucar.edu"),
    "port": ("SAM_DEV_PG_PORT", "5432"),
    "user": ("SAM_DEV_PG_USER", None),
    "password": ("SAM_DEV_PG_PASSWORD", None),
    "dbname": ("SAM_DEV_PG_DB", "sam_dev"),
    "require_ssl": ("SAM_DEV_PG_REQUIRE_SSL", "true"),
}
VIEWS_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "postgres", "views.sql")
CHUNK_ROWS = 50_000
MAINTENANCE_DB = "postgres"
# MySQL's `_ci` columns compare case- and accent-insensitively; an ICU primary-strength
# collation on exactly those columns keeps ==, LIKE, IN and ORDER BY meaning the same
# on Postgres (docs/plans/POSTGRES_MIGRATION.md gotcha 6).
CI_COLLATION = "sam_ci"


# ----------------------------
# Configuration
# ----------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--source", metavar="URL", help="mysql+pymysql://user:pw@host:port/db (default: config.yaml local)")
    p.add_argument("--pg-host")
    p.add_argument("--pg-port")
    p.add_argument("--pg-user")
    p.add_argument("--pg-db")
    p.add_argument("--no-ssl", action="store_true", help="sslmode=prefer instead of require")
    p.add_argument("--no-swap", action="store_true", help="leave <db>_next in place for inspection")
    return p.parse_args(argv)


def target_from(environ, args=None):
    """SAM_DEV_PG_* with CLI overrides; the password never comes from argv."""
    t = {k: environ.get(var, default) for k, (var, default) in ENV_DEFAULTS.items()}
    for k in ("host", "port", "user"):
        if args is not None and getattr(args, f"pg_{k}", None):
            t[k] = getattr(args, f"pg_{k}")
    if args is not None and args.pg_db:
        t["dbname"] = args.pg_db
    require_ssl = str(t.pop("require_ssl")).lower() in ("true", "1", "yes")
    if args is not None and args.no_ssl:
        require_ssl = False
    t["sslmode"] = "require" if require_ssl else "prefer"
    t["port"] = int(t["port"])
    for k in ("user", "password"):
        if not t[k]:
            raise SystemExit(f"{ENV_DEFAULTS[k][0]} is not set (see .env.example)")
    return t


def source_from(cfg, url=None):
    if url:
        u = urlparse(url)
        return {"host": u.hostname, "port": u.port or 3306, "user": u.username,
                "password": u.password, "database": u.path.lstrip("/")}
    local = cfg["local"]
    return {"host": local["host"], "port": int(local.get("port", 3306)), "user": local["user"],
            "password": local["password"], "database": local["database"]}


def pg_connect(target, dbname=None):
    conn = psycopg2.connect(host=target["host"], port=target["port"], user=target["user"],
                            password=target["password"], dbname=dbname or target["dbname"],
                            sslmode=target["sslmode"], application_name="sam-load-postgres")
    conn.autocommit = True
    return conn


def pg_url(target, dbname):
    return URL.create("postgresql+psycopg2", username=target["user"], password=target["password"],
                      host=target["host"], port=target["port"], database=dbname,
                      query={"sslmode": target["sslmode"], "application_name": "sam-load-postgres"})


# ----------------------------
# Schema from the ORM
# ----------------------------
def fk_free_metadata():
    """A copy of the ORM's base tables without FK constraints; the FKs come back as tuples."""
    import sam  # noqa: F401  registers every model on Base
    from sam.base import Base
    md = MetaData()
    fks = []
    for t in Base.metadata.tables.values():
        if t.info.get("is_view"):
            continue
        for c in t.constraints:
            if isinstance(c, ForeignKeyConstraint):
                fks.append((t.name, tuple(c.column_keys), c.referred_table.name,
                            tuple(e.column.name for e in c.elements)))
        _copy_table_without_fks(t, md)
    unique_index_names(md)
    return md, fks


def mysql_column_facts(my_conn, database):
    """(table, column) -> (CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME) for the source's string columns."""
    with my_conn.cursor() as c:
        c.execute("SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME "
                  "FROM information_schema.columns "
                  "WHERE TABLE_SCHEMA=%s AND CHARACTER_MAXIMUM_LENGTH IS NOT NULL", (database,))
        return {(t, col): (n, coll) for t, col, n, coll in c.fetchall()}


def widen_strings(md, lengths):
    """Postgres enforces varchar(n) where MySQL only advised the ORM: take the source's width.

    Returns the (table, column, orm_length, db_length) drifts so the ORM can be fixed.
    """
    drifts = []
    for t in md.tables.values():
        for col in t.columns:
            n = lengths.get((t.name, col.name))
            if isinstance(col.type, String) and col.type.length and n and n > col.type.length:
                drifts.append((t.name, col.name, col.type.length, n))
                col.type = String(n, collation=col.type.collation)
    return drifts


def collation_sql():
    return (f"CREATE COLLATION {_q(CI_COLLATION)} (provider = icu, locale = 'und-u-ks-level1', "
            "deterministic = false)")


def apply_collations(md, collations):
    """Every string column MySQL declares `_ci` gets the ICU collation; `_bin` columns stay default.

    The copied Column shares its type object with the ORM, so the type is replaced, never mutated.
    """
    touched = []
    for t in md.tables.values():
        for col in t.columns:
            coll = collations.get((t.name, col.name))
            if coll and coll.endswith("_ci") and isinstance(col.type, String) and not isinstance(col.type, Enum):
                new_type = copy.copy(col.type)
                new_type.collation = CI_COLLATION
                col.type = new_type
                touched.append((t.name, col.name))
    return touched


def _copy_table_without_fks(t, md):
    """Rebuild columns, unique constraints and indexes only: a Column.copy() keeps its ForeignKey."""
    nt = Table(t.name, md, *[
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable,
               autoincrement=c.autoincrement,
               server_default=c.server_default.arg if c.server_default is not None else None)
        for c in t.columns])
    for uc in t.constraints:
        if isinstance(uc, UniqueConstraint):
            nt.append_constraint(UniqueConstraint(*[c.name for c in uc.columns], name=uc.name))
    for ix in t.indexes:
        Index(ix.name, *[nt.c[c.name] for c in ix.columns], unique=ix.unique)


def unique_index_names(md):
    """MySQL scopes index names per table, Postgres per schema: shared names get a table prefix."""
    owners = {}
    for t in md.tables.values():
        for ix in t.indexes:
            owners.setdefault(ix.name, []).append(ix)
    for name, indexes in owners.items():
        if len(indexes) > 1:
            for ix in indexes:
                ix.name = f"{ix.table.name}_{name}"[:63]


def serial_columns(md):
    for t in md.sorted_tables:
        pk = list(t.primary_key.columns)
        if len(pk) == 1 and isinstance(pk[0].type, Integer) and pk[0].autoincrement in (True, "auto"):
            yield t.name, pk[0].name


def _q(name):
    """Double-quote an identifier (ORM table/column names, never user input)."""
    return '"' + name.replace('"', '""') + '"'


# ----------------------------
# Row coercion for COPY text format
# ----------------------------
def _escape(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


DEFAULT_MARKER = "\\D"


def coerce(value, is_bool, default_on_null=False):
    """One COPY text field: NULL is \\N (or the column default), booleans t/f, strings escaped."""
    if value is None:
        return DEFAULT_MARKER if default_on_null else "\\N"
    if is_bool:
        return "t" if value else "f"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, bytes):
        return "\\\\x" + value.hex()
    if isinstance(value, (dict, list)):
        return _escape(json.dumps(value))
    s = str(value)
    if s.startswith("0000-00-00"):
        return DEFAULT_MARKER if default_on_null else "\\N"
    return _escape(s)


def column_specs(table):
    """(is_boolean, default_on_null) per column: a NULL (pymysql's zero-date) in a NOT NULL
    column with a server default takes that default instead of failing the COPY."""
    return [(isinstance(c.type, Boolean), not c.nullable and c.server_default is not None)
            for c in table.columns]


def copy_lines(rows, specs, stats=None):
    for row in rows:
        fields = [coerce(v, b, d) for v, (b, d) in zip(row, specs)]
        if stats is not None:
            stats["defaults"] = stats.get("defaults", 0) + fields.count(DEFAULT_MARKER)
        yield "\t".join(fields) + "\n"


def copy_table(my_conn, pg_conn, table, stats=None):
    """Stream one table MySQL -> COPY; returns (source_count, target_count)."""
    cols = [c.name for c in table.columns]
    specs = column_specs(table)
    select = "SELECT " + ", ".join(f"`{c}`" for c in cols) + f" FROM `{table.name}`"
    copy = (f"COPY {_q(table.name)} ({', '.join(map(_q, cols))}) FROM STDIN "
            f"(FORMAT text, DEFAULT '{DEFAULT_MARKER}')")
    with my_conn.cursor(pymysql.cursors.SSCursor) as src, pg_conn.cursor() as dst:
        src.execute(select)
        while True:
            rows = src.fetchmany(CHUNK_ROWS)
            if not rows:
                break
            dst.copy_expert(copy, io.StringIO("".join(copy_lines(rows, specs, stats))))
    with my_conn.cursor() as c:
        c.execute(f"SELECT COUNT(*) FROM `{table.name}`")
        n_src = c.fetchone()[0]
    with pg_conn.cursor() as c:
        c.execute(f"SELECT COUNT(*) FROM {_q(table.name)}")
        n_dst = c.fetchone()[0]
    return n_src, n_dst


# ----------------------------
# Constraints, sequences, views
# ----------------------------
def mysql_fk_names(my_conn, database):
    """(child, child_cols, parent) -> MySQL constraint name, so PG keeps the same names."""
    q = """
    SELECT CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME,
           GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) AS cols
    FROM information_schema.key_column_usage
    WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL
    GROUP BY CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME
    """
    with my_conn.cursor() as c:
        c.execute(q, (database,))
        return {(child, tuple(cols.split(",")), parent): name for name, child, parent, cols in c.fetchall()}


def fk_statement(fk, name, not_valid):
    child, child_cols, parent, parent_cols = fk
    stmt = (f"ALTER TABLE {_q(child)} ADD CONSTRAINT {_q(name[:63])} "
            f"FOREIGN KEY ({', '.join(map(_q, child_cols))}) "
            f"REFERENCES {_q(parent)} ({', '.join(map(_q, parent_cols))})")
    return stmt + " NOT VALID" if not_valid else stmt


def add_foreign_keys(pg_conn, fks, names, unvalidated):
    """One ALTER per FK, NOT VALID for the policy edges. Failures are advisory on purpose:
    the grandfathered fk_dav_charge_dav_activity_id fails on every engine."""
    failed = []
    with pg_conn.cursor() as cur:
        for fk in fks:
            name = names.get(fk[:3]) or f"fk_{fk[0]}_{'_'.join(fk[1])}"
            try:
                cur.execute(fk_statement(fk, name, name in unvalidated))
            except psycopg2.Error as e:
                failed.append((name, str(e).strip().splitlines()[0]))
    print(f"🔗 {len(fks) - len(failed)}/{len(fks)} FK constraints added"
          f" ({len(unvalidated)} NOT VALID by policy)")
    for name, err in failed:
        print(f"    ⚠️  {name}: {err}")
    return failed


def setval_sql(table, col):
    """Next id = max+1; a table whose ids start at 0 (or is empty) leaves the sequence at 1."""
    return (f"SELECT setval(pg_get_serial_sequence(%s, %s), GREATEST(COALESCE(MAX({_q(col)}), 1), 1), "
            f"COALESCE(MAX({_q(col)}), 0) >= 1) FROM {_q(table)}")


def reset_sequences(pg_conn, md):
    with pg_conn.cursor() as cur:
        for table, col in serial_columns(md):
            cur.execute(setval_sql(table, col), (table, col))


def view_statements(sql_text):
    """Split on `;` at end of line, dropping `--` comment lines; a statement may start with a comment."""
    statements = []
    for chunk in sql_text.split(";\n"):
        body = "\n".join(line for line in chunk.splitlines() if not line.lstrip().startswith("--")).strip()
        if body:
            statements.append(body)
    return statements


def apply_views(pg_conn, path):
    """The views are part of the schema: a missing file is a failed load, not a skip."""
    if not os.path.exists(path):
        print(f"❌ {path} is missing; the copy would have no views", file=sys.stderr)
        return [(path, "missing")]
    with open(path) as f:
        statements = view_statements(f.read())
    failed = []
    with pg_conn.cursor() as cur:
        for stmt in statements:
            try:
                cur.execute(stmt)
            except psycopg2.Error as e:
                failed.append((stmt.split("\n", 1)[0][:60], str(e).strip().splitlines()[0]))
    print(f"👁️  {len(statements) - len(failed)}/{len(statements)} view statements applied")
    for head, err in failed:
        print(f"    ⚠️  {head}: {err}")
    return failed


# ----------------------------
# Swap
# ----------------------------
def swap_sql(db, prev_exists):
    """Rename <db>_next over <db>; the rename of <db> itself is skipped when it does not exist."""
    stmts = []
    if prev_exists:
        stmts.append(f"ALTER DATABASE {_q(db)} RENAME TO {_q(db + '_prev')}")
    stmts.append(f"ALTER DATABASE {_q(db + '_next')} RENAME TO {_q(db)}")
    if prev_exists:
        stmts.append(f"DROP DATABASE {_q(db + '_prev')}")
    return stmts


def swap(maint, db):
    """Rename needs an idle target: our own sessions are terminated, anyone else's stops the swap."""
    with maint.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        prev_exists = cur.fetchone() is not None
        # Only client backends count: RENAME evicts autovacuum workers on its own.
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid() AND usename = current_user "
                    "AND backend_type = 'client backend'", (db,))
        cur.execute("SELECT usename, application_name, client_addr FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid() AND backend_type = 'client backend'", (db,))
        others = cur.fetchall()
        if others:
            print(f"❌ {db} still has {len(others)} session(s) from other roles; left {db}_next in place:")
            for u, app, addr in others:
                print(f"    {u} {app or '-'} {addr or '-'}")
            return 1
        for stmt in swap_sql(db, prev_exists):
            cur.execute(stmt)
    print(f"🔁 {db}_next is now {db}")
    return 0


# ----------------------------
# Main
# ----------------------------
def main(argv=None):
    args = parse_args(argv)
    load_dotenv(find_dotenv())
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    target = target_from(os.environ, args)
    source = source_from(cfg, args.source)
    db, scratch = target["dbname"], f"{target['dbname']}_next"
    print(f"Source: mysql {source['host']}:{source['port']}/{source['database']}  ->  "
          f"target: postgres {target['host']}:{target['port']}/{db} as {target['user']} ({target['sslmode']})")

    my = pymysql.connect(host=source["host"], port=source["port"], user=source["user"],
                         password=source["password"], database=source["database"])
    with my.cursor() as c:
        c.execute(leak_query("users", "username", preserved_usernames(cfg)))
        real_rows = c.fetchone()[0]
    print(f"Source tier: {'REAL usernames in ' + str(real_rows) + ' users rows' if real_rows else 'obfuscated'}")
    if real_rows:
        print("WARNING: source holds real usernames; this copy is not for the public repo", file=sys.stderr)

    maint = pg_connect(target, MAINTENANCE_DB)
    with maint.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_q(scratch)}")
        cur.execute(f"CREATE DATABASE {_q(scratch)}")
    print(f"🧱 {scratch} created; building schema from the ORM ...")

    md, fks = fk_free_metadata()
    facts = mysql_column_facts(my, source["database"])
    for table, col, orm_len, db_len in widen_strings(md, {k: n for k, (n, _) in facts.items()}):
        print(f"  ⚠️  {table}.{col}: ORM String({orm_len}) but the source column holds {db_len}; using {db_len}")
    ci_columns = apply_collations(md, {k: coll for k, (_, coll) in facts.items()})
    engine = create_engine(pg_url(target, scratch))
    with engine.begin() as conn:
        conn.exec_driver_sql(collation_sql())
    md.create_all(engine)
    engine.dispose()
    print(f"  {len(ci_columns)} case-insensitive columns declared COLLATE {CI_COLLATION}")

    pg = pg_connect(target, scratch)
    mismatched = []
    for table in md.sorted_tables:
        stats = {}
        n_src, n_dst = copy_table(my, pg, table, stats)
        flag = "" if n_src == n_dst else "  ❌ MISMATCH"
        note = f"  ({stats['defaults']:,} NULL -> column default)" if stats.get("defaults") else ""
        print(f"  {table.name}: mysql={n_src:,} pg={n_dst:,}{flag}{note}")
        if n_src != n_dst:
            mismatched.append(table.name)
    print(f"📥 {len(md.sorted_tables)} tables copied")

    add_foreign_keys(pg, fks, mysql_fk_names(my, source["database"]),
                     set(cfg.get("settings", {}).get("unvalidated_fks", [])))
    reset_sequences(pg, md)
    view_failures = apply_views(pg, VIEWS_SQL)
    pg.close()
    my.close()

    if mismatched:
        print(f"❌ row counts differ for: {', '.join(mismatched)}; {scratch} left in place", file=sys.stderr)
        return 1
    if view_failures:
        print(f"❌ {len(view_failures)} view statement(s) failed; {scratch} left in place", file=sys.stderr)
        return 1
    if args.no_swap:
        print(f"ℹ️  --no-swap: {scratch} left in place")
        return 0
    return swap(maint, db)


if __name__ == "__main__":
    sys.exit(main())
