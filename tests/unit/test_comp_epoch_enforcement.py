"""Tests for cutover-epoch enforcement on `sam-admin accounting --comp`.

The CLI must refuse to write rows whose ``start_date`` is before
``COMP_CHARGING_EPOCH`` — pre-epoch historical data is not rewritten by
this tool. ``--epoch YYYY-MM-DD`` overrides the hard-coded constant.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.admin import cli
from sam.summaries.comp_summaries import COMP_CHARGING_EPOCH


pytestmark = pytest.mark.unit


class TestCompEpochEnforcement:

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

    def test_pre_epoch_date_refused(self, runner, mock_db_session):
        pre = COMP_CHARGING_EPOCH - timedelta(days=1)
        result = runner.invoke(cli, [
            'accounting', '--comp',
            '--machine', 'derecho',
            '--date', pre.isoformat(),
        ])
        assert result.exit_code == 2
        assert "COMP_CHARGING_EPOCH" in result.output

    def test_at_epoch_date_allowed(self, runner, mock_db_session):
        # At-epoch should NOT emit the epoch-error string. The run may
        # then fail later (plugin missing / no data) — that's fine; we
        # only assert the epoch gate didn't fire.
        result = runner.invoke(cli, [
            'accounting', '--comp',
            '--machine', 'derecho',
            '--date', COMP_CHARGING_EPOCH.isoformat(),
        ])
        assert "COMP_CHARGING_EPOCH" not in result.output

    def test_pre_epoch_date_allowed_with_override(self, runner, mock_db_session):
        pre = COMP_CHARGING_EPOCH - timedelta(days=30)
        result = runner.invoke(cli, [
            'accounting', '--comp',
            '--machine', 'derecho',
            '--date', pre.isoformat(),
            '--epoch', pre.isoformat(),
        ])
        assert "COMP_CHARGING_EPOCH" not in result.output
