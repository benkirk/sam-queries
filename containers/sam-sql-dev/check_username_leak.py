#!/usr/bin/env python3
"""Fail (exit 1) if any username column in the local clone still holds a real name.

Every `username` / `act_username` column is checked, not a fixed pair, and the
preserved names and accepted exposures come from config.yaml so the lists
cannot drift from the anonymizer's. Row counts print alongside: a table
reading clean is often empty.
"""
import argparse
import sys

import yaml
from sqlalchemy import create_engine, text

USERNAME_COLUMNS = ("username", "act_username")


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def preserved_usernames(cfg):
    return sorted(cfg.get("anonymization", {}).get("preserve_usernames") or [])


def accepted_exposures(cfg):
    """`table.column` entries reported but not failed on; each is a recorded decision."""
    return set(cfg.get("anonymization", {}).get("accepted_exposures") or [])


def leak_query(table, column, preserved):
    """Distinct non-anonymized values in one column; `user\\_%` is the anonymizer's shape."""
    q = (f"SELECT COUNT(*), COUNT(DISTINCT `{column}`) FROM `{table}` "
         f"WHERE `{column}` IS NOT NULL AND `{column}` NOT LIKE 'user\\_%'")
    if preserved:
        names = ", ".join(f"'{u}'" for u in preserved)
        q += f" AND `{column}` NOT IN ({names})"
    return q


def check_leaks(cfg):
    local = cfg["local"]
    engine = create_engine(f"mysql+pymysql://{local['user']}:{local['password']}"
                           f"@{local['host']}:{local.get('port', 3306)}/{local['database']}")
    preserved = preserved_usernames(cfg)
    accepted = accepted_exposures(cfg)
    print(f"Preserved usernames (config.yaml): {', '.join(preserved) or 'none'}")
    leaks = 0
    with engine.connect() as conn:
        cols = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :db AND column_name IN :cols ORDER BY 1, 2"),
            {"db": local["database"], "cols": USERNAME_COLUMNS}).fetchall()
        for table, column in cols:
            rows = conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
            leaked_rows, leaked_names = conn.execute(text(leak_query(table, column, preserved))).one()
            if not leaked_rows:
                flag = "✓"
            elif f"{table}.{column}" in accepted:
                flag = "accepted"
            else:
                flag = "✗ LEAK"
                leaks += leaked_rows
            print(f"  {flag:8} {table}.{column}: {rows:,} rows, "
                  f"{leaked_names:,} real name(s) in {leaked_rows:,} row(s)")
    print(f"\n{'✗ FAIL' if leaks else '✓ OK'}: {leaks:,} row(s) with non-anonymized usernames")
    return 1 if leaks else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args(argv)
    return check_leaks(load_config(args.config))


if __name__ == "__main__":
    sys.exit(main())
