"""Unit tests for the GladeCsvReader (acct.glade.YYYY-MM-DD parser)."""

from datetime import date
from pathlib import Path

import pytest

from cli.accounting.disk_usage import (
    DiskUsageEntry,
    GladeCsvReader,
    get_disk_usage_reader,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_basic_row_kib_to_bytes(tmp_path):
    # col6 = 100 KiB → bytes = 100 * 1024 = 102400
    csv = '"2026-04-18","/gpfs/csfs1/cesm","cesm","gdicker","4986","100","7","0"\n'
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    entries = GladeCsvReader(str(f)).read()
    assert len(entries) == 1
    e = entries[0]
    assert e.activity_date == date(2026, 4, 18)
    assert e.projcode == "CESM"               # uppercased
    assert e.username == "gdicker"
    assert e.number_of_files == 4986
    assert e.bytes == 100 * 1024              # KiB → bytes
    assert e.directory_path == "/gpfs/csfs1/cesm"
    assert e.reporting_interval == 7
    assert e.cos == 0
    assert e.act_username is None              # normal row, no audit label


def test_skips_gpfsnobody(tmp_path):
    csv = (
        '"2026-04-18","/gpfs/csfs1/cisl/HDIG","hdig","gpfsnobody","2","32","7","0"\n'
        '"2026-04-18","/gpfs/csfs1/cesm","cesm","gdicker","1","32","7","0"\n'
    )
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    entries = GladeCsvReader(str(f)).read()
    assert [e.username for e in entries] == ["gdicker"]


def test_skips_numeric_username(tmp_path):
    # Legacy "uid was never resolved" row — username column is just digits.
    csv = (
        '"2026-04-18","/gpfs/csfs1/cisl/sssg0001","sssg0001","34607","1","0","7","0"\n'
        '"2026-04-18","/gpfs/csfs1/cisl/sssg0001","sssg0001","ivette","1","0","7","0"\n'
    )
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    entries = GladeCsvReader(str(f)).read()
    assert [e.username for e in entries] == ["ivette"]


def test_snapshot_date_from_rows(tmp_path):
    csv = (
        '"2026-04-18","/p","x","u","1","1","7","0"\n'
        '"2026-04-18","/p2","y","u2","1","1","7","0"\n'
    )
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    r = GladeCsvReader(str(f))
    r.read()
    assert r.snapshot_date == date(2026, 4, 18)


def test_snapshot_date_falls_back_to_filename(tmp_path):
    # No data rows, filename carries the date.
    f = _write(tmp_path, "acct.glade.2026-04-18", "")
    r = GladeCsvReader(str(f))
    r.read()
    assert r.snapshot_date == date(2026, 4, 18)


def test_malformed_row_silently_skipped(tmp_path):
    csv = (
        '"2026-04-18","/p","x","u","not-a-number","1","7","0"\n'
        '"2026-04-18","/p","x","alice","1","1","7","0"\n'
    )
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    entries = GladeCsvReader(str(f)).read()
    assert [e.username for e in entries] == ["alice"]


def test_short_row_silently_skipped(tmp_path):
    csv = '"2026-04-18","/p","x"\n'           # too few columns
    f = _write(tmp_path, "acct.glade.2026-04-18", csv)
    entries = GladeCsvReader(str(f)).read()
    assert entries == []


def test_registry_dispatch():
    r = get_disk_usage_reader("Campaign_Store", "/tmp/whatever")
    assert isinstance(r, GladeCsvReader)


def test_registry_unknown_resource_raises():
    with pytest.raises(NotImplementedError):
        get_disk_usage_reader("FrobOS", "/tmp/whatever")
