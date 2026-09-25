"""Structured filters: (column, operator, value) triples validated against a reflected Table.

Column names are looked up in ``Table.c``, never interpolated; values are
coerced by the column's type and always bound.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, List, Optional, Tuple

from sqlalchemy import Column, Table
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.sql import sqltypes

MAX_FILTERS = 10
MAX_VALUE_LEN = 1000
MAX_IN_ITEMS = 100


class Op(str, Enum):
    eq = 'eq'
    ne = 'ne'
    lt = 'lt'
    le = 'le'
    gt = 'gt'
    ge = 'ge'
    like = 'like'
    in_ = 'in'
    isnull = 'isnull'
    notnull = 'notnull'


OP_LABELS = {Op.eq: '=', Op.ne: '≠', Op.lt: '<', Op.le: '≤', Op.gt: '>', Op.ge: '≥',
             Op.like: 'like', Op.in_: 'in', Op.isnull: 'is null', Op.notnull: 'is not null'}
NULL_OPS = frozenset({Op.isnull, Op.notnull})

_ORDERED = (Op.eq, Op.ne, Op.lt, Op.le, Op.gt, Op.ge, Op.in_, *NULL_OPS)
_OPS_BY_KIND = {
    'text':     (*_ORDERED, Op.like),
    'other':    (*_ORDERED, Op.like),
    'long':     (Op.eq, Op.ne, Op.like, *NULL_OPS),
    'bool':     (Op.eq, Op.ne, *NULL_OPS),
    'json':     tuple(NULL_OPS),
    'binary':   tuple(NULL_OPS),
}


def column_kind(col: Column) -> str:
    """One of int num bool datetime date time text long json binary other."""
    t = col.type
    # MySQL reflects BOOLEAN as TINYINT(1); the width is the only trace of it.
    if isinstance(t, sqltypes.Boolean) or (isinstance(t, TINYINT) and t.display_width == 1):
        return 'bool'
    if isinstance(t, sqltypes.Integer):
        return 'int'
    if isinstance(t, sqltypes.Numeric):
        return 'num'
    if isinstance(t, sqltypes.DateTime):
        return 'datetime'
    if isinstance(t, sqltypes.Date):
        return 'date'
    if isinstance(t, sqltypes.Time):
        return 'time'
    if isinstance(t, sqltypes._Binary):
        return 'binary'
    if isinstance(t, sqltypes.JSON):
        return 'json'
    if isinstance(t, sqltypes.Text):
        return 'long'
    if isinstance(t, sqltypes.String):
        return 'long' if t.length is None or t.length > 255 else 'text'
    return 'other'


def ops_for(col: Column) -> Tuple[Op, ...]:
    return _OPS_BY_KIND.get(column_kind(col), _ORDERED)


@dataclass(frozen=True)
class RawFilter:
    col: str
    op: str
    value: str = ''


_CLAUSES = {
    Op.eq: operator.eq, Op.ne: operator.ne, Op.lt: operator.lt, Op.le: operator.le,
    Op.gt: operator.gt, Op.ge: operator.ge, Op.like: Column.like, Op.in_: Column.in_,
    Op.isnull: lambda c, _: c.is_(None), Op.notnull: lambda c, _: c.is_not(None),
}


@dataclass(frozen=True)
class Filter:
    column: Column
    op: Op
    value: Any = None

    def clause(self):
        return _CLAUSES[self.op](self.column, self.value)


class FilterError(ValueError):
    def __init__(self, index: Optional[int], message: str):
        super().__init__(message)
        self.index = index
        self.message = message


_TRUE = {'1', 'true', 't', 'yes', 'y'}
_FALSE = {'0', 'false', 'f', 'no', 'n'}


def coerce(col: Column, raw: str) -> Any:
    """The raw string as the column's Python type; raises ValueError on a mismatch."""
    kind = column_kind(col)
    s = raw.strip() if kind not in ('text', 'long', 'other') else raw
    try:
        if kind == 'int':
            return int(s)
        if kind == 'num':
            return Decimal(s)
        if kind == 'bool':
            low = s.lower()
            if low in _TRUE or low in _FALSE:
                return low in _TRUE
            raise ValueError
        if kind == 'datetime':
            return datetime.fromisoformat(s)
        if kind == 'date':
            return date.fromisoformat(s)
        if kind == 'time':
            return time.fromisoformat(s)
    except (ValueError, InvalidOperation):
        raise ValueError(f'{raw!r} is not a valid {kind} for {col.name}') from None
    return raw


def parse_filters(raw: Iterable[RawFilter], table: Table, *,
                  hidden: frozenset = frozenset()) -> Tuple[List[Filter], List[FilterError]]:
    """Validate every row; return the good filters and one error per bad row."""
    filters, errors = [], []
    for i, rf in enumerate(raw):
        if i >= MAX_FILTERS:
            errors.append(FilterError(None, f'at most {MAX_FILTERS} filters'))
            break
        try:
            filters.append(_parse_one(rf, table, hidden))
        except ValueError as exc:
            errors.append(FilterError(i, str(exc)))
    return filters, errors


def _parse_one(rf: RawFilter, table: Table, hidden) -> Filter:
    if rf.col not in table.c or rf.col in hidden:
        raise ValueError(f'unknown column {rf.col!r}')
    col = table.c[rf.col]
    try:
        op = Op(rf.op)
    except ValueError:
        raise ValueError(f'unknown operator {rf.op!r}') from None
    if op not in ops_for(col):
        raise ValueError(f'{OP_LABELS[op]} is not available on {column_kind(col)} column {col.name}')
    if op in NULL_OPS:
        return Filter(col, op)
    if len(rf.value) > MAX_VALUE_LEN:
        raise ValueError(f'value longer than {MAX_VALUE_LEN} characters')
    if op is Op.like:
        return Filter(col, op, rf.value)
    if op is Op.in_:
        items = [v for v in (p.strip() for p in rf.value.split(',')) if v]
        if not items or len(items) > MAX_IN_ITEMS:
            raise ValueError(f'in needs 1 to {MAX_IN_ITEMS} comma-separated values')
        return Filter(col, op, [coerce(col, v) for v in items])
    return Filter(col, op, coerce(col, rf.value))
