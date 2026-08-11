#!/usr/bin/env python3
"""Delete rows whose foreign keys point at parents the sampler did not bring over.

``bootstrap_clone.py`` samples large tables rather than copying them, so a child row
routinely survives a parent that did not. This script deletes those orphans so the
clone's next step — re-applying the ~117 FK constraints from the remote schema — can
succeed.

Two properties of that job are easy to get wrong, and both bit us on the ``xras_*``
tables:

**Deleting orphans creates orphans.** A single pass is only correct for FK chains one
level deep. Emptying ``xras_action_log`` of rows whose ``source_action_id`` is dangling
strands any ``xras_activation_event`` row that referenced them, and nothing revisits
it. So the sweep repeats until a full pass deletes nothing — a fixed point, which
terminates because passes only ever remove rows.

**The sweep order is `information_schema`'s, not the dependency graph's.** A parent
table can therefore be cleaned while its children still reference it, which MySQL
rejects outright::

    1451 Cannot delete or update a parent row: a foreign key constraint fails
    (`sam`.`xras_activation_event`, CONSTRAINT `xras_activation_event_action_fk` ...)

Rather than topologically sorting the graph, the sweep runs with
``FOREIGN_KEY_CHECKS=0``: a delete-only cleanup is *transiently* inconsistent by
construction, and the fixed-point loop above is what restores consistency. Checks go
back on at the end, and the clone's constraint re-apply step is the real verdict.

(``anonymize_sam_db.py`` solves the same hazard for the same two tables by hand, with
an ordering comment. This script cannot: it discovers its FKs at runtime.)
"""
import pymysql, yaml

CONFIG_FILE = "config.yaml"

#: Give up rather than spin forever if a pass somehow never stops deleting. Real FK
#: depth here is ~3; anything approaching this is a bug in the loop, not deep data.
MAX_PASSES = 10


def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def cleanup_orphans():
    cfg = load_config()
    conn = pymysql.connect(
        host=cfg["local"]["host"],
        user=cfg["local"]["user"],
        password=cfg["local"]["password"],
        database=cfg["local"]["database"]
    )

    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, referenced_table_name, referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_schema=%s AND referenced_table_name IS NOT NULL
        """, (cfg["local"]["database"],))
        fks = cur.fetchall()

    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")

            for pass_no in range(1, MAX_PASSES + 1):
                deleted_this_pass = 0

                for table, col, parent, parent_col in fks:
                    query = f"""
                    DELETE c FROM {table} c
                    LEFT JOIN {parent} p ON c.{col} = p.{parent_col}
                    WHERE c.{col} IS NOT NULL AND p.{parent_col} IS NULL;
                    """
                    n = cur.execute(query)
                    if n:
                        print(f"  pass {pass_no}: {table}.{col} → "
                              f"{parent}.{parent_col} — {n:,} orphan(s)")
                    deleted_this_pass += n

                total += deleted_this_pass
                if deleted_this_pass == 0:
                    print(f"✅ Orphan cleanup complete after {pass_no} pass(es), "
                          f"{total:,} row(s) deleted.")
                    break
            else:
                print(f"⚠️  Still deleting rows after {MAX_PASSES} passes "
                      f"({total:,} so far) — stopping. Inspect the FK graph.")

            conn.commit()
    except Exception:
        # All-or-nothing, deliberately. A half-applied sweep leaves the clone in a
        # state neither this script nor the FK re-apply step can reason about — and
        # it is precisely the rollback-on-error behaviour that saved the database
        # when the old predicate was live (see the module docstring).
        conn.rollback()
        raise
    finally:
        # A session variable, not transactional — restore it without committing, so
        # this cannot resurrect a rolled-back sweep.
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")


if __name__ == "__main__":
    cleanup_orphans()
