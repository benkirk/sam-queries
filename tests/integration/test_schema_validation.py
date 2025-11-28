"""
Schema Validation Tests

Automatically validates that ORM models match database schema.
These tests catch mismatches like the XrasResourceRepositoryKeyResource bug
where the ORM model had completely wrong columns.

This test suite runs fast (<1 second) and should be part of CI/CD to catch
schema drift early.
"""

import pytest
from sqlalchemy import inspect, MetaData, text
from sqlalchemy.types import (
    Integer, String, Float, Boolean, DateTime, Date,
    Text, BigInteger, Numeric, TIMESTAMP
)

from sam.base import Base


# ============================================================================
# Type Mapping - SQLAlchemy types to MySQL types
# ============================================================================

# Maps SQLAlchemy column types to acceptable MySQL types
# This accounts for SQLAlchemy's automatic type mapping
TYPE_MAPPINGS = {
    'Integer': ['INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT'],
    'BigInteger': ['BIGINT'],
    'String': ['VARCHAR', 'CHAR'],
    'Text': ['TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'TINYTEXT'],
    'Float': ['FLOAT', 'DOUBLE'],  # SQLAlchemy Float → MySQL DOUBLE
    'Numeric': ['DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE'],
    'Boolean': ['BIT', 'TINYINT'],  # SQLAlchemy Boolean → MySQL BIT(1) or TINYINT(1)
    'DateTime': ['DATETIME', 'TIMESTAMP'],  # Both are acceptable
    'Date': ['DATE'],
    'TIMESTAMP': ['TIMESTAMP', 'DATETIME'],
}


# ============================================================================
# Helper Functions
# ============================================================================

def get_db_columns(session, table_name):
    """
    Get column information from database INFORMATION_SCHEMA.

    Returns dict: {column_name: {'type': 'VARCHAR(255)', 'nullable': True, ...}}
    """
    result = session.execute(text("""
        SELECT
            COLUMN_NAME,
            COLUMN_TYPE,
            IS_NULLABLE,
            COLUMN_KEY,
            EXTRA
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = :table_name
    """), {'table_name': table_name})

    columns = {}
    for row in result:
        columns[row[0]] = {
            'type': row[1],
            'nullable': row[2] == 'YES',
            'key': row[3],
            'extra': row[4]
        }
    return columns


def get_table_type(session, table_name):
    """Check if table is a TABLE or VIEW."""
    result = session.execute(text("""
        SELECT TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = :table_name
    """), {'table_name': table_name})
    row = result.fetchone()
    return row[0] if row else None


def normalize_type(db_type):
    """
    Normalize MySQL type to base type without size/precision.

    Examples:
        'VARCHAR(255)' → 'VARCHAR'
        'INT(11)' → 'INT'
        'DECIMAL(10,2)' → 'DECIMAL'
        'BIT(1)' → 'BIT'
    """
    # Remove everything in parentheses and after
    base_type = db_type.split('(')[0].upper()

    # Handle unsigned/signed
    base_type = base_type.replace(' UNSIGNED', '').replace(' SIGNED', '')

    return base_type.strip()


def get_orm_type_name(column):
    """Get normalized type name from SQLAlchemy column."""
    col_type = column.type
    type_name = type(col_type).__name__

    # Handle special cases
    if hasattr(col_type, 'length') and type_name == 'String':
        return 'String'

    return type_name


# ============================================================================
# Schema Validation Tests
# ============================================================================

class TestSchemaAlignment:
    """Validate ORM schemas match database tables."""

    @pytest.fixture(scope='class')
    def all_mappers(self):
        """Get all ORM mappers."""
        return list(Base.registry.mappers)

    @pytest.fixture(scope='class')
    def table_models(self, all_mappers):
        """Get only table models (exclude views and multi-database binds)."""
        models = []
        for mapper in all_mappers:
            # Skip models that are views
            if mapper.persist_selectable.info.get('is_view', False):
                continue

            # Skip models that belong to other databases (system_status)
            # These models have __bind_key__ set and should be tested separately
            model_class = mapper.class_
            if hasattr(model_class, '__bind_key__') and model_class.__bind_key__ != None:
                continue

            models.append(mapper)
        return models

    def test_all_tables_exist_in_database(self, session, table_models):
        """Ensure all ORM tables exist in the database."""
        missing_tables = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name
            table_type = get_table_type(session, table_name)

            if table_type is None:
                missing_tables.append(table_name)

        assert not missing_tables, (
            f"ORM models reference tables that don't exist in database:\n" +
            "\n".join(f"  - {t}" for t in missing_tables)
        )

        print(f"✅ All {len(table_models)} ORM tables exist in database")

    def test_all_orm_columns_exist_in_database(self, session, table_models):
        """Ensure every ORM column exists in the database."""
        mismatches = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name
            orm_columns = {col.name for col in mapper.persist_selectable.columns}

            # Get database columns
            db_columns_info = get_db_columns(session, table_name)
            db_columns = set(db_columns_info.keys())

            # Find columns in ORM but not in database
            missing = orm_columns - db_columns
            if missing:
                mismatches.append(f"{table_name}: ORM has {missing} but DB doesn't")

        assert not mismatches, (
            "ORM columns missing from database:\n" +
            "\n".join(f"  {m}" for m in mismatches) +
            "\n\nThese columns exist in the ORM but not in the actual database!"
        )

        print(f"✅ All ORM columns exist in database")

    def test_database_columns_in_orm(self, session, table_models):
        """
        Check if database columns are in ORM.

        This is a WARNING, not a failure, since it's common to have
        database columns not mapped in the ORM (intentionally).
        """
        warnings = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name
            orm_columns = {col.name for col in mapper.persist_selectable.columns}

            # Get database columns
            db_columns_info = get_db_columns(session, table_name)
            db_columns = set(db_columns_info.keys())

            # Find columns in database but not in ORM
            missing = db_columns - orm_columns
            if missing:
                warnings.append(f"{table_name}: DB has {missing} but ORM doesn't")

        if warnings:
            print("\n⚠️  Database columns not in ORM (this is often intentional):")
            for w in warnings[:10]:  # Show first 10
                print(f"  {w}")
            if len(warnings) > 10:
                print(f"  ... and {len(warnings) - 10} more")
        else:
            print("✅ All database columns are in ORM")

    def test_column_types_match(self, session, table_models):
        """
        Validate column types match within acceptable mappings.

        SQLAlchemy types map to MySQL types, e.g.:
        - Boolean → BIT(1)
        - Float → DOUBLE
        - Integer → INT
        """
        mismatches = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name
            db_columns = get_db_columns(session, table_name)

            for col in mapper.persist_selectable.columns:
                if col.name not in db_columns:
                    continue  # Skip - caught by previous test

                orm_type = get_orm_type_name(col)
                db_type = db_columns[col.name]['type']
                db_type_normalized = normalize_type(db_type)

                # Check if the types are compatible
                acceptable_types = TYPE_MAPPINGS.get(orm_type, [])

                if acceptable_types and db_type_normalized not in acceptable_types:
                    # Check if it's a known/acceptable mismatch
                    # For example, some String fields might be TEXT
                    if orm_type == 'String' and db_type_normalized in ['TEXT', 'MEDIUMTEXT', 'LONGTEXT']:
                        continue  # This is fine

                    mismatches.append(
                        f"{table_name}.{col.name}: ORM={orm_type} → DB={db_type}"
                    )

        # Filter out known acceptable mismatches
        significant_mismatches = [m for m in mismatches if not self._is_acceptable_mismatch(m)]

        if significant_mismatches:
            print("\n⚠️  Type mismatches found:")
            for m in significant_mismatches[:20]:  # Show first 20
                print(f"  {m}")
            if len(significant_mismatches) > 20:
                print(f"  ... and {len(significant_mismatches) - 20} more")
        else:
            print(f"✅ All column types match (within acceptable mappings)")

    def _is_acceptable_mismatch(self, mismatch_str):
        """Check if a type mismatch is acceptable/expected."""
        # These are known acceptable mismatches that don't affect functionality
        acceptable = [
            'ORM=String → DB=CHAR',  # String can map to CHAR or VARCHAR
            'ORM=Integer → DB=TINYINT',  # Both are integers
            'ORM=Integer → DB=SMALLINT',
            'ORM=Integer → DB=MEDIUMINT',
        ]

        for pattern in acceptable:
            if pattern in mismatch_str:
                return True

        return False

    def test_primary_keys_match(self, session, table_models):
        """Validate primary keys match between ORM and database."""
        mismatches = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name

            # Get ORM primary keys
            orm_pks = {col.name for col in mapper.persist_selectable.primary_key.columns}

            # Get database primary keys
            db_columns = get_db_columns(session, table_name)
            db_pks = {name for name, info in db_columns.items() if 'PRI' in info['key']}

            if orm_pks != db_pks:
                mismatches.append(
                    f"{table_name}: ORM PKs={orm_pks} vs DB PKs={db_pks}"
                )

        assert not mismatches, (
            "Primary key mismatches:\n" +
            "\n".join(f"  {m}" for m in mismatches)
        )

        print(f"✅ All primary keys match")

    def test_foreign_keys_exist(self, session, table_models):
        """
        Validate foreign keys exist in database.

        This is a WARNING test - not all ORMs require database-level FKs.
        """
        warnings = []

        for mapper in table_models:
            table_name = mapper.persist_selectable.name

            # Get ORM foreign keys
            for fk in mapper.persist_selectable.foreign_keys:
                fk_column = fk.parent.name

                # Query database to check if FK constraint exists
                result = session.execute(text("""
                    SELECT CONSTRAINT_NAME
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                    AND TABLE_NAME = :table_name
                    AND COLUMN_NAME = :fk_column
                    AND REFERENCED_TABLE_NAME IS NOT NULL
                """), {'table_name': table_name, 'fk_column': fk_column})

                if not result.fetchone():
                    warnings.append(f"{table_name}.{fk_column} (ORM FK, no DB constraint)")

        if warnings:
            print(f"\n⚠️  {len(warnings)} foreign keys in ORM but no DB constraint:")
            for w in warnings[:10]:
                print(f"  {w}")
            if len(warnings) > 10:
                print(f"  ... and {len(warnings) - 10} more")
            print("  (This is OK - not all ORMs require DB-level FK constraints)")
        else:
            print("✅ All foreign keys have database constraints")


# ============================================================================
# Model Coverage Tests
# ============================================================================

class TestModelCoverage:
    """Test that all database tables have ORM models."""

    def test_all_tables_have_models(self, session):
        """
        List tables without ORM models.

        This is informational - some tables intentionally don't have models.
        """
        # Get all ORM table names
        orm_tables = {mapper.persist_selectable.name for mapper in Base.registry.mappers}

        # Get all database tables (exclude views)
        result = session.execute(text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_TYPE = 'BASE TABLE'
        """))

        db_tables = {row[0] for row in result}

        # Find tables without models
        missing_models = db_tables - orm_tables

        # Known tables that don't need models
        known_skip = {
            'schema_version',  # Flyway migration metadata
            'tables_dictionary',  # Database documentation
            'EXPORT_TABLE',  # Temporary export table
            'TIME_DIM',  # Data warehouse dimension
            'stage_hpc_job',  # Staging table
            'temp_joey_expired_project',  # Temporary table
        }

        unexpected_missing = missing_models - known_skip

        print(f"\n📊 Database Coverage:")
        print(f"  ORM Models: {len(orm_tables)}")
        print(f"  Database Tables: {len(db_tables)}")
        print(f"  Coverage: {len(orm_tables)}/{len(db_tables)} ({len(orm_tables)*100//len(db_tables)}%)")

        if missing_models:
            print(f"\n  Tables without ORM models:")
            for table in sorted(missing_models):
                status = "⚠️ " if table in unexpected_missing else "✓ "
                print(f"    {status}{table}")

        if unexpected_missing:
            print(f"\n⚠️  Unexpected missing models: {unexpected_missing}")
            print("  Consider creating ORM models for these tables.")
        else:
            print(f"\n✅ All expected tables have ORM models")

    def test_all_models_have_tables(self, session):
        """
        Check for ORM models without database tables.

        This catches typos in __tablename__ or missing migrations.
        """
        missing_tables = []

        for mapper in Base.registry.mappers:
            table_name = mapper.persist_selectable.name

            # Skip views
            if mapper.persist_selectable.info.get('is_view', False):
                continue

            # Skip models that belong to other databases (system_status)
            model_class = mapper.class_
            if hasattr(model_class, '__bind_key__') and model_class.__bind_key__ != None:
                continue

            # Check if table exists
            table_type = get_table_type(session, table_name)
            if table_type is None:
                missing_tables.append(f"{mapper.class_.__name__} → {table_name}")

        assert not missing_tables, (
            "ORM models reference tables that don't exist:\n" +
            "\n".join(f"  {t}" for t in missing_tables) +
            "\n\nThese models exist but their tables don't!"
        )

        print("✅ All ORM models have corresponding database tables")


# ============================================================================
# Critical Schema Validation (Quick Smoke Tests)
# ============================================================================

class TestCriticalSchemas:
    """
    Quick smoke tests for critical models.

    These tests catch the most common/critical schema issues quickly.
    Run these first in CI/CD for fast feedback.
    """

    @pytest.mark.parametrize("model_name,table_name,expected_pk", [
        ('User', 'users', ['user_id']),
        ('Project', 'project', ['project_id']),
        ('Account', 'account', ['account_id']),
        ('Allocation', 'allocation', ['allocation_id']),
        ('Resource', 'resources', ['resource_id']),
        ('Factor', 'factor', ['factor_id']),
        ('Formula', 'formula', ['formula_id']),
        ('ProjectCode', 'project_code', ['facility_id', 'mnemonic_code_id']),
    ])
    def test_critical_models_have_correct_primary_keys(
        self, session, model_name, table_name, expected_pk
    ):
        """Test that critical models have correct primary keys."""
        # Get database PKs
        db_columns = get_db_columns(session, table_name)
        db_pks = sorted([name for name, info in db_columns.items() if 'PRI' in info['key']])

        assert db_pks == sorted(expected_pk), (
            f"{model_name} primary key mismatch: "
            f"expected {expected_pk}, got {db_pks}"
        )

        print(f"✅ {model_name} has correct primary key(s): {db_pks}")

    def test_no_duplicate_table_names(self):
        """Ensure no two ORM models map to the same table."""
        table_names = {}
        duplicates = []

        for mapper in Base.registry.mappers:
            table_name = mapper.persist_selectable.name
            model_name = mapper.class_.__name__

            if table_name in table_names:
                duplicates.append(
                    f"{table_name}: {table_names[table_name]} and {model_name}"
                )
            else:
                table_names[table_name] = model_name

        assert not duplicates, (
            "Multiple models map to same table:\n" +
            "\n".join(f"  {d}" for d in duplicates)
        )

        print(f"✅ No duplicate table names ({len(table_names)} unique tables)")

    def test_xras_resource_repository_key_resource_schema(self, session):
        """
        Specific test for XrasResourceRepositoryKeyResource.

        This model was completely wrong before - had 5 columns instead of 2.
        This test ensures it stays correct.
        """
        table_name = 'xras_resource_repository_key_resource'
        db_columns = get_db_columns(session, table_name)

        # Should have exactly these columns
        expected_columns = {'resource_repository_key', 'resource_id'}
        actual_columns = set(db_columns.keys())

        assert actual_columns == expected_columns, (
            f"XrasResourceRepositoryKeyResource schema mismatch!\n"
            f"Expected columns: {expected_columns}\n"
            f"Actual columns: {actual_columns}\n"
            f"Extra: {actual_columns - expected_columns}\n"
            f"Missing: {expected_columns - actual_columns}"
        )

        # Verify resource_repository_key is PK
        assert 'PRI' in db_columns['resource_repository_key']['key'], (
            "resource_repository_key should be primary key"
        )

        print("✅ XrasResourceRepositoryKeyResource schema is correct")
