"""LOG_TZ renders collector log lines in a display zone; the process TZ (data) is untouched."""

import importlib.util
import logging
import os

import pytest

from _paths import REPO_ROOT

_PATH = os.path.join(str(REPO_ROOT), "collectors", "lib", "logging_utils.py")


@pytest.fixture
def logging_utils():
    spec = importlib.util.spec_from_file_location("_collector_logging_utils", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    logging.basicConfig(handlers=[logging.NullHandler()], force=True)


def _stamp(logging_utils, tmp_path, monkeypatch, log_tz):
    if log_tz is None:
        monkeypatch.delenv("LOG_TZ", raising=False)
    else:
        monkeypatch.setenv("LOG_TZ", log_tz)
    log = tmp_path / "c.log"
    logging_utils.setup_logging(log_file=str(log))
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    record.created = 1790000000.0   # 2026-09-21 14:13:20 UTC
    return logging.getLogger().handlers[-1].formatter.formatTime(record, "%Y-%m-%d %H:%M:%S")


def test_log_tz_renders_in_that_zone(logging_utils, tmp_path, monkeypatch):
    assert _stamp(logging_utils, tmp_path, monkeypatch, "America/Denver") == "2026-09-21 08:13:20"
    assert _stamp(logging_utils, tmp_path, monkeypatch, "UTC") == "2026-09-21 14:13:20"


def test_unset_or_unknown_log_tz_keeps_the_stock_formatter(logging_utils, tmp_path, monkeypatch, capsys):
    for value in (None, "Not/AZone"):
        _stamp(logging_utils, tmp_path, monkeypatch, value)
        assert type(logging.getLogger().handlers[-1].formatter) is logging.Formatter
    assert "unknown LOG_TZ='Not/AZone'" in capsys.readouterr().err
