#!/usr/bin/env python3
"""
ORM Inventory Script

Analyzes all SQLAlchemy ORM models and compares them against the database schema.
Generates a comprehensive report showing:
- All ORM models and their table mappings
- Tables without ORM models
- Schema mismatches (columns, types)
- Relationship mappings
"""

import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from sqlalchemy import inspect, text
from sqlalchemy.orm import class_mapper
from sam.session import create_sam_engine
import sam


def get_all_orm_models():
    """Find all ORM model classes."""
    from sam.base import Base

    models = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table_name = mapper.mapped_table.name
        models[table_name] = cls

    return models


def get_database_tables(engine):
    """Get all tables and views from the database."""
    inspector = inspect(engine)

    tables = set(inspector.get_table_names())
    views = set(inspector.get_view_names())

    return tables, views


def get_table_columns(engine, table_name: str) -> Dict:
    """Get column information for a table."""
    inspector = inspect(engine)
    columns = {}

    for col in inspector.get_columns(table_name):
        columns[col['name']] = {
            'type': str(col['type']),
            'nullable': col['nullable'],
            'default': col.get('default'),
            'autoincrement': col.get('autoincrement', False)
        }

    return columns


def get_orm_columns(model_class) -> Dict:
    """Get column information from ORM model."""
    mapper = class_mapper(model_class)
    columns = {}

    for col in mapper.columns:
        columns[col.name] = {
            'type': str(col.type),
            'nullable': col.nullable,
            'primary_key': col.primary_key,
            'foreign_keys': len(col.foreign_keys) > 0
        }

    return columns


def get_orm_relationships(model_class) -> Dict:
    """Get relationship information from ORM model."""
    mapper = class_mapper(model_class)
    relationships = {}

    for rel in mapper.relationships:
        relationships[rel.key] = {
            'target': rel.mapper.class_.__name__,
            'direction': rel.direction.name,
            'uselist': rel.uselist
        }

    return relationships


def compare_columns(db_cols: Dict, orm_cols: Dict) -> List[str]:
    """Compare database columns with ORM columns."""
    issues = []

    # Check for missing columns in ORM
    db_col_names = set(db_cols.keys())
    orm_col_names = set(orm_cols.keys())

    missing_in_orm = db_col_names - orm_col_names
    if missing_in_orm:
        issues.append(f"Missing in ORM: {', '.join(sorted(missing_in_orm))}")

    extra_in_orm = orm_col_names - db_col_names
    if extra_in_orm:
        issues.append(f"Extra in ORM: {', '.join(sorted(extra_in_orm))}")

    # Check matching columns for type differences
    for col_name in db_col_names & orm_col_names:
        db_type = db_cols[col_name]['type'].upper()
        orm_type = orm_cols[col_name]['type'].upper()

        # Normalize type names for comparison
        if 'VARCHAR' in db_type and 'VARCHAR' in orm_type:
            continue
        if 'INT' in db_type and 'INT' in orm_type:
            continue
        if 'DECIMAL' in db_type and 'NUMERIC' in orm_type:
            continue
        if 'DATETIME' in db_type and 'DATETIME' in orm_type:
            continue
        if 'TIMESTAMP' in db_type and 'TIMESTAMP' in orm_type:
            continue
        if 'TEXT' in db_type and 'TEXT' in orm_type:
            continue
        if 'TINYINT' in db_type and ('BOOLEAN' in orm_type or 'TINYINT' in orm_type):
            continue
        if 'BIGINT' in db_type and 'BIGINT' in orm_type:
            continue

        # Flag significant differences
        if db_type != orm_type:
            issues.append(f"Type mismatch '{col_name}': DB={db_type}, ORM={orm_type}")

    return issues


def generate_report(engine):
    """Generate comprehensive ORM inventory report."""

    print("=" * 80)
    print("SAM ORM INVENTORY REPORT")
    print("=" * 80)
    print()

    # Get all data
    orm_models = get_all_orm_models()
    db_tables, db_views = get_database_tables(engine)

    print(f"📊 SUMMARY")
    print(f"  ORM Models: {len(orm_models)}")
    print(f"  Database Tables: {len(db_tables)}")
    print(f"  Database Views: {len(db_views)}")
    print()

    # Check coverage
    all_db_objects = db_tables | db_views
    orm_table_names = set(orm_models.keys())

    tables_without_orms = (db_tables - orm_table_names) - {'EXPORT_TABLE', 'TIME_DIM'}  # Known system tables
    views_without_orms = db_views - orm_table_names

    print(f"✅ COVERAGE")
    print(f"  Tables with ORMs: {len(db_tables & orm_table_names)}/{len(db_tables)}")
    print(f"  Views with ORMs: {len(db_views & orm_table_names)}/{len(db_views)}")
    print()

    if tables_without_orms:
        print(f"⚠️  TABLES WITHOUT ORM MODELS ({len(tables_without_orms)}):")
        for table in sorted(tables_without_orms):
            print(f"    - {table}")
        print()

    if views_without_orms:
        print(f"ℹ️  VIEWS WITHOUT ORM MODELS ({len(views_without_orms)}):")
        for view in sorted(views_without_orms):
            print(f"    - {view}")
        print()

    # Detailed model analysis
    print("=" * 80)
    print("DETAILED MODEL ANALYSIS")
    print("=" * 80)
    print()

    models_by_category = defaultdict(list)

    for table_name, model_class in sorted(orm_models.items()):
        module = model_class.__module__
        category = module.split('.')[-1] if '.' in module else 'other'
        models_by_category[category].append((table_name, model_class))

    total_issues = 0
    models_with_issues = []

    for category in sorted(models_by_category.keys()):
        models = models_by_category[category]
        print(f"📁 {category.upper()} ({len(models)} models)")
        print("-" * 80)

        for table_name, model_class in sorted(models):
            is_view = table_name in db_views
            table_type = "VIEW" if is_view else "TABLE"

            print(f"\n  {model_class.__name__} → {table_name} ({table_type})")

            # Get columns
            if not is_view:  # Skip column comparison for views
                try:
                    db_cols = get_table_columns(engine, table_name)
                    orm_cols = get_orm_columns(model_class)

                    issues = compare_columns(db_cols, orm_cols)

                    if issues:
                        total_issues += len(issues)
                        models_with_issues.append(table_name)
                        print(f"    ⚠️  Issues found:")
                        for issue in issues:
                            print(f"        - {issue}")
                    else:
                        print(f"    ✅ Schema matches ({len(orm_cols)} columns)")

                    # Show relationships
                    relationships = get_orm_relationships(model_class)
                    if relationships:
                        print(f"    🔗 Relationships: {len(relationships)}")
                        for rel_name, rel_info in sorted(relationships.items()):
                            direction = rel_info['direction']
                            target = rel_info['target']
                            many = "List" if rel_info['uselist'] else "Single"
                            print(f"        - {rel_name} → {target} ({direction}, {many})")

                except Exception as e:
                    print(f"    ❌ Error analyzing: {e}")
                    total_issues += 1
                    models_with_issues.append(table_name)
            else:
                # For views, just show basic info
                orm_cols = get_orm_columns(model_class)
                print(f"    ℹ️  View with {len(orm_cols)} columns")
                relationships = get_orm_relationships(model_class)
                if relationships:
                    print(f"    🔗 Relationships: {len(relationships)}")

        print()

    # Summary
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print()
    print(f"Total ORM Models: {len(orm_models)}")
    print(f"Models with Issues: {len(models_with_issues)}")
    print(f"Total Issues Found: {total_issues}")
    print()

    if models_with_issues:
        print("Models requiring attention:")
        for model in sorted(models_with_issues):
            print(f"  - {model}")
    else:
        print("✅ All models match database schema!")

    print()
    print("=" * 80)


def test_connection(engine):
    """Test basic database connectivity."""
    print("Testing database connection...")

    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) as user_count FROM users"))
            user_count = result.fetchone()[0]
            print(f"✅ Connection successful! Found {user_count} users in database.")
            return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False


def main():
    """Main entry point."""
    print("Connecting to local MySQL database...")
    print()

    # Create connection for local MySQL
    connection_string = 'mysql+pymysql://root:root@127.0.0.1:3306/sam'

    try:
        engine, SessionLocal = create_sam_engine(connection_string)

        if not test_connection(engine):
            return 1

        print()
        generate_report(engine)

        return 0

    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
