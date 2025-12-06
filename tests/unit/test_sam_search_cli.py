import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from sam_search_cli import cli
from sam import User, Project

class TestSamSearchCli:
    
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        # We need to patch the session creation in the CLI
        # The CLI does:
        # engine, _ = create_sam_engine()
        # ctx.session = Session(engine)
        
        # We'll patch create_sam_engine in sam.session where it is defined
        # And patch Session in sam_search_cli where it is imported
        
        with patch('sam.session.create_sam_engine') as mock_create_engine, \
             patch('sam_search_cli.Session') as mock_session_cls:
            
            mock_create_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    def test_cli_help(self, runner):
        result = runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert "Search and query the SAM database" in result.output

    def test_user_command_help(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--help'])
        assert result.exit_code == 0
        assert "Search for users" in result.output

    def test_user_search_exact_found(self, runner, mock_db_session):
        # Assuming 'benkirk' exists in the test DB as per conftest.py
        result = runner.invoke(cli, ['user', 'benkirk'])
        assert result.exit_code == 0
        assert "User Information" in result.output
        assert "benkirk" in result.output

    def test_user_search_exact_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', 'nonexistent_user_999'])
        assert result.exit_code == 1
        assert "User not found" in result.output

    def test_user_search_pattern_found(self, runner, mock_db_session):
        # Assuming 'ben%' will match 'benkirk'
        result = runner.invoke(cli, ['user', '--search', 'ben%'])
        assert result.exit_code == 0
        assert "Found" in result.output
        assert "benkirk" in result.output

    def test_user_search_pattern_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--search', 'nonexistent_pattern_999'])
        assert result.exit_code == 1
        assert "No users found matching" in result.output

    def test_user_with_list_projects(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', 'benkirk', '--list-projects'])
        assert result.exit_code == 0
        assert "projects for benkirk" in result.output

    def test_user_verbose(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', 'benkirk', '--verbose'])
        assert result.exit_code == 0
        assert "User ID" in result.output
        # Verbose should show academic status if available, etc.
        
    def test_project_search_exact_found(self, runner, mock_db_session):
        # Assuming 'SCSG0001' exists in the test DB
        result = runner.invoke(cli, ['project', 'SCSG0001'])
        assert result.exit_code == 0
        assert "Project Information" in result.output
        assert "SCSG0001" in result.output

    def test_project_search_exact_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', 'NONEXISTENT001'])
        assert result.exit_code == 1
        assert "Project not found" in result.output

    def test_project_search_pattern(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', '--search', 'SCSG%'])
        assert result.exit_code == 0
        assert "SCSG0001" in result.output

    def test_project_list_users(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', 'SCSG0001', '--list-users'])
        assert result.exit_code == 0
        assert "Active users for SCSG0001" in result.output

    def test_project_verbose(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', 'SCSG0001', '--verbose'])
        assert result.exit_code == 0
        assert "Abstract" in result.output

    def test_upcoming_expirations(self, runner, mock_db_session):
        # This might return nothing, but it should run successfully
        result = runner.invoke(cli, ['project', '--upcoming-expirations'])
        assert result.exit_code == 0
        # Check for header
        assert "allocations expiring" in result.output

    def test_recent_expirations(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project', '--recent-expirations'])
        assert result.exit_code == 0
        assert "recently expired projects" in result.output

    def test_abandoned_users(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--abandoned'])
        assert result.exit_code == 0
        # Output check depends on data, but ensuring no crash

    def test_users_with_active_project(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--has-active-project'])
        assert result.exit_code == 0
        assert "Found" in result.output

    def test_missing_args_user(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user'])
        assert result.exit_code == 1
        assert "Error: Please provide exactly one of" in result.output

    def test_missing_args_project(self, runner, mock_db_session):
        result = runner.invoke(cli, ['project'])
        assert result.exit_code == 1
        assert "Error: Please provide exactly one of" in result.output
