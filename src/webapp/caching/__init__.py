"""Caching facade -- the single entry point for every cache layer in the webapp.

Owns the Flask-Caching extension and the chart SVG caches, and proxies the
sam-package allocation usage cache so stats and clear semantics are unified::

    caching.init_app(app)                       # in the application factory
    @caching.flask.cached(timeout=300, query_string=True)
    @caching.chart_cached(name='usage_timeseries', maxsize=128)

    caching.stats()          # the admin Configuration card
    caching.clear('chart')   # 'flask' | 'chart' | 'usage' | 'scans' | 'jobs' | None
"""

import importlib
import logging
import os
from typing import Callable, List, Optional

from sam.caching import BucketedTTLCache, CacheBase, registered_caches
from webapp.caching.chart import ChartCache, chart_cached as _chart_decorator
from webapp.caching.flask_adapter import FlaskCacheAdapter

logger = logging.getLogger(__name__)

#: Modules that construct a :class:`BucketedTTLCache` at import time. Order is
#: the registry order, hence the admin card's display order and the order of
#: :meth:`Caching.adapters`. Imported lazily inside
#: :meth:`Caching.bucketed_caches` — importing them at module scope would
#: cycle (both webapp packages import this facade).
_BUCKETED_CACHE_MODULES = (
    'sam.queries.usage_cache',
    'webapp.disk_scans.cache',
    'webapp.jobs.cache',
    'sam.integration.awards.cache',
    'sam.integration.xras_api.cache',
)


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

    # Decorators

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

    # Bucketed TTL caches (registry-driven)

    @staticmethod
    def bucketed_caches() -> List[BucketedTTLCache]:
        """Every :class:`BucketedTTLCache` in the app, in a deterministic order.

        The caches self-register at import, so the import here is what makes
        the registry complete — and importing by name (rather than at module
        scope) keeps ``webapp.caching`` free of an import cycle with
        ``webapp.jobs`` / ``webapp.disk_scans``, which import the facade back.

        A module that fails to import (plugin extra not installed, say) is
        skipped: the admin card degrades to the caches that did load rather
        than 500ing.

        Adding a bucketed cache means adding it to ``_BUCKETED_CACHE_MODULES``
        and nothing else — :meth:`adapters`, :meth:`stats`, :meth:`clear`,
        :attr:`categories` and the flask adapter's foreign-keyspace skip list
        all derive from here.
        """
        for module in _BUCKETED_CACHE_MODULES:
            try:
                importlib.import_module(module)
            except Exception as exc:
                logger.warning("Caching: %s did not import (%s); "
                               "its cache will be absent from the admin card.",
                               module, exc)
        return registered_caches()

    @property
    def categories(self) -> tuple:
        """Valid ``clear(category)`` values, in admin-card order."""
        return ('flask', 'chart', *(c.category for c in self.bucketed_caches()))

    # Introspection

    def adapters(self) -> List[CacheBase]:
        """All adapters known to the facade, including the proxied usage cache.

        Order: flask first, then chart caches in registration order, then each
        bucketed cache's enabled buckets in declaration order (usage, then the
        two fs-scans buckets, then the two jobs buckets). Stable across
        processes since registration order is deterministic.
        """
        out: List[CacheBase] = [self._flask_adapter, *self._chart_caches]
        for cache in self.bucketed_caches():
            out.extend(cache.live_adapters())
        return out

    def stats(self) -> dict:
        """Single dict for the admin card. Stable shape, group-by-category.

        ``usage`` is a single info dict (one bucket, rendered as one row);
        ``scans`` and ``jobs`` are per-bucket lists the template loops over.
        """
        from flask import current_app
        from sam.queries.usage_cache import usage_cache_info

        out = {
            'backend':         current_app.config.get('CACHE_TYPE'),
            'default_timeout': current_app.config.get('CACHE_DEFAULT_TIMEOUT'),
            'flask':           self._flask_adapter.info(),
            'chart':           [c.info() for c in self._chart_caches],
        }
        for cache in self.bucketed_caches():
            out[cache.category] = cache.info()
        # The usage cache predates the per-bucket list shape and the card
        # renders it as a single row; keep that contract.
        out['usage'] = usage_cache_info()
        return out

    def clear(self, category: Optional[str] = None) -> dict:
        """Invalidate caches by category. Returns {category: count_cleared}."""
        result: dict = {}
        if category in (None, 'flask'):
            result['flask'] = self._flask_adapter.clear()
        if category in (None, 'chart'):
            result['chart'] = sum(c.clear() for c in self._chart_caches)
        for cache in self.bucketed_caches():
            if category in (None, cache.category):
                result[cache.category] = cache.purge()
        return result


# Module-level singleton — import this from anywhere in webapp.
caching = Caching()


__all__ = ['Caching', 'caching']
