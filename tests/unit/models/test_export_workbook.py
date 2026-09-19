"""Unit tests for sam.export.workbook — no DB, no xlsx reader dependency."""

import io
import re
import zipfile
from datetime import date, datetime

import pytest

from sam.export import Column, build_workbook

COLS = [
    Column('name', 'Name', 20, 'text'),
    Column('amount', 'Amount', 16, 'num'),
    Column('pct', 'Pct', 10, 'pct'),
    Column('when', 'When', 12, 'date'),
]


def _sheet_names(xlsx_bytes):
    """Pull worksheet names out of xl/workbook.xml (avoids needing a reader lib)."""
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
        workbook_xml = zf.read('xl/workbook.xml').decode('utf-8')
    return re.findall(r'<sheet name="([^"]*)"', workbook_xml)


def test_build_workbook_is_a_valid_xlsx_zip():
    data = build_workbook([('Derecho', COLS, [
        {'name': 'proj a', 'amount': 1000, 'pct': 42.5, 'when': date(2026, 9, 30)},
    ])])
    assert data[:2] == b'PK'
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert 'xl/workbook.xml' in names
    assert 'xl/worksheets/sheet1.xml' in names


def test_one_sheet_per_tuple_and_names_preserved():
    data = build_workbook([
        ('Derecho', COLS, []),
        ('Casper', COLS, []),
    ])
    assert _sheet_names(data) == ['Derecho', 'Casper']


def test_long_sheet_name_truncated_to_31_chars():
    long_name = 'X' * 50
    (name,) = _sheet_names(build_workbook([(long_name, COLS, [])]))
    assert name == 'X' * 31


def test_illegal_chars_sanitized():
    (name,) = _sheet_names(build_workbook([('a/b:c[d]?', COLS, [])]))
    assert not set(name) & set('[]:*?/\\')


def test_duplicate_names_deduped_case_insensitively():
    names = _sheet_names(build_workbook([
        ('Derecho', COLS, []),
        ('derecho', COLS, []),
        ('DERECHO', COLS, []),
    ]))
    assert len(names) == len(set(n.lower() for n in names)) == 3


def test_empty_rows_and_datetime_and_none_cells_do_not_raise():
    data = build_workbook([('S', COLS, [
        {'name': 'a', 'amount': None, 'pct': None, 'when': datetime(2026, 1, 1, 9, 0)},
        {'name': '', 'amount': 'not-a-number', 'pct': 0, 'when': 'not-a-date'},
    ])])
    assert zipfile.is_zipfile(io.BytesIO(data))
