"""Unit tests for the rate-limit facade and event ring.

Covers:
- `_key_func` branches (M2M → user → IP fallback)
- `record_429` Redis path: ZADD, retention trim, hard cap
- `record_429` deque fallback when Redis client is None
- 429 errorhandler response shapes (JSON / HTMX / HTML)
- `Limiting.stats()` dict shape + URI credential masking

Each test runs inside the `app` fixture's app context. The facade is a
module-level singleton initialized once during create_app(); we exercise
its bound methods directly. The Redis-backed paths use a `fakeredis.FakeRedis`
swapped onto `app.extensions['limiter_events']` so we don't depend on a
real Redis container.
"""

import json
import time

import fakeredis
import pytest
from flask import g

from webapp.limiter import _key_func, _format_429_response, limiter as facade
from webapp.limiter.events import (
    record_429,
    recent,
    top_offenders,
    active_blocks,
    init_events,
)


# ── _key_func ──────────────────────────────────────────────────────────


def test_key_func_falls_back_to_ip(app):
    """Anonymous request → 'ip:<addr>' (get_remote_address default)."""
    with app.test_request_context('/', environ_base={'REMOTE_ADDR': '10.0.0.5'}):
        assert _key_func() == 'ip:10.0.0.5'


def test_key_func_uses_api_key_user_when_set(app):
    with app.test_request_context('/', environ_base={'REMOTE_ADDR': '10.0.0.5'}):
        g.api_key_user = 'collector'
        assert _key_func() == 'apikey:collector'


def test_key_func_uses_authenticated_user(app, monkeypatch):
    """`flask_login.current_user.is_authenticated` → 'user:<username>'."""
    class _U:
        is_authenticated = True
        username = 'benkirk'

        def get_id(self):
            return self.username

    # Patch the `current_user` proxy that _key_func imports lazily.
    import flask_login
    monkeypatch.setattr(flask_login, 'current_user', _U())
    with app.test_request_context('/'):
        assert _key_func() == 'user:benkirk'


# ── record_429 (Redis path) ────────────────────────────────────────────


@pytest.fixture
def fake_redis_events(app):
    """Swap the events store onto a FakeRedis for the duration of one test."""
    fake = fakeredis.FakeRedis()
    with app.app_context():
        original = app.extensions.get('limiter_events')
        init_events(app, fake)
        try:
            yield fake
        finally:
            fake.flushall()
            if original is not None:
                app.extensions['limiter_events'] = original


def test_record_429_writes_event_to_redis_sorted_set(app, fake_redis_events):
    with app.test_request_context('/'):
        record_429('ip:10.0.0.5', 'auth.login', 'POST', '/auth/login',
                   '5 per minute')
    assert fake_redis_events.zcard('ratelimit:events') == 1
    raw = fake_redis_events.zrange('ratelimit:events', 0, -1)[0]
    event = json.loads(raw)
    assert event['actor'] == 'ip:10.0.0.5'
    assert event['endpoint'] == 'auth.login'
    assert event['limit'] == '5 per minute'
    assert 'id' in event   # uuid for ZADD member uniqueness


def test_record_429_drops_entries_past_retention_window(app, fake_redis_events):
    """Old events (older than RATELIMIT_EVENT_RETENTION_HOURS) get trimmed."""
    retention_h = app.config['RATELIMIT_EVENT_RETENTION_HOURS']
    old_score = time.time() - (retention_h + 1) * 3600
    fake_redis_events.zadd('ratelimit:events', {
        json.dumps({'ts': old_score, 'id': 'stale', 'actor': 'ip:1.2.3.4'}): old_score,
    })
    with app.test_request_context('/'):
        record_429('ip:5.6.7.8', 'x', 'GET', '/', '30 per minute')
    members = fake_redis_events.zrange('ratelimit:events', 0, -1)
    assert len(members) == 1
    assert json.loads(members[0])['actor'] == 'ip:5.6.7.8'


def test_record_429_enforces_hard_cap(app, fake_redis_events, monkeypatch):
    """ZREMRANGEBYRANK caps memory growth at RATELIMIT_EVENT_MAX."""
    monkeypatch.setitem(app.config, 'RATELIMIT_EVENT_MAX', 5)
    with app.test_request_context('/'):
        for i in range(12):
            record_429(f'ip:10.0.0.{i}', 'x', 'GET', '/', '30 per minute')
    assert fake_redis_events.zcard('ratelimit:events') == 5


# ── record_429 (deque fallback) ────────────────────────────────────────


def test_record_429_falls_back_to_deque_when_redis_none(app):
    with app.app_context():
        init_events(app, redis_client=None)
        with app.test_request_context('/'):
            record_429('ip:10.0.0.9', 'x', 'GET', '/', '30 per minute')
        events = recent(limit=10)
    assert len(events) == 1
    assert events[0]['actor'] == 'ip:10.0.0.9'


# ── recent / top_offenders ─────────────────────────────────────────────


def test_recent_returns_newest_first(app, fake_redis_events):
    with app.test_request_context('/'):
        for actor in ['ip:1', 'ip:2', 'ip:3']:
            record_429(actor, 'x', 'GET', '/', 'X')
        events = recent(limit=10)
    assert [e['actor'] for e in events] == ['ip:3', 'ip:2', 'ip:1']


def test_top_offenders_counts_by_actor(app, fake_redis_events):
    with app.test_request_context('/'):
        for _ in range(5):
            record_429('ip:offender', 'x', 'GET', '/', 'X')
        for _ in range(2):
            record_429('ip:other', 'x', 'GET', '/', 'X')
        ranked = top_offenders(n=5)
    assert ranked[0] == ('ip:offender', 5)
    assert ('ip:other', 2) in ranked


def test_active_blocks_includes_recent_actors_only(app, fake_redis_events):
    """Only actors with a 429 in the last 60s show up as actively blocked."""
    old_score = time.time() - 120
    fake_redis_events.zadd('ratelimit:events', {
        json.dumps({
            'ts': old_score, 'id': 'a', 'actor': 'ip:stale',
            'endpoint': 'x', 'method': 'GET', 'path': '/', 'limit': 'X',
        }): old_score,
    })
    with app.test_request_context('/'):
        record_429('ip:fresh', 'x', 'GET', '/', '30 per minute')
        blocks = active_blocks()
    actors = [b['actor'] for b in blocks]
    assert 'ip:fresh' in actors
    assert 'ip:stale' not in actors


# ── 429 errorhandler response shape ────────────────────────────────────
#
# These tests exercise `_format_429_response` directly under a synthetic
# request context. The handler itself is just `_format_429_response` plus
# logging + event recording, so testing the formatter covers the shape
# branches without registering test-only routes against the
# session-scoped app fixture (Flask 3 blocks `app.route()` after the
# first request has been served).


def test_429_handler_returns_json_for_api_paths(app):
    """JSON envelope for /api/* requests."""
    with app.test_request_context('/api/v1/anything'):
        resp = _format_429_response('5 per minute', '30')
    assert resp.status_code == 429
    assert resp.is_json
    body = resp.get_json()
    assert body['error'] == 'rate_limit_exceeded'
    assert body['limit'] == '5 per minute'
    assert body['retry_after'] == '30'


def test_429_handler_returns_html_fragment_for_htmx(app):
    """HX-Request → fragment body, status 429, text/html, no `<html>`."""
    with app.test_request_context('/anything',
                                    headers={'HX-Request': 'true'}):
        resp = _format_429_response('5 per minute', None)
    assert resp.status_code == 429
    assert resp.mimetype == 'text/html'
    assert b'Too many requests' in resp.data
    assert b'<html' not in resp.data.lower()


def test_429_handler_returns_full_html_otherwise(app):
    """Non-API, non-HTMX → full HTML page extending base.html."""
    with app.test_request_context('/anything'):
        resp = _format_429_response('5 per minute', None)
    assert resp.status_code == 429
    assert resp.mimetype == 'text/html'
    assert b'<html' in resp.data.lower()


# ── Limiting.stats() ──────────────────────────────────────────────────


def test_stats_returns_documented_shape(app):
    with app.app_context():
        s = facade.stats()
    assert set(s.keys()) >= {
        'enabled', 'storage', 'tiers',
        'events_24h', 'top_offenders_24h', 'active_blocks_count',
    }
    assert set(s['tiers'].keys()) == {'auth_login', 'm2m', 'authed', 'anon'}
    assert isinstance(s['events_24h'], int)
    assert isinstance(s['active_blocks_count'], int)


def test_storage_uri_credentials_are_masked():
    """User:password in the URI is replaced with ***."""
    from webapp.limiter import Limiting
    assert Limiting._masked_uri('redis://user:secret@host:6379/1') == 'redis://***@host:6379/1'
    assert Limiting._masked_uri('redis://host:6379/1') == 'redis://host:6379/1'
    assert Limiting._masked_uri('memory://') == 'memory://'
