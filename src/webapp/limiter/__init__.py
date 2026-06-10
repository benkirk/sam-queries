"""
Rate-limit facade — single entry point for Flask-Limiter, storage backend
selection, and the 429 errorhandler.

Mirrors `webapp.caching.Caching`: a module-level singleton with `init_app`
that reconciles the configured Redis storage URI against a live PING and
falls back to per-worker `memory://` (with a warning) when Redis is
unreachable. The webapp keeps serving in either case.

Usage in `webapp.run.create_app`::

    from webapp.limiter import limiter
    limiter.init_app(app)        # call immediately after caching.init_app

Anywhere else::

    from webapp.limiter import limiter
    @limiter.limiter.limit(...)  # raw Flask-Limiter decorator passthrough
    @limiter.limiter.exempt      # opt out of the global default

Configuration is read from `app.config` (`RATELIMIT_*`, see
`webapp/config.py`).
"""

import logging
from typing import Any

from flask import current_app, g, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)


def _key_func() -> str:
    """Identify the request actor for limit accounting.

    Order matters: M2M API-key callers must never share a bucket with
    interactive users sharing the same source IP.
    """
    api_user = getattr(g, 'api_key_user', None)
    if api_user:
        return f'apikey:{api_user}'

    try:
        from flask_login import current_user
        if current_user.is_authenticated:
            username = getattr(current_user, 'username', None) or str(current_user.get_id())
            return f'user:{username}'
    except Exception:
        pass

    return f'ip:{get_remote_address()}'


class Limiting:
    """Owns the Flask-Limiter instance, resolves the storage backend at
    init_app time, and registers the 429 errorhandler.

    On construction the storage URI defaults to memory://. `init_app`
    upgrades to redis:// when `RATELIMIT_STORAGE_URI` is set and a PING
    succeeds; otherwise it stays on memory:// and logs a warning. This
    mirrors `webapp.caching.Caching` exactly.
    """

    def __init__(self) -> None:
        self.limiter = Limiter(
            key_func=_key_func,
            headers_enabled=True,
            strategy='fixed-window',
            # Don't pass storage_uri here — it would shadow app.config.
            # init_app reconciles the URI before delegating.
            default_limits=[lambda: current_app.config['RATELIMIT_AUTHED']],
        )
        self._redis_client = None
        self._resolved_storage_uri = 'memory://'

    # ── Wire-up ─────────────────────────────────────────────────────────

    def init_app(self, app) -> None:
        uri = (app.config.get('RATELIMIT_STORAGE_URI') or '').strip()
        if uri.startswith('redis://') or uri.startswith('rediss://'):
            try:
                from sam.caching import make_redis_client
                self._redis_client = make_redis_client(uri)
                self._resolved_storage_uri = uri
            except Exception as exc:
                logger.warning(
                    "Limiter: RATELIMIT_STORAGE_URI=%s set but Redis is "
                    "unreachable (%s); falling back to memory://.",
                    uri, exc,
                )
                self._redis_client = None
                self._resolved_storage_uri = 'memory://'
        else:
            if uri:
                logger.warning(
                    "Limiter: RATELIMIT_STORAGE_URI=%r is not a redis:// URI; "
                    "falling back to per-worker memory://.", uri)
            else:
                logger.warning(
                    "Limiter: RATELIMIT_STORAGE_URI is not set; rate limits "
                    "fall back to per-worker memory:// and are NOT shared "
                    "across gunicorn workers/pods.")
            self._redis_client = None
            self._resolved_storage_uri = 'memory://'

        # Flask-Limiter reads RATELIMIT_STORAGE_URI from app.config inside
        # init_app, so write the resolved value back before delegating.
        app.config['RATELIMIT_STORAGE_URI'] = self._resolved_storage_uri

        # Flask-Limiter's init_app early-returns when enabled=False
        # (storage + before_request handler never get wired). That's a
        # problem in tests, where we want storage initialized so the
        # `enabled` flag can be flipped on per-test. Force init_app to
        # run with enabled=True regardless, then restore the requested
        # state afterward — the request-time `if self.enabled` check
        # honors the runtime flag and skips bucket math when it's False.
        requested_enabled = bool(app.config.get('RATELIMIT_ENABLED', True))
        app.config['RATELIMIT_ENABLED'] = True
        self.limiter.enabled = True
        self.limiter.init_app(app)
        app.config['RATELIMIT_ENABLED'] = requested_enabled
        self.limiter.enabled = requested_enabled

        # Initialize the event ring + errorhandler last so they pick up
        # the resolved storage decision.
        from webapp.limiter.events import init_events
        init_events(app, self._redis_client)
        _register_429_handler(app)

    # ── Introspection (Configuration tile + admin page) ────────────────

    @property
    def storage_uri(self) -> str:
        return self._resolved_storage_uri

    @staticmethod
    def _masked_uri(uri: str) -> str:
        """Strip user:password if the URI carries credentials."""
        if '@' not in uri:
            return uri
        scheme, _, rest = uri.partition('://')
        creds, _, host = rest.rpartition('@')
        if not creds:
            return uri
        return f'{scheme}://***@{host}'

    def stats(self) -> dict[str, Any]:
        """Single dict for the admin Configuration tile + page."""
        from webapp.limiter.events import recent, top_offenders, active_blocks
        cfg = current_app.config
        events_24h = recent(limit=cfg['RATELIMIT_EVENT_MAX'])
        return {
            'enabled':              bool(self.limiter.enabled),
            'storage':              self._masked_uri(self._resolved_storage_uri),
            'tiers': {
                'auth_login': cfg['RATELIMIT_AUTH_LOGIN'],
                'm2m':        cfg['RATELIMIT_M2M'],
                'authed':     cfg['RATELIMIT_AUTHED'],
                'anon':       cfg['RATELIMIT_ANON'],
            },
            'events_24h':           len(events_24h),
            'top_offenders_24h':    top_offenders(n=5),
            'active_blocks_count':  len(active_blocks()),
        }


# ── 429 errorhandler ────────────────────────────────────────────────────


def _register_429_handler(app) -> None:
    @app.errorhandler(429)
    def _handle_429(e):
        actor = _key_func()
        limit_str = str(getattr(e, 'description', '')) or 'rate limit exceeded'
        retry_after = None
        # Flask-Limiter attaches a `Retry-After` header on the response
        # object it raises; the value is also available via e.description
        # in some versions. We surface it on every response shape.
        if hasattr(e, 'get_response'):
            try:
                retry_after = e.get_response().headers.get('Retry-After')
            except Exception:
                retry_after = None

        app.logger.warning(
            "rate_limit_exceeded actor=%s endpoint=%s method=%s path=%s limit=%r",
            actor, request.endpoint, request.method, request.path, limit_str,
        )
        from webapp.limiter.events import record_429
        record_429(actor, request.endpoint, request.method, request.path, limit_str)

        response = _format_429_response(limit_str, retry_after)
        if retry_after is not None:
            response.headers.setdefault('Retry-After', retry_after)
        return response


def _format_429_response(limit_str: str, retry_after: str | None):
    """Content-type-aware 429 body: JSON for /api/, HTMX fragment when the
    request advertises `HX-Request: true`, otherwise a full HTML page.
    """
    if request.path.startswith('/api/'):
        body = {'error': 'rate_limit_exceeded', 'limit': limit_str}
        if retry_after is not None:
            body['retry_after'] = retry_after
        response = jsonify(body)
        response.status_code = 429
        return response

    if request.headers.get('HX-Request'):
        response = current_app.response_class(
            render_template('errors/429_htmx.html', limit=limit_str, retry_after=retry_after),
            status=429,
            mimetype='text/html',
        )
        return response

    response = current_app.response_class(
        render_template('errors/429.html', limit=limit_str, retry_after=retry_after),
        status=429,
        mimetype='text/html',
    )
    return response


# Module-level singleton — import this from anywhere in webapp.
limiter = Limiting()


__all__ = ['Limiting', 'limiter', '_key_func']
