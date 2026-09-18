#!/usr/bin/env python3
"""Compare the tables in a committed blob against the database about to replace it.

Prints the tables the database adds (with row counts) and exits 1 if the
database lacks a table the blob carries (a regenerated blob missing one would
break every CI restore) or holds rows in a table the blob ships empty (a test
leftover, which the leak check only catches when its name looks real).
"""
import argparse
import lzma
import re
import sys

from sqlalchemy import create_engine, text

_USE = re.compile(rb'^USE `([^`]+)`;')
_CREATE = re.compile(rb'^CREATE TABLE `([^`]+)`')
_INSERT = re.compile(rb'^INSERT INTO `([^`]+)`')


def blob_tables(path):
    """``(tables, populated)`` as `db.table` sets from a mysqldump --all-databases blob."""
    found, populated, db = set(), set(), None
    with lzma.open(path, 'rb') as fh:
        for line in fh:
            m = _USE.match(line)
            if m:
                db = m.group(1).decode()
                continue
            m = _CREATE.match(line) or _INSERT.match(line)
            if m and db:
                name = f'{db}.{m.group(1).decode()}'
                found.add(name)
                if line.startswith(b'INSERT'):
                    populated.add(name)
    return found, populated


def db_tables(engine, schemas):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema IN :schemas AND table_type = 'BASE TABLE'"),
            {'schemas': tuple(schemas)}).fetchall()
        tables = {f'{s}.{t}' for s, t in rows}
        counts = {}
        for name in tables:
            s, t = name.split('.', 1)
            counts[name] = conn.execute(text(f'SELECT COUNT(*) FROM `{s}`.`{t}`')).scalar()
    return tables, counts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--blob', required=True, help='the committed sam-obfuscated.sql.xz')
    p.add_argument('--url', required=True, help='SQLAlchemy URL of the database to dump')
    p.add_argument('--schemas', default='sam,system_status')
    args = p.parse_args(argv)

    committed, populated = blob_tables(args.blob)
    current, counts = db_tables(create_engine(args.url), args.schemas.split(','))
    schemas = set(args.schemas.split(','))
    committed = {t for t in committed if t.split('.', 1)[0] in schemas}

    added = sorted(current - committed)
    missing = sorted(committed - current)
    leftovers = sorted(t for t in committed & current
                       if t not in populated and counts[t])
    print(f'Committed blob: {len(committed)} tables; database: {len(current)} tables')
    for name in added:
        print(f'  + {name}: {counts[name]:,} rows')
    for name in missing:
        print(f'  - {name}: in the blob, NOT in the database')
    for name in leftovers:
        print(f'  ! {name}: {counts[name]:,} rows, but the blob ships it empty')
    if missing or leftovers:
        print('\nFAIL: the new blob would ' + (
            'drop tables CI restores' if missing else 'ship rows tests left behind')
            + '; reset the test database from the committed blob first '
              '(make test-db-reset test-db-up).')
        return 1
    print('\nOK: every committed table is present' + (
        f'; {len(added)} new' if added else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
