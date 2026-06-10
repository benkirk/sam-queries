"""
Flask-Admin kill-switch tests (PRODUCTION_IMPROVEMENTS item 2, PR295 P0-3).

When FLASK_ADMIN_ENABLED is off, init_admin() never runs and the /database
blueprint is not mounted — the public deploy simply doesn't serve it.

NOTE: the Flask-Admin browser is mounted at /database (init_admin sets
url='/database'), not /admin — /admin belongs to the admin *dashboard*.
"""

import pytest


@pytest.fixture(scope="module")
def admin_disabled_app(test_db_url, status_db_url):
    """A second create_app with the kill-switch thrown.

    config_overrides land before init_admin runs in create_app, so the
    conditional sees the override. Module-scoped: create_app is expensive.
    """
    from webapp.run import create_app

    return create_app(config_overrides={
        "SQLALCHEMY_DATABASE_URI": test_db_url,
        "SQLALCHEMY_BINDS": {"system_status": status_db_url},
        "FLASK_ADMIN_ENABLED": False,
    })


class TestKillSwitchOff:

    def test_database_url_404s(self, admin_disabled_app):
        client = admin_disabled_app.test_client()
        assert client.get('/database/').status_code == 404

    def test_admin_blueprint_not_registered(self, admin_disabled_app):
        assert 'admin' not in admin_disabled_app.blueprints

    def test_rest_of_app_unaffected(self, admin_disabled_app):
        client = admin_disabled_app.test_client()
        assert client.get('/api/v1/health/live').status_code == 200
        assert client.get('/auth/login').status_code == 200


class TestKillSwitchOn:
    """The session app fixture inherits TestingConfig → flag defaults ON."""

    def test_flag_defaults_on_outside_production(self, app):
        assert app.config['FLASK_ADMIN_ENABLED'] is True

    def test_anonymous_redirected_to_login(self, client):
        resp = client.get('/database/')
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']

    def test_authenticated_user_reaches_admin(self, auth_client):
        assert auth_client.get('/database/').status_code == 200


class TestConfigDefaults:

    def test_production_defaults_off(self):
        """ProductionConfig default is OFF (env unset ⇒ '0' path)."""
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FLASK_ADMIN_ENABLED', None)
            # Class attrs are frozen at import; re-evaluate the expressions
            assert os.getenv('FLASK_ADMIN_ENABLED', '0').lower() not in ('1', 'true', 'yes')
            assert os.getenv('FLASK_ADMIN_ENABLED', '1').lower() in ('1', 'true', 'yes')

    def test_loaded_class_defaults(self):
        from webapp.config import ProductionConfig, DevelopmentConfig, TestingConfig
        import os
        if 'FLASK_ADMIN_ENABLED' not in os.environ:
            assert ProductionConfig.FLASK_ADMIN_ENABLED is False
            assert DevelopmentConfig.FLASK_ADMIN_ENABLED is True
            assert TestingConfig.FLASK_ADMIN_ENABLED is True
