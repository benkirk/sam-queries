"""End-to-end rate-limit enforcement tests.

TestingConfig keeps `RATELIMIT_ENABLED=False` so xdist workers don't trip
global limits, so each test in this module flips the singleton facade
on for the duration of the test, overrides specific tier strings to
small numbers, and clears any per-worker memory:// state between tests.

We exercise:
- per-IP `/auth/login` POST limit (default `5 per minute; 20 per hour`)
- AUTHED default applied globally (overridden to a small N for speed)
- per-IP M2M limit through `@api_key_required` (overridden to a small N)
- `@limiter.exempt` on `/api/v1/health/*` (never 429 even under load)
- Unblock action drops every bucket for an actor; the next request
  from that actor succeeds.
"""

import pytest

from webapp.limiter import limiter as facade


# ── Test enablement ────────────────────────────────────────────────────


@pytest.fixture
def enabled_limiter(app):
    """Flip the limiter on for one test and reset state afterwards.

    The singleton facade is shared across xdist workers (within a single
    worker, that is), so we MUST restore enabled=False on the way out.
    Storage manipulation needs an app context — `Limiter.storage` asserts
    on `self._storage` which is only resolved with one pushed.
    """
    with app.app_context():
        facade.limiter.enabled = True
        app.config['RATELIMIT_ENABLED'] = True
        _clear_memory_storage()
        try:
            yield
        finally:
            facade.limiter.enabled = False
            app.config['RATELIMIT_ENABLED'] = False
            _clear_memory_storage()


def _clear_memory_storage():
    """Drop every bucket from the per-worker memory:// store.

    Tolerates the case where `Limiter._storage` hasn't been set up
    (e.g. a worker whose `app` fixture wired init_app with enabled=False
    via a path that skipped storage construction) — there's simply
    nothing to clear in that case.
    """
    try:
        storage = facade.limiter.storage
    except (AssertionError, AttributeError):
        return
    inner = getattr(storage, 'storage', None)
    if isinstance(inner, dict):
        inner.clear()


# ── /auth/login POST per-IP throttle ──────────────────────────────────


def test_login_post_429s_after_five_attempts(client, app, enabled_limiter):
    """5 per minute; 20 per hour — sixth POST in a minute returns 429."""
    for _ in range(5):
        resp = client.post('/auth/login', data={
            'username': 'nope', 'password': 'nope',
        })
        # 200 (bad creds → re-render) or 302 (redirect on success) — never 429 yet.
        assert resp.status_code != 429
    resp = client.post('/auth/login', data={'username': 'nope', 'password': 'nope'})
    assert resp.status_code == 429


def test_oidc_callback_uses_auth_login_tier(client, app, enabled_limiter):
    """/auth/oidc/callback throttles at RATELIMIT_AUTH_LOGIN (5/min), the
    auth-attempt tier, not the looser anon tier [PR295 P2-4]."""
    for _ in range(5):
        # No oauth extension under TestingConfig → flash + 302 to login;
        # the limiter still counts the hit.
        resp = client.get('/auth/oidc/callback')
        assert resp.status_code != 429
    resp = client.get('/auth/oidc/callback')
    assert resp.status_code == 429


# ── AUTHED default applies globally ────────────────────────────────────


def test_authed_default_eventually_429s(auth_client, app, enabled_limiter,
                                          monkeypatch):
    """The global default_limits = RATELIMIT_AUTHED — hammer a vanilla
    authed GET past that threshold and expect a 429."""
    monkeypatch.setitem(app.config, 'RATELIMIT_AUTHED', '3 per minute')

    # An authenticated endpoint that doesn't hit DB-heavy code paths.
    # /admin/htmx/configuration is admin-permission gated — benkirk has
    # SYSTEM_ADMIN via USER_PERMISSION_OVERRIDES — and it doesn't carry a
    # per-route override, so it inherits the AUTHED tier.
    url = '/admin/htmx/configuration'
    saw_429 = False
    for _ in range(8):
        resp = auth_client.get(url)
        if resp.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "expected a 429 after exceeding RATELIMIT_AUTHED"


# ── M2M tier inside @api_key_required ────────────────────────────────


def test_m2m_limit_fires_on_api_key_routes(api_key_client, app,
                                             enabled_limiter, monkeypatch):
    """The M2M tier is stacked inside @api_key_required and keys per-IP."""
    monkeypatch.setitem(app.config, 'RATELIMIT_M2M', '2 per minute')

    # /api/v1/status/derecho — an @api_key_required POST.
    url = '/api/v1/status/derecho'
    saw_429 = False
    for _ in range(6):
        resp = api_key_client.post(url, json={'fake': 'payload'})
        if resp.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "expected a 429 after exceeding RATELIMIT_M2M"


# ── Health probes exempt ──────────────────────────────────────────────


@pytest.mark.parametrize('path', ['/api/v1/health/', '/api/v1/health/live',
                                    '/api/v1/health/ready'])
def test_health_endpoints_never_429(client, app, enabled_limiter,
                                       monkeypatch, path):
    """@limiter.exempt routes never get throttled."""
    monkeypatch.setitem(app.config, 'RATELIMIT_AUTHED', '1 per minute')
    for _ in range(40):
        resp = client.get(path)
        assert resp.status_code != 429, (
            f"{path} should be exempt but returned 429"
        )


# ── Unblock action ────────────────────────────────────────────────────


def test_unblock_clears_bucket_and_allows_next_request(client, auth_client,
                                                         app, enabled_limiter,
                                                         monkeypatch):
    """Block an IP, call unblock, verify the next request succeeds.

    Uses `client` (unauthenticated) to generate the block on /auth/login
    POST, then uses `auth_client` (benkirk → SYSTEM_ADMIN) to POST the
    unblock; finally `client` retries the original request and expects
    something other than 429.
    """
    # Establish a block on the default 127.0.0.1 source IP.
    for _ in range(6):
        client.post('/auth/login', data={'username': 'x', 'password': 'x'})
    resp = client.post('/auth/login', data={'username': 'x', 'password': 'x'})
    assert resp.status_code == 429

    # Unblock that actor — the /auth/login limit keys on get_remote_address,
    # which for the Flask test client is '127.0.0.1'.
    actor = 'ip:127.0.0.1'
    resp = auth_client.post('/admin/htmx/rate-limits/unblock',
                            data={'actor': actor})
    assert resp.status_code == 200, resp.data

    # Retry from the same source: now allowed.
    resp = client.post('/auth/login', data={'username': 'x', 'password': 'x'})
    assert resp.status_code != 429
