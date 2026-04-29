#!/usr/bin/env python3
"""Audit prod DB schema vs ORM models — beyond what test_schema_validation.py covers.

Catches the DiskActivity-class drift that PR #209 fixed pointwise:
indexes, unique constraints, FK actions, and column nullable/default
mismatches that the existing column/PK checks miss.

Read-only. Connects via PROD_SAM_DB_{SERVER,USERNAME,PASSWORD}. Skips
gracefully (exit 0) when:
  - any of those env vars is unset (CI / clean checkout)
  - the host is unreachable (off-VPN — sam-sql.ucar.edu requires UCAR VPN)

Use --strict to flip skips into hard failures (e.g. nightly job from
an on-VPN runner that *should* error if it can't reach prod).
"""
import argparse
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

# Make 'sam' and 'scripts.lib' importable regardless of cwd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
sys.path.insert(0, str(HERE.parent))

from sqlalchemy import URL, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from scripts.lib.schema_introspection import (
    diff_indexes,
    find_rename_pairs,
    get_db_columns,
    get_db_foreign_keys,
    get_db_indexes,
    get_orm_indexes,
    get_table_type,
    is_acceptable_type_mismatch,
    iter_table_mappers,
    normalize_type,
    TYPE_MAPPINGS,
)


REQUIRED_ENV = ('PROD_SAM_DB_SERVER', 'PROD_SAM_DB_USERNAME', 'PROD_SAM_DB_PASSWORD')
CONNECT_TIMEOUT_SECS = 5


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--strict', action='store_true',
                   help='Fail (non-zero exit) on missing env or unreachable host '
                        'instead of skipping with exit 0.')
    p.add_argument('--report', default='indexes,fks,columns',
                   help='Comma-separated checks to run. '
                        'Default: indexes,fks,columns')
    return p.parse_args()


def skip_or_fail(message: str, *, strict: bool) -> int:
    """Exit 0 with a skip message, or 1 in --strict mode."""
    prefix = "ERROR" if strict else "SKIPPED"
    print(f"[{prefix}] {message}", file=sys.stderr)
    return 1 if strict else 0


def check_env(strict: bool) -> int:
    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        return skip_or_fail(
            f"prod creds not in environment (missing: {', '.join(missing)}). "
            f"Set {', '.join(REQUIRED_ENV)} to run this audit.",
            strict=strict,
        )
    return 0


def can_reach(host: str, port: int = 3306, timeout: float = CONNECT_TIMEOUT_SECS) -> bool:
    """TCP-connect probe. Returns False on DNS failure, refusal, or timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.gaierror, socket.timeout):
        return False


def build_prod_engine():
    url = URL.create(
        drivername='mysql+pymysql',
        username=os.environ['PROD_SAM_DB_USERNAME'],
        password=os.environ['PROD_SAM_DB_PASSWORD'],
        host=os.environ['PROD_SAM_DB_SERVER'],
        database=os.environ.get('PROD_SAM_DB_NAME', 'sam'),
    )
    require_ssl = os.getenv('PROD_SAM_DB_REQUIRE_SSL', 'true').lower() in ('true', '1', 'yes')
    connect_args = {'connect_timeout': CONNECT_TIMEOUT_SECS}
    if require_ssl:
        connect_args['ssl'] = {'ssl_disabled': False}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


# ============================================================================
# Per-table audits
# ============================================================================


def audit_indexes(session, mapper) -> list:
    """Index findings, with rename pairs collapsed to one line each.

    A "rename" is when DB and ORM both declare an index on the same
    columns with the same uniqueness, but under different names. That
    pair shows up as a single RENAME line — actionable as one change
    (rename the ORM Index to match prod). Unpaired entries surface as
    real drift.
    """
    table = mapper.persist_selectable.name
    db_idx = get_db_indexes(session, table)
    orm_idx = get_orm_indexes(mapper)
    diff = find_rename_pairs(diff_indexes(db_idx, orm_idx))

    findings = []

    for cols, uniq, db_name, orm_name in diff['renames']:
        kind = 'UNIQUE' if uniq else 'index'
        findings.append(
            f"RENAME {kind} on ({', '.join(cols)}): "
            f"DB='{db_name}' vs ORM='{orm_name}' — rename ORM to match prod"
        )
    for name, cols, uniq in diff['in_db_not_orm']:
        kind = 'UNIQUE INDEX' if uniq else 'INDEX'
        findings.append(
            f"DB has {kind} '{name}' on ({', '.join(cols)}) — ORM does not declare it"
        )
    for name, cols, uniq in diff['in_orm_not_db']:
        kind = 'UNIQUE INDEX' if uniq else 'INDEX'
        findings.append(
            f"ORM declares {kind} '{name}' on ({', '.join(cols)}) — DB does not have it"
        )
    for name, (db_cols, db_uniq), (orm_cols, orm_uniq) in diff['mismatched']:
        findings.append(
            f"INDEX '{name}' differs: DB=(cols={db_cols}, unique={db_uniq}) "
            f"vs ORM=(cols={orm_cols}, unique={orm_uniq})"
        )
    return findings


# ORM-side FKs that are intentionally declared without a matching DB-level
# constraint. Each entry is (table_name, frozenset_of_columns) plus a
# one-line reason. Used to suppress the corresponding finding so the
# legitimate drift stays visible.
IGNORED_FK_DRIFT = {
    # Self-referential FK driving the nested-set parent/children relationship
    # used by the admin organizations dashboard. Prod doesn't enforce the FK
    # constraint at the DB level, but the ORM declaration is load-bearing for
    # SQLAlchemy joins (subqueryload(Organization.children) etc).
    ('organization', frozenset({'parent_org_id'})): (
        'self-FK drives Organization.parent/.children nested-set relationship'),
}


def audit_foreign_keys(session, mapper) -> list:
    table = mapper.persist_selectable.name
    db_fks = get_db_foreign_keys(session, table)
    findings = []

    orm_fk_cols = set()
    for fk in mapper.persist_selectable.foreign_keys:
        orm_fk_cols.add(fk.parent.name)

    db_fk_cols = set()
    for cname, info in db_fks.items():
        db_fk_cols.update(info['columns'])

    missing_in_db = orm_fk_cols - db_fk_cols
    if missing_in_db and (table, frozenset(missing_in_db)) not in IGNORED_FK_DRIFT:
        findings.append(
            f"ORM declares FK on column(s) {sorted(missing_in_db)} but DB has no FK constraint"
        )

    extra_in_db = db_fk_cols - orm_fk_cols
    if extra_in_db:
        findings.append(
            f"DB has FK constraint on column(s) {sorted(extra_in_db)} but ORM does not declare it"
        )

    return findings


def audit_columns(session, mapper) -> list:
    table = mapper.persist_selectable.name
    db_cols = get_db_columns(session, table)
    findings = []

    orm_col_names = {c.name for c in mapper.persist_selectable.columns}
    db_col_names = set(db_cols.keys())

    missing = orm_col_names - db_col_names
    if missing:
        findings.append(f"ORM columns missing from DB: {sorted(missing)}")

    # Type comparison
    for col in mapper.persist_selectable.columns:
        if col.name not in db_cols:
            continue
        orm_type = type(col.type).__name__
        db_raw = db_cols[col.name]['type']
        db_norm = normalize_type(db_raw)
        acceptable = TYPE_MAPPINGS.get(orm_type, [])
        if not acceptable or db_norm in acceptable:
            continue
        # String → TEXT widening is benign
        if orm_type == 'String' and db_norm in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT'):
            continue
        msg = f"{col.name}: ORM={orm_type} → DB={db_raw}"
        if not is_acceptable_type_mismatch(msg):
            findings.append(f"type mismatch — {msg}")

        # Nullability mismatch (warn only — frequently intentional in MySQL)
        db_nullable = db_cols[col.name]['nullable']
        if col.nullable != db_nullable:
            findings.append(
                f"{col.name}: nullable mismatch — ORM={col.nullable} vs DB={db_nullable}"
            )

    return findings


# ============================================================================
# Reporting
# ============================================================================


def main() -> int:
    args = parse_args()
    checks = {c.strip() for c in args.report.split(',') if c.strip()}

    rc = check_env(strict=args.strict)
    if rc != 0:
        return rc

    host = os.environ['PROD_SAM_DB_SERVER']
    if not can_reach(host):
        return skip_or_fail(
            f"cannot reach {host}:3306 within {CONNECT_TIMEOUT_SECS}s "
            "(VPN required? sam-sql.ucar.edu is only reachable over the UCAR VPN)",
            strict=args.strict,
        )

    print(f"[INFO] connecting to {host} (read-only INFORMATION_SCHEMA queries)…")
    try:
        engine = build_prod_engine()
        SessionLocal = sessionmaker(bind=engine)
    except OperationalError as e:
        return skip_or_fail(f"connection setup failed: {e}", strict=args.strict)

    # Importing 'sam' loads all ORM models into Base.registry
    import sam  # noqa: F401
    from sam.base import Base

    session = SessionLocal()
    try:
        rc = run_audit(session, Base, checks)
    finally:
        session.close()

    # Also run the existing ORM inventory pass (column coverage + relationship
    # map) against the same prod engine, so `make check-db-vs-orms` captures
    # both reports in one invocation.
    print()
    print("#" * 72)
    print("# orm_inventory: full column / relationship coverage report")
    print("#" * 72)
    try:
        from orm_inventory import generate_report
        generate_report(engine, issues_only=True)
    except Exception as e:
        print(f"[WARN] orm_inventory failed: {e}", file=sys.stderr)
    finally:
        engine.dispose()

    return rc


def run_audit(session, Base, checks: set) -> int:
    print(f"[INFO] checks: {sorted(checks)}\n")
    total_findings = 0
    tables_with_findings = 0
    tables_audited = 0

    for mapper in sorted(iter_table_mappers(Base.registry),
                         key=lambda m: m.persist_selectable.name):
        table = mapper.persist_selectable.name

        # Skip if the table doesn't exist in this DB (model coverage problem,
        # already surfaced by test_all_tables_exist_in_database)
        if get_table_type(session, table) != 'BASE TABLE':
            continue

        tables_audited += 1
        findings = []

        if 'indexes' in checks:
            findings += [('idx', f) for f in audit_indexes(session, mapper)]
        if 'fks' in checks:
            findings += [('fk',  f) for f in audit_foreign_keys(session, mapper)]
        if 'columns' in checks:
            findings += [('col', f) for f in audit_columns(session, mapper)]

        if not findings:
            continue

        tables_with_findings += 1
        total_findings += len(findings)
        print(f"TABLE: {table}  ({mapper.class_.__name__})")
        for kind, msg in findings:
            print(f"  ⚠️  [{kind}] {msg}")
        print()

    print("=" * 72)
    print(f"audited {tables_audited} tables — "
          f"{tables_with_findings} with findings, {total_findings} total findings")

    # Exit 0 even when there are findings — this is a report tool, not a gate.
    # The CI guards in test_schema_validation.py are the gate.
    return 0


if __name__ == '__main__':
    sys.exit(main())
