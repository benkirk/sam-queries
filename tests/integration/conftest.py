"""
pytest fixtures for integration tests

Provides Flask test client, authentication, and database fixtures for
integration testing across the full stack.
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'python'))

from webui.run import create_app
from sam.core.users import User


@pytest.fixture(scope='session')
def app():
    """
    Create Flask app for testing.

    Uses session scope so the app is created once per test session.
    Configures app for testing mode with CSRF disabled.
    """
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def client(app):
    """
    Create Flask test client.

    Returns an unauthenticated test client. Use this for testing
    endpoints that don't require authentication.
    """
    return app.test_client()


@pytest.fixture
def auth_client(client, session, app):
    """
    Create authenticated test client (logged in as benkirk).

    Simulates a logged-in user by setting Flask-Login session data.
    The user 'benkirk' is used as it exists in the test database.
    """
    with client:
        # Get benkirk user
        user = User.get_by_username(session, 'benkirk')
        assert user is not None, "Test user 'benkirk' not found in database"

        # Simulate login by setting session
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
            sess['_fresh'] = True

        yield client
