"""
Security response header tests (PRODUCTION_IMPROVEMENTS item 3).

Baseline headers ride every response via webapp.utils.security_headers;
HSTS only when SESSION_COOKIE_SECURE is set (ProductionConfig).
"""

from flask import Flask

from webapp.utils.security_headers import init_security_headers


class TestHeadersOnApp:
    """Against the real session app fixture (TestingConfig, no HTTPS)."""

    def test_baseline_headers_present(self, client):
        resp = client.get('/auth/login')
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'
        assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'
        assert resp.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'

    def test_headers_on_api_routes_too(self, client):
        resp = client.get('/api/v1/health/live')
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'

    def test_hsts_absent_when_not_secure(self, client):
        """TestingConfig has SESSION_COOKIE_SECURE=False → no HSTS."""
        resp = client.get('/auth/login')
        assert 'Strict-Transport-Security' not in resp.headers


class TestHSTSGate:
    """HSTS gating, isolated on a bare Flask app (no second create_app)."""

    def _app(self, secure):
        app = Flask(__name__)
        app.config['SESSION_COOKIE_SECURE'] = secure

        @app.route('/ping')
        def ping():
            return 'pong'

        init_security_headers(app)
        return app

    def test_hsts_present_when_secure(self):
        resp = self._app(secure=True).test_client().get('/ping')
        assert resp.headers['Strict-Transport-Security'] == \
            'max-age=31536000; includeSubDomains'

    def test_hsts_absent_when_insecure(self):
        resp = self._app(secure=False).test_client().get('/ping')
        assert 'Strict-Transport-Security' not in resp.headers

    def test_route_can_override(self):
        app = self._app(secure=False)

        @app.route('/framed')
        def framed():
            from flask import make_response
            r = make_response('ok')
            r.headers['X-Frame-Options'] = 'DENY'
            return r

        resp = app.test_client().get('/framed')
        assert resp.headers['X-Frame-Options'] == 'DENY'
