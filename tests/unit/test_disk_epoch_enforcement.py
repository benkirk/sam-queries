"""Tests for cutover-epoch enforcement on `sam-admin accounting --disk`.

The CLI must refuse to write rows whose snapshot date is before
``DISK_CHARGING_TIB_EPOCH`` — that range stays in legacy decimal-TB-year
units and is never rewritten by this tool.
"""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.admin import cli
from sam.summaries.disk_summaries import DISK_CHARGING_TIB_EPOCH


pytestmark = pytest.mark.unit


def _make_acct_glade(tmp_path: Path, snap_date: date) -> Path:
    """Write a minimal acct.glade.YYYY-MM-DD with a single junk row."""
    p = tmp_path / f"acct.glade.{snap_date.isoformat()}"
    p.write_text(
        f'"{snap_date.isoformat()}","/gpfs/csfs1/cesm","cesm","gdicker","1","1","7","0"\n'
    )
    return p


class TestEpochEnforcement:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        """Hand the admin CLI our SAVEPOINT'd session."""
        with patch('sam.session.create_sam_engine') as mock_engine, \
             patch('cli.cmds.admin.Session') as mock_session_cls:
            mock_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    def test_pre_epoch_date_refused(self, runner, mock_db_session, tmp_path):
        pre = DISK_CHARGING_TIB_EPOCH - timedelta(days=1)
        f = _make_acct_glade(tmp_path, pre)
        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', 'Campaign_Store',
            '--user-usage', str(f),
            '--date', pre.isoformat(),
            '--dry-run',
        ])
        assert result.exit_code == 2
        assert "DISK_CHARGING_TIB_EPOCH" in result.output

    def test_at_epoch_date_allowed(self, runner, mock_db_session, tmp_path):
        f = _make_acct_glade(tmp_path, DISK_CHARGING_TIB_EPOCH)
        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', 'Campaign_Store',
            '--user-usage', str(f),
            '--date', DISK_CHARGING_TIB_EPOCH.isoformat(),
            '--dry-run',
            '--skip-errors',
        ])
        # Exit 0 (dry-run, nothing written); CESM/gdicker may not resolve in
        # test DB, but the epoch gate is what we're testing. Acceptable
        # outcomes: 0 (clean), or 2 if user resolution fails — either way
        # the EPOCH error must NOT appear.
        assert "DISK_CHARGING_TIB_EPOCH" not in result.output
