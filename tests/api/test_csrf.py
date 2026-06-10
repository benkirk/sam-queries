"""
CSRF protection tests (PRODUCTION_IMPROVEMENTS item 3, commit 4).

TestingConfig keeps WTF_CSRF_ENABLED=False so the rest of the suite is
untouched; flask-wtf reads the flag per-request, so these tests opt in at
runtime via the `csrf_enabled` fixture.

Token plumbing under test:
  - HTMX mutations inherit the token from <body hx-headers='{"X-CSRFToken"...}'>
    in dashboards/base.html (asserted via raw header injection here).
  - Plain forms (login, impersonate) embed a hidden csrf_token input.
  - Basic-auth M2M routes (@api_key_required status ingestion,
    @login_or_token_required cache refreshes) are @csrf.exempt.
"""

import re

import pytest


@pytest.fixture
def csrf_enabled(app):
    """Flip CSRF enforcement on for one test; flask-wtf checks per-request."""
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        yield
    finally:
        app.config['WTF_CSRF_ENABLED'] = False


def _get_csrf_token(authed_client):
    """Harvest a session-bound token from the dashboard <meta> tag."""
    html = authed_client.get('/user/').get_data(as_text=True)
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf-token meta tag missing from dashboards/base.html"
    return m.group(1)


class TestSessionRoutesProtected:

    def test_htmx_post_without_token_400(self, csrf_enabled, auth_client):
        resp = auth_client.post('/project-members/SCSG0001/add',
                                data={'username': 'benkirk'})
        assert resp.status_code == 400

    def test_htmx_400_is_friendly_fragment(self, csrf_enabled, auth_client):
        resp = auth_client.post('/project-members/SCSG0001/add',
                                data={'username': 'benkirk'},
                                headers={'HX-Request': 'true'})
        assert resp.status_code == 400
        assert 'alert-danger' in resp.get_data(as_text=True)

    def test_htmx_post_with_header_token_passes_csrf(self, csrf_enabled, auth_client):
        token = _get_csrf_token(auth_client)
        resp = auth_client.post('/project-members/SCSG0001/add',
                                data={'username': 'benkirk'},
                                headers={'X-CSRFToken': token,
                                         'HX-Request': 'true'})
        # benkirk is already a member, so the route re-renders the form with
        # a validation error — the point is it got PAST the CSRF gate.
        assert resp.status_code != 400 or \
            'alert-danger' not in resp.get_data(as_text=True)

    def test_session_api_put_without_token_400_json(self, csrf_enabled, auth_client):
        resp = auth_client.put('/api/v1/projects/SCSG0001/admin',
                               json={'username': 'benkirk'})
        assert resp.status_code == 400
        assert 'CSRF' in resp.get_json()['error']

    def test_get_routes_unaffected(self, csrf_enabled, auth_client):
        assert auth_client.get('/user/').status_code == 200


class TestLoginForm:

    def test_login_form_includes_token(self, csrf_enabled, client):
        html = client.get('/auth/login').get_data(as_text=True)
        assert 'name="csrf_token"' in html

    def test_login_post_without_token_400(self, csrf_enabled, client):
        resp = client.post('/auth/login',
                           data={'username': 'benkirk', 'password': 'x'})
        assert resp.status_code == 400

    def test_login_post_with_token_passes(self, csrf_enabled, client):
        html = client.get('/auth/login').get_data(as_text=True)
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        assert m, "hidden csrf_token input missing from login form"
        resp = client.post('/auth/login',
                           data={'username': 'benkirk', 'password': 'x',
                                 'csrf_token': m.group(1)})
        assert resp.status_code in (200, 302)   # stub login proceeds


class TestM2MExemptions:

    def test_status_post_basic_auth_unaffected(self, csrf_enabled, api_key_client):
        """Collector ingestion carries no cookies/token; must not 400 on CSRF."""
        resp = api_key_client.post('/api/v1/status/jupyterhub',
                                   json={'total_users': 1},
                                   content_type='application/json')
        assert resp.status_code != 400 or \
            'CSRF' not in resp.get_data(as_text=True)

    def test_refresh_post_token_auth_exempt(self, csrf_enabled, api_key_client):
        resp = api_key_client.post('/api/v1/directory_access/refresh')
        assert resp.status_code == 200
