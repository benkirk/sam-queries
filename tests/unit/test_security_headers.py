"""
Security response header tests (PRODUCTION_IMPROVEMENTS item 3 + CSP).

Baseline headers ride every response via webapp.utils.security_headers;
HSTS only when SESSION_COOKIE_SECURE is set (ProductionConfig); the CSP
header (generated from the vendor registry — see test_csp.py for policy
content) follows CSP_MODE.
"""

import pytest
from flask import Flask

from webapp.utils.security_headers import init_security_headers


class TestHeadersOnApp:
    """Against the real session app fixture (TestingConfig, no HTTPS)."""

    def test_baseline_headers_present(self, client):
        resp = client.get('/auth/login')
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'
        assert resp.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'

    def test_headers_on_api_routes_too(self, client):
        resp = client.get('/api/v1/health/live')
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'

    def test_hsts_absent_when_not_secure(self, client):
        """TestingConfig has SESSION_COOKIE_SECURE=False → no HSTS."""
        resp = client.get('/auth/login')
        assert 'Strict-Transport-Security' not in resp.headers

    def test_csp_enforced_by_default(self, client):
        """Code default is CSP_MODE=enforce: the enforcing header rides
        every response and frame-ancestors supersedes X-Frame-Options."""
        resp = client.get('/auth/login')
        policy = resp.headers['Content-Security-Policy']
        assert "script-src 'self'" in policy
        assert "frame-ancestors 'self'" in policy
        assert 'Content-Security-Policy-Report-Only' not in resp.headers
        assert 'X-Frame-Options' not in resp.headers


class TestCSPModes:
    """CSP_MODE gating, isolated on a bare Flask app."""

    def _app(self, mode):
        app = Flask(__name__)
        app.config['CSP_MODE'] = mode

        @app.route('/ping')
        def ping():
            return 'pong'

        @app.route('/database/table/foo')
        def admin_table():
            return 'flask-admin stand-in'

        init_security_headers(app)
        return app

    def test_off(self):
        resp = self._app('off').test_client().get('/ping')
        assert 'Content-Security-Policy' not in resp.headers
        assert 'Content-Security-Policy-Report-Only' not in resp.headers
        assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'

    def test_report_only(self):
        resp = self._app('report-only').test_client().get('/ping')
        assert 'Content-Security-Policy' not in resp.headers
        policy = resp.headers['Content-Security-Policy-Report-Only']
        assert "default-src 'self'" in policy
        # frame-ancestors is ignored in Report-Only → XFO must survive
        assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'

    def test_enforce(self):
        resp = self._app('enforce').test_client().get('/ping')
        policy = resp.headers['Content-Security-Policy']
        assert "frame-ancestors 'self'" in policy
        assert 'Content-Security-Policy-Report-Only' not in resp.headers
        # frame-ancestors supersedes XFO under enforcement
        assert 'X-Frame-Options' not in resp.headers

    @pytest.mark.parametrize('mode', ['report-only', 'enforce'])
    def test_flask_admin_path_carveout(self, mode):
        resp = self._app(mode).test_client().get('/database/table/foo')
        assert 'Content-Security-Policy' not in resp.headers
        assert 'Content-Security-Policy-Report-Only' not in resp.headers

    def test_unknown_mode_fails_loud(self):
        with pytest.raises(ValueError, match='CSP_MODE'):
            self._app('enforcing-ish')


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
