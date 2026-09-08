"""Thin xlsx workbook builder over xlsxwriter. No Flask/matplotlib imports.

A sheet is ``(name, columns, rows)``: rows are dicts keyed by ``Column.key``.
Numeric/date columns become real cells (sortable/summable in Excel), not
display strings. Reusable by any dashboard export and, later, the CLI.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional, Sequence, Tuple

import xlsxwriter

_ILLEGAL = re.compile(r"[\[\]:*?/\\]")
_MAX_SHEET_NAME = 31

# Column kind -> Excel number format.
_NUM_FORMATS = {"int": "#,##0", "num": "#,##0", "pct": "0.0%", "date": "yyyy-mm-dd"}


@dataclass(frozen=True)
class Column:
    """One spreadsheet column; kind ∈ {'text','int','num','pct','date'}."""

    key: str
    header: str
    width: int = 16
    kind: str = "text"


Sheet = Tuple[str, Sequence[Column], Sequence[Dict[str, Any]]]


def build_workbook(sheets: Sequence[Sheet]) -> bytes:
    """Render sheets to xlsx bytes: frozen header, autofilter, sized columns."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    header_fmt = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "bottom": 1})
    cell_fmts = {k: wb.add_format({"num_format": v}) for k, v in _NUM_FORMATS.items()}
    used: set[str] = set()

    for name, columns, rows in sheets:
        ws = wb.add_worksheet(_safe_sheet_name(name, used))
        ws.freeze_panes(1, 0)
        for c, col in enumerate(columns):
            ws.set_column(c, c, col.width)
            ws.write(0, c, col.header, header_fmt)
        for r, row in enumerate(rows, start=1):
            for c, col in enumerate(columns):
                _write_cell(ws, r, c, row.get(col.key), col.kind, cell_fmts)
        if rows:
            ws.autofilter(0, 0, len(rows), len(columns) - 1)

    wb.close()
    return buf.getvalue()


def _safe_sheet_name(name: str, used: set) -> str:
    """Sanitize to Excel's 31-char limit and enforce case-insensitive uniqueness."""
    cleaned = (_ILLEGAL.sub("-", name).strip() or "Sheet")[:_MAX_SHEET_NAME]
    base, n = cleaned, 1
    while cleaned.lower() in used:
        suffix = f"-{n}"
        cleaned = base[: _MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    used.add(cleaned.lower())
    return cleaned


def _write_cell(ws, r: int, c: int, value: Optional[Any], kind: str, fmts: Dict) -> None:
    if value is None or value == "":
        ws.write_blank(r, c, None)
        return
    if kind in ("int", "num", "pct"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            ws.write(r, c, str(value))
            return
        # percent_used arrives as 0-100; the '0.0%' format multiplies by 100.
        ws.write_number(r, c, num / 100.0 if kind == "pct" else num, fmts[kind])
        return
    if kind == "date" and isinstance(value, (datetime, date)):
        ws.write_datetime(r, c, value, fmts["date"])
        return
    ws.write(r, c, str(value))
