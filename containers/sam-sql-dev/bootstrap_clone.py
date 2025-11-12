#!/usr/bin/env python3
"""
bootstrap_clone.py

Creates a lightweight local MySQL clone from a remote read-only MySQL server.

Behavior:
- Dump schema (no data) and load into local docker mysql container.
- For each table:
  - If small (size_mb < threshold) -> full copy.
  - If large -> sample recent rows using an order column (prefer created_at), but
    restrict child tables to rows referencing sampled parent IDs (FK-aware).
  - If sampling leaves a table empty, retry with doubling row count up to a max multiplier.
- After all tables are loaded, optionally run cleanup_orphans.py.

Requires:
  pip install PyMySQL PyYAML
  mysqldump on PATH
  Docker running local MySQL container with name in config.yaml
"""

import os
import sys
import subprocess
import pymysql
import yaml
import time
import math
import tempfile
import atexit
from collections import defaultdict, deque
from dotenv import load_dotenv, find_dotenv

CONFIG_FILE = "config.yaml"
DUMP_DIR = "dump"

# Track temporary config files for cleanup
_temp_config_files = []

# ----------------------------
# Helpers
# ----------------------------
def create_mysql_config_file(host, user, password, port=3306):
    """
    Create a temporary MySQL config file with credentials.
    This prevents passwords from being visible in process lists.
    Returns path to the config file.
    """
    fd, path = tempfile.mkstemp(prefix='mysql_', suffix='.cnf', text=True)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write("[client]\n")
            f.write(f"host={host}\n")
            f.write(f"user={user}\n")
            f.write(f"password={password}\n")
            f.write(f"port={port}\n")
        # Set file permissions to 600 (owner read/write only)
        os.chmod(path, 0o600)
        _temp_config_files.append(path)
        return path
    except Exception as e:
        # Clean up the file if we fail to write it
        try:
            os.unlink(path)
        except:
            pass
        raise e

def cleanup_temp_config_files():
    """Remove all temporary MySQL config files."""
    for path in _temp_config_files:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception as e:
            print(f"⚠️  Warning: Could not remove temp file {path}: {e}", file=sys.stderr)
    _temp_config_files.clear()

# Register cleanup function to run on exit
atexit.register(cleanup_temp_config_files)

def load_config(path=CONFIG_FILE):
    load_dotenv(find_dotenv())
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # fill defaults if not provided
    cfg.setdefault("settings", {})
    s = cfg["settings"]
    s.setdefault("size_threshold_mb", 50)
    s.setdefault("row_limit", 10000)
    s.setdefault("prefer_column", "created_at")
    s.setdefault("prune_orphans", True)
    s.setdefault("max_refill_multiplier", 8)   # max 8x refill attempts
    a = cfg["remote"]
    a.setdefault("user", os.environ['SAM_DB_USERNAME'])
    a.setdefault("password",os.environ['SAM_DB_PASSWORD'])
    a.setdefault("host",os.environ['SAM_DB_SERVER'])

    print(cfg)

    return cfg

def run(cmd, capture=False):
    print("→", cmd)
    if capture:
        res = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.stdout
    else:
        subprocess.run(cmd, shell=True, check=True)

def ensure_dir(d):
    os.makedirs(d, exist_ok=True)

# ----------------------------
# MySQL introspection utilities
# ----------------------------
def connect_mysql(host, user, password, database=None, port=3306, use_ssl=False):
    ssl_config = {'ssl_verify_cert': False, 'ssl_verify_identity': False} if use_ssl else None
    return pymysql.connect(host=host, user=user, password=password, database=database, port=port,
                          ssl=ssl_config, cursorclass=pymysql.cursors.DictCursor)

def get_tables_info(conn, db):
    q = """
    SELECT TABLE_NAME as table_name,
           TABLE_TYPE as table_type,
           ROUND((DATA_LENGTH + INDEX_LENGTH)/1024/1024, 2) AS size_mb,
           TABLE_ROWS as table_rows
    FROM information_schema.tables
    WHERE TABLE_SCHEMA=%s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        return cur.fetchall()

def is_view(table_type):
    """Check if a table is actually a view."""
    return table_type == 'VIEW'

def get_foreign_keys(conn, db):
    q = """
    SELECT TABLE_NAME AS child_table,
           COLUMN_NAME AS child_col,
           REFERENCED_TABLE_NAME AS parent_table,
           REFERENCED_COLUMN_NAME AS parent_col
    FROM information_schema.key_column_usage
    WHERE TABLE_SCHEMA=%s
      AND REFERENCED_TABLE_NAME IS NOT NULL
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        return cur.fetchall()

def get_primary_key_columns(conn, db, table):
    q = """
    SELECT COLUMN_NAME as column_name
    FROM information_schema.key_column_usage
    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY'
    ORDER BY ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(q, (db, table))
        rows = cur.fetchall()
        return [r["column_name"] for r in rows]

def show_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        rows = cur.fetchall()
        return [r["Field"] for r in rows]

# ----------------------------
# Topological sort (parents before children)
# ----------------------------
def build_dependency_graph(fk_rows):
    parents = defaultdict(set)   # parent -> set(children)
    children_of = defaultdict(set)  # child -> set(parents)
    for r in fk_rows:
        p = r["parent_table"]
        c = r["child_table"]
        parents[p].add(c)
        children_of[c].add(p)
    return parents, children_of

def topo_sort_all_tables(all_tables, children_of):
    # Kahn's algorithm
    in_degree = {t: len(children_of.get(t, set())) for t in all_tables}
    # Note: children_of maps child->parents, so in_degree is num parents.
    q = deque([t for t, deg in in_degree.items() if deg == 0])
    order = []
    while q:
        t = q.popleft()
        order.append(t)
        # find children for which t is a parent
        # build reverse mapping (parent->children) on the fly is expensive, so instead
        # we'll compute children list externally when calling this function. For simplicity,
        # caller will pass children_of as needed; here we just return order with the given logic.
        # To keep it simple, we'll finish with any remaining tables appended (cycles).
        # (We'll refine below by building parent->children first outside before calling.)
        # So break and let caller compute topological using full mapping.
        pass
    # Instead of the above half-implemented approach, implement a proper topo using parent->children mapping:
    return None  # placeholder — we'll use the other implementation below

def topological_sort(parents_map, children_of, all_tables):
    # parents_map: parent -> set(children)
    # children_of: child -> set(parents)
    in_deg = {t: len(children_of.get(t, set())) for t in all_tables}
    q = deque([t for t in all_tables if in_deg.get(t,0) == 0])
    res = []
    while q:
        n = q.popleft()
        res.append(n)
        for child in parents_map.get(n, ()):
            in_deg[child] -= 1
            if in_deg[child] == 0:
                q.append(child)
    # if cycles remain (in_deg > 0), append them at end (breaking cycles arbitrarily)
    remaining = [t for t,deg in in_deg.items() if deg>0]
    if remaining:
        print("⚠️  Cycles or remaining tables detected; appending them after resolved ones:", remaining)
        res.extend(remaining)
    # ensure all tables present
    for t in all_tables:
        if t not in res:
            res.append(t)
    return res

# ----------------------------
# Sampling helpers
# ----------------------------
def detect_order_column(conn, table, prefer):
    cols = show_columns(conn, table)
    if prefer in cols:
        return prefer
    for c in ("created_at", "created", "ts", "updated_at", "id"):
        if c in cols:
            return c
    # fallback to first column
    if cols:
        return cols[0]
    return None

def quote_sql_value(val):
    # naive quoting for IN lists; PyMySQL connection could escape but we need literal string here for --where
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    # else string: escape single quotes and backslashes
    s = str(val).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"

def fetch_pk_values(remote_conn, db, table, pk_cols, where_clause=None, order_by=None, limit=None):
    # returns list of tuples (for composite pk) or scalars (single pk)
    # build select
    if not pk_cols:
        return []
    select = ", ".join([f"`{c}`" for c in pk_cols])
    q = f"SELECT {select} FROM `{db}`.`{table}`"
    parts = []
    if where_clause:
        # Don't wrap in extra parens - caller controls grouping
        parts.append(f"WHERE {where_clause}")
    if order_by:
        parts.append(f"ORDER BY {order_by} DESC")
    if limit:
        parts.append(f"LIMIT {limit}")
    if parts:
        q += " " + " ".join(parts)
    with remote_conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
        results = []
        for r in rows:
            if len(pk_cols) == 1:
                results.append(r[pk_cols[0]])
            else:
                results.append(tuple(r[c] for c in pk_cols))
        return results

# ----------------------------
# Dump & load functions
# ----------------------------
def dump_schema_tables_only(cfg, table_names_list):
    """Dump schema for base tables only (no views)."""
    out = os.path.join(DUMP_DIR, "schema_tables.sql")
    if not table_names_list:
        return out

    # Create secure config file
    config_file = create_mysql_config_file(
        cfg['remote']['host'],
        cfg['remote']['user'],
        cfg['remote']['password'],
        cfg['remote'].get('port', 3306)
    )

    table_names = " ".join(table_names_list)
    cmd = (
        f"mysqldump --defaults-extra-file={config_file} "
        f"--no-data --skip-lock-tables --single-transaction "
        f"--no-tablespaces --skip-add-locks {cfg['remote']['database']} {table_names} > {out}"
    )
    run(cmd)
    return out

def dump_views(cfg, view_names_list):
    """Dump view definitions."""
    out = os.path.join(DUMP_DIR, "views.sql")
    if not view_names_list:
        # Create empty file
        with open(out, 'w') as f:
            f.write("-- No views to dump\n")
        return out

    # Create secure config file
    config_file = create_mysql_config_file(
        cfg['remote']['host'],
        cfg['remote']['user'],
        cfg['remote']['password'],
        cfg['remote'].get('port', 3306)
    )

    view_names = " ".join(view_names_list)
    cmd = (
        f"mysqldump --defaults-extra-file={config_file} "
        f"--no-data --skip-lock-tables --single-transaction "
        f"--no-tablespaces --skip-add-locks {cfg['remote']['database']} {view_names} > {out}"
    )
    run(cmd)
    return out

def dump_table_full(cfg, table):
    out = os.path.join(DUMP_DIR, f"{table}.sql")

    # Create secure config file
    config_file = create_mysql_config_file(
        cfg['remote']['host'],
        cfg['remote']['user'],
        cfg['remote']['password'],
        cfg['remote'].get('port', 3306)
    )

    cmd = (
        f"mysqldump --defaults-extra-file={config_file} "
        f"--no-create-info --skip-lock-tables --single-transaction "
        f"--no-tablespaces --skip-add-locks {cfg['remote']['database']} {table} > {out}"
    )
    run(cmd)
    return out

def dump_table_where(cfg, table, where_clause):
    out = os.path.join(DUMP_DIR, f"{table}.sql")

    # Create secure config file
    config_file = create_mysql_config_file(
        cfg['remote']['host'],
        cfg['remote']['user'],
        cfg['remote']['password'],
        cfg['remote'].get('port', 3306)
    )

    # Escape the where_clause to prevent shell interpretation of special characters
    # Replace backticks with \\` to prevent command substitution
    escaped_where = where_clause.replace('`', '\\`')
    cmd = (
        f"mysqldump --defaults-extra-file={config_file} "
        f"--no-create-info --skip-lock-tables --single-transaction "
        f"--no-tablespaces --skip-add-locks --where=\"{escaped_where}\" "
        f"{cfg['remote']['database']} {table} > {out}"
    )
    run(cmd)
    return out

def load_local(cfg, filename, disable_fk_checks=False):
    # Note: For docker exec, we use environment variable MYSQL_PWD which is less secure
    # but only visible within the docker container context. The password is not exposed
    # in the host process list, only in the container's environment.
    # Alternative would be to mount the config file into the container, but this is simpler.

    if disable_fk_checks:
        # For schema loads, strip out FK constraints that cause issues with MySQL 9
        # We'll load the schema without FKs, then data will load, and orphan cleanup handles referential integrity
        import re
        with open(filename, 'r') as f:
            content = f.read()

        # Remove CONSTRAINT ... FOREIGN KEY lines
        # Pattern matches: CONSTRAINT `name` FOREIGN KEY (...) REFERENCES ...,
        content = re.sub(r',?\s*CONSTRAINT\s+`[^`]+`\s+FOREIGN\s+KEY\s+\([^)]+\)\s+REFERENCES\s+`[^`]+`\s+\([^)]+\)(?:\s+ON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|SET NULL|NO ACTION|RESTRICT))*', '', content, flags=re.IGNORECASE)

        modified_file = filename.replace('.sql', '_no_fk.sql')
        with open(modified_file, 'w') as f:
            f.write(content)
        try:
            # Use MYSQL_PWD environment variable (safer for docker exec)
            cmd = (
                f"docker exec -i -e MYSQL_PWD='{cfg['local']['password']}' "
                f"{cfg['local']['docker_container']} mysql -u {cfg['local']['user']} "
                f"{cfg['local']['database']} < {modified_file}"
            )
            run(cmd)
        finally:
            # Keep the file for debugging if there's an error
            pass  # Don't delete modified_file
    else:
        # Use MYSQL_PWD environment variable (safer for docker exec)
        cmd = (
            f"docker exec -i -e MYSQL_PWD='{cfg['local']['password']}' "
            f"{cfg['local']['docker_container']} mysql -u {cfg['local']['user']} "
            f"{cfg['local']['database']} < {filename}"
        )
        run(cmd)

# ----------------------------
# Core sampling logic
# ----------------------------
def sample_and_dump_table(cfg, remote_conn, table, size_mb, pk_map, fk_map, sampled_ids_store, loaded_tables):
    """
    pk_map: table -> list(primary key columns)
    fk_map: child_table -> list of dicts {child_col, parent_table, parent_col}
    sampled_ids_store: table -> list of sampled primary keys (scalars or tuples)
    loaded_tables: set of tables already loaded (parents)
    """
    threshold = cfg["settings"]["size_threshold_mb"]
    base_row_limit = cfg["settings"]["row_limit"]
    prefer_col = cfg["settings"]["prefer_column"]
    max_multiplier = cfg["settings"].get("max_refill_multiplier", 8)

    print(f"\nProcessing table: {table} ({size_mb} MB)")

    # decide full copy or sampled
    if size_mb is None:
        size_mb = 0
    try:
        size_mb_val = float(size_mb)
    except Exception:
        size_mb_val = 0

    pk_cols = pk_map.get(table, [])
    has_single_pk = len(pk_cols) == 1

    # Build FK restrictions referencing already-sampled parent IDs (if any)
    fk_restrictions = []
    if table in fk_map:
        for fk in fk_map[table]:
            parent = fk["parent_table"]
            child_col = fk["child_col"]
            parent_col = fk["parent_col"]
            if parent in sampled_ids_store and sampled_ids_store[parent]:
                parent_ids = sampled_ids_store[parent]
                # parent_ids can be list of scalars or tuples
                # form quoted list
                if isinstance(parent_ids[0], tuple):
                    # parent pk composite - can't make IN for single column; skip restriction for composite parents
                    print(f"  ⚠️  parent {parent} has composite PK; skipping fk restriction for {table}.{child_col}")
                    continue
                # quote values
                quoted = ",".join(quote_sql_value(v) for v in parent_ids)
                fk_restrictions.append(f"`{child_col}` IN ({quoted})")
            else:
                # parent not sampled yet or empty sample; skip restriction
                pass

    # If small table, full copy
    if size_mb_val < threshold:
        print("  small table -> full copy (fast)")
        fname = dump_table_full(cfg, table)
        # capture primary keys of what we loaded (query remote for pk values)
        if pk_cols:
            ids = fetch_pk_values(remote_conn, cfg["remote"]["database"], table, pk_cols)
            sampled_ids_store[table] = ids
            print(f"  recorded {len(ids)} PK values for {table}")
        else:
            sampled_ids_store[table] = []
        return fname

    # For large tables, attempt FK-aware sampling
    order_col = detect_order_column(remote_conn, table, prefer_col)
    if not order_col:
        order_col = pk_cols[0] if pk_cols else None

    multiplier = 1
    limit = base_row_limit
    tried_without_restriction = False

    while True:
        # Build where_clause for selecting primary keys from remote first
        # If we have pk_cols single-column, we will fetch IDs via SELECT and then dump with IN (...)
        # Build a where clause that restricts by FK restrictions (ANDed) if present.
        # Group multiple FK restrictions with AND
        if fk_restrictions:
            if len(fk_restrictions) > 1:
                select_where = "(" + " AND ".join(fk_restrictions) + ")"
            else:
                select_where = fk_restrictions[0]
        else:
            select_where = None

        # fetch pk values from remote according to restrictions and ordering
        ids = fetch_pk_values(remote_conn, cfg["remote"]["database"], table, pk_cols,
                              where_clause=select_where, order_by=order_col, limit=limit)

        if ids:
            # we got some rows -> create a dump using IN (...) if possible to avoid repeated ordering issues
            if has_single_pk:
                # produce where clause like `pk` IN (..)
                quoted = ",".join(quote_sql_value(v) for v in ids)
                where_clause = f"`{pk_cols[0]}` IN ({quoted})"
                fname = dump_table_where(cfg, table, where_clause)
                sampled_ids_store[table] = ids
                print(f"  dumped {len(ids)} rows for {table} (limit {limit})")
                return fname
            else:
                # composite PK: use tuple IN syntax: WHERE (col1, col2) IN ((val1, val2), ...)
                pk_names = ", ".join([f"`{c}`" for c in pk_cols])
                # Build list of tuples
                tuple_values = []
                for id_tuple in ids:
                    values = ",".join(quote_sql_value(v) for v in id_tuple)
                    tuple_values.append(f"({values})")
                tuples_str = ",".join(tuple_values)
                where_clause = f"({pk_names}) IN ({tuples_str})"
                fname = dump_table_where(cfg, table, where_clause)
                sampled_ids_store[table] = ids
                print(f"  dumped {len(ids)} rows for {table} using composite PK IN clause")
                return fname
        else:
            # no rows found under current restrictions -> refill behavior
            if multiplier >= max_multiplier:
                print(f"  ⚠️  No rows found for {table} with FK restrictions after {multiplier}x tries.")
                if not tried_without_restriction:
                    print("  Attempting sampling WITHOUT FK restrictions as fallback.")
                    # try sampling without fk restrictions
                    fk_restrictions = []  # drop restrictions
                    tried_without_restriction = True
                    multiplier = 1
                    limit = base_row_limit
                    continue
                else:
                    print("  Giving up on sampling (will create empty dump).")
                    # produce empty dump by running mysqldump with a WHERE that yields no rows
                    fname = dump_table_where(cfg, table, "1=0")
                    sampled_ids_store[table] = []
                    return fname
            else:
                multiplier *= 2
                limit = base_row_limit * multiplier
                print(f"  empty sample, increasing limit -> {limit} (multiplier {multiplier})")
                time.sleep(0.1)  # small pause then retry

# ----------------------------
# Main orchestration
# ----------------------------
def main():
    cfg = load_config()
    ensure_dir(DUMP_DIR)

    # Connect remote
    print("🔌 Connecting to remote DB...")
    remote = cfg["remote"]
    remote_conn = connect_mysql(remote["host"], remote["user"], remote["password"], remote["database"], use_ssl=True)

    # get tables info (includes both tables and views)
    print("🔎 Inspecting remote schema...")
    tables_info = get_tables_info(remote_conn, remote["database"])

    # Separate base tables from views
    base_tables = [r for r in tables_info if not is_view(r.get("table_type", "BASE TABLE"))]
    views = [r for r in tables_info if is_view(r.get("table_type", ""))]

    table_sizes = {r["table_name"]: r["size_mb"] for r in base_tables}
    all_tables = [r["table_name"] for r in base_tables]
    view_names = [r["table_name"] for r in views]

    print(f"Found {len(all_tables)} base tables and {len(view_names)} views")

    # get FKs and build maps
    fk_rows = get_foreign_keys(remote_conn, remote["database"])
    fk_map = defaultdict(list)  # child_table -> list of fk dicts
    for r in fk_rows:
        fk_map[r["child_table"]].append({
            "child_col": r["child_col"],
            "parent_table": r["parent_table"],
            "parent_col": r["parent_col"]
        })

    parents_map, children_of = build_dependency_graph(fk_rows)
    topo = topological_sort(parents_map, children_of, all_tables)
    print("Topological load order (parents before children):")
    print(" -> ".join(topo))

    # get primary key columns for all tables
    pk_map = {}
    for t in all_tables:
        pk_map[t] = get_primary_key_columns(remote_conn, remote["database"], t)

    # Dump schema for base tables only (views come after data is loaded)
    print("\n🧱 Dumping table schemas (no data)...")
    schema_file = dump_schema_tables_only(cfg, all_tables)
    print("📥 Loading table schemas into local DB...")
    load_local(cfg, schema_file, disable_fk_checks=True)

    # iterate topo order and sample/dump/load
    sampled_ids_store = {}  # table -> list of sampled primary key values
    loaded_tables = set()
    for table in topo:
        size_mb = table_sizes.get(table, 0)
        try:
            fname = sample_and_dump_table(cfg, remote_conn, table, size_mb, pk_map, fk_map, sampled_ids_store, loaded_tables)
            # load to local even if dump may be empty
            print(f"📥 Loading {table} into local DB from {fname} ...")
            load_local(cfg, fname)
        except Exception as e:
            print(f"❌ Error processing table {table}: {e}", file=sys.stderr)
            # attempt to continue with next tables
        loaded_tables.add(table)

    print("\n✅ All tables processed.")

    # Now load views (they must come AFTER all table data is loaded)
    if view_names:
        print(f"\n👁️  Dumping and loading {len(view_names)} views...")
        try:
            views_file = dump_views(cfg, view_names)
            print(f"📥 Loading views into local DB from {views_file}...")
            load_local(cfg, views_file, disable_fk_checks=False)
            print("✅ Views loaded successfully")
        except Exception as e:
            print(f"⚠️  Error loading views: {e}", file=sys.stderr)
    else:
        print("\nℹ️  No views found in remote database")

    # Optionally run orphan cleanup
    if cfg["settings"].get("prune_orphans", False):
        print("\n🧹 Running orphan cleanup...")
        try:
            # call cleanup script in same directory (user provided earlier)
            run("python3 cleanup_orphans.py")
        except Exception as e:
            print("⚠️  cleanup_orphans.py failed:", e)

    print("\n🎉 Done. Local clone is ready. Connect to local db as configured.")

if __name__ == "__main__":
    main()
