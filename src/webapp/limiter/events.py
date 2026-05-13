"""
Rate-limit event recording and bucket inspection.

Backs the dedicated admin page and the Configuration tile counter:

- `record_429`        — invoked from the 429 errorhandler; stores a small
                        JSON-encoded event in a Redis sorted set with
                        bounded retention (`RATELIMIT_EVENT_RETENTION_HOURS`)
                        and a hard rank cap (`RATELIMIT_EVENT_MAX`).
                        Falls back to a per-worker bounded deque when no
                        Redis client is wired.
- `recent`            — most-recent events, newest-first.
- `top_offenders`     — `Counter`-derived ranking over a window.
- `active_blocks`     — actors whose last 429 falls inside the shortest
                        per-IP tier window (best-effort heuristic).
- `clear_bucket`      — best-effort bucket invalidation for one actor,
                        used by the admin "Unblock" action.

`init_events(app, redis_client)` stashes the client + a per-worker deque
on `app.extensions['limiter_events']` so the helpers can be called from
any request context.
"""

import json
import logging
import time
import uuid
from collections import Counter, deque
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

_EVENT_KEY = 'ratelimit:events'
_ACTIVE_BLOCK_WINDOW = 60   # seconds; matches the shortest per-minute tier


def init_events(app, redis_client) -> None:
    """Wire the events ring (Redis-backed or per-worker deque) onto `app`."""
    maxlen = int(app.config.get('RATELIMIT_EVENT_MAX', 1000))
    app.extensions['limiter_events'] = {
        'redis': redis_client,
        'fallback': deque(maxlen=maxlen),
    }


def _store() -> dict:
    return current_app.extensions['limiter_events']


def _retention_seconds() -> int:
    return int(current_app.config.get('RATELIMIT_EVENT_RETENTION_HOURS', 24)) * 3600


def _max_events() -> int:
    return int(current_app.config.get('RATELIMIT_EVENT_MAX', 1000))


def record_429(actor: str, endpoint: str | None, method: str,
               path: str, limit_str: str) -> None:
    """Persist one 429 event. Cheap; safe to call from the errorhandler."""
    now = time.time()
    event = {
        'ts': now,
        'id': uuid.uuid4().hex,         # ZADD members must be unique
        'actor': actor,
        'endpoint': endpoint or '',
        'method': method,
        'path': path,
        'limit': limit_str,
    }
    payload = json.dumps(event, separators=(',', ':'))
    store = _store()
    client = store['redis']
    if client is None:
        store['fallback'].append(event)
        return

    try:
        cutoff = now - _retention_seconds()
        pipe = client.pipeline()
        pipe.zadd(_EVENT_KEY, {payload: now})
        pipe.zremrangebyscore(_EVENT_KEY, '-inf', cutoff)
        # Hard rank cap: keep the newest _max_events() entries by removing
        # everything outside that newest-N rank window.
        pipe.zremrangebyrank(_EVENT_KEY, 0, -_max_events() - 1)
        pipe.execute()
    except Exception as exc:
        logger.warning("limiter.events: ZADD failed (%s); event dropped", exc)


def recent(limit: int = 200) -> list[dict[str, Any]]:
    """Return up to `limit` most-recent events, newest-first."""
    store = _store()
    client = store['redis']
    if client is None:
        return list(reversed(store['fallback']))[:limit]

    try:
        raw = client.zrevrange(_EVENT_KEY, 0, limit - 1)
    except Exception as exc:
        logger.warning("limiter.events: ZREVRANGE failed (%s)", exc)
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        try:
            out.append(json.loads(entry))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def top_offenders(window_seconds: int = 86400, n: int = 10) -> list[tuple[str, int]]:
    """Top `n` actors by 429 count over the last `window_seconds`."""
    cutoff = time.time() - window_seconds
    events = recent(limit=_max_events())
    counter = Counter(e['actor'] for e in events if e.get('ts', 0) >= cutoff)
    return counter.most_common(n)


def active_blocks() -> list[dict[str, Any]]:
    """Actors whose last 429 fell inside the per-minute window.

    Approximation — we don't peek into Flask-Limiter's storage buckets
    directly because the key layout varies by storage backend. An actor
    with a 429 in the last `_ACTIVE_BLOCK_WINDOW` seconds is, for any
    `N per minute` tier, still over quota with very high probability.
    """
    cutoff = time.time() - _ACTIVE_BLOCK_WINDOW
    events = recent(limit=_max_events())
    seen: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get('ts', 0) < cutoff:
            continue
        actor = e.get('actor', '')
        if actor and actor not in seen:
            seen[actor] = {
                'actor': actor,
                'last_429_ts': e['ts'],
                'last_endpoint': e.get('endpoint', ''),
                'last_path': e.get('path', ''),
                'limit': e.get('limit', ''),
            }
    return sorted(seen.values(), key=lambda r: r['last_429_ts'], reverse=True)


def clear_bucket(actor_key: str) -> int:
    """Best-effort: drop every Flask-Limiter storage key that contains the
    actor key. Returns the count of keys removed.

    For Redis storage, Flask-Limiter encodes the key_func result into the
    storage key directly (e.g. `LIMITER/ip:10.0.0.5/...`), so a SCAN over
    `*<actor_key>*` reliably matches every bucket for that actor across
    all tiers. For memory storage we have no cross-worker reach; the
    in-process limiter is reset opportunistically below.
    """
    if not actor_key:
        return 0

    removed = 0
    store = _store()
    client = store['redis']
    if client is not None:
        try:
            pattern = f'*{actor_key}*'.encode()
            for key in client.scan_iter(match=pattern, count=200):
                if client.delete(key):
                    removed += 1
        except Exception as exc:
            logger.warning("limiter.events: clear_bucket SCAN failed (%s)", exc)

    # Also clear any in-process limits.storage entries that match.
    try:
        from webapp.limiter import limiter as _facade
        storage = _facade.limiter.storage
        if hasattr(storage, 'storage') and isinstance(storage.storage, dict):
            for k in [k for k in storage.storage if actor_key in str(k)]:
                storage.storage.pop(k, None)
                removed += 1
    except Exception:
        pass
    return removed
