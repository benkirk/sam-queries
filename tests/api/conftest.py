"""
pytest fixtures for API endpoint tests

Flask app, client, and auth_client fixtures are defined in the root
tests/conftest.py file and are available to all test modules.
"""

import base64
import pytest


@pytest.fixture
def api_key_client(client, session, app):
    """
    Test client authenticated via HTTP Basic Auth API key.

    Wraps the Flask test client to automatically include the
    'Authorization: Basic ...' header on every request, using the
    test API key defined in TestingConfig.API_KEYS.

    Also maintains a Flask-Login session (as benkirk/admin) so that
    GET routes decorated with @login_required still work in the same
    test class.
    """
    from sam.core.users import User

    # Inject a fast (rounds=4) test API key directly into the app config.
    # This avoids depending on which config class is active at test time.
    import bcrypt
    test_key = 'test-api-key'
    test_hash = bcrypt.hashpw(test_key.encode(), bcrypt.gensalt(rounds=4)).decode()
    app.config['API_KEYS'] = {'collector': test_hash}

    credentials = base64.b64encode(f'collector:{test_key}'.encode()).decode('ascii')
    auth_header = {'Authorization': f'Basic {credentials}'}

    # Also set up a session so @login_required GET routes work
    with client:
        user = User.get_by_username(session, 'benkirk')
        assert user is not None, "Test user 'benkirk' not found in database"

        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
            sess['_fresh'] = True

        # Wrap client methods to inject the auth header automatically
        original_post = client.post
        original_get  = client.get
        original_patch = client.patch
        original_delete = client.delete

        def post_with_auth(path, **kwargs):
            kwargs.setdefault('headers', {}).update(auth_header)
            return original_post(path, **kwargs)

        def get_with_auth(path, **kwargs):
            kwargs.setdefault('headers', {}).update(auth_header)
            return original_get(path, **kwargs)

        def patch_with_auth(path, **kwargs):
            kwargs.setdefault('headers', {}).update(auth_header)
            return original_patch(path, **kwargs)

        def delete_with_auth(path, **kwargs):
            kwargs.setdefault('headers', {}).update(auth_header)
            return original_delete(path, **kwargs)

        client.post   = post_with_auth
        client.get    = get_with_auth
        client.patch  = patch_with_auth
        client.delete = delete_with_auth

        yield client
