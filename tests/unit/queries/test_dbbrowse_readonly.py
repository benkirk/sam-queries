"""read_only_connection: writes fail, timeouts fire, and nothing leaks to the next checkout."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import StaticPool

from dbbrowse import (ReadOnlyNotEngaged, UnsupportedDialect, is_read_only_violation,
                      is_timeout, read_only_connection)

NOOP_WRITE = text('UPDATE users SET username = username WHERE 1 = 0')


@pytest.fixture
def one_conn_engine(test_db_url):
    """A single pooled connection, so the next checkout is the one the guard used."""
    eng = create_engine(test_db_url, pool_size=1, max_overflow=0)
    yield eng
    eng.dispose()


def test_write_inside_guard_is_refused(one_conn_engine):
    with read_only_connection(one_conn_engine) as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(NOOP_WRITE)
    assert is_read_only_violation(exc.value)


def test_nothing_leaks_to_the_next_checkout(one_conn_engine):
    with read_only_connection(one_conn_engine, timeout_ms=1234) as conn:
        conn.execute(text('SELECT 1'))
    with one_conn_engine.connect() as conn:
        conn.execute(NOOP_WRITE)
        if conn.dialect.name == 'postgresql':
            assert conn.execute(text('SHOW transaction_read_only')).scalar() == 'off'
        else:
            assert conn.execute(text('SELECT @@SESSION.max_execution_time')).scalar() == 0
        conn.rollback()


def test_nothing_leaks_after_an_exception(one_conn_engine):
    with pytest.raises(RuntimeError):
        with read_only_connection(one_conn_engine):
            raise RuntimeError('before any query')
    with one_conn_engine.connect() as conn:
        conn.execute(NOOP_WRITE)
        conn.rollback()


@pytest.mark.postgres_only
def test_postgres_statement_timeout(engine):
    with read_only_connection(engine, timeout_ms=50) as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(text('SELECT pg_sleep(1)'))
    assert is_timeout(exc.value)


@pytest.mark.postgres_only
def test_postgres_autocommit_engine_is_refused(test_db_url):
    eng = create_engine(test_db_url, isolation_level='AUTOCOMMIT')
    try:
        with pytest.raises(ReadOnlyNotEngaged):
            with read_only_connection(eng):
                pass
    finally:
        eng.dispose()


@pytest.mark.mysql_only
def test_mysql_statement_timeout(engine):
    # max_execution_time interrupts a SELECT; SLEEP() inside one returns 1 when cut short.
    with read_only_connection(engine, timeout_ms=50) as conn:
        try:
            interrupted = conn.execute(text('SELECT SLEEP(2) FROM users LIMIT 1')).scalar()
        except DBAPIError as exc:
            assert is_timeout(exc)
        else:
            assert interrupted == 1


def test_sqlite_query_only_and_restore(tmp_path):
    eng = create_engine(f'sqlite:///{tmp_path}/t.db', poolclass=StaticPool)
    with eng.begin() as conn:
        conn.execute(text('CREATE TABLE t (id INTEGER PRIMARY KEY)'))
    with read_only_connection(eng) as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(text('INSERT INTO t (id) VALUES (1)'))
    assert is_read_only_violation(exc.value)
    with eng.connect() as conn:
        assert conn.execute(text('PRAGMA query_only')).scalar() == 0
    eng.dispose()


def test_unknown_dialect_fails_closed():
    class _Dialect:
        name = 'oracle'

    class _Conn:
        dialect = _Dialect()
        closed = False

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    class _Engine:
        conn = _Conn()

        def connect(self):
            return self.conn

    eng = _Engine()
    with pytest.raises(UnsupportedDialect):
        with read_only_connection(eng):
            pass
    assert eng.conn.closed
