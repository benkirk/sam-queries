"""
Tests for the api_key_required decorator.

Covers:
  - No Authorization header → 401
  - Malformed / non-Basic Authorization header → 401
  - Unknown username → 401
  - Wrong password (correct username) → 401
  - Correct credentials → request proceeds (payload validation error, not auth error)
  - WWW-Authenticate header present on 401 responses
  - g.api_key_user is set on successful auth
"""

import base64
import pytest
import bcrypt
from flask import g


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basic(username: str, password: str) -> str:
    """Return an 'Authorization: Basic ...' header value."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_app(app):
    """
    App fixture with a known API_KEYS config for auth decorator tests.
    Uses rounds=4 for fast hashing in tests. Saves and restores the
    original API_KEYS so the mutation doesn't leak into other tests that
    share the session-scoped `app`.
    """
    original = app.config.get("API_KEYS")
    good_hash = bcrypt.hashpw(b"good-password", bcrypt.gensalt(rounds=4)).decode()
    app.config["API_KEYS"] = {"testuser": good_hash}
    try:
        yield app
    finally:
        app.config["API_KEYS"] = original


@pytest.fixture
def api_test_client(api_app):
    """Unauthenticated test client against the api_app fixture."""
    return api_app.test_client()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestApiKeyRequired:
    """Tests for the @api_key_required decorator via the status POST routes."""

    # The Derecho POST route is a convenient target: it requires api_key_required
    # and returns 400 (bad payload) when auth succeeds but data is missing.
    TARGET = "/api/v1/status/derecho"

    def test_no_auth_header_returns_401(self, api_test_client):
        """Request with no Authorization header is rejected."""
        resp = api_test_client.post(self.TARGET, json={})
        assert resp.status_code == 401

    def test_401_includes_www_authenticate_header(self, api_test_client):
        """401 response must include WWW-Authenticate to follow RFC 7235."""
        resp = api_test_client.post(self.TARGET, json={})
        assert "WWW-Authenticate" in resp.headers
        assert 'Basic realm="SAM API"' in resp.headers["WWW-Authenticate"]

    def test_non_basic_scheme_returns_401(self, api_test_client):
        """Bearer / Token schemes are not accepted."""
        resp = api_test_client.post(
            self.TARGET,
            json={},
            headers={"Authorization": "Bearer some-token"},
        )
        assert resp.status_code == 401

    def test_unknown_username_returns_401(self, api_test_client):
        """A username not in API_KEYS is rejected even with any password."""
        resp = api_test_client.post(
            self.TARGET,
            json={},
            headers={"Authorization": _basic("nobody", "good-password")},
        )
        assert resp.status_code == 401

    def test_wrong_password_returns_401(self, api_test_client):
        """Correct username but wrong password is rejected."""
        resp = api_test_client.post(
            self.TARGET,
            json={},
            headers={"Authorization": _basic("testuser", "wrong-password")},
        )
        assert resp.status_code == 401

    def test_correct_credentials_pass_auth(self, api_test_client):
        """
        Valid credentials let the request reach the view.
        Empty payload → 400 (bad data), not 401 (auth failure).
        """
        resp = api_test_client.post(
            self.TARGET,
            json={},
            headers={"Authorization": _basic("testuser", "good-password")},
        )
        # Auth passed — schema/payload validation produces 400 or 500, never 401
        assert resp.status_code != 401

    def test_401_body_is_json(self, api_test_client):
        """Error responses are JSON so API clients can parse them."""
        resp = api_test_client.post(self.TARGET, json={})
        data = resp.get_json()
        assert data is not None
        assert "error" in data

    def test_api_key_user_stored_in_g(self, api_app):
        """
        On successful auth, g.api_key_user is set to the authenticated username.
        Verified by calling the decorator directly inside a test_request_context.
        """
        from webapp.utils.api_auth import api_key_required

        captured = {}

        @api_key_required
        def dummy_view():
            captured["api_key_user"] = g.api_key_user
            return "ok", 200

        with api_app.test_request_context(
            "/",
            method="POST",
            headers={"Authorization": _basic("testuser", "good-password")},
        ):
            result = dummy_view()
            assert captured.get("api_key_user") == "testuser"

    # ------------------------------------------------------------------
    # Additional routes — smoke test auth on all POST status endpoints
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("route", [
        "/api/v1/status/derecho",
        "/api/v1/status/casper",
        "/api/v1/status/jupyterhub",
        "/api/v1/status/outage",
    ])
    def test_all_post_routes_reject_missing_auth(self, api_test_client, route):
        """Every status ingest route requires api_key_required."""
        resp = api_test_client.post(route, json={})
        assert resp.status_code == 401

    @pytest.mark.parametrize("route", [
        "/api/v1/status/derecho",
        "/api/v1/status/casper",
        "/api/v1/status/jupyterhub",
        "/api/v1/status/outage",
    ])
    def test_all_post_routes_accept_valid_auth(self, api_test_client, route):
        """Every status ingest route accepts valid api key credentials."""
        resp = api_test_client.post(
            route,
            json={},
            headers={"Authorization": _basic("testuser", "good-password")},
        )
        assert resp.status_code != 401
