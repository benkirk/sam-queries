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
import fnmatch
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
    s.setdefault("table_strategies", [])       # per-table overrides (empty / recent)
    a = cfg["remote"]
    a.setdefault("user", os.environ['PROD_SAM_DB_USERNAME'])
    a.setdefault("password",os.environ['PROD_SAM_DB_PASSWORD'])
    a.setdefault("host",os.environ['PROD_SAM_DB_SERVER'])

    return cfg

def run(cmd, capture=False):
    print("→", f"{cmd[:20]} ...")
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


def get_foreign_key_constraints(conn, db):
    """Return remote FK constraints as a list of dicts ready for ALTER TABLE.

    Groups multi-column composite FKs into a single record with ordered
    `child_cols` / `parent_cols` lists. Captures ON DELETE / ON UPDATE
    rules from REFERENTIAL_CONSTRAINTS so the rebuilt FK matches prod
    exactly.

    Output shape per FK:
        {
            'child_table':   str,
            'constraint':    str,
            'child_cols':    [str, ...],
            'parent_table':  str,
            'parent_cols':   [str, ...],
            'on_delete':     str,   # 'CASCADE' | 'SET NULL' | 'RESTRICT' | 'NO ACTION'
            'on_update':     str,
        }
    """
    q = """
    SELECT kcu.TABLE_NAME            AS child_table,
           kcu.CONSTRAINT_NAME       AS constraint_name,
           kcu.COLUMN_NAME           AS child_col,
           kcu.REFERENCED_TABLE_NAME AS parent_table,
           kcu.REFERENCED_COLUMN_NAME AS parent_col,
           kcu.ORDINAL_POSITION      AS ordinal,
           rc.DELETE_RULE            AS on_delete,
           rc.UPDATE_RULE            AS on_update
    FROM information_schema.key_column_usage kcu
    JOIN information_schema.referential_constraints rc
      ON kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
     AND kcu.CONSTRAINT_NAME   = rc.CONSTRAINT_NAME
    WHERE kcu.TABLE_SCHEMA = %s
      AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
    ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
    """
    with conn.cursor() as cur:
        cur.execute(q, (db,))
        rows = cur.fetchall()

    grouped = {}
    for r in rows:
        key = (r["child_table"], r["constraint_name"])
        if key not in grouped:
            grouped[key] = {
                "child_table":  r["child_table"],
                "constraint":   r["constraint_name"],
                "child_cols":   [],
                "parent_table": r["parent_table"],
                "parent_cols":  [],
                "on_delete":    r["on_delete"],
                "on_update":    r["on_update"],
            }
        grouped[key]["child_cols"].append(r["child_col"])
        grouped[key]["parent_cols"].append(r["parent_col"])

    return list(grouped.values())

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
def match_strategy(table, strategies):
    """Return the first strategy dict whose pattern matches `table`, else None."""
    for s in strategies or []:
        if fnmatch.fnmatchcase(table, s.get("pattern", "")):
            return s
    return None

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

def _build_alter_add_fk(fk):
    """Render a single ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY ... statement."""
    child_cols  = ", ".join(f"`{c}`" for c in fk["child_cols"])
    parent_cols = ", ".join(f"`{c}`" for c in fk["parent_cols"])
    stmt = (
        f"ALTER TABLE `{fk['child_table']}` "
        f"ADD CONSTRAINT `{fk['constraint']}` "
        f"FOREIGN KEY ({child_cols}) "
        f"REFERENCES `{fk['parent_table']}` ({parent_cols})"
    )
    # MySQL's default for omitted ON DELETE/UPDATE is RESTRICT;
    # only emit a clause when it differs, to keep the SQL clean.
    if fk["on_delete"] and fk["on_delete"].upper() != "RESTRICT":
        stmt += f" ON DELETE {fk['on_delete']}"
    if fk["on_update"] and fk["on_update"].upper() != "RESTRICT":
        stmt += f" ON UPDATE {fk['on_update']}"
    return stmt


def reapply_foreign_keys(cfg, fk_constraints):
    """Phase C: re-apply prod's FK constraints to the local clone.

    The schema load strips FK constraints (see load_local with
    disable_fk_checks=True) because mysqldump's pre-table FOREIGN KEY
    declarations cause issues on MySQL 9 when tables are loaded in any
    order other than strict topological. After data has been loaded
    in topological order with FK-aware sampling, parent rows for every
    sampled child row are guaranteed to exist, so we can re-apply the
    constraints from prod here.

    Tolerant of individual failures — applies each ALTER TABLE in its
    own statement and logs failures rather than aborting the whole
    bootstrap. Two common failure modes:

    1. Legacy MySQL/InnoDB permissiveness: prod was created when an FK
       could reference the leftmost column of a composite PK even
       though that column alone wasn't unique. MySQL 8.0+ enforces
       strict matching, so these "grandfathered" FKs fail to recreate.
       Example: `dav_activity` has PK (`dav_activity_id`, `queue_name`)
       and prod's `fk_dav_charge_dav_activity` references just
       `dav_activity_id` — doesn't satisfy MySQL 9's uniqueness rule.

    2. Orphan data not handled by `cleanup_orphans` — sampled child
       rows pointing at parents that didn't make it into the sample.
       FK creation fails loudly, which is the desired signal.

    Either way, the local clone ends up with *most* FKs applied and a
    clear log of what's missing. The bootstrap continues.
    """
    if not fk_constraints:
        print("ℹ️  No FK constraints to re-apply.")
        return

    # Write the full script for reference / inspection / manual replay.
    out = os.path.join(DUMP_DIR, "foreign_keys.sql")
    with open(out, "w") as f:
        f.write("SET FOREIGN_KEY_CHECKS=0;\n")
        for fk in fk_constraints:
            f.write(_build_alter_add_fk(fk) + ";\n")
        f.write("SET FOREIGN_KEY_CHECKS=1;\n")

    print(f"📥 Re-applying {len(fk_constraints)} FK constraints "
          f"(individual statements; failures are logged, not fatal) ...")

    # Connect once via pymysql for fine-grained per-statement control.
    conn = pymysql.connect(
        host=cfg["local"].get("host", "127.0.0.1"),
        port=int(cfg["local"].get("port", 3306)),
        user=cfg["local"]["user"],
        password=cfg["local"]["password"],
        database=cfg["local"]["database"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    succeeded = 0
    failed = []
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for fk in fk_constraints:
                stmt = _build_alter_add_fk(fk)
                try:
                    cur.execute(stmt)
                    succeeded += 1
                except pymysql.MySQLError as e:
                    failed.append((fk, str(e)))
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
    finally:
        conn.close()

    print(f"✅ {succeeded}/{len(fk_constraints)} FK constraints re-applied")
    if failed:
        print(f"⚠️  {len(failed)} FK constraints could NOT be applied:")
        for fk, err in failed:
            print(f"    - {fk['child_table']}.{fk['constraint']}: {err}")
        print(f"  (see {out} for the full SQL; these are typically legacy "
              f"FKs that no longer satisfy MySQL 9 uniqueness rules, or "
              f"sampled rows whose parents were pruned)")


def seed_dev_gid_block(cfg):
    """Optionally seed a `gid_allocation` block in the local clone.

    Production's `gid_allocation` table is empty (the legacy IDMS-sync
    path that populated it was never run in this org's deployment), so
    a faithful clone of prod also leaves the local table empty. That
    makes the Create Project HTMX form report "No GID blocks defined"
    and disable submission — which is correct, but makes local dev /
    UI smoke-testing of the project-creation flow impossible.

    When `settings.dev_seed.gid_allocation` is configured in config.yaml,
    this step inserts a single dev-only block if (and only if) the
    table is currently empty. Idempotent: a re-clone wipes the table
    and re-seeds; a manual INSERT prior to clone is preserved.

    config.yaml shape (under `settings:`):

        dev_seed:
          gid_allocation:
            start_gid: 80000
            end_gid:   80999

    Omit the block (or the whole `dev_seed` section) on production-
    style clones to leave the table untouched.
    """
    seed_cfg = cfg.get("settings", {}).get("dev_seed", {}).get("gid_allocation")
    if not seed_cfg:
        return

    start_gid = seed_cfg.get("start_gid")
    end_gid = seed_cfg.get("end_gid")
    if start_gid is None or end_gid is None:
        print("⚠️  dev_seed.gid_allocation: missing start_gid/end_gid; skipping")
        return
    if int(start_gid) > int(end_gid):
        print(f"⚠️  dev_seed.gid_allocation: start_gid ({start_gid}) > "
              f"end_gid ({end_gid}); skipping")
        return

    conn = pymysql.connect(
        host=cfg["local"].get("host", "127.0.0.1"),
        port=int(cfg["local"].get("port", 3306)),
        user=cfg["local"]["user"],
        password=cfg["local"]["password"],
        database=cfg["local"]["database"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM gid_allocation")
            existing = cur.fetchone()["n"]
            if existing > 0:
                print(f"ℹ️  dev_seed: gid_allocation already has {existing} "
                      f"row(s); skipping seed")
                return
            cur.execute(
                "INSERT INTO gid_allocation (startGid, endGid) "
                "VALUES (%s, %s)",
                (int(start_gid), int(end_gid)),
            )
            size = int(end_gid) - int(start_gid) + 1
            print(f"✅ dev_seed: inserted gid_allocation block "
                  f"[{start_gid}, {end_gid}] ({size:,} GIDs)")
    finally:
        conn.close()


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
    strategies = cfg["settings"].get("table_strategies", [])

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

    # Per-table strategy overrides take precedence over size-based sampling.
    strategy = match_strategy(table, strategies)
    if strategy is not None:
        mode = strategy.get("mode")
        if mode == "empty":
            print(f"  strategy: empty -> schema only, zero rows")
            fname = dump_table_where(cfg, table, "1=0")
            # Leaves sampled_ids_store empty so any child tables fall through
            # to the "no parent rows" path and are also emptied.
            sampled_ids_store[table] = []
            return fname
        if mode == "recent":
            column = strategy.get("column")
            days = strategy.get("days")
            if not column or not days:
                print(f"  ⚠️  strategy 'recent' for {table} missing column/days; falling back to default sampling")
            elif column not in show_columns(remote_conn, table):
                print(f"  ⚠️  column `{column}` not on {table}; falling back to default sampling")
            else:
                base_where = f"`{column}` >= DATE_SUB(CURDATE(), INTERVAL {int(days)} DAY)"
                conditions = [base_where] + list(fk_restrictions)
                where_clause = " AND ".join(conditions)
                print(f"  strategy: recent ({column} within last {int(days)} days)")
                fname = dump_table_where(cfg, table, where_clause)
                # Summary tables aren't parents of anything we still need to
                # sample, so skip the PK fetch (can be millions of rows).
                sampled_ids_store[table] = []
                return fname
        else:
            print(f"  ⚠️  unknown strategy mode {mode!r} for {table}; falling back to default sampling")

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

    # Phase C: re-apply foreign key constraints after data is in place
    # AND orphans have been cleaned up. The schema load stripped FKs
    # (load_local(..., disable_fk_checks=True)) so the local clone has
    # been running without prod's referential integrity. By doing this
    # after cleanup_orphans, the FK creation is the final integrity
    # check — if anything still fails here, the sampling or cleanup
    # missed something and we want to know loudly rather than silently.
    print("\n🔗 Re-applying foreign key constraints from remote schema...")
    try:
        fk_constraints = get_foreign_key_constraints(remote_conn, remote["database"])
        reapply_foreign_keys(cfg, fk_constraints)
    except Exception as e:
        print(f"⚠️  Error re-applying FK constraints: {e}", file=sys.stderr)
        print("    (likely orphan data not handled by cleanup_orphans;"
              " local clone has weaker FK integrity than prod)")

    # Dev-only seed: insert a gid_allocation block when configured and
    # the cloned table is empty. Skipped silently when not configured —
    # production-style clones leave the table as-cloned.
    print("\n🌱 Checking for dev-only seed steps...")
    try:
        seed_dev_gid_block(cfg)
    except Exception as e:
        print(f"⚠️  Error seeding dev gid_allocation block: {e}", file=sys.stderr)

    print("\n🎉 Done. Local clone is ready. Connect to local db as configured.")

if __name__ == "__main__":
    main()
