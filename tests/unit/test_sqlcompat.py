"""sam.sqlcompat: the dialect-keyed fragments render the right spelling per backend."""
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects import mysql, postgresql

from sam import sqlcompat

pytestmark = pytest.mark.unit


def _bind(name):
    return SimpleNamespace(dialect=SimpleNamespace(name=name))


def _session(name):
    return SimpleNamespace(get_bind=lambda: _bind(name))


class TestRowConstructor:
    def test_mysql_and_mariadb_spell_row(self):
        assert sqlcompat.row_constructor(_bind('mysql')) == 'ROW'
        assert sqlcompat.row_constructor(_session('mariadb')) == 'ROW'

    def test_postgres_spells_a_bare_tuple(self):
        assert sqlcompat.row_constructor(_bind('postgresql')) == ''
        assert sqlcompat.row_constructor(_session('postgresql')) == ''


class TestSchemaPredicate:
    def test_each_backend_scopes_to_its_own_database(self):
        assert sqlcompat.schema_predicate(_bind('mysql')) == 'TABLE_SCHEMA = DATABASE()'
        assert sqlcompat.schema_predicate(_bind('postgresql')) == 'table_schema = current_schema()'


class TestSamNow:
    def test_statement_time_on_postgres_and_now_elsewhere(self):
        assert str(sqlcompat.sam_now().compile(dialect=mysql.dialect())) == 'now()'
        assert str(sqlcompat.sam_now().compile(dialect=postgresql.dialect())) == \
            'CAST(statement_timestamp() AS TIMESTAMP)'


class TestCiLike:
    def test_renders_lower_like_lower_on_both(self):
        t = Table('users', MetaData(), Column('username', String(35)))
        expr = sqlcompat.ci_like(t.c.username, 'ben%')
        for dialect in (mysql.dialect(), postgresql.dialect()):
            sql = str(expr.compile(dialect=dialect))
            assert sql.startswith('lower(users.username) LIKE lower(')
            assert 'ILIKE' not in sql
        escaped = str(sqlcompat.ci_like(t.c.username, r'%a\_b', escape='\\').compile(dialect=postgresql.dialect()))
        assert ' ESCAPE ' in escaped

    def test_no_ilike_survives_in_sam_or_webapp(self):
        """Postgres rejects ILIKE under the sam_ci collation; every SAM site goes through ci_like.

        system_status is exempt: its own Postgres database has no such collation.
        """
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / 'src'
        offenders = [str(p.relative_to(src)) for sub in ('sam', 'webapp', 'cli', 'scheduling')
                     for p in (src / sub).rglob('*.py')
                     if p.name != 'sqlcompat.py' and '.ilike(' in p.read_text(encoding='utf-8')]
        assert offenders == []
