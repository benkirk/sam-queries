"""
In-memory TTL cache for get_allocation_summary_with_usage() results.

Sits transparently behind the query function. Works in both webapp and CLI.
Bypass with force_refresh=True; purge programmatically with purge_usage_cache().

Configuration is read from Flask app.config when available, falling back to
environment variables so the module works outside a Flask context (CLI, tests).

  ALLOCATION_USAGE_CACHE_TTL  — TTL in seconds (0 = disabled, default 3600)
  ALLOCATION_USAGE_CACHE_SIZE — max LRU entries  (0 = disabled, default 200)
"""

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from sam.caching import CacheBase, TTLCacheAdapter
from sam.caching.ttl import disabled_info
from sam.queries.allocations import get_allocation_summary_with_usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config(key: str, default: int) -> int:
    """Read config from Flask app context if available, else env var, else default."""
    try:
        from flask import current_app
        return int(current_app.config.get(key, default))
    except RuntimeError:
        return int(os.environ.get(key, default))


def _normalize(value: Any):
    """Make list/string/None values hashable for use as cache key components."""
    if isinstance(value, list):
        return tuple(sorted(str(v) for v in value))
    return value


# ---------------------------------------------------------------------------
# Lazy-initialized adapter
# ---------------------------------------------------------------------------

_adapter: Optional[TTLCacheAdapter] = None
_init_lock = threading.RLock()
_disabled = False   # set True when TTL or SIZE == 0


def get_cache_adapter() -> Optional[TTLCacheAdapter]:
    """Return the shared CacheBase adapter, initializing on first call.

    Returns None when caching is disabled by config (TTL or SIZE == 0).
    Used by the Caching facade in webapp to surface this cache's stats
    alongside the webapp-owned chart and Flask caches.
    """
    global _adapter, _disabled

    with _init_lock:
        if _adapter is not None or _disabled:
            return None if _disabled else _adapter

        ttl  = _get_config('ALLOCATION_USAGE_CACHE_TTL',  3600)
        size = _get_config('ALLOCATION_USAGE_CACHE_SIZE', 200)

        if ttl <= 0 or size <= 0:
            _disabled = True
            return None

        _adapter = TTLCacheAdapter(name='allocation_usage', maxsize=size, ttl=ttl)
        return _adapter


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cached_allocation_usage(
    session,
    *,
    resource_name=None,
    facility_name=None,
    allocation_type=None,
    projcode=None,
    active_only: bool = True,
    active_at: Optional[datetime] = None,
    include_adjustments: bool = True,
    force_refresh: bool = False,
    root_only: bool = False,
    _summary=None,
) -> List[Dict]:
    """
    Cached wrapper for get_allocation_summary_with_usage().

    Cache key is built from all parameters at day granularity (active_at → date).
    Identical calls within the TTL window return cached results without hitting DB.

    Args:
        force_refresh: Bypass the cache and recompute from DB.  The fresh result
                       is stored back into the cache for subsequent callers.
        _summary: Optional pre-computed get_allocation_summary() result to pass through
                  to get_allocation_summary_with_usage(), skipping that internal call.
                  Only effective on a cache miss (cached results are returned as-is).

    All other args are forwarded unchanged to get_allocation_summary_with_usage().
    """
    adapter = get_cache_adapter()

    if adapter is None:
        # Cache disabled — call through directly
        return get_allocation_summary_with_usage(
            session=session,
            resource_name=resource_name,
            facility_name=facility_name,
            allocation_type=allocation_type,
            projcode=projcode,
            active_only=active_only,
            active_at=active_at,
            include_adjustments=include_adjustments,
            root_only=root_only,
            _summary=_summary,
        )

    key = (
        _normalize(resource_name),
        _normalize(facility_name),
        _normalize(allocation_type),
        _normalize(projcode),
        active_only,
        active_at.date() if isinstance(active_at, datetime) else active_at,
        include_adjustments,
        root_only,
    )

    with adapter.lock:
        if not force_refresh and key in adapter:
            return adapter[key]
        # Remove stale entry so we can re-insert after the query
        adapter.pop(key, None)

    result = get_allocation_summary_with_usage(
        session=session,
        resource_name=resource_name,
        facility_name=facility_name,
        allocation_type=allocation_type,
        projcode=projcode,
        active_only=active_only,
        active_at=active_at,
        include_adjustments=include_adjustments,
        root_only=root_only,
        _summary=_summary,
    )

    with adapter.lock:
        try:
            adapter[key] = result
        except ValueError:
            # Cache full and all entries are unexpired (TTLCache raises ValueError
            # when maxsize is reached and no expired items are available to evict).
            pass

    return result


def purge_usage_cache() -> int:
    """Clear all cached usage data. Returns number of entries cleared."""
    adapter = get_cache_adapter()
    if adapter is None:
        return 0
    return adapter.clear()


def usage_cache_info() -> Dict:
    """Return cache statistics for monitoring/admin display.

    Delegates to the adapter's `info()` (canonical CacheBase shape).
    Backwards-compatible: the legacy keys (`enabled`, `currsize`,
    `maxsize`, `ttl`) are still present; new fields (`hits`, `misses`,
    `bytes_approx`, `name`, `extras`) are additive.
    """
    adapter = get_cache_adapter()
    if adapter is None:
        ttl  = _get_config('ALLOCATION_USAGE_CACHE_TTL',  3600)
        size = _get_config('ALLOCATION_USAGE_CACHE_SIZE', 200)
        return disabled_info('allocation_usage', maxsize=size, ttl=ttl)
    return adapter.info()
