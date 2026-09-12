"""Pure-function gates for containers/sam-sql-dev/load_postgres.py: no MySQL, no Postgres.

The loader is a standalone script, so it is imported by path (as test_bootstrap_clone does).
"""
import datetime
import decimal
import importlib.util
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import Boolean, ForeignKeyConstraint

pytestmark = pytest.mark.unit

CLONE_DIR = Path(__file__).resolve().parents[2] / 'containers' / 'sam-sql-dev'


@pytest.fixture(scope='module')
def loader():
    sys.path.insert(0, str(CLONE_DIR))  # `from check_username_leak import ...`
    try:
        spec = importlib.util.spec_from_file_location('load_postgres', CLONE_DIR / 'load_postgres.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(CLONE_DIR))


class TestTarget:
    ENV = {'SAM_DEV_PG_USER': 'u', 'SAM_DEV_PG_PASSWORD': 'pw'}

    def test_defaults_point_at_cnpg_with_ssl(self, loader):
        t = loader.target_from(self.ENV)
        assert (t['host'], t['port'], t['user'], t['dbname'], t['sslmode']) == \
            ('csg-postgres.k8s.ucar.edu', 5432, 'u', 'sam_dev', 'require')

    def test_env_then_cli_override(self, loader):
        env = {**self.ENV, 'SAM_DEV_PG_HOST': '127.0.0.1', 'SAM_DEV_PG_PORT': '5433',
               'SAM_DEV_PG_REQUIRE_SSL': 'false'}
        t = loader.target_from(env)
        assert (t['host'], t['port'], t['sslmode']) == ('127.0.0.1', 5433, 'prefer')
        args = loader.parse_args(['--pg-host', 'h2', '--pg-db', 'other'])
        t = loader.target_from(env, args)
        assert (t['host'], t['dbname']) == ('h2', 'other')

    def test_missing_user_or_password_is_refused(self, loader):
        with pytest.raises(SystemExit):
            loader.target_from({'SAM_DEV_PG_PASSWORD': 'pw'})
        with pytest.raises(SystemExit):
            loader.target_from({'SAM_DEV_PG_USER': 'u'})

    def test_source_url_and_config(self, loader):
        s = loader.source_from({}, 'mysql+pymysql://u:p@db.local:3307/sam')
        assert s == {'host': 'db.local', 'port': 3307, 'user': 'u', 'password': 'p', 'database': 'sam'}
        s = loader.source_from({'local': {'host': 'localhost', 'user': 'root', 'password': 'root',
                                          'database': 'sam'}})
        assert (s['host'], s['port'], s['database']) == ('localhost', 3306, 'sam')


class TestSchema:
    def test_every_base_table_survives_without_fks(self, loader):
        md, fks = loader.fk_free_metadata()
        from sam.base import Base
        base_tables = [t for t in Base.metadata.tables.values() if not t.info.get('is_view')]
        assert len(md.tables) == len(base_tables)
        assert not any(isinstance(c, ForeignKeyConstraint) for t in md.tables.values() for c in t.constraints)
        assert sum(len(t.indexes) for t in md.tables.values()) == sum(len(t.indexes) for t in base_tables)
        assert len(fks) == sum(1 for t in base_tables for c in t.constraints if isinstance(c, ForeignKeyConstraint))
        assert len(md.sorted_tables) == len(md.tables)

    def test_fk_tuples_name_child_and_parent_columns(self, loader):
        _, fks = loader.fk_free_metadata()
        assert ('account', ('project_id',), 'project', ('project_id',)) in fks

    def test_the_orm_ddl_compiles_for_postgres_with_boolean_defaults(self, loader):
        """server_default=text('0') on a Boolean is a MySQL-ism Postgres rejects (POSTGRES_MIGRATION.md #10)."""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable
        md, _ = loader.fk_free_metadata()
        for t in md.tables.values():
            ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
            for col in t.columns:
                if isinstance(col.type, Boolean) and col.server_default is not None:
                    assert re.search(rf'\b{col.name} BOOLEAN DEFAULT (true|false)\b', ddl), f'{t.name}.{col.name}'

    def test_no_column_becomes_timestamptz_on_postgres(self, loader):
        """TIMESTAMP(3) is timezone=3 to the generic type (POSTGRES_MIGRATION.md #3)."""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable
        md, _ = loader.fk_free_metadata()
        for t in md.tables.values():
            ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
            assert 'WITH TIME ZONE' not in ddl, t.name

    def test_index_names_are_unique_across_the_schema(self, loader):
        """MySQL allows the same index name on several tables; Postgres does not (POSTGRES_MIGRATION.md #11)."""
        md, _ = loader.fk_free_metadata()
        names = [ix.name for t in md.tables.values() for ix in t.indexes]
        assert len(names) == len(set(names))
        assert all(len(n) <= 63 for n in names)
        assert 'allocation_allocation_account_fk' in names  # the shared one that surfaced first

    def test_string_columns_widen_to_the_source_length(self, loader):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        md = MetaData()
        Table('users', md, Column('user_id', Integer, primary_key=True), Column('title', String(15)),
              Column('username', String(35)), Column('notes', String(10)))
        drifts = loader.widen_strings(md, {('users', 'title'): 40, ('users', 'username'): 35,
                                           ('users', 'notes'): 5})
        assert drifts == [('users', 'title', 15, 40)]
        assert md.tables['users'].c.title.type.length == 40
        assert md.tables['users'].c.notes.type.length == 10  # never narrowed

    def test_ci_columns_take_the_icu_collation_and_bin_columns_do_not(self, loader):
        """MySQL `_ci` semantics ride along as an ICU collation (POSTGRES_MIGRATION.md #6)."""
        from sqlalchemy import Column, Enum, Integer, MetaData, String, Table, Text
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable
        md = MetaData()
        t = Table('users', md, Column('user_id', Integer, primary_key=True),
                  Column('username', String(35)), Column('email', String(100)),
                  Column('notes', Text), Column('kind', Enum('a', 'b', name='kind')))
        touched = loader.apply_collations(md, {
            ('users', 'username'): 'utf8mb3_general_ci', ('users', 'email'): 'utf8mb3_bin',
            ('users', 'notes'): 'utf8mb4_0900_ai_ci', ('users', 'kind'): 'utf8mb3_general_ci'})
        assert touched == [('users', 'username'), ('users', 'notes')]
        assert t.c.username.type.collation == 'sam_ci' and t.c.username.type.length == 35
        assert t.c.email.type.collation is None
        ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
        assert 'username VARCHAR(35) COLLATE "sam_ci"' in ddl and 'notes TEXT COLLATE "sam_ci"' in ddl
        assert 'email VARCHAR(100),' in ddl
        assert loader.collation_sql() == ('CREATE COLLATION "sam_ci" (provider = icu, '
                                          "locale = 'und-u-ks-level1', deterministic = false)")

    def test_widening_keeps_the_collation(self, loader):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        md = MetaData()
        Table('users', md, Column('user_id', Integer, primary_key=True), Column('title', String(15)))
        loader.apply_collations(md, {('users', 'title'): 'utf8mb3_general_ci'})
        loader.widen_strings(md, {('users', 'title'): 45})
        assert (md.tables['users'].c.title.type.length, md.tables['users'].c.title.type.collation) == (45, 'sam_ci')

    def test_the_orm_type_object_is_never_mutated(self, loader):
        """The copy shares type objects with the ORM (MySQL prod); a collation must not leak back."""
        from sam.core.users import User
        md, _ = loader.fk_free_metadata()
        loader.apply_collations(md, {('users', 'username'): 'utf8mb3_general_ci'})
        assert md.tables['users'].c.username.type.collation == 'sam_ci'
        assert User.__table__.c.username.type.collation is None

    def test_serial_columns_are_single_integer_autoincrement_pks(self, loader):
        md, _ = loader.fk_free_metadata()
        serials = dict(loader.serial_columns(md))
        assert serials['users'] == 'user_id' and serials['project'] == 'project_id'
        assert 'dav_activity' not in serials  # composite PK


class TestCoerce:
    def test_null_and_booleans(self, loader):
        assert loader.coerce(None, False) == '\\N'
        assert loader.coerce(1, True) == 't' and loader.coerce(0, True) == 'f'
        assert loader.coerce(7, False) == '7'
        assert loader.coerce(True, False) == 't'

    def test_dates_decimals_and_zero_dates(self, loader):
        assert loader.coerce(datetime.datetime(2026, 9, 12, 8, 30, 5), False) == '2026-09-12 08:30:05'
        assert loader.coerce(datetime.date(2026, 9, 12), False) == '2026-09-12'
        assert loader.coerce(decimal.Decimal('12.50'), False) == '12.50'
        assert loader.coerce('0000-00-00 00:00:00', False) == '\\N'

    def test_text_is_escaped_for_copy(self, loader):
        assert loader.coerce('a\tb\nc\\d', False) == 'a\\tb\\nc\\\\d'
        assert loader.coerce({'k': 'v'}, False) == '{"k": "v"}'
        assert loader.coerce(b'\x01\xff', False) == '\\\\x01ff'

    def test_lines_join_fields_with_tabs(self, loader):
        lines = list(loader.copy_lines([(1, None, 'x')], [(True, False), (False, False), (False, False)]))
        assert lines == ['t\t\\N\tx\n']

    def test_a_null_in_a_not_null_defaulted_column_takes_the_default(self, loader):
        from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, text
        t = Table('t', MetaData(), Column('id', Integer, primary_key=True),
                  Column('name', String(10)),
                  Column('creation_time', DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
                  Column('note', String(10), nullable=False))
        assert loader.column_specs(t) == [(False, False), (False, False), (False, True), (False, False)]
        stats = {}
        lines = list(loader.copy_lines([(1, None, None, 'n'), (2, 'a', '0000-00-00 00:00:00', 'n')],
                                       loader.column_specs(t), stats))
        assert lines == ['1\t\\N\t\\D\tn\n', '2\ta\t\\D\tn\n'] and stats == {'defaults': 2}


class TestSql:
    def test_fk_statement_and_not_valid(self, loader):
        fk = ('disk_charge', ('disk_activity_id',), 'disk_activity', ('disk_activity_id',))
        assert loader.fk_statement(fk, 'fk_disk_charge_disk_activity', True) == (
            'ALTER TABLE "disk_charge" ADD CONSTRAINT "fk_disk_charge_disk_activity" '
            'FOREIGN KEY ("disk_activity_id") REFERENCES "disk_activity" ("disk_activity_id") NOT VALID')
        assert loader.fk_statement(fk, 'x', False).endswith('("disk_activity_id")')

    def test_view_statements_keep_commented_statements(self, loader):
        text = ("-- header\nCREATE VIEW a AS\nSELECT 1;\n\n-- about b\nCREATE VIEW b AS\n"
                "SELECT 2, -- trailing\n 3;\n")
        stmts = loader.view_statements(text)
        assert stmts == ['CREATE VIEW a AS\nSELECT 1', 'CREATE VIEW b AS\nSELECT 2, -- trailing\n 3']

    def test_the_shipped_views_file_holds_all_seven(self, loader):
        with open(loader.VIEWS_SQL) as f:
            stmts = loader.view_statements(f.read())
        names = sorted(s.split()[4] for s in stmts)
        assert names == ['comp_activity_charge', 'xras_action', 'xras_allocation',
                         'xras_hpc_allocation_amount', 'xras_request', 'xras_role', 'xras_user']

    def test_setval_never_goes_below_one(self, loader):
        sql = loader.setval_sql('disk_cos', 'disk_cos_id')
        assert 'GREATEST(COALESCE(MAX("disk_cos_id"), 1), 1)' in sql
        assert 'COALESCE(MAX("disk_cos_id"), 0) >= 1' in sql and sql.endswith('FROM "disk_cos"')

    def test_swap_sql_in_order_and_first_run(self, loader):
        stmts = loader.swap_sql('sam_dev', True)
        assert stmts == ['ALTER DATABASE "sam_dev" RENAME TO "sam_dev_prev"',
                         'ALTER DATABASE "sam_dev_next" RENAME TO "sam_dev"',
                         'DROP DATABASE "sam_dev_prev"']
        assert loader.swap_sql('sam_dev', False) == \
            ['ALTER DATABASE "sam_dev_next" RENAME TO "sam_dev"']
