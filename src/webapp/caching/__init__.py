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
    caching.clear('chart')     # category in {'flask','chart','usage',None}
"""

from typing import Callable, List, Optional

from sam.caching import CacheBase
from webapp.caching.chart import ChartCache, chart_cached as _chart_decorator
from webapp.caching.flask_adapter import FlaskCacheAdapter


class Caching:
    """Single facade for every cache layer the webapp owns or proxies."""

    def __init__(self):
        from flask_caching import Cache
        self.flask = Cache()
        self._flask_adapter = FlaskCacheAdapter(self.flask)
        self._chart_caches: List[ChartCache] = []

    def init_app(self, app, **flask_config) -> None:
        self.flask.init_app(app, **flask_config)

    # ── Decorators ──────────────────────────────────────────────────────

    def chart_cached(self, *, name: str, maxsize: int,
                     key_fn: Optional[Callable] = None):
        """Decorator factory for matplotlib SVG memoization.

        Each decorated function gets its own bounded LRU. The cache is
        registered with the facade so it shows up in `stats()` and
        responds to `clear('chart')`.
        """
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
        return out

    def stats(self) -> dict:
        """Single dict for the admin card. Stable shape, group-by-category."""
        from flask import current_app
        from sam.queries.usage_cache import usage_cache_info

        return {
            'backend':         current_app.config.get('CACHE_TYPE'),
            'default_timeout': current_app.config.get('CACHE_DEFAULT_TIMEOUT'),
            'flask':           self._flask_adapter.info(),
            'chart':           [c.info() for c in self._chart_caches],
            'usage':           usage_cache_info(),
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
        return result


# Module-level singleton — import this from anywhere in webapp.
caching = Caching()


__all__ = ['Caching', 'caching']
