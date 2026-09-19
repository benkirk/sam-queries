"""Component gallery (dev-only /dev/gallery) — flag gating + render smoke.

The flag mirrors FLASK_ADMIN_ENABLED: ON outside production, OFF in production.
The render test is also the gallery's smoke test — a broken fragment fixture
500s here rather than only in the browser.
"""

import pytest


@pytest.fixture(scope="module")
def gallery_disabled_app(test_db_url, status_db_url):
    """A second create_app with the gallery flag off (mirrors test_admin_killswitch).

    config_overrides land before the gated register_blueprint runs, so the
    conditional sees the override. Module-scoped: create_app is expensive.
    """
    from webapp.run import create_app

    return create_app(config_overrides={
        "SQLALCHEMY_DATABASE_URI": test_db_url,
        "SQLALCHEMY_BINDS": {"system_status": status_db_url},
        "COMPONENT_GALLERY_ENABLED": False,
    })


class TestFlagOn:
    """The session app fixture inherits TestingConfig -> flag defaults ON."""

    def test_flag_defaults_on_outside_production(self, app):
        assert app.config['COMPONENT_GALLERY_ENABLED'] is True

    def test_gallery_renders(self, auth_client):
        resp = auth_client.get('/dev/gallery/')
        assert resp.status_code == 200
        assert b'Component gallery' in resp.data

    @pytest.mark.parametrize('layout', ['desktop', 'tablet', 'mobile'])
    @pytest.mark.parametrize('theme', ['light', 'dark'])
    def test_renders_across_axes(self, auth_client, layout, theme):
        """Every theme x layout state renders without a 500 (the render smoke)."""
        resp = auth_client.get(f'/dev/gallery/?layout={layout}&theme={theme}')
        assert resp.status_code == 200
        # layout-aware macros are on the page in every state
        assert b'ladder-range' in resp.data

    def test_anonymous_redirected_to_login(self, client):
        resp = client.get('/dev/gallery/')
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']


class TestFlagOff:

    def test_route_404s(self, gallery_disabled_app):
        client = gallery_disabled_app.test_client()
        assert client.get('/dev/gallery/').status_code == 404

    def test_blueprint_not_registered(self, gallery_disabled_app):
        assert 'component_gallery' not in gallery_disabled_app.blueprints

    def test_rest_of_app_unaffected(self, gallery_disabled_app):
        client = gallery_disabled_app.test_client()
        assert client.get('/auth/login').status_code == 200


class TestConfigDefaults:

    def test_loaded_class_defaults(self):
        from webapp.config import ProductionConfig, DevelopmentConfig, TestingConfig
        import os
        if 'COMPONENT_GALLERY_ENABLED' not in os.environ:
            assert ProductionConfig.COMPONENT_GALLERY_ENABLED is False
            assert DevelopmentConfig.COMPONENT_GALLERY_ENABLED is True
            assert TestingConfig.COMPONENT_GALLERY_ENABLED is True
