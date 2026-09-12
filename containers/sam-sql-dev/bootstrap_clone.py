#!/usr/bin/env python3
"""Clone a subset of the remote SAM MySQL database into the local docker MySQL.

Schema is dumped without data and loaded with FK constraints stripped; each
table is then copied in full (small), sampled by recent rows (large), or
handled by a `table_strategies` entry in config.yaml; views load after the
data; FK constraints are re-applied from the remote schema and orphans are
pruned. Reads the remote with SELECT only. Exits non-zero if any table failed.
Requires: PyMySQL, PyYAML, mysqldump on PATH, the local docker container up.
"""

import argparse
import atexit
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque

import pymysql
import yaml
from dotenv import find_dotenv, load_dotenv

DUMP_DIR = "dump"
MYSQLDUMP_FLAGS = ("--skip-lock-tables", "--single-transaction",
                   "--no-tablespaces", "--skip-add-locks")

_temp_files = []


def _remove_temp_files():
    for path in _temp_files:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_remove_temp_files)


# ----------------------------
# Config and credentials
# ----------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default="config.yaml")
    return p.parse_args(argv)


def load_config(path):
    load_dotenv(find_dotenv())
    with open(path) as f:
        cfg = yaml.safe_load(f)
    s = cfg.setdefault("settings", {})
    s.setdefault("size_threshold_mb", 50)
    s.setdefault("row_limit", 10000)
    s.setdefault("prefer_column", "created_at")
    s.setdefault("prune_orphans", True)
    s.setdefault("max_refill_multiplier", 8)
    s.setdefault("table_strategies", [])
    s.setdefault("unvalidated_fks", [])
    r = cfg["remote"]
    r.setdefault("user", os.environ["PROD_SAM_DB_USERNAME"])
    r.setdefault("password", os.environ["PROD_SAM_DB_PASSWORD"])
    r.setdefault("host", os.environ["PROD_SAM_DB_SERVER"])
    r.setdefault("port", 3306)
    return cfg


def write_defaults_file(remote):
    """A mode-600 [client] file keeps the remote password off every argv."""
    fd, path = tempfile.mkstemp(prefix="mysql_", suffix=".cnf", text=True)
    with os.fdopen(fd, "w") as f:
        f.write("[client]\n")
        f.write(f"host={remote['host']}\nuser={remote['user']}\n")
        f.write(f"password={remote['password']}\nport={remote['port']}\n")
    os.chmod(path, 0o600)
    _temp_files.append(path)
    return path


# ----------------------------
# Subprocess helpers (argv lists, never a shell)
# ----------------------------
def client_flags(help_text):
    """mysqldump 9.x dumps masking policies by default, which an 8.0 server denies."""
    return ("--skip-masking-policies",) if "masking-policies" in help_text else ()


def mysqldump_argv(cfg, *extra, tables=()):
    return ["mysqldump", f"--defaults-extra-file={cfg['remote']['_defaults_file']}",
            *MYSQLDUMP_FLAGS, *cfg["remote"].get("_client_flags", ()), *extra,
            cfg["remote"]["database"], *tables]


def docker_mysql_argv(cfg):
    """`-e MYSQL_PWD` with no value forwards it from our env, off the argv."""
    local = cfg["local"]
    return ["docker", "exec", "-i", "-e", "MYSQL_PWD", local["docker_container"],
            "mysql", "-u", local["user"], local["database"]]


def dump(cfg, out, *extra, tables=()):
    argv = mysqldump_argv(cfg, *extra, tables=tables)
    print("→", " ".join(a for a in argv[2:] if not a.startswith("--where")))
    with open(out, "w") as fh:
        subprocess.run(argv, check=True, stdout=fh)
    return out


def load_local(cfg, filename):
    env = dict(os.environ, MYSQL_PWD=cfg["local"]["password"])
    with open(filename) as fh:
        subprocess.run(docker_mysql_argv(cfg), check=True, stdin=fh, env=env)


# ----------------------------
# MySQL introspection
# ----------------------------
def connect_remote(remote):
    return pymysql.connect(host=remote["host"], user=remote["user"], password=remote["password"],
                           database=remote["database"], port=int(remote["port"]),
                           ssl={"ssl_verify_cert": False, "ssl_verify_identity": False},
                           cursorclass=pymysql.cursors.DictCursor)


def get_tables_info(conn, db):
    q = """
    SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type,
           ROUND((DATA_LENGTH + INDEX_LENGTH)/1024/1024, 2) AS size_mb
    FROM information_schema.tables
    WHERE TABLE_SCHEMA=%s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        return cur.fetchall()


def get_foreign_keys(conn, db):
    q = """
    SELECT TABLE_NAME AS child_table, COLUMN_NAME AS child_col,
           REFERENCED_TABLE_NAME AS parent_table, REFERENCED_COLUMN_NAME AS parent_col
    FROM information_schema.key_column_usage
    WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        return cur.fetchall()


def get_foreign_key_constraints(conn, db):
    """Remote FKs grouped per constraint (composite keys keep column order) with their rules."""
    q = """
    SELECT kcu.TABLE_NAME AS child_table, kcu.CONSTRAINT_NAME AS constraint_name,
           kcu.COLUMN_NAME AS child_col, kcu.REFERENCED_TABLE_NAME AS parent_table,
           kcu.REFERENCED_COLUMN_NAME AS parent_col,
           rc.DELETE_RULE AS on_delete, rc.UPDATE_RULE AS on_update
    FROM information_schema.key_column_usage kcu
    JOIN information_schema.referential_constraints rc
      ON kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA AND kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
    WHERE kcu.TABLE_SCHEMA = %s AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
    ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        rows = cur.fetchall()
    grouped = {}
    for r in rows:
        key = (r["child_table"], r["constraint_name"])
        fk = grouped.setdefault(key, {
            "child_table": r["child_table"], "constraint": r["constraint_name"],
            "child_cols": [], "parent_table": r["parent_table"], "parent_cols": [],
            "on_delete": r["on_delete"], "on_update": r["on_update"],
        })
        fk["child_cols"].append(r["child_col"])
        fk["parent_cols"].append(r["parent_col"])
    return list(grouped.values())


def get_primary_key_columns(conn, db, table):
    q = """
    SELECT COLUMN_NAME AS column_name FROM information_schema.key_column_usage
    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY'
    ORDER BY ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(q, (db, table))
        return [r["column_name"] for r in cur.fetchall()]


def show_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        return [r["Field"] for r in cur.fetchall()]


# ----------------------------
# Topological sort (parents before children)
# ----------------------------
def build_dependency_graph(fk_rows):
    parents = defaultdict(set)      # parent -> children
    children_of = defaultdict(set)  # child -> parents
    for r in fk_rows:
        parents[r["parent_table"]].add(r["child_table"])
        children_of[r["child_table"]].add(r["parent_table"])
    return parents, children_of


def _on_cycle(node, parents_map, unresolved):
    """True if `node` reaches itself through tables not yet placed in the order."""
    seen, stack = set(), list(parents_map.get(node, ()))
    while stack:
        n = stack.pop()
        if n == node:
            return True
        if n in unresolved and n not in seen:
            seen.add(n)
            stack.extend(parents_map.get(n, ()))
    return False


def topological_sort(parents_map, children_of, all_tables):
    """Kahn's algorithm; a cycle is broken at one of its own members so the rest stays ordered."""
    in_deg = {t: len(children_of.get(t, set())) for t in all_tables}
    q = deque([t for t in all_tables if in_deg[t] == 0])
    res = []
    broken = []
    while len(res) < len(all_tables):
        if not q:
            unresolved = {t for t in all_tables if t not in res}
            n = min((t for t in unresolved if _on_cycle(t, parents_map, unresolved)),
                    key=lambda t: in_deg[t])
            broken.append(n)
            in_deg[n] = 0
            q.append(n)
        n = q.popleft()
        res.append(n)
        for child in parents_map.get(n, ()):
            if in_deg[child] > 0:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    q.append(child)
    if broken:
        print("⚠️  FK cycles broken at:", broken)
    return res


# ----------------------------
# Sampling helpers
# ----------------------------
def match_strategy(table, strategies):
    for s in strategies or []:
        if fnmatch.fnmatchcase(table, s.get("pattern", "")):
            return s
    return None


def detect_order_column(conn, table, prefer):
    cols = show_columns(conn, table)
    for c in (prefer, "created_at", "created", "ts", "updated_at", "id"):
        if c in cols:
            return c
    return cols[0] if cols else None


def quote_sql_value(val):
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("\\", "\\\\").replace("'", "''") + "'"


def fk_restrictions(table, fk_map, sampled_ids):
    """`col IN (...)` per FK whose parent was *sampled*; a parent copied in full needs none."""
    clauses = []
    for fk in fk_map.get(table, ()):
        ids = sampled_ids.get(fk["parent_table"])
        if not ids:
            continue
        if isinstance(ids[0], tuple):
            print(f"  ⚠️  parent {fk['parent_table']} has a composite PK; "
                  f"no restriction for {table}.{fk['child_col']}")
            continue
        clauses.append(f"`{fk['child_col']}` IN ({','.join(quote_sql_value(v) for v in ids)})")
    return clauses


def fetch_pk_values(conn, db, table, pk_cols, where=None, order_by=None, limit=None):
    q = f"SELECT {', '.join(f'`{c}`' for c in pk_cols)} FROM `{db}`.`{table}`"
    if where:
        q += f" WHERE {where}"
    if order_by:
        q += f" ORDER BY {order_by} DESC"
    if limit:
        q += f" LIMIT {limit}"
    with conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
    if len(pk_cols) == 1:
        return [r[pk_cols[0]] for r in rows]
    return [tuple(r[c] for c in pk_cols) for r in rows]


# ----------------------------
# Dump and load
# ----------------------------
FK_CLAUSE = re.compile(
    r",?\s*CONSTRAINT\s+`[^`]+`\s+FOREIGN\s+KEY\s+\([^)]+\)\s+REFERENCES\s+`[^`]+`\s+\([^)]+\)"
    r"(?:\s+ON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|SET NULL|NO ACTION|RESTRICT))*",
    re.IGNORECASE)


def strip_foreign_keys(sql):
    """Drop inline FK clauses so tables load in any order; reapply_foreign_keys restores them."""
    return FK_CLAUSE.sub("", sql)


def dump_schema_tables_only(cfg, tables):
    out = os.path.join(DUMP_DIR, "schema_tables.sql")
    dump(cfg, out, "--no-data", tables=tables)
    with open(out) as f:
        stripped = strip_foreign_keys(f.read())
    out_no_fk = os.path.join(DUMP_DIR, "schema_tables_no_fk.sql")
    with open(out_no_fk, "w") as f:
        f.write(stripped)
    return out_no_fk


def dump_views(cfg, views):
    return dump(cfg, os.path.join(DUMP_DIR, "views.sql"), "--no-data", tables=views)


def dump_table_full(cfg, table):
    return dump(cfg, os.path.join(DUMP_DIR, f"{table}.sql"), "--no-create-info", tables=[table])


def dump_table_where(cfg, table, where):
    return dump(cfg, os.path.join(DUMP_DIR, f"{table}.sql"), "--no-create-info",
                f"--where={where}", tables=[table])


def empty_dump(table):
    """A `mode: empty` table never touches the remote: nothing to dump."""
    out = os.path.join(DUMP_DIR, f"{table}.sql")
    with open(out, "w") as f:
        f.write(f"-- {table}: emptied by table_strategies (mode: empty)\n")
    return out


def build_alter_add_fk(fk):
    child_cols = ", ".join(f"`{c}`" for c in fk["child_cols"])
    parent_cols = ", ".join(f"`{c}`" for c in fk["parent_cols"])
    stmt = (f"ALTER TABLE `{fk['child_table']}` ADD CONSTRAINT `{fk['constraint']}` "
            f"FOREIGN KEY ({child_cols}) REFERENCES `{fk['parent_table']}` ({parent_cols})")
    for rule in ("on_delete", "on_update"):
        if fk[rule] and fk[rule].upper() != "RESTRICT":
            stmt += f" {rule.upper().replace('_', ' ')} {fk[rule]}"
    return stmt


def reapply_foreign_keys(cfg, fk_constraints):
    """Re-add the remote's FKs one statement at a time; a failure is logged, not fatal.

    Runs with FOREIGN_KEY_CHECKS=0 so existing rows are not validated here:
    cleanup_orphans.py, which runs next, discovers FKs from the local schema
    and prunes what violates them. Known failures: FKs grandfathered under
    older InnoDB that reference a non-unique prefix of a composite PK.
    """
    out = os.path.join(DUMP_DIR, "foreign_keys.sql")
    with open(out, "w") as f:
        f.write("SET FOREIGN_KEY_CHECKS=0;\n")
        f.writelines(build_alter_add_fk(fk) + ";\n" for fk in fk_constraints)
        f.write("SET FOREIGN_KEY_CHECKS=1;\n")
    print(f"🔗 Re-applying {len(fk_constraints)} FK constraints ...")
    local = cfg["local"]
    conn = pymysql.connect(host=local.get("host", "127.0.0.1"), port=int(local.get("port", 3306)),
                           user=local["user"], password=local["password"],
                           database=local["database"], autocommit=True)
    failed = []
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for fk in fk_constraints:
                try:
                    cur.execute(build_alter_add_fk(fk))
                except pymysql.MySQLError as e:
                    failed.append((fk, str(e)))
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
    finally:
        conn.close()
    print(f"✅ {len(fk_constraints) - len(failed)}/{len(fk_constraints)} FK constraints re-applied")
    for fk, err in failed:
        print(f"    ⚠️  {fk['child_table']}.{fk['constraint']}: {err}")
    if failed:
        print(f"    (full SQL in {out})")
    return failed


# ----------------------------
# Per-table sampling
# ----------------------------
def sample_and_dump_table(cfg, conn, table, size_mb, pk_map, fk_map, sampled_ids):
    """Dump one table; record its PK values in `sampled_ids` only when it was sampled."""
    s = cfg["settings"]
    db = cfg["remote"]["database"]
    print(f"\nProcessing table: {table} ({size_mb} MB)")
    restrictions = fk_restrictions(table, fk_map, sampled_ids)

    strategy = match_strategy(table, s["table_strategies"])
    if strategy is not None:
        mode = strategy.get("mode")
        if mode == "empty":
            print("  strategy: empty -> schema only, zero rows")
            return empty_dump(table)
        if mode == "recent":
            column, days = strategy.get("column"), strategy.get("days")
            if not column or not days:
                print(f"  ⚠️  strategy 'recent' for {table} lacks column/days; default sampling")
            elif column not in show_columns(conn, table):
                print(f"  ⚠️  column `{column}` not on {table}; default sampling")
            else:
                where = " AND ".join(
                    [f"`{column}` >= DATE_SUB(CURDATE(), INTERVAL {int(days)} DAY)"] + restrictions)
                print(f"  strategy: recent ({column} within last {int(days)} days)")
                return dump_table_where(cfg, table, where)
        else:
            print(f"  ⚠️  unknown strategy mode {mode!r} for {table}; default sampling")

    if float(size_mb or 0) < s["size_threshold_mb"]:
        print("  small table -> full copy")
        return dump_table_full(cfg, table)

    # Large table: the newest `row_limit` rows that reference sampled parents,
    # doubling the limit while the sample comes back empty.
    pk_cols = pk_map.get(table, [])
    order_col = detect_order_column(conn, table, s["prefer_column"]) or (pk_cols[0] if pk_cols else None)
    limit, multiplier = s["row_limit"], 1
    while True:
        where = " AND ".join(restrictions) or None
        ids = fetch_pk_values(conn, db, table, pk_cols, where=where, order_by=order_col, limit=limit)
        if ids:
            if len(pk_cols) == 1:
                clause = f"`{pk_cols[0]}` IN ({','.join(quote_sql_value(v) for v in ids)})"
            else:
                tuples = ",".join("(" + ",".join(quote_sql_value(v) for v in t) + ")" for t in ids)
                clause = f"({', '.join(f'`{c}`' for c in pk_cols)}) IN ({tuples})"
            sampled_ids[table] = ids
            print(f"  dumped {len(ids)} rows for {table} (limit {limit})")
            return dump_table_where(cfg, table, clause)
        if multiplier < s["max_refill_multiplier"]:
            multiplier *= 2
            limit = s["row_limit"] * multiplier
            print(f"  empty sample, raising limit -> {limit}")
        elif restrictions:
            print("  still empty; retrying without FK restrictions")
            restrictions, limit, multiplier = [], s["row_limit"], 1
        else:
            print("  giving up: empty dump")
            sampled_ids[table] = []
            return empty_dump(table)


# ----------------------------
# Main
# ----------------------------
def main(argv=None):
    args = parse_args(argv)
    cfg = load_config(args.config)
    os.makedirs(DUMP_DIR, exist_ok=True)
    remote = cfg["remote"]
    remote["_defaults_file"] = write_defaults_file(remote)
    remote["_client_flags"] = client_flags(
        subprocess.run(["mysqldump", "--help"], capture_output=True, text=True).stdout)

    print("🔌 Connecting to remote DB...")
    conn = connect_remote(remote)
    print("🔎 Inspecting remote schema...")
    info = get_tables_info(conn, remote["database"])
    base_tables = [r for r in info if r["table_type"] != "VIEW"]
    all_tables = [r["table_name"] for r in base_tables]
    table_sizes = {r["table_name"]: r["size_mb"] for r in base_tables}
    view_names = [r["table_name"] for r in info if r["table_type"] == "VIEW"]
    print(f"Found {len(all_tables)} base tables and {len(view_names)} views")

    fk_rows = get_foreign_keys(conn, remote["database"])
    fk_map = defaultdict(list)
    for r in fk_rows:
        fk_map[r["child_table"]].append(r)
    parents_map, children_of = build_dependency_graph(fk_rows)
    topo = topological_sort(parents_map, children_of, all_tables)
    pk_map = {t: get_primary_key_columns(conn, remote["database"], t) for t in all_tables}

    print("\n🧱 Dumping table schemas (no data, FKs stripped)...")
    load_local(cfg, dump_schema_tables_only(cfg, all_tables))

    sampled_ids = {}
    failed = []
    for table in topo:
        try:
            fname = sample_and_dump_table(cfg, conn, table, table_sizes.get(table, 0),
                                          pk_map, fk_map, sampled_ids)
            print(f"📥 Loading {table} ...")
            load_local(cfg, fname)
        except Exception as e:
            print(f"❌ {table}: {e}", file=sys.stderr)
            failed.append(table)

    if view_names:
        print(f"\n👁️  Loading {len(view_names)} views (after all table data)...")
        try:
            load_local(cfg, dump_views(cfg, view_names))
        except Exception as e:
            print(f"❌ views: {e}", file=sys.stderr)
            failed.append("<views>")

    # FKs go on before the orphan sweep: cleanup_orphans.py reads them from
    # the local schema, so with the sweep first it would find nothing to prune.
    print("\n🔗 Re-applying foreign key constraints from the remote schema...")
    reapply_foreign_keys(cfg, get_foreign_key_constraints(conn, remote["database"]))
    if cfg["settings"]["prune_orphans"]:
        print("\n🧹 Pruning orphans...")
        rc = subprocess.run([sys.executable, "cleanup_orphans.py", "--config", args.config]).returncode
        if rc:
            failed.append("<cleanup_orphans>")

    if failed:
        print(f"\n❌ {len(failed)} step(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\n🎉 Done. Local clone is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
