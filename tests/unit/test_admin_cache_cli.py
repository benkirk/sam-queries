"""
CliRunner tests for `sam-admin cache --refresh`.

The command is a thin HTTP client for POST /api/v1/admin/cache/refresh —
the caches live in the webapp worker (and shared Redis), not the DB, so it
hits the live endpoint. These tests mock `requests.post` and assert the URL,
auth, params, and exit-code handling.
"""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cli.cmds.admin import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_db_session(session):
    """Neutralize the CLI group's DB connect (cache command doesn't use it)."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.core.context.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


@pytest.fixture
def api_creds(monkeypatch):
    monkeypatch.setenv('SAM_API_USER', 'collector')
    monkeypatch.setenv('SAM_API_PASS', 'secret')
    monkeypatch.setenv('SAM_API_BASE', 'http://webapp.test')


def _ok_response(cleared):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'status': 'ok', 'cleared': cleared}
    return resp


class TestCacheCli:

    def test_refresh_all_posts_to_endpoint(self, runner, mock_db_session, api_creds):
        with patch('requests.post',
                   return_value=_ok_response({'flask': 5, 'chart': 2,
                                              'usage': 0, 'scans': 1})) as mock_post:
            result = runner.invoke(cli, ['cache', '--refresh'])
        assert result.exit_code == 0, result.output
        args, kwargs = mock_post.call_args
        assert args[0] == 'http://webapp.test/api/v1/admin/cache/refresh'
        assert kwargs['auth'] == ('collector', 'secret')
        assert kwargs['params'] == {}

    def test_refresh_category_passes_param(self, runner, mock_db_session, api_creds):
        with patch('requests.post',
                   return_value=_ok_response({'chart': 2})) as mock_post:
            result = runner.invoke(cli, ['cache', '--refresh', '--category', 'chart'])
        assert result.exit_code == 0, result.output
        assert mock_post.call_args.kwargs['params'] == {'category': 'chart'}

    def test_json_output(self, runner, mock_db_session, api_creds):
        with patch('requests.post', return_value=_ok_response({'flask': 5})):
            result = runner.invoke(cli, ['--format', 'json', 'cache', '--refresh'])
        assert result.exit_code == 0, result.output
        assert '"cleared"' in result.output
        assert '"flask": 5' in result.output

    def test_missing_action_errors(self, runner, mock_db_session, api_creds):
        result = runner.invoke(cli, ['cache'])
        assert result.exit_code == 2
        assert '--refresh' in result.output

    def test_missing_credentials_errors(self, runner, mock_db_session, monkeypatch):
        monkeypatch.delenv('SAM_API_USER', raising=False)
        monkeypatch.delenv('SAM_API_PASS', raising=False)
        result = runner.invoke(cli, ['cache', '--refresh'])
        assert result.exit_code == 2
        assert 'SAM_API_USER' in result.output

    def test_http_error_reported(self, runner, mock_db_session, api_creds):
        bad = MagicMock()
        bad.status_code = 401
        bad.text = 'Unauthorized'
        with patch('requests.post', return_value=bad):
            result = runner.invoke(cli, ['cache', '--refresh'])
        assert result.exit_code == 2
        assert '401' in result.output

    def test_unreachable_webapp_reported(self, runner, mock_db_session, api_creds):
        import requests
        with patch('requests.post',
                   side_effect=requests.RequestException('connection refused')):
            result = runner.invoke(cli, ['cache', '--refresh'])
        assert result.exit_code == 2
        assert 'could not reach' in result.output
