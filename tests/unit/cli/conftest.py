"""Fixtures shared across the CLI tests.

Only the byte-identical module-level copies live here; the class-scoped
`runner` / `mock_db_session` variants and the divergent bodies stay in their
own files, where they shadow these.
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_db_session(session):
    """Bind the CLI group to the test's SAVEPOINT session."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.core.context.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


@pytest.fixture(autouse=True)
def status_db_not_configured():
    """Every CLI test starts with no status DB, so none dials the one a dev ``.env`` names.

    Tests that need the ledger re-patch ``create_status_engine`` to the SQLite bind.
    """
    with patch('system_status.session.create_status_engine',
               side_effect=RuntimeError('system_status database connection is not configured')):
        yield
