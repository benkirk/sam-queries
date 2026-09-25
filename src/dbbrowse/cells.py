"""A fetched value as display text: exact, unformatted, NULL distinct from empty."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from .query import truncated


@dataclass(frozen=True)
class CellView:
    text: str
    kind: str                  # 'null' | 'binary' | 'json' | 'num' | 'bool' | 'date' | 'text'
    truncated: bool = False
    multiline: bool = False


def render_cell(value: Any, column_kind: str, *, chars: int, pretty: bool = False) -> CellView:
    if value is None:
        return CellView('NULL', 'null')
    if column_kind == 'binary':
        return CellView(f'<binary {value} bytes>', 'binary')
    if isinstance(value, (bytes, bytearray, memoryview)):
        return CellView(f'<binary {len(bytes(value))} bytes>', 'binary')
    if isinstance(value, bool):
        return CellView('true' if value else 'false', 'bool')
    if isinstance(value, (int, float, Decimal)):
        return CellView(str(value), 'num')
    if isinstance(value, datetime):
        return CellView(value.isoformat(sep=' '), 'date')
    if isinstance(value, (date, time)):
        return CellView(value.isoformat(), 'date')
    if not isinstance(value, str):
        value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

    text, cut = truncated(value, chars)
    if pretty and not cut and (column_kind == 'json' or text.lstrip()[:1] in ('{', '[')):
        try:
            return CellView(json.dumps(json.loads(text), indent=2, ensure_ascii=False),
                            'json', multiline=True)
        except ValueError:
            pass
    return CellView(text, 'json' if column_kind == 'json' else 'text', cut, '\n' in text)
