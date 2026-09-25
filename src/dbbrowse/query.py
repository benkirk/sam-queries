"""One page of rows, a row by key, one full cell, an on-demand count.

Pages are keyset (``pk > :after``) when the table has a single-column PK and
no user sort, else OFFSET capped at ``MAX_OFFSET``. Large columns are cut
server-side with ``substr`` so a page never ships a megabyte-per-cell payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Column, Table, Text, cast, func, select
from sqlalchemy.engine import Connection

from .filters import Filter, column_kind

MAX_OFFSET = 10_000
GRID_CHARS = 200
DETAIL_CHARS = 4_000
CELL_MAX_CHARS = 1_000_000
TOP_VALUES = 20


class OffsetTooDeep(ValueError):
    pass


@dataclass
class PageRequest:
    filters: List[Filter] = field(default_factory=list)
    sort: Optional[str] = None
    desc: bool = False
    page: int = 1
    per_page: int = 50
    after: Any = None             # coerced keyset cursor


@dataclass
class Page:
    columns: List[Column]
    rows: List[Dict[str, Any]]
    has_next: bool
    keyset: bool
    offset: int = 0
    next_after: Any = None


def _expr(col: Column, chars: int):
    """What to SELECT for a column: length for binary, a +1-char prefix for large text."""
    kind = column_kind(col)
    if kind == 'binary':
        return func.length(col)
    if kind == 'json':
        return func.substr(cast(col, Text), 1, chars + 1)
    if kind == 'long':
        return func.substr(col, 1, chars + 1)
    return col


def _select(table: Table, columns: Sequence[Column], chars: int):
    return select(*(_expr(c, chars).label(f'c{i}') for i, c in enumerate(columns))).select_from(table)


def _rows(result, columns) -> List[Dict[str, Any]]:
    return [{c.name: row[i] for i, c in enumerate(columns)} for row in result]


def keyset_column(table: Table, pk: Sequence[str], req: PageRequest) -> Optional[Column]:
    return table.c[pk[0]] if len(pk) == 1 and req.sort is None else None


def build_page_select(table: Table, columns: Sequence[Column], req: PageRequest,
                      pk: Sequence[str]):
    stmt = _select(table, columns, GRID_CHARS).where(*(f.clause() for f in req.filters))
    key = keyset_column(table, pk, req)
    if key is not None:
        if req.after is not None:
            stmt = stmt.where(key > req.after)
        return stmt.order_by(key).limit(req.per_page + 1)

    offset = (max(req.page, 1) - 1) * req.per_page
    if offset > MAX_OFFSET:
        raise OffsetTooDeep(f'pages past row {MAX_OFFSET:,} are not served; add a filter')
    order = []
    if req.sort is not None:
        col = table.c[req.sort]
        order.append(col.desc() if req.desc else col.asc())
    order += [table.c[p] for p in pk if p != req.sort]
    return stmt.order_by(*order).limit(req.per_page + 1).offset(offset)


def fetch_page(conn: Connection, table: Table, columns: Sequence[Column],
               req: PageRequest, pk: Sequence[str]) -> Page:
    rows = _rows(conn.execute(build_page_select(table, columns, req, pk)), columns)
    has_next = len(rows) > req.per_page
    rows = rows[:req.per_page]
    key = keyset_column(table, pk, req)
    if key is not None:
        return Page(list(columns), rows, has_next, keyset=True,
                    next_after=rows[-1].get(key.name) if has_next and rows else None)
    return Page(list(columns), rows, has_next, keyset=False,
                offset=(max(req.page, 1) - 1) * req.per_page)


def top_values(conn: Connection, table: Table, column: Column, filters: Sequence[Filter],
               *, limit: int = TOP_VALUES) -> List[Tuple[Any, int]]:
    """The ``limit`` most frequent values of ``column`` under ``filters``, NULL included."""
    n = func.count().label('n')
    stmt = (select(column, n).select_from(table).where(*(f.clause() for f in filters))
            .group_by(column).order_by(n.desc(), column).limit(limit))
    return [(row[0], row[1]) for row in conn.execute(stmt)]


def exact_count(conn: Connection, table: Table, filters: Sequence[Filter]) -> int:
    stmt = select(func.count()).select_from(table).where(*(f.clause() for f in filters))
    return conn.execute(stmt).scalar_one()


def fetch_by_key(conn: Connection, table: Table, columns: Sequence[Column],
                 key: Dict[str, Any], *, chars: int = DETAIL_CHARS, limit: int = 2):
    """Rows whose key columns equal ``key``; ``limit=2`` tells one from many."""
    stmt = (_select(table, columns, chars)
            .where(*(table.c[k] == v for k, v in key.items())).limit(limit))
    return _rows(conn.execute(stmt), columns)


def fetch_cell(conn: Connection, table: Table, column: Column, key: Dict[str, Any]):
    rows = fetch_by_key(conn, table, [column], key, chars=CELL_MAX_CHARS, limit=2)
    return rows[0][column.name] if len(rows) == 1 else None


def truncated(value: Any, chars: int) -> Tuple[Any, bool]:
    """Cut a ``substr`` result that came back one char over the limit."""
    if isinstance(value, str) and len(value) > chars:
        return value[:chars], True
    return value, False
