"""Webapp infrastructure smoke tests — Phase 4a acceptance gate.

These two tests prove that the Phase 4 fixture scaffolding works end-to-end:
  1. `app` / `client` — Flask app is constructable via
     `create_app(config_overrides={'SQLALCHEMY_DATABASE_URI': test_db_url})`
     and responds to unauthenticated GETs.
  2. `auth_client` — Flask-Login session-cookie login as `benkirk` lets
     authenticated routes through (benkirk gets the full Permission set
     via `USER_PERMISSION_OVERRIDES['benkirk']` in `webapp.utils.rbac`).

If either fails, no Phase 4 port can proceed. If both pass, the rest of
Phase 4 is unblocked and the fixture patterns in `new_tests/conftest.py`
can be relied on.
"""
import pytest

pytestmark = pytest.mark.smoke


def test_liveness_endpoint_returns_200(client):
    """Unauthenticated GET /api/v1/health/live succeeds.

    The `/live` endpoint is a Kubernetes liveness probe: pure in-process
    check, no DB pings, no `@login_required`. This is the cleanest
    smoke test for the `app` + `client` fixtures — if `create_app(
    config_overrides=...)` built a valid Flask application and the
    `api_health_bp` blueprint is wired up, this returns 200.

    We deliberately don't hit `/api/v1/health/` (the full health check) —
    that pings every configured `SQLALCHEMY_BINDS` entry, and we only
    override the primary `SQLALCHEMY_DATABASE_URI` in the `app` fixture,
    leaving the `system_status` bind pointing at its default
    (unreachable from the test environment). Status-bind-specific
    tests are Phase 4f concerns.
    """
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "alive"


def test_auth_client_is_logged_in_as_benkirk(auth_client):
    """`auth_client` reaches a `@login_required` route as benkirk.

    `/api/v1/users/me` returns the currently-authenticated user. A 200
    response with `username == 'benkirk'` confirms that Flask-Login picked
    up the session cookie set by the `auth_client` fixture AND that the
    load_user callback resolved the user against the mysql-test DB.
    """
    resp = auth_client.get("/api/v1/users/me")
    assert resp.status_code == 200
    assert resp.get_json()["username"] == "benkirk"
