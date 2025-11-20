"""
Development authentication bypass for automated testing.

Set environment variable: DISABLE_AUTH=1
Then specify test user: DEV_AUTO_LOGIN_USER=benkirk
"""

import os
from flask import g
from flask_login import login_user
from webui.auth.models import AuthUser
from webui.auth.providers import get_auth_provider


def auto_login_middleware(app, db):
    """
    Auto-login middleware for development/debugging.

    Set environment variables:
        DISABLE_AUTH=1          - Enable auto-login
        DEV_AUTO_LOGIN_USER=benkirk  - Username to auto-login as

    Usage:
        from webui.utils.dev_auth import auto_login_middleware
        auto_login_middleware(app, db)
    """

    @app.before_request
    def before_request():
        """Auto-login configured user if DISABLE_AUTH is set."""
        # Only run if explicitly enabled
        if os.environ.get('DISABLE_AUTH') != '1':
            return

        # Skip if already authenticated
        from flask_login import current_user
        if current_user.is_authenticated:
            return

        # Get configured auto-login user
        auto_login_username = os.environ.get('DEV_AUTO_LOGIN_USER', 'benkirk')

        # Authenticate the user (stub requires non-empty password)
        provider = get_auth_provider('stub', db_session=db.session)
        sam_user = provider.authenticate(auto_login_username, 'dev-password')  # Stub accepts any password

        if sam_user:
            # Get dev role mapping from config
            dev_role_mapping = app.config.get('DEV_ROLE_MAPPING', {})
            auth_user = AuthUser(sam_user, dev_role_mapping=dev_role_mapping)

            # Auto-login without session persistence
            login_user(auth_user, remember=False)

            # Store in g for debugging
            g.auto_login = True
            g.auto_login_user = auto_login_username
        else:
            print(f"WARNING: Could not auto-login user '{auto_login_username}'")
