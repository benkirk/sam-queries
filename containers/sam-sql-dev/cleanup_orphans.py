#!/usr/bin/env python3
"""Delete rows whose foreign keys point at parents the sampler did not bring over.

``bootstrap_clone.py`` samples large tables rather than copying them, so a child row
routinely survives a parent that did not. FKs are discovered from the *local*
``information_schema``, so this must run after ``reapply_foreign_keys`` put them
back (the schema loads with FKs stripped; before that step there is nothing to find).

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
back on at the end.

FKs named in ``settings.unvalidated_fks`` are skipped and their violating rows
counted instead: a parent emptied by policy (``disk_activity``) would otherwise take
every ``disk_charge`` row with it.
"""
import argparse
import sys

import pymysql
import yaml

#: Give up rather than spin forever if a pass somehow never stops deleting. Real FK
#: depth here is ~3; anything approaching this is a bug in the loop, not deep data.
MAX_PASSES = 10


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def cleanup_orphans(cfg):
    exempt = set(cfg.get("settings", {}).get("unvalidated_fks", []))
    conn = pymysql.connect(
        host=cfg["local"]["host"],
        port=int(cfg["local"].get("port", 3306)),
        user=cfg["local"]["user"],
        password=cfg["local"]["password"],
        database=cfg["local"]["database"],
    )

    with conn.cursor() as cur:
        cur.execute("""
            SELECT constraint_name, table_name, column_name,
                   referenced_table_name, referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_schema=%s AND referenced_table_name IS NOT NULL
        """, (cfg["local"]["database"],))
        rows = cur.fetchall()
    fks = [r[1:] for r in rows if r[0] not in exempt]
    skipped = [r for r in rows if r[0] in exempt]
    if not fks:
        print("⚠️  No foreign keys in the local schema; nothing to sweep "
              "(run after the FK re-apply step).")

    total = 0
    converged = False
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
                    converged = True
                    break
            else:
                print(f"⚠️  Still deleting rows after {MAX_PASSES} passes "
                      f"({total:,} so far) — stopping. Inspect the FK graph.")

            for name, table, col, parent, parent_col in skipped:
                cur.execute(f"SELECT COUNT(*) FROM {table} c LEFT JOIN {parent} p "
                            f"ON c.{col} = p.{parent_col} "
                            f"WHERE c.{col} IS NOT NULL AND p.{parent_col} IS NULL")
                print(f"  ℹ️  {name}: {cur.fetchone()[0]:,} row(s) left unvalidated by policy")

            conn.commit()
    except Exception:
        # All-or-nothing, deliberately. A half-applied sweep leaves the clone in a
        # state neither this script nor the FK re-apply step can reason about.
        conn.rollback()
        raise
    finally:
        # A session variable, not transactional — restore it without committing, so
        # this cannot resurrect a rolled-back sweep.
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
    return 0 if converged else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args(argv)
    return cleanup_orphans(load_config(args.config))


if __name__ == "__main__":
    sys.exit(main())
