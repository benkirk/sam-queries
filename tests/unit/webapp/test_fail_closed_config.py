"""
Fail-closed production configuration tests (PRODUCTION_IMPROVEMENTS item 1).

ProductionConfig.validate() must reject any configuration that would let a
public deployment fall back to permissive dev behavior:
  - AUTH_PROVIDER != 'oidc' (StubAuthProvider accepts any password) [PR295 P0-2]
  - DISABLE_AUTH=1 (dev auto-login bypass)                          [PR295 P0-1]

The auto-login middleware must additionally refuse to register its
before_request hook unless the loaded config class opts in
(DEV_AUTO_LOGIN_ALLOWED, True only in DevelopmentConfig) — this covers app
builds that pass config_overrides and therefore skip validate().

Follows the env-patch + class-attribute-flip pattern of
TestProductionConfigOIDCValidation in test_oidc_auth.py (AUTH_PROVIDER is
frozen onto the class at import time, so tests flip it in try/finally).
"""

import logging
import os
from unittest.mock import patch

import pytest
from flask import Flask


VALID_OIDC_ENV = {
    'FLASK_SECRET_KEY': 'a' * 64,
    'AUTH_PROVIDER': 'oidc',
    'OIDC_CLIENT_ID': 'test-client-id',
    'OIDC_CLIENT_SECRET': 'test-client-secret',
    'OIDC_ISSUER': 'https://login.microsoftonline.com/test-tenant/v2.0',
    'DISABLE_AUTH': '0',
}


def _validate_with_provider(provider, env):
    """Run ProductionConfig.validate() with AUTH_PROVIDER class attr flipped."""
    from webapp.config import ProductionConfig

    with patch.dict(os.environ, env, clear=False):
        original = ProductionConfig.AUTH_PROVIDER
        ProductionConfig.AUTH_PROVIDER = provider
        try:
            ProductionConfig.validate()
        finally:
            ProductionConfig.AUTH_PROVIDER = original


class TestProductionValidateFailsClosed:

    def test_rejects_stub_provider(self):
        env = dict(VALID_OIDC_ENV, AUTH_PROVIDER='stub')
        with pytest.raises(EnvironmentError, match="requires AUTH_PROVIDER=oidc"):
            _validate_with_provider('stub', env)

    def test_rejects_ldap_provider(self):
        env = dict(VALID_OIDC_ENV, AUTH_PROVIDER='ldap')
        with pytest.raises(EnvironmentError, match="requires AUTH_PROVIDER=oidc"):
            _validate_with_provider('ldap', env)

    def test_rejects_disable_auth(self):
        env = dict(VALID_OIDC_ENV, DISABLE_AUTH='1')
        with pytest.raises(EnvironmentError, match="DISABLE_AUTH=1"):
            _validate_with_provider('oidc', env)

    def test_accepts_full_oidc_env(self):
        _validate_with_provider('oidc', dict(VALID_OIDC_ENV))

    def test_rejects_oidc_with_missing_vars(self):
        env = dict(VALID_OIDC_ENV, OIDC_CLIENT_SECRET='')
        with pytest.raises(EnvironmentError, match="missing required env vars"):
            _validate_with_provider('oidc', env)


class TestAutoLoginRegistrationGuard:

    def _bare_app(self, allowed):
        app = Flask(__name__)
        app.config['DEV_AUTO_LOGIN_ALLOWED'] = allowed
        return app

    def test_not_registered_when_disallowed(self):
        """Production/Testing-shaped config: hook never registered, even with DISABLE_AUTH=1."""
        from webapp.utils.dev_auth import auto_login_middleware

        app = self._bare_app(allowed=False)
        with patch.dict(os.environ, {'DISABLE_AUTH': '1'}):
            auto_login_middleware(app, None)
        assert app.before_request_funcs == {}

    def test_registered_when_allowed(self):
        """DevelopmentConfig-shaped config: hook registers as before."""
        from webapp.utils.dev_auth import auto_login_middleware

        app = self._bare_app(allowed=True)
        auto_login_middleware(app, None)
        assert len(app.before_request_funcs.get(None, [])) == 1

    def test_default_is_disallowed(self):
        """A config that never mentions the flag fails closed."""
        from webapp.utils.dev_auth import auto_login_middleware

        app = Flask(__name__)
        auto_login_middleware(app, None)
        assert app.before_request_funcs == {}

    def test_config_class_defaults(self):
        from webapp.config import (DevelopmentConfig, ProductionConfig,
                                   SAMWebappConfig, TestingConfig)

        assert SAMWebappConfig.DEV_AUTO_LOGIN_ALLOWED is False
        assert DevelopmentConfig.DEV_AUTO_LOGIN_ALLOWED is True
        assert ProductionConfig.DEV_AUTO_LOGIN_ALLOWED is False
        assert TestingConfig.DEV_AUTO_LOGIN_ALLOWED is False


class TestLimiterStorageWarning:

    def _init_limiter(self, app):
        """Run a fresh Limiting facade's init_app against a bare Flask app."""
        from webapp.limiter import Limiting

        facade = Limiting()
        app.config.setdefault('RATELIMIT_ENABLED', False)
        app.config.setdefault('RATELIMIT_AUTHED', '200 per minute')
        facade.init_app(app)

    def test_warns_on_empty_storage_uri(self, caplog):
        app = Flask(__name__)
        app.config['RATELIMIT_STORAGE_URI'] = ''
        with caplog.at_level(logging.WARNING, logger='webapp.limiter'):
            self._init_limiter(app)
        assert any('memory://' in r.message and 'not set' in r.message
                   for r in caplog.records)

    def test_warns_on_non_redis_storage_uri(self, caplog):
        app = Flask(__name__)
        app.config['RATELIMIT_STORAGE_URI'] = 'mongodb://nope'
        with caplog.at_level(logging.WARNING, logger='webapp.limiter'):
            self._init_limiter(app)
        assert any('not a redis://' in r.message for r in caplog.records)
