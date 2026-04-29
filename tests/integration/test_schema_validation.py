"""Schema validation tests — ORM ↔ MySQL schema alignment.

Ported verbatim from tests/integration/test_schema_validation.py as the
first integration port into new_tests/. These assertions are structural
(does this column type map cleanly, do PKs match, etc.) so they're
immune to data drift from snapshot refreshes — which makes them the
ideal first port.

No write operations. No mocking. Runs against the mysql-test container
via the session fixture in new_tests/conftest.py.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from sam.base import Base

# Pull in the shared introspection helpers used by scripts/check_db_drift.py
# so the CI guard and the prod audit script enforce the *same* drift rules.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scripts.lib.schema_introspection import (
    diff_indexes,
    get_db_foreign_keys,
    get_db_indexes,
    get_orm_indexes,
    iter_table_mappers,
)


pytestmark = pytest.mark.integration


# ============================================================================
# Index drift guard — prevents the DiskActivity bug class from regressing
# ============================================================================
#
# If the test container has DBA-added indexes the ORM intentionally doesn't
# track (e.g. analytics-only indexes), add their (table, index_name) pair
# here with a comment explaining why.
IGNORED_DB_INDEXES = {
    # ('table_name', 'index_name'),
}


# ============================================================================
# Foreign-key drift allowlists
# ============================================================================
#
# Promote-aware allowlists for test_foreign_keys_exist /
# test_foreign_key_actions_match. Mirrors scripts/check_db_drift.py's
# IGNORED_FK_DRIFT — keep the two in sync when adding entries.

# ORM-declared FKs that intentionally have no DB-level constraint.
# Keyed by (table_name, frozenset(columns)) → reason.
IGNORED_ORM_FK_DRIFT = {
    # Self-FK drives Organization.parent / .children nested-set relationship.
    # Prod doesn't enforce the FK at the DB level (cycles in the tree during
    # bulk reorgs), but the ORM declaration is load-bearing for SQLAlchemy
    # joins (subqueryload(Organization.children) etc).
    ('organization', frozenset({'parent_org_id'})):
        'self-FK drives Organization nested-set relationship',
    # dav_activity has composite PK (dav_activity_id, queue_name); MySQL
    # rejects a single-column FK to one half of a composite key, so the
    # DB-level constraint can't exist. The ORM-side FK still lets
    # SQLAlchemy resolve DavCharge.dav_activity for object-graph loads.
    ('dav_charge', frozenset({'dav_activity_id'})):
        'composite PK on dav_activity prevents single-column DB FK',
}

# DB-side FKs whose ON DELETE / ON UPDATE rules are non-default.
# Keyed by (table_name, constraint_name) → (delete_rule, update_rule, reason).
# Seed: every FK in the test container whose action pair isn't ('NO ACTION',
# 'NO ACTION'). Default MySQL behaviour ('RESTRICT' / 'NO ACTION') is treated
# as the implicit baseline and not allowlisted.
#
# When an ORM model eventually adds matching ondelete=/onupdate= to its
# ForeignKey(...) declaration, drop the corresponding entry here and the
# test will start asserting the ORM declaration matches the DB instead.
IGNORED_FK_ACTION_DRIFT = {
    ('access_branch_resource', 'access_branch_resource_access_branch_fk'):
        ('CASCADE', 'CASCADE', 'access-branch sweep cascades to membership rows'),
    ('access_branch_resource', 'access_branch_resource_resources_fk'):
        ('CASCADE', 'CASCADE', 'access-branch sweep cascades to membership rows'),
    ('adhoc_group_tag', 'adhoc_group_tag_id_fk'):
        ('CASCADE', 'CASCADE', 'tag rows are owned by adhoc_group; delete-with-parent'),
    ('adhoc_system_account_entry', 'adhoc_system_account_entry_group_id_fk'):
        ('CASCADE', 'CASCADE', 'entry rows are owned by adhoc_group; delete-with-parent'),
    ('archive_charge_summary', 'fk_archive_charge_summary_date'):
        ('CASCADE', 'NO ACTION', 'TIME_DIM rebuild cascades summary cleanup'),
    ('dav_charge_summary', 'fk_dav_charge_summary_date'):
        ('CASCADE', 'NO ACTION', 'TIME_DIM rebuild cascades summary cleanup'),
    ('disk_charge_summary', 'fk_disk_charge_summary_date'):
        ('CASCADE', 'NO ACTION', 'TIME_DIM rebuild cascades summary cleanup'),
    ('hpc_charge_summary', 'fk_hpc_charge_summary_date'):
        ('CASCADE', 'NO ACTION', 'TIME_DIM rebuild cascades summary cleanup'),
}

# MySQL reports unset action rules as 'NO ACTION'; the SQLAlchemy default
# (no ondelete=/onupdate= argument) is also conceptually 'NO ACTION'.
# 'RESTRICT' is a synonym (per MySQL docs §13.1.20.5). Collapse all three
# into a single canonical sentinel for comparison.
_DEFAULT_FK_ACTIONS = {None, 'NO ACTION', 'RESTRICT'}


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
        """Every ORM-declared FK must have a matching DB-level constraint.

        Promoted from informational to assertive after the test container
        gained prod-faithful FK constraints (containers/sam-sql-dev/
        bootstrap_clone.py reapply step). Legitimate ORM-only FKs go in
        IGNORED_ORM_FK_DRIFT at module scope with a one-line reason.
        """
        problems = []
        for mapper in table_models:
            name = mapper.persist_selectable.name
            db_fks = get_db_foreign_keys(session, name)
            db_fk_cols = set()
            for info in db_fks.values():
                db_fk_cols.update(info['columns'])
            orm_fk_cols = {fk.parent.name for fk in mapper.persist_selectable.foreign_keys}
            missing = orm_fk_cols - db_fk_cols
            if not missing:
                continue
            if (name, frozenset(missing)) in IGNORED_ORM_FK_DRIFT:
                continue
            problems.append(
                f"{name}: ORM declares FK on column(s) {sorted(missing)} "
                f"but DB has no constraint"
            )

        assert not problems, (
            "Foreign-key drift between ORM and DB:\n"
            + "\n".join(f"  {p}" for p in problems)
            + "\n\nFix: add the FK to the DB schema (and to "
              "containers/sam-sql-dev/bootstrap_clone.py's reapply set), "
              "or add (table, frozenset({...})) to IGNORED_ORM_FK_DRIFT "
              "with a comment."
        )

    def test_foreign_key_actions_match(self, session, table_models):
        """Every DB-level FK action pair must be the default or allowlisted.

        Catches surprise CASCADE / SET NULL drift in the prod-faithful
        FK set. Each non-default rule pair must appear in
        IGNORED_FK_ACTION_DRIFT keyed by (table, constraint_name) with
        the expected (delete_rule, update_rule, reason).

        ORM models currently don't declare ondelete=/onupdate= on any
        ForeignKey, so this is a one-direction guard: DB → allowlist.
        Once a model adds matching ondelete=/onupdate=, drop its
        allowlist entry and this test will start verifying the ORM
        declaration tracks the DB.
        """
        problems = []
        for mapper in table_models:
            table = mapper.persist_selectable.name
            db_fks = get_db_foreign_keys(session, table)
            for cname, info in db_fks.items():
                db_del = info['on_delete']
                db_upd = info['on_update']
                is_default = (
                    db_del in _DEFAULT_FK_ACTIONS
                    and db_upd in _DEFAULT_FK_ACTIONS
                )
                if is_default:
                    continue

                allow = IGNORED_FK_ACTION_DRIFT.get((table, cname))
                if allow is None:
                    problems.append(
                        f"{table}.{cname}: DB has non-default actions "
                        f"(ON DELETE {db_del}, ON UPDATE {db_upd}) "
                        f"but no IGNORED_FK_ACTION_DRIFT entry"
                    )
                    continue

                allow_del, allow_upd, _reason = allow
                if (allow_del, allow_upd) != (db_del, db_upd):
                    problems.append(
                        f"{table}.{cname}: allowlisted as "
                        f"({allow_del}, {allow_upd}) but DB now reports "
                        f"({db_del}, {db_upd})"
                    )

        assert not problems, (
            "Foreign-key action drift between DB and allowlist:\n"
            + "\n".join(f"  {p}" for p in problems)
            + "\n\nFix: update IGNORED_FK_ACTION_DRIFT to match the "
              "intended action pair, or align the DB schema."
        )


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


# ============================================================================
# Index alignment — prevents PR #209-style drift (DiskActivity unique index)
# ============================================================================


class TestIndexAlignment:
    """Every non-PRIMARY index in the DB should be declared in the ORM, and
    vice versa. Catches the DiskActivity bug class: a UNIQUE index that exists
    in production but was missing from __table_args__."""

    def test_unique_constraints_match(self, session):
        """Every UNIQUE index in DB must have a matching ORM declaration.

        Direct regression guard against PR #209 (DiskActivity was missing
        `disk_activity_unique_idx`). Promoted to assertive after the
        domain-batched drift cleanup eliminated all known UNIQUE drift.
        Add (table, index_name) entries to IGNORED_DB_INDEXES at the top
        of this file for any DBA-added UNIQUE the ORM intentionally won't
        track.
        """
        problems = []
        for mapper in iter_table_mappers(Base.registry):
            table = mapper.persist_selectable.name
            db_idx = get_db_indexes(session, table)
            orm_idx = get_orm_indexes(mapper)
            diff = diff_indexes(db_idx, orm_idx)

            for name, cols, uniq in diff['in_db_not_orm']:
                if not uniq:
                    continue
                if (table, name) in IGNORED_DB_INDEXES:
                    continue
                problems.append(
                    f"{table}: DB has UNIQUE INDEX '{name}' on ({', '.join(cols)}) — "
                    f"ORM does not declare it"
                )

        assert not problems, (
            "Unique constraint drift between DB and ORM:\n"
            + "\n".join(f"  {p}" for p in problems)
            + "\n\nFix: declare the missing Index(..., unique=True) or "
              "UniqueConstraint in the model's __table_args__."
        )

    def test_indexes_match(self, session):
        """All non-PRIMARY indexes should align between DB and ORM (informational).

        Reports both directions plus same-name/different-shape mismatches.
        Currently informational — promote to assertion once known drifts
        are triaged. Until then, the strict guard above catches the most
        common bug class (missing UNIQUE).
        """
        in_db_not_orm = []
        in_orm_not_db = []
        mismatched = []

        for mapper in iter_table_mappers(Base.registry):
            table = mapper.persist_selectable.name
            db_idx = get_db_indexes(session, table)
            orm_idx = get_orm_indexes(mapper)
            diff = diff_indexes(db_idx, orm_idx)

            for name, cols, uniq in diff['in_db_not_orm']:
                if (table, name) in IGNORED_DB_INDEXES:
                    continue
                kind = 'UNIQUE' if uniq else 'index'
                in_db_not_orm.append(f"{table}.{name} ({kind}, cols={cols})")
            for name, cols, uniq in diff['in_orm_not_db']:
                kind = 'UNIQUE' if uniq else 'index'
                in_orm_not_db.append(f"{table}.{name} ({kind}, cols={cols})")
            for name, db_shape, orm_shape in diff['mismatched']:
                mismatched.append(f"{table}.{name}: DB={db_shape} vs ORM={orm_shape}")

        if in_db_not_orm:
            print(f"\n⚠️  {len(in_db_not_orm)} indexes in DB not declared in ORM:")
            for s in in_db_not_orm[:20]:
                print(f"  {s}")
            if len(in_db_not_orm) > 20:
                print(f"  ... and {len(in_db_not_orm) - 20} more")
        if in_orm_not_db:
            print(f"\n⚠️  {len(in_orm_not_db)} indexes in ORM not present in DB:")
            for s in in_orm_not_db[:20]:
                print(f"  {s}")
        if mismatched:
            print(f"\n⚠️  {len(mismatched)} same-name indexes with different shape:")
            for s in mismatched[:20]:
                print(f"  {s}")
        if not (in_db_not_orm or in_orm_not_db or mismatched):
            print("✅ All indexes align between DB and ORM")
