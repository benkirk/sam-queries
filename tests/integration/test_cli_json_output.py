"""Integration tests for `--format json` on sam-search and sam-admin.

Drive the Click CLIs end-to-end with `CliRunner`, asserting:
  - exit codes match the rich-mode behaviour
  - stdout is valid JSON (parses with `json.loads`)
  - the envelope has a `kind` field plus the documented top-level keys
  - progress bars (UserAbandonedCommand etc.) don't corrupt stdout

The `mock_db_session` pattern matches tests/unit/test_sam_search_cli.py:
patch `cli.cmds.search.Session` (and `cli.cmds.admin.Session`) so the
CLI runs against the SAVEPOINT'd test session.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli as search_cli
from cli.cmds.admin import cli as admin_cli


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_search_session(session):
    with patch('sam.session.create_sam_engine') as mock_eng, \
         patch('cli.cmds.search.Session') as mock_cls:
        mock_eng.return_value = (MagicMock(), None)
        mock_cls.return_value = session
        yield session


@pytest.fixture
def mock_admin_session(session):
    with patch('sam.session.create_sam_engine') as mock_eng, \
         patch('cli.cmds.admin.Session') as mock_cls:
        mock_eng.return_value = (MagicMock(), None)
        mock_cls.return_value = session
        yield session


def _parse_json(output: str) -> dict:
    """`output_json` writes pure JSON to stdout, so the entire output
    must parse — no log-line slicing needed."""
    return json.loads(output)


# ----------------------------------------------------------------------
# User domain
# ----------------------------------------------------------------------

class TestUserJSON:

    def test_user_exact_envelope(self, runner, mock_search_session, multi_project_user):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'user', multi_project_user.username]
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'user'
        assert data['username'] == multi_project_user.username
        # JSON always includes sub-builders regardless of --verbose
        assert 'detail' in data
        assert 'projects' in data
        assert 'emails' in data

    def test_user_not_found_envelope(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'user', 'nonexistent_user_999']
        )
        assert result.exit_code == 1
        data = _parse_json(result.output)
        assert data == {
            'kind': 'user',
            'error': 'not_found',
            'username': 'nonexistent_user_999',
        }

    def test_user_pattern_search_envelope(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'user', '--search', 'ben']
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'user_search_results'
        assert data['pattern'] == 'ben'
        assert data['count'] >= 1
        assert any(u['username'] == 'benkirk' for u in data['users'])

    def test_user_pattern_search_not_found(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'user', '--search',
                         'nonexistent_pattern_xyz']
        )
        assert result.exit_code == 1
        data = _parse_json(result.output)
        assert data['kind'] == 'user_search_results'
        assert data['count'] == 0

    def test_user_progress_bar_disabled_in_json(self, runner, mock_search_session):
        """`UserWithProjectsCommand` uses `track()`. Stdout must remain
        pure JSON when --format json is set."""
        result = runner.invoke(
            search_cli, ['--format', 'json', 'user', '--has-active-project']
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'users_with_active_projects'


# ----------------------------------------------------------------------
# Project domain
# ----------------------------------------------------------------------

class TestProjectJSON:

    def test_project_exact_envelope(self, runner, mock_search_session, active_project):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'project', active_project.projcode]
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'project'
        assert data['projcode'] == active_project.projcode
        for required in ('detail', 'allocations', 'rolling', 'tree', 'users'):
            assert required in data, f"missing top-level key: {required}"

    def test_project_not_found_envelope(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'project', 'NONEXISTENT001']
        )
        assert result.exit_code == 1
        data = _parse_json(result.output)
        assert data == {
            'kind': 'project',
            'error': 'not_found',
            'projcode': 'NONEXISTENT001',
        }

    def test_project_pattern_envelope(self, runner, mock_search_session, active_project):
        pattern = f'{active_project.projcode[:4]}%'
        result = runner.invoke(
            search_cli, ['--format', 'json', 'project', '--search', pattern]
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'project_search_results'
        assert data['count'] >= 1
        assert any(p['projcode'] == active_project.projcode for p in data['projects'])

    def test_project_tree_marks_current(self, runner, mock_search_session, active_project):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'project', active_project.projcode]
        )
        data = _parse_json(result.output)
        # Walk tree and verify exactly one node is is_current=True
        seen = []

        def walk(n):
            if n['is_current']:
                seen.append(n['projcode'])
            for c in n['children']:
                walk(c)
        walk(data['tree'])
        assert seen == [active_project.projcode]

    def test_upcoming_expirations_envelope(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli, ['--format', 'json', 'project', '--upcoming-expirations']
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'expiring_projects'
        assert 'rows' in data
        assert isinstance(data['rows'], list)


# ----------------------------------------------------------------------
# Allocations & Accounting
# ----------------------------------------------------------------------

class TestAllocationsJSON:

    def test_allocations_summary_envelope(self, runner, mock_search_session):
        result = runner.invoke(
            search_cli,
            ['--format', 'json', 'allocations', '--total-resources',
             '--total-facilities', '--total-types', '--total-projects']
        )
        assert result.exit_code == 0
        data = _parse_json(result.output)
        assert data['kind'] == 'allocation_summary'
        assert 'rows' in data
        assert 'count' in data


class TestAccountingJSON:

    def test_accounting_envelope_or_empty(self, runner, mock_search_session):
        """Accounting may have zero rows on the test snapshot — both 0
        rows (exit 1) and N rows (exit 0) emit a valid envelope."""
        result = runner.invoke(
            search_cli, ['--format', 'json', 'accounting', '--last', '7d']
        )
        assert result.exit_code in (0, 1)
        data = _parse_json(result.output)
        assert data['kind'] == 'comp_charge_summary'
        assert 'rows' in data
        assert 'count' in data


# ----------------------------------------------------------------------
# Admin CLI
# ----------------------------------------------------------------------

class TestAdminJSON:

    def test_admin_user_envelope(self, runner, mock_admin_session, multi_project_user):
        result = runner.invoke(
            admin_cli, ['--format', 'json', 'user', multi_project_user.username]
        )
        # UserAdminCommand prints rich validation chatter even in JSON
        # mode (the validate path is admin-only Rich).  Just confirm
        # the search-result envelope precedes any extra output.
        assert result.exit_code == 0
        # First {...} block must be valid user envelope
        first_brace = result.output.index('{')
        # Find the closing brace of the JSON envelope by parsing
        # incrementally; output_json emits exactly one indented object.
        decoder = json.JSONDecoder()
        data, _end = decoder.raw_decode(result.output[first_brace:])
        assert data['kind'] == 'user'
        assert data['username'] == multi_project_user.username

    def test_admin_project_json_with_notify_rejected(self, runner, mock_admin_session):
        result = runner.invoke(
            admin_cli,
            ['--format', 'json', 'project', '--upcoming-expirations', '--notify']
        )
        assert result.exit_code == 2
        data = _parse_json(result.output)
        assert data['error'] == 'json_unsupported_for_writes'

    def test_admin_project_json_with_deactivate_rejected(self, runner, mock_admin_session):
        result = runner.invoke(
            admin_cli,
            ['--format', 'json', 'project', '--recent-expirations', '--deactivate']
        )
        assert result.exit_code == 2
        data = _parse_json(result.output)
        assert data['error'] == 'json_unsupported_for_writes'
