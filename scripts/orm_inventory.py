#!/usr/bin/env python3
"""ORM Inventory Script.

Compares all SQLAlchemy ORM models against a database schema and reports:
  - Tables present in DB but not modeled
  - Views present in DB but not modeled
  - Per-model column drift (missing/extra columns, type mismatches)
  - Relationship summary

Type-mismatch detection delegates to `scripts/lib/schema_introspection.py`
so this script and `tests/integration/test_schema_validation.py` use the
SAME acceptable-pairing rules (e.g. SQLAlchemy `Boolean` ↔ MySQL BIT/TINYINT,
`Float` ↔ FLOAT/DOUBLE, `String` ↔ VARCHAR/CHAR, `DateTime` ↔ DATETIME/TIMESTAMP).

Two modes:
  * Called from `scripts/check_db_drift.py` via `generate_report(engine)` —
    invoked with `issues_only=True` so output is terse.
  * Run standalone (`python scripts/orm_inventory.py`) — uses the SAM_DB_*
    environment via `sam.session.create_sam_engine()` (whatever DB the
    `.env` is currently pointed at). No hardcoded localhost URL.
"""

import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

# Make `sam` and `scripts.lib` importable regardless of cwd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
sys.path.insert(0, str(HERE.parent))

from sqlalchemy import inspect, text
from sqlalchemy.orm import class_mapper

from scripts.lib.schema_introspection import (
    is_acceptable_type_mismatch,
    normalize_type,
    TYPE_MAPPINGS,
)


# ---------------------------------------------------------------------------
# ORM / DB inspection helpers
# ---------------------------------------------------------------------------


def get_all_orm_models() -> Dict[str, type]:
    """Return {table_name: ORM class} for every mapper registered to Base."""
    from sam.base import Base
    return {
        m.mapped_table.name: m.class_
        for m in Base.registry.mappers
    }


def get_database_tables(engine):
    """Return (set_of_tables, set_of_views) from the bound DB."""
    inspector = inspect(engine)
    return set(inspector.get_table_names()), set(inspector.get_view_names())


def get_table_columns(engine, table_name: str) -> Dict:
    inspector = inspect(engine)
    return {
        col['name']: {
            'type': str(col['type']),
            'nullable': col['nullable'],
            'default': col.get('default'),
            'autoincrement': col.get('autoincrement', False),
        }
        for col in inspector.get_columns(table_name)
    }


def get_orm_columns(model_class) -> Dict:
    mapper = class_mapper(model_class)
    return {
        col.name: {
            'type': str(col.type),
            'orm_type_name': type(col.type).__name__,
            'nullable': col.nullable,
            'primary_key': col.primary_key,
            'foreign_keys': bool(col.foreign_keys),
        }
        for col in mapper.columns
    }


def get_orm_relationships(model_class) -> Dict:
    mapper = class_mapper(model_class)
    return {
        rel.key: {
            'target': rel.mapper.class_.__name__,
            'direction': rel.direction.name,
            'uselist': rel.uselist,
        }
        for rel in mapper.relationships
    }


# ---------------------------------------------------------------------------
# Column comparison — uses shared TYPE_MAPPINGS so this stays in sync with
# tests/integration/test_schema_validation.py
# ---------------------------------------------------------------------------


def compare_columns(db_cols: Dict, orm_cols: Dict) -> List[str]:
    """Return human-readable issues. Empty list = ORM matches DB."""
    issues: List[str] = []

    db_names = set(db_cols)
    orm_names = set(orm_cols)

    missing_in_orm = db_names - orm_names
    if missing_in_orm:
        issues.append(f"Missing in ORM: {', '.join(sorted(missing_in_orm))}")

    extra_in_orm = orm_names - db_names
    if extra_in_orm:
        issues.append(f"Extra in ORM: {', '.join(sorted(extra_in_orm))}")

    for col_name in db_names & orm_names:
        db_raw = db_cols[col_name]['type']
        orm_short = orm_cols[col_name]['orm_type_name']
        db_norm = normalize_type(db_raw)

        acceptable = TYPE_MAPPINGS.get(orm_short)
        if acceptable is None:
            # ORM uses a type we don't have rules for (e.g. dialect-specific);
            # fall back to a loose case-insensitive match.
            if orm_short.upper() != db_norm:
                issues.append(
                    f"Type mismatch '{col_name}': DB={db_raw}, ORM={orm_short}"
                )
            continue

        if db_norm in acceptable:
            continue

        # SQLAlchemy String → MySQL TEXT widening is a routine no-op.
        if orm_short == 'String' and db_norm in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'TINYTEXT'):
            continue

        msg = f"Type mismatch '{col_name}': DB={db_raw}, ORM={orm_short}"
        if not is_acceptable_type_mismatch(msg):
            issues.append(msg)

    return issues


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


# Tables we know are present in prod but intentionally have no ORM model
# (system tables, partitioning helpers, etc.).
_KNOWN_UNMODELED_TABLES = {
    'EXPORT_TABLE',
    'TIME_DIM',
    'schema_version',
    'tables_dictionary',
    'stage_hpc_job',
    'temp_joey_expired_project',
}


def generate_report(engine, issues_only: bool = True):
    """Generate the inventory report.

    Args:
        engine: SQLAlchemy engine bound to the DB to audit.
        issues_only: When True (default) only print models with findings —
            best for running alongside `check_db_drift.py`. Set False for
            a full per-model dump when running this script standalone for
            exploration.
    """
    print("=" * 72)
    print("SAM ORM INVENTORY REPORT")
    print("=" * 72)

    orm_models = get_all_orm_models()
    db_tables, db_views = get_database_tables(engine)
    orm_table_names = set(orm_models)

    tables_without_orms = (db_tables - orm_table_names) - _KNOWN_UNMODELED_TABLES
    views_without_orms = db_views - orm_table_names

    print(f"\nORM models      : {len(orm_models)}")
    print(f"DB tables       : {len(db_tables)} "
          f"({len(db_tables & orm_table_names)} modeled)")
    print(f"DB views        : {len(db_views)} "
          f"({len(db_views & orm_table_names)} modeled)")

    if tables_without_orms:
        print(f"\n⚠️  Tables without ORM models ({len(tables_without_orms)}):")
        for t in sorted(tables_without_orms):
            print(f"    - {t}")

    if views_without_orms:
        print(f"\nℹ️  Views without ORM models ({len(views_without_orms)}):")
        for v in sorted(views_without_orms):
            print(f"    - {v}")

    print()
    print("-" * 72)
    print("PER-MODEL ANALYSIS" + (" (issues only)" if issues_only else ""))
    print("-" * 72)

    # Group by category for readable per-domain output (only matters in
    # full-dump mode).
    models_by_category = defaultdict(list)
    for table_name, model_class in sorted(orm_models.items()):
        category = (model_class.__module__.split('.')[-1]
                    if '.' in model_class.__module__ else 'other')
        models_by_category[category].append((table_name, model_class))

    total_issues = 0
    models_with_issues: List[str] = []

    for category in sorted(models_by_category):
        models = models_by_category[category]
        category_printed = False

        for table_name, model_class in sorted(models):
            is_view = table_name in db_views

            if is_view:
                if not issues_only:
                    if not category_printed:
                        print(f"\n📁 {category.upper()} ({len(models)} models)")
                        category_printed = True
                    print(f"  {model_class.__name__} → {table_name} (VIEW)")
                continue

            try:
                db_cols = get_table_columns(engine, table_name)
                orm_cols = get_orm_columns(model_class)
                issues = compare_columns(db_cols, orm_cols)
            except Exception as e:
                issues = [f"Error analyzing: {e}"]

            if issues:
                total_issues += len(issues)
                models_with_issues.append(table_name)
                if not category_printed:
                    print(f"\n📁 {category.upper()}")
                    category_printed = True
                print(f"  ⚠️  {model_class.__name__} → {table_name}")
                for issue in issues:
                    print(f"        - {issue}")
            elif not issues_only:
                if not category_printed:
                    print(f"\n📁 {category.upper()} ({len(models)} models)")
                    category_printed = True
                print(f"  ✅ {model_class.__name__} → {table_name} "
                      f"({len(orm_cols)} columns)")

    print()
    print("=" * 72)
    print("FINAL SUMMARY")
    print("=" * 72)
    print(f"Total ORM models      : {len(orm_models)}")
    print(f"Models with issues    : {len(models_with_issues)}")
    print(f"Total issues found    : {total_issues}")

    if models_with_issues:
        print("\nModels requiring attention:")
        for model in sorted(models_with_issues):
            print(f"  - {model}")
    else:
        print("\n✅ All models match database schema.")

    print("=" * 72)
    return total_issues


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> int:
    from sam.session import create_sam_engine
    try:
        engine, _ = create_sam_engine()
    except Exception as e:
        print(f"❌ Failed to create engine from environment: {e}", file=sys.stderr)
        return 1

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        print(f"❌ Connection failed: {e}", file=sys.stderr)
        return 1

    # Standalone runs default to the full dump for exploration.
    generate_report(engine, issues_only=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
