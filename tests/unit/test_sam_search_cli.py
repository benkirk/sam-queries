"""Integration tests for the sam-search CLI tool.

Ported from tests/unit/test_sam_search_cli.py. Transformations:

- Hardcoded `'benkirk'` / `'SCSG0001'` CLI args are replaced by values
  pulled from the `multi_project_user` and `active_project` fixtures at
  runtime. Assertions on CLI output use the same fixture-derived values,
  so the file is self-consistent and survives snapshot refreshes.
- `test_user_search_exact_not_found` / `test_project_search_exact_not_found`
  keep their nonsense identifiers — those don't depend on snapshot data.

The `mock_db_session` fixture patches `cli.cmds.search.Session` so the CLI
uses our SAVEPOINT'd test session instead of opening its own connection.
"""
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli


pytestmark = pytest.mark.unit


class TestSamSearchCli:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        """Patch the CLI's session factory to hand back our SAVEPOINT'd session.

        The CLI does:
            engine, _ = create_sam_engine()
            ctx.session = Session(engine)
        We patch both call sites so the CLI runs against the test DB
        inside the test's savepoint — all queries succeed, any writes
        (none here) would be rolled back at teardown.
        """
        with patch('sam.session.create_sam_engine') as mock_create_engine, \
             patch('cli.cmds.search.Session') as mock_session_cls:
            mock_create_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    # ------------------------------------------------------------------
    # Help output (no DB)
    # ------------------------------------------------------------------

    def test_cli_help(self, runner):
        result = runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert "Search and query the SAM database" in result.output

    def test_cli_help_short(self, runner):
        result = runner.invoke(cli, ['-h'])
        assert result.exit_code == 0
        assert "Search and query the SAM database" in result.output

    def test_user_command_help(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--help'])
        assert result.exit_code == 0
        assert "Search for users" in result.output

    # ------------------------------------------------------------------
    # User search — derive args from multi_project_user fixture
    # ------------------------------------------------------------------

    def test_user_search_exact_found(self, runner, mock_db_session, multi_project_user):
        username = multi_project_user.username
        result = runner.invoke(cli, ['user', username])
        assert result.exit_code == 0
        assert "User Information" in result.output
        assert username in result.output

    def test_user_search_exact_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', 'nonexistent_user_999'])
        assert result.exit_code == 1
        assert "User not found" in result.output

    def test_user_search_pattern_found(self, runner, mock_db_session):
        """Pattern search finds `benkirk`, the guaranteed non-obfuscated test user.

        Two quirks forced this to hardcode `benkirk`:
          (1) cli/user/commands.py:37 calls `pattern.replace('%','').replace('_','')`
              before passing to search_users. So a pattern including the literal
              underscore in `user_<hex>` obfuscated usernames becomes `userhex`
              and matches nothing.
          (2) All regular users in the obfuscated snapshot are `user_<hex>`.
              They are substring-unreachable once `_` is stripped.

        `benkirk` is the deliberately-preserved test account — not obfuscated —
        precisely so tests that need "a specific known username" have a target.
        Use `ben` as the search prefix; the CLI wraps with `%`.
        """
        result = runner.invoke(cli, ['user', '--search', 'ben'])
        assert result.exit_code == 0
        assert "Found" in result.output
        assert 'benkirk' in result.output

    def test_user_search_pattern_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--search', 'nonexistent_pattern_999'])
        assert result.exit_code == 1
        assert "No users found matching" in result.output

    def test_user_with_list_projects(self, runner, mock_db_session, multi_project_user):
        username = multi_project_user.username
        result = runner.invoke(cli, ['user', username, '--list-projects'])
        assert result.exit_code == 0
        assert f"projects for {username}" in result.output

    def test_user_verbose(self, runner, mock_db_session, multi_project_user):
        result = runner.invoke(cli, ['user', multi_project_user.username, '--verbose'])
        assert result.exit_code == 0
        assert "User ID" in result.output

    # ------------------------------------------------------------------
    # Project search — derive args from active_project fixture
    # ------------------------------------------------------------------

    def test_project_search_exact_found(self, runner, mock_db_session, active_project):
        projcode = active_project.projcode
        result = runner.invoke(cli, ['project', projcode])
        assert result.exit_code == 0
        assert "Project Information" in result.output
        assert projcode in result.output

    def test_project_search_exact_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', 'NONEXISTENT001'])
        assert result.exit_code == 1
        assert "Project not found" in result.output

    def test_project_search_pattern(self, runner, mock_db_session, active_project):
        projcode = active_project.projcode
        pattern = f'{projcode[:4]}%'
        result = runner.invoke(cli, ['project', '--search', pattern])
        assert result.exit_code == 0
        assert projcode in result.output

    def test_project_list_users(self, runner, mock_db_session, active_project):
        projcode = active_project.projcode
        result = runner.invoke(cli, ['project', projcode, '--list-users'])
        assert result.exit_code == 0
        assert f"Active users for {projcode}" in result.output

    def test_project_verbose(self, runner, mock_db_session, active_project):
        result = runner.invoke(cli, ['project', active_project.projcode, '--verbose'])
        assert result.exit_code == 0
        assert "Abstract" in result.output

    # ------------------------------------------------------------------
    # Structural queries — don't care which data comes back
    # ------------------------------------------------------------------

    def test_upcoming_expirations(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', '--upcoming-expirations'])
        assert result.exit_code == 0
        assert "allocations expiring" in result.output

    def test_recent_expirations(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', '--recent-expirations'])
        assert result.exit_code == 0
        assert "recently expired projects" in result.output

    def test_abandoned_users(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--abandoned'])
        assert result.exit_code == 0

    def test_users_with_active_project(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--has-active-project'])
        assert result.exit_code == 0
        assert "Found" in result.output

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_missing_args_user(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user'])
        assert result.exit_code == 1
        assert "Error: Please provide exactly one of" in result.output

    def test_missing_args_project(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project'])
        assert result.exit_code == 1
        assert "Error: Please provide exactly one of" in result.output
