"""The request log line names its actor: the API-key name for a Basic-auth
caller, the username for a session, nothing when unauthenticated. Captured
off `app.logger` directly — it does not propagate to the root logger.
"""

import logging

import pytest

pytestmark = pytest.mark.webapp


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def run_lines(app):
    handler = _Lines()
    app.logger.addHandler(handler)
    try:
        yield handler.lines
    finally:
        app.logger.removeHandler(handler)


def _line_for(lines, path):
    matches = [ln for ln in lines if f' {path} → ' in ln]
    assert matches, lines
    return matches[-1]


def test_an_api_key_request_is_attributed_to_the_key(api_key_client, run_lines):
    api_key_client.get('/api/v1/users/benkirk')
    line = _line_for(run_lines, '/api/v1/users/benkirk')
    assert 'who=apikey:collector' in line
    assert line.rstrip().endswith(')') or 'rid=' in line


def test_a_session_request_is_attributed_to_the_user(auth_client, run_lines):
    auth_client.get('/api/v1/users/me')
    assert 'who=user:benkirk' in _line_for(run_lines, '/api/v1/users/me')


def test_an_unauthenticated_request_carries_no_actor(client, run_lines):
    client.get('/api/v1/users/benkirk')
    assert 'who=' not in _line_for(run_lines, '/api/v1/users/benkirk')
