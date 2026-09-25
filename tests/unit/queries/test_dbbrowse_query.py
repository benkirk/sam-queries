"""Filters, page SELECTs and cell rendering: compiled on both dialects, run on the test DB."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Integer, LargeBinary,
                        MetaData, Numeric, String, Table, Text)
from sqlalchemy.dialects import mysql, postgresql

from dbbrowse import (GRID_CHARS, MAX_FILTERS, OffsetTooDeep, Op, PageRequest, RawFilter,
                      column_kind, exact_count, fetch_by_key, fetch_cell, fetch_page,
                      parse_filters, read_only_connection, reflect_table, render_cell,
                      top_values)
from dbbrowse.query import build_page_select

T = Table(
    'weirdTable', MetaData(),
    Column('id', Integer, primary_key=True),
    Column('resourceRepositoryKey', String(40)),
    Column('amount', Numeric(12, 2)),
    Column('active', Boolean),
    Column('created', DateTime),
    Column('day', Date),
    Column('notes', Text),
    Column('payload', JSON),
    Column('image', LargeBinary),
)


def _sql(stmt, dialect):
    return str(stmt.compile(dialect=dialect, compile_kwargs={'literal_binds': True}))


def test_column_kinds():
    assert [column_kind(c) for c in T.c] == [
        'int', 'text', 'num', 'bool', 'datetime', 'date', 'long', 'json', 'binary']
    assert column_kind(Column('x', String(4000))) == 'long'


@pytest.mark.mysql_only
def test_reflected_mysql_tinyint1_is_a_bool(engine):
    with read_only_connection(engine) as conn:
        users = reflect_table(conn, None, 'users')
    assert column_kind(users.c.active) == 'bool'
    filters, errors = parse_filters([RawFilter('active', 'eq', 'yes')], users)
    assert not errors and filters[0].value is True


def test_parse_coerces_every_type():
    raw = [RawFilter('id', 'in', '1, 2,3'), RawFilter('amount', 'ge', '1.50'),
           RawFilter('active', 'eq', 'yes'), RawFilter('created', 'lt', '2026-09-01'),
           RawFilter('day', 'eq', '2026-09-01'), RawFilter('notes', 'like', '%x%'),
           RawFilter('payload', 'notnull')]
    filters, errors = parse_filters(raw, T)
    assert not errors
    assert [f.value for f in filters] == [
        [1, 2, 3], Decimal('1.50'), True, datetime(2026, 9, 1), date(2026, 9, 1), '%x%', None]


@pytest.mark.parametrize('rf, message', [
    (RawFilter('nope', 'eq', '1'), 'unknown column'),
    (RawFilter('id', 'regex', '1'), 'unknown operator'),
    (RawFilter('id', 'eq', 'abc'), 'not a valid int'),
    (RawFilter('active', 'eq', 'maybe'), 'not a valid bool'),
    (RawFilter('payload', 'eq', '{}'), 'not available'),
    (RawFilter('image', 'like', '%'), 'not available'),
    (RawFilter('id', 'in', ','), 'comma-separated'),
    (RawFilter('id', 'in', ','.join(['1'] * 101)), 'comma-separated'),
    (RawFilter('notes', 'eq', 'x' * 1001), 'longer than'),
])
def test_parse_rejects(rf, message):
    filters, errors = parse_filters([RawFilter('id', 'eq', '1'), rf], T)
    assert len(filters) == 1
    assert errors[0].index == 1 and message in errors[0].message


def test_hidden_columns_are_unfilterable():
    _, errors = parse_filters([RawFilter('notes', 'like', '$2b$%')], T, hidden=frozenset({'notes'}))
    assert 'unknown column' in errors[0].message


def test_filter_count_is_capped():
    _, errors = parse_filters([RawFilter('id', 'isnull')] * (MAX_FILTERS + 1), T)
    assert errors[-1].index is None


@pytest.mark.parametrize('dialect', [mysql.dialect(), postgresql.dialect()], ids=['mysql', 'pg'])
def test_keyset_select_quotes_identifiers_and_cuts_large_columns(dialect):
    filters, _ = parse_filters([RawFilter('resourceRepositoryKey', 'eq', 'x')], T)
    sql = _sql(build_page_select(T, list(T.c), PageRequest(filters=filters, after=10), ['id']),
               dialect)
    q = '`' if dialect.name == 'mysql' else '"'
    assert f'{q}weirdTable{q}' in sql and f'{q}resourceRepositoryKey{q}' in sql
    assert f'substr({q}weirdTable{q}.notes, 1, {GRID_CHARS + 1})' in sql
    assert f'length({q}weirdTable{q}.image)' in sql
    assert f'{q}weirdTable{q}.id > 10' in sql
    assert 'LIMIT 51' in sql and 'OFFSET' not in sql


def test_sorted_select_uses_offset_with_pk_tiebreak():
    req = PageRequest(sort='created', desc=True, page=3, per_page=20)
    sql = _sql(build_page_select(T, list(T.c), req, ['id']), postgresql.dialect())
    assert 'ORDER BY "weirdTable".created DESC, "weirdTable".id' in sql
    assert 'LIMIT 21 OFFSET 40' in sql


def test_deep_offset_is_refused():
    with pytest.raises(OffsetTooDeep):
        build_page_select(T, list(T.c), PageRequest(sort='id', page=1000, per_page=200), ['id'])


def test_fetch_against_the_test_db(engine):
    with read_only_connection(engine) as conn:
        users = reflect_table(conn, None, 'users')
        filters, _ = parse_filters([RawFilter('username', 'eq', 'benkirk')], users)
        page = fetch_page(conn, users, list(users.c), PageRequest(filters=filters), ['user_id'])
        assert [r['username'] for r in page.rows] == ['benkirk']
        assert page.keyset and not page.has_next
        assert exact_count(conn, users, filters) == 1

        uid = page.rows[0]['user_id']
        rows = fetch_by_key(conn, users, list(users.c), {'user_id': uid})
        assert len(rows) == 1 and rows[0]['username'] == 'benkirk'
        assert fetch_cell(conn, users, users.c.username, {'user_id': uid}) == 'benkirk'


def test_top_values_orders_by_frequency_under_filters(engine):
    with read_only_connection(engine) as conn:
        users = reflect_table(conn, None, 'users')
        pairs = top_values(conn, users, users.c.locked, [], limit=5)
        filters, _ = parse_filters([RawFilter('username', 'eq', 'benkirk')], users)
        one = top_values(conn, users, users.c.username, filters)
    counts = [n for _, n in pairs]
    assert counts == sorted(counts, reverse=True) and sum(counts) > 1
    assert one == [('benkirk', 1)]


def test_keyset_pages_do_not_overlap(engine):
    with read_only_connection(engine) as conn:
        users = reflect_table(conn, None, 'users')
        cols = [users.c.user_id]
        first = fetch_page(conn, users, cols, PageRequest(per_page=5), ['user_id'])
        second = fetch_page(conn, users, cols, PageRequest(per_page=5, after=first.next_after),
                            ['user_id'])
    ids = [r['user_id'] for r in first.rows + second.rows]
    assert first.has_next and ids == sorted(set(ids)) and len(ids) == 10


@pytest.mark.parametrize('value, kind, pretty, expected', [
    (None, 'int', False, ('NULL', 'null', False)),
    (12, 'binary', False, ('<binary 12 bytes>', 'binary', False)),
    (Decimal('1234567.80'), 'num', False, ('1234567.80', 'num', False)),
    (True, 'bool', False, ('true', 'bool', False)),
    (datetime(2026, 9, 1, 8, 30), 'datetime', False, ('2026-09-01 08:30:00', 'date', False)),
    ('x' * 201, 'long', False, ('x' * 200, 'text', True)),
    ('{"a": 1}', 'long', True, ('{\n  "a": 1\n}', 'json', False)),
    ('{"a": ', 'long', True, ('{"a": ', 'text', False)),
])
def test_render_cell(value, kind, pretty, expected):
    cell = render_cell(value, kind, chars=200, pretty=pretty)
    assert (cell.text, cell.kind, cell.truncated) == expected


def test_ops_enum_values_are_url_stable():
    assert [o.value for o in Op] == [
        'eq', 'ne', 'lt', 'le', 'gt', 'ge', 'like', 'in', 'isnull', 'notnull']
