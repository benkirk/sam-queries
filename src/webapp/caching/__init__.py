"""
Caching facade — the single entry point for every cache layer in the webapp.

Owns the Flask-Caching extension and the chart SVG caches, and proxies the
sam-package allocation usage cache for unified stats and clear semantics.

Usage
-----

In the application factory::

    from webapp.caching import caching
    caching.init_app(app)

Decorating an HTTP route (Flask-Caching pass-through)::

    from webapp.caching import caching
    @caching.flask.cached(timeout=300, query_string=True)
    def my_view(): ...

Decorating a chart-generating function::

    @caching.chart_cached(name='usage_timeseries', maxsize=128)
    def generate_usage_timeseries(daily_charges) -> str: ...

Inspecting all caches (used by the admin Configuration card)::

    caching.stats()            # → dict for the template
    caching.clear('chart')     # category in {'flask','chart','usage','scans',None}
"""

import logging
import os
from typing import Callable, List, Optional

from sam.caching import CacheBase
from webapp.caching.chart import ChartCache, chart_cached as _chart_decorator
from webapp.caching.flask_adapter import FlaskCacheAdapter

logger = logging.getLogger(__name__)


class Caching:
    """Single facade for every cache layer the webapp owns or proxies.

    On construction, checks ``CACHE_REDIS_URL``: if set and reachable,
    routes chart caches to Redis-backed adapters; otherwise falls back
    to per-worker in-process caches. The fallback is load-bearing —
    if Redis is unreachable at startup, the webapp must keep serving.
    """

    def __init__(self):
        from flask_caching import Cache

        self._redis_client = self._init_redis_client()
        self.flask = Cache()
        self._flask_adapter = FlaskCacheAdapter(self.flask, redis_client=self._redis_client)
        self._chart_caches: List[CacheBase] = []

    @staticmethod
    def _init_redis_client():
        url = os.environ.get('CACHE_REDIS_URL')
        if not url:
            return None
        try:
            from sam.caching import make_redis_client
            client = make_redis_client(url)
            logger.info("Caching: connected to Redis at %s", url)
            return client
        except Exception as exc:
            logger.warning(
                "Caching: CACHE_REDIS_URL=%s set but Redis is unreachable (%s); "
                "falling back to per-worker in-process caches.",
                url, exc,
            )
            return None

    def init_app(self, app, **flask_config) -> None:
        # Reconcile Flask-Caching backend with our reachability check:
        # if run.py set CACHE_TYPE=RedisCache (because CACHE_REDIS_URL
        # was set in the env) but our PING failed, downgrade to
        # SimpleCache so Flask-Caching doesn't try to talk to a dead
        # Redis on every request. This keeps the fallback truly
        # load-bearing.
        if (app.config.get('CACHE_TYPE') == 'RedisCache'
                and self._redis_client is None):
            logger.warning(
                "Caching: downgrading CACHE_TYPE RedisCache → SimpleCache "
                "because Redis is unreachable; Flask-Cache layer falls back."
            )
            app.config['CACHE_TYPE'] = 'SimpleCache'
        self.flask.init_app(app, **flask_config)

    # ── Decorators ──────────────────────────────────────────────────────

    def chart_cached(self, *, name: str, maxsize: int,
                     key_fn: Optional[Callable] = None):
        """Decorator factory for matplotlib SVG memoization.

        Each decorated function gets its own cache. With Redis
        configured, all workers share a single Redis-backed cache for
        each name; otherwise each worker holds its own bounded
        OrderedDict (per-worker fallback).
        """
        if self._redis_client is not None:
            from webapp.caching.redis_chart import RedisChartCache, chart_cached_redis
            cache: CacheBase = RedisChartCache(name=name, client=self._redis_client)
            self._chart_caches.append(cache)
            return chart_cached_redis(cache, key_fn=key_fn)

        cache = ChartCache(name=name, maxsize=maxsize)
        self._chart_caches.append(cache)
        return _chart_decorator(cache, key_fn=key_fn)

    # ── Introspection ───────────────────────────────────────────────────

    def adapters(self) -> List[CacheBase]:
        """All adapters known to the facade, including the proxied usage cache.

        Order: flask first, then chart caches in registration order, then
        the usage cache (if enabled). Stable across processes since
        registration order is deterministic.
        """
        out: List[CacheBase] = [self._flask_adapter, *self._chart_caches]
        try:
            from sam.queries.usage_cache import get_cache_adapter
            usage = get_cache_adapter()
        except Exception:
            usage = None
        if usage:
            out.append(usage)
        try:
            from webapp.disk_scans.cache import get_cache_adapter as get_scans_adapter
            scans = get_scans_adapter()
        except Exception:
            scans = None
        if scans:
            out.append(scans)
        return out

    def stats(self) -> dict:
        """Single dict for the admin card. Stable shape, group-by-category."""
        from flask import current_app
        from sam.queries.usage_cache import usage_cache_info
        from webapp.disk_scans.cache import fs_scans_cache_info

        return {
            'backend':         current_app.config.get('CACHE_TYPE'),
            'default_timeout': current_app.config.get('CACHE_DEFAULT_TIMEOUT'),
            'flask':           self._flask_adapter.info(),
            'chart':           [c.info() for c in self._chart_caches],
            'usage':           usage_cache_info(),
            'scans':           fs_scans_cache_info(),
        }

    def clear(self, category: Optional[str] = None) -> dict:
        """Invalidate caches by category. Returns {category: count_cleared}."""
        result: dict = {}
        if category in (None, 'flask'):
            result['flask'] = self._flask_adapter.clear()
        if category in (None, 'chart'):
            result['chart'] = sum(c.clear() for c in self._chart_caches)
        if category in (None, 'usage'):
            from sam.queries.usage_cache import purge_usage_cache
            result['usage'] = purge_usage_cache()
        if category in (None, 'scans'):
            from webapp.disk_scans.cache import purge_fs_scans_cache
            result['scans'] = purge_fs_scans_cache()
        return result


# Module-level singleton — import this from anywhere in webapp.
caching = Caching()


__all__ = ['Caching', 'caching']
