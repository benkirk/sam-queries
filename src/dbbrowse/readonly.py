"""The read-only, time-bounded connection every dbbrowse query runs on.

One context manager, one branch per dialect, fail-closed on anything else.
It never commits: exit always rolls back, so even a guard regression cannot
persist a write.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError


class UnsupportedDialect(RuntimeError):
    pass


class ReadOnlyNotEngaged(RuntimeError):
    pass


@contextmanager
def read_only_connection(engine: Engine, *, timeout_ms: int = 5000) -> Iterator[Connection]:
    """A Connection inside a READ ONLY transaction with a per-statement timeout."""
    timeout_ms = int(timeout_ms)
    conn = engine.connect()
    restore = None
    try:
        name = conn.dialect.name
        if name == 'postgresql':
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SELECT set_config('statement_timeout', :ms, true)"),
                         {'ms': str(timeout_ms)})
            # An AUTOCOMMIT engine accepts SET TRANSACTION with only a warning.
            if conn.execute(text('SHOW transaction_read_only')).scalar() != 'on':
                raise ReadOnlyNotEngaged(engine.url.render_as_string(hide_password=True))
        elif name in ('mysql', 'mariadb'):
            restore = _mysql_timeout(conn, timeout_ms)
            # Applies to the NEXT transaction; raises 1568 rather than committing
            # if one is already open, so it cannot end someone's writes.
            conn.execute(text('SET TRANSACTION READ ONLY'))
        elif name == 'sqlite':
            conn.execute(text('PRAGMA query_only = ON'))
            restore = text('PRAGMA query_only = OFF')
        else:
            raise UnsupportedDialect(name)
        yield conn
    finally:
        try:
            conn.rollback()
            if restore is not None:
                conn.execute(restore)
                conn.commit()   # ends the empty transaction the restore opened
        except Exception:
            conn.invalidate()   # never return a connection whose session state is unknown
        finally:
            conn.close()


def _mysql_timeout(conn: Connection, timeout_ms: int):
    """Set the session statement timeout and return the statement that restores it."""
    if getattr(conn.dialect, 'is_mariadb', False):
        prior = conn.execute(text('SELECT @@SESSION.max_statement_time')).scalar()
        conn.execute(text('SET SESSION max_statement_time = :s'), {'s': timeout_ms / 1000})
        conn.rollback()
        return text('SET SESSION max_statement_time = :s').bindparams(s=prior)
    prior = conn.execute(text('SELECT @@SESSION.max_execution_time')).scalar()
    conn.execute(text('SET SESSION max_execution_time = :ms'), {'ms': timeout_ms})
    conn.rollback()   # the SELECT above began a transaction; SET TRANSACTION needs none open
    return text('SET SESSION max_execution_time = :ms').bindparams(ms=prior)


_TIMEOUT_CODES = {3024, 1969}          # MySQL max_execution_time, MariaDB max_statement_time
_READ_ONLY_CODES = {1792}              # MySQL/MariaDB write in a READ ONLY transaction


def _code(exc: BaseException):
    orig = getattr(exc, 'orig', exc)
    args = getattr(orig, 'args', ())
    return args[0] if args and isinstance(args[0], int) else None


def is_timeout(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    return (getattr(exc.orig, 'pgcode', None) == '57014'
            or _code(exc) in _TIMEOUT_CODES)


def is_read_only_violation(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    return (getattr(exc.orig, 'pgcode', None) == '25006'
            or _code(exc) in _READ_ONLY_CODES
            or 'readonly' in str(exc.orig).lower().replace(' ', '')
            or 'query_only' in str(exc.orig).lower())
