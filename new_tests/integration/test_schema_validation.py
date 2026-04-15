"""Schema validation tests — ORM ↔ MySQL schema alignment.

Ported verbatim from tests/integration/test_schema_validation.py as the
first integration port into new_tests/. These assertions are structural
(does this column type map cleanly, do PKs match, etc.) so they're
immune to data drift from snapshot refreshes — which makes them the
ideal first port.

No write operations. No mocking. Runs against the mysql-test container
via the session fixture in new_tests/conftest.py.
"""
import pytest
from sqlalchemy import text

from sam.base import Base


pytestmark = pytest.mark.integration


# ============================================================================
# Type Mapping — SQLAlchemy types to MySQL types
# ============================================================================

# Maps SQLAlchemy column types to acceptable MySQL types.
# Accounts for SQLAlchemy's automatic type mapping (e.g. Boolean → BIT(1)).
TYPE_MAPPINGS = {
    'Integer':    ['INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT'],
    'BigInteger': ['BIGINT'],
    'String':     ['VARCHAR', 'CHAR'],
    'Text':       ['TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'TINYTEXT'],
    'Float':      ['FLOAT', 'DOUBLE'],
    'Numeric':    ['DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE'],
    'Boolean':    ['BIT', 'TINYINT'],
    'DateTime':   ['DATETIME', 'TIMESTAMP'],
    'Date':       ['DATE'],
    'TIMESTAMP':  ['TIMESTAMP', 'DATETIME'],
}


# ============================================================================
# Helpers
# ============================================================================


def get_db_columns(session, table_name):
    """Return {column_name: {'type', 'nullable', 'key', 'extra'}}."""
    result = session.execute(text("""
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
    """), {'table_name': table_name})
    return {
        row[0]: {
            'type':     row[1],
            'nullable': row[2] == 'YES',
            'key':      row[3],
            'extra':    row[4],
        }
        for row in result
    }


def get_table_type(session, table_name):
    """Return 'BASE TABLE' | 'VIEW' | None."""
    result = session.execute(text("""
        SELECT TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
    """), {'table_name': table_name})
    row = result.fetchone()
    return row[0] if row else None


def normalize_type(db_type):
    """'VARCHAR(255)' → 'VARCHAR'. Strips size and sign."""
    base = db_type.split('(')[0].upper()
    base = base.replace(' UNSIGNED', '').replace(' SIGNED', '')
    return base.strip()


def get_orm_type_name(column):
    """Normalized SQLAlchemy type name for a column."""
    return type(column.type).__name__


def _is_acceptable_mismatch(mismatch_str):
    """Known-good MySQL ↔ SQLAlchemy type pairings we don't flag."""
    acceptable = [
        'ORM=String → DB=CHAR',
        'ORM=Integer → DB=TINYINT',
        'ORM=Integer → DB=SMALLINT',
        'ORM=Integer → DB=MEDIUMINT',
    ]
    return any(p in mismatch_str for p in acceptable)


# ============================================================================
# Schema alignment
# ============================================================================


class TestSchemaAlignment:
    """Every ORM model should align with its underlying MySQL table."""

    @pytest.fixture(scope='class')
    def all_mappers(self):
        return list(Base.registry.mappers)

    @pytest.fixture(scope='class')
    def table_models(self, all_mappers):
        """Only models backed by a real table (not views, not cross-bind)."""
        models = []
        for mapper in all_mappers:
            if mapper.persist_selectable.info.get('is_view', False):
                continue
            model_class = mapper.class_
            if hasattr(model_class, '__bind_key__') and model_class.__bind_key__ is not None:
                continue
            models.append(mapper)
        return models

    def test_all_tables_exist_in_database(self, session, table_models):
        missing = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            if get_table_type(session, name) is None:
                missing.append(name)
        assert not missing, (
            "ORM models reference tables that don't exist:\n"
            + "\n".join(f"  - {t}" for t in missing)
        )
        print(f"✅ All {len(table_models)} ORM tables exist in database")

    def test_all_orm_columns_exist_in_database(self, session, table_models):
        mismatches = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            orm_cols = {c.name for c in mapper.persist_selectable.columns}
            db_cols = set(get_db_columns(session, name).keys())
            missing = orm_cols - db_cols
            if missing:
                mismatches.append(f"{name}: ORM has {missing} but DB doesn't")
        assert not mismatches, (
            "ORM columns missing from database:\n"
            + "\n".join(f"  {m}" for m in mismatches)
        )
        print("✅ All ORM columns exist in database")

    def test_database_columns_in_orm(self, session, table_models):
        """Informational — not all DB columns need an ORM mapping."""
        warnings = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            orm_cols = {c.name for c in mapper.persist_selectable.columns}
            db_cols = set(get_db_columns(session, name).keys())
            extra = db_cols - orm_cols
            if extra:
                warnings.append(f"{name}: DB has {extra} but ORM doesn't")
        if warnings:
            print("\n⚠️  Database columns not in ORM (often intentional):")
            for w in warnings[:10]:
                print(f"  {w}")
            if len(warnings) > 10:
                print(f"  ... and {len(warnings) - 10} more")
        else:
            print("✅ All database columns are in ORM")

    def test_column_types_match(self, session, table_models):
        """Informational — surfaces type mismatches beyond known-good pairings."""
        mismatches = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            db_cols = get_db_columns(session, name)
            for col in mapper.persist_selectable.columns:
                if col.name not in db_cols:
                    continue
                orm_type = get_orm_type_name(col)
                db_type = db_cols[col.name]['type']
                db_norm = normalize_type(db_type)
                acceptable = TYPE_MAPPINGS.get(orm_type, [])
                if acceptable and db_norm not in acceptable:
                    if orm_type == 'String' and db_norm in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT'):
                        continue
                    mismatches.append(f"{name}.{col.name}: ORM={orm_type} → DB={db_type}")

        significant = [m for m in mismatches if not _is_acceptable_mismatch(m)]
        if significant:
            print("\n⚠️  Type mismatches found:")
            for m in significant[:20]:
                print(f"  {m}")
            if len(significant) > 20:
                print(f"  ... and {len(significant) - 20} more")
        else:
            print("✅ All column types match (within acceptable mappings)")

    def test_primary_keys_match(self, session, table_models):
        mismatches = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            orm_pks = {c.name for c in mapper.persist_selectable.primary_key.columns}
            db_cols = get_db_columns(session, name)
            db_pks = {col for col, info in db_cols.items() if 'PRI' in info['key']}
            if orm_pks != db_pks:
                mismatches.append(f"{name}: ORM PKs={orm_pks} vs DB PKs={db_pks}")
        assert not mismatches, (
            "Primary key mismatches:\n"
            + "\n".join(f"  {m}" for m in mismatches)
        )
        print("✅ All primary keys match")

    def test_foreign_keys_exist(self, session, table_models):
        """Informational — not all ORMs require DB-level FK constraints."""
        warnings = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            for fk in mapper.persist_selectable.foreign_keys:
                fk_col = fk.parent.name
                result = session.execute(text("""
                    SELECT CONSTRAINT_NAME
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                      AND COLUMN_NAME = :fk_column
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                """), {'table_name': name, 'fk_column': fk_col})
                if not result.fetchone():
                    warnings.append(f"{name}.{fk_col} (ORM FK, no DB constraint)")
        if warnings:
            print(f"\n⚠️  {len(warnings)} foreign keys in ORM but no DB constraint")
            for w in warnings[:10]:
                print(f"  {w}")
            if len(warnings) > 10:
                print(f"  ... and {len(warnings) - 10} more")
        else:
            print("✅ All foreign keys have database constraints")


# ============================================================================
# Model coverage
# ============================================================================


class TestModelCoverage:

    def test_all_tables_have_models(self, session):
        """Informational — catches tables we might want to model."""
        orm_tables = {m.persist_selectable.name for m in Base.registry.mappers}
        result = session.execute(text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_TYPE = 'BASE TABLE'
        """))
        db_tables = {row[0] for row in result}
        missing = db_tables - orm_tables
        known_skip = {
            'schema_version',
            'tables_dictionary',
            'EXPORT_TABLE',
            'TIME_DIM',
            'stage_hpc_job',
            'temp_joey_expired_project',
        }
        unexpected = missing - known_skip
        print(f"\n📊 DB coverage: {len(orm_tables)}/{len(db_tables)} "
              f"({len(orm_tables) * 100 // len(db_tables)}%)")
        if unexpected:
            print(f"⚠️  Unexpected missing models: {unexpected}")

    def test_all_models_have_tables(self, session):
        """Catches typos in __tablename__ and missing migrations."""
        missing = []
        for mapper in Base.registry.mappers:
            name = mapper.persist_selectable.name
            if mapper.persist_selectable.info.get('is_view', False):
                continue
            model_class = mapper.class_
            if hasattr(model_class, '__bind_key__') and model_class.__bind_key__ is not None:
                continue
            if get_table_type(session, name) is None:
                missing.append(f"{mapper.class_.__name__} → {name}")
        assert not missing, (
            "ORM models reference tables that don't exist:\n"
            + "\n".join(f"  {t}" for t in missing)
        )
        print("✅ All ORM models have corresponding database tables")


# ============================================================================
# Critical model smoke tests (fast feedback in CI)
# ============================================================================


class TestCriticalSchemas:

    @pytest.mark.parametrize("model_name,table_name,expected_pk", [
        ('User',        'users',         ['user_id']),
        ('Project',     'project',       ['project_id']),
        ('Account',     'account',       ['account_id']),
        ('Allocation',  'allocation',    ['allocation_id']),
        ('Resource',    'resources',     ['resource_id']),
        ('Factor',      'factor',        ['factor_id']),
        ('Formula',     'formula',       ['formula_id']),
        ('ProjectCode', 'project_code',  ['facility_id', 'mnemonic_code_id']),
    ])
    def test_critical_models_have_correct_primary_keys(
        self, session, model_name, table_name, expected_pk
    ):
        db_cols = get_db_columns(session, table_name)
        db_pks = sorted(col for col, info in db_cols.items() if 'PRI' in info['key'])
        assert db_pks == sorted(expected_pk), (
            f"{model_name} primary key mismatch: "
            f"expected {expected_pk}, got {db_pks}"
        )

    def test_no_duplicate_table_names(self):
        table_names = {}
        duplicates = []
        for mapper in Base.registry.mappers:
            name = mapper.persist_selectable.name
            if name in table_names:
                duplicates.append(f"{name}: {table_names[name]} and {mapper.class_.__name__}")
            else:
                table_names[name] = mapper.class_.__name__
        assert not duplicates, (
            "Multiple models map to same table:\n"
            + "\n".join(f"  {d}" for d in duplicates)
        )

    def test_xras_resource_repository_key_resource_schema(self, session):
        """Regression guard — this model was wrong once, with 5 columns instead of 2."""
        table_name = 'xras_resource_repository_key_resource'
        db_cols = get_db_columns(session, table_name)
        expected = {'resource_repository_key', 'resource_id'}
        actual = set(db_cols.keys())
        assert actual == expected, (
            f"XrasResourceRepositoryKeyResource schema mismatch!\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}"
        )
        assert 'PRI' in db_cols['resource_repository_key']['key']
