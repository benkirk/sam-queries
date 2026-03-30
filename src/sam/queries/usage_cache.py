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

from cachetools import TTLCache

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
# Lazy-initialized cache
# ---------------------------------------------------------------------------

_cache: Optional[TTLCache] = None
_lock = threading.RLock()
_disabled = False   # set True when TTL or SIZE == 0


def _get_cache() -> Optional[TTLCache]:
    """Return the shared TTLCache, initializing on first call. Returns None if disabled."""
    global _cache, _disabled

    with _lock:
        if _cache is not None or _disabled:
            return None if _disabled else _cache

        ttl  = _get_config('ALLOCATION_USAGE_CACHE_TTL',  3600)
        size = _get_config('ALLOCATION_USAGE_CACHE_SIZE', 200)

        if ttl <= 0 or size <= 0:
            _disabled = True
            return None

        _cache = TTLCache(maxsize=size, ttl=ttl)
        return _cache


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
) -> List[Dict]:
    """
    Cached wrapper for get_allocation_summary_with_usage().

    Cache key is built from all parameters at day granularity (active_at → date).
    Identical calls within the TTL window return cached results without hitting DB.

    Args:
        force_refresh: Bypass the cache and recompute from DB.  The fresh result
                       is stored back into the cache for subsequent callers.

    All other args are forwarded unchanged to get_allocation_summary_with_usage().
    """
    cache = _get_cache()

    if cache is None:
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
        )

    key = (
        _normalize(resource_name),
        _normalize(facility_name),
        _normalize(allocation_type),
        _normalize(projcode),
        active_only,
        active_at.date() if isinstance(active_at, datetime) else active_at,
        include_adjustments,
    )

    with _lock:
        if not force_refresh and key in cache:
            return cache[key]
        # Remove stale entry so we can re-insert after the query
        cache.pop(key, None)

    result = get_allocation_summary_with_usage(
        session=session,
        resource_name=resource_name,
        facility_name=facility_name,
        allocation_type=allocation_type,
        projcode=projcode,
        active_only=active_only,
        active_at=active_at,
        include_adjustments=include_adjustments,
    )

    with _lock:
        try:
            cache[key] = result
        except ValueError:
            # Cache full and all entries are unexpired (TTLCache raises ValueError
            # when maxsize is reached and no expired items are available to evict).
            pass

    return result


def purge_usage_cache() -> int:
    """Clear all cached usage data. Returns number of entries cleared."""
    cache = _get_cache()
    if cache is None:
        return 0
    with _lock:
        n = len(cache)
        cache.clear()
    return n


def usage_cache_info() -> Dict:
    """Return cache statistics for monitoring/admin display."""
    cache = _get_cache()
    if cache is None:
        ttl  = _get_config('ALLOCATION_USAGE_CACHE_TTL',  3600)
        size = _get_config('ALLOCATION_USAGE_CACHE_SIZE', 200)
        return {
            'enabled': False,
            'currsize': 0,
            'maxsize': size,
            'ttl': ttl,
        }
    with _lock:
        return {
            'enabled': True,
            'currsize': len(cache),
            'maxsize': cache.maxsize,
            'ttl': cache.ttl,
        }
