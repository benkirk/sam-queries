"""
In-memory TTL cache for get_allocation_summary_with_usage() results.

Sits transparently behind the query function. Works in both webapp and CLI.
Bypass with force_refresh=True; purge programmatically with purge_usage_cache().

Backend, lazy init and the get/compute/store dance come from
:class:`sam.caching.BucketedTTLCache` (shared with ``webapp.disk_scans.cache``
and ``webapp.jobs.cache``) — a Redis-backed adapter shared across gunicorn
workers when ``CACHE_REDIS_URL`` is reachable, a per-worker in-process TTL
cache otherwise. This one has a single bucket; all that lives here is the
cache key.

Configuration is read from Flask app.config when available, falling back to
environment variables so the module works outside a Flask context (CLI, tests).

  ALLOCATION_USAGE_CACHE_TTL  — TTL in seconds (0 = disabled, default 3600)
  ALLOCATION_USAGE_CACHE_SIZE — max LRU entries  (0 = disabled, default 200)
"""

from datetime import datetime
from typing import Dict, List, Optional

import logging

from sam.caching import BucketedTTLCache, BucketSpec, CacheBase, norm
from sam.queries.allocations import get_allocation_summary_with_usage

logger = logging.getLogger(__name__)


#: One bucket — allocation usage has no second staleness population the way
#: fs-scans (passive vs explorer) and jobs (closed vs open window) do.
_CACHE = BucketedTTLCache('usage_cache', 'usage', {
    'default': BucketSpec(
        name='allocation_usage',
        ttl_key='ALLOCATION_USAGE_CACHE_TTL', ttl_default=3600,
        size_key='ALLOCATION_USAGE_CACHE_SIZE', size_default=200,
    ),
})


def get_cache_adapter() -> Optional[CacheBase]:
    """Return the shared CacheBase adapter, initializing on first call.

    Returns None when caching is disabled by config (TTL or SIZE == 0).
    """
    return _CACHE.adapter('default')


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

    Cache key is built from all parameters at day granularity (active_at -> date).
    Identical calls within the TTL window return cached results without hitting DB.

    Args:
        force_refresh: Bypass the cache and recompute from DB.  The fresh result
                       is stored back into the cache for subsequent callers.
        _summary: Optional pre-computed get_allocation_summary() result to pass through
                  to get_allocation_summary_with_usage(), skipping that internal call.
                  Only effective on a cache miss (cached results are returned as-is).

    All other args are forwarded unchanged to get_allocation_summary_with_usage().
    """
    def _compute():
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

    # Day granularity on active_at: allocation usage doesn't move within a
    # day, and keying on the raw timestamp would make every request a miss.
    key = (
        norm(resource_name),
        norm(facility_name),
        norm(allocation_type),
        norm(projcode),
        active_only,
        active_at.date() if isinstance(active_at, datetime) else active_at,
        include_adjustments,
        root_only,
    )
    return _CACHE.get_or_compute('default', key, _compute,
                                 force_refresh=force_refresh)


def purge_usage_cache() -> int:
    """Clear all cached usage data. Returns number of entries cleared."""
    return _CACHE.purge()


def usage_cache_info() -> Dict:
    """Return cache statistics for monitoring/admin display.

    Delegates to the adapter's `info()` (canonical CacheBase shape).
    Backwards-compatible: the legacy keys (`enabled`, `currsize`,
    `maxsize`, `ttl`) are still present; new fields (`hits`, `misses`,
    `bytes_approx`, `name`, `extras`) are additive. A dict rather than the
    per-bucket list the multi-bucket caches return — this cache has exactly
    one bucket and the Admin card renders it as a single row.
    """
    return _CACHE.info()[0]
