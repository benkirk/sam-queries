"""Dialect-keyed fragments for the raw-SQL sites SQLAlchemy Core cannot express.

SAM runs on MySQL in production and Postgres in development from one ORM
(docs/plans/POSTGRES_MIGRATION.md). Everything that can go through Core does;
these are the leftovers, in two families. Bind -> fragment string, for text()
statements: the VALUES row-constructor spelling, the information_schema scope
predicate, the string-aggregate. Expression constructors, for Core: the
statement clock, and a LIKE that Postgres accepts under the nondeterministic
`sam_ci` collation (ILIKE is rejected there).
"""
from sqlalchemy import DateTime, func
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

MYSQL_DIALECTS = ('mysql', 'mariadb')


class sam_now(FunctionElement):
    """The database clock at statement start, naive, on both backends.

    MySQL NOW() already is. Postgres now() is the TRANSACTION start, so a row
    inserted earlier in the same transaction never reads as current; it also
    returns timestamptz, which cannot be compared with the naive stamps.
    """
    type = DateTime()
    inherit_cache = True


@compiles(sam_now)
def _sam_now_default(element, compiler, **kw):
    return 'now()'


@compiles(sam_now, 'postgresql')
def _sam_now_postgresql(element, compiler, **kw):
    return 'CAST(statement_timestamp() AS TIMESTAMP)'


def dialect_name(bind) -> str:
    """'mysql' / 'postgresql' from a Session, Connection or Engine."""
    if hasattr(bind, 'get_bind'):
        bind = bind.get_bind()
    return bind.dialect.name


def row_constructor(bind) -> str:
    """The VALUES table constructor is `VALUES ROW(...)` on MySQL, `VALUES (...)` on Postgres."""
    return 'ROW' if dialect_name(bind) in MYSQL_DIALECTS else ''


def schema_predicate(bind) -> str:
    """information_schema predicate for the connected database's own tables."""
    if dialect_name(bind) in MYSQL_DIALECTS:
        return 'TABLE_SCHEMA = DATABASE()'
    return 'table_schema = current_schema()'


def group_concat(bind, expr: str, sep: str = ',') -> str:
    """Comma-joined aggregate of a column expression: GROUP_CONCAT on MySQL, STRING_AGG on Postgres."""
    if dialect_name(bind) in MYSQL_DIALECTS:
        return f"GROUP_CONCAT({expr} SEPARATOR '{sep}')"
    return f"STRING_AGG(CAST({expr} AS TEXT), '{sep}')"


def ci_like(column, pattern, escape=None):
    """Case-insensitive LIKE on both backends: exactly what `.ilike()` renders on MySQL."""
    return func.lower(column).like(func.lower(pattern), escape=escape)
