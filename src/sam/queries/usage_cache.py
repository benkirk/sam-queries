"""
In-memory TTL cache for get_allocation_summary_with_usage() results.

Sits transparently behind the query function. Works in both webapp and CLI.
Bypass with force_refresh=True; purge programmatically with purge_usage_cache().

Backend, lazy init and the get/compute/store dance come from
:class:`sam.caching.BucketedTTLCache` (shared with ``webapp.disk_scans.cache``
and ``webapp.jobs.cache``) — a Redis-backed adapter shared across gunicorn
workers when ``CACHE_REDIS_URL`` is reachable, a per-worker in-process TTL
cache otherwise. Two buckets: usage, and the calendar's monthly burn, whose
past months do not move and so earn a longer TTL. What lives here is the keys.

Configuration is read from Flask app.config when available, falling back to
environment variables so the module works outside a Flask context (CLI, tests).

  ALLOCATION_USAGE_CACHE_TTL  — TTL in seconds (0 = disabled, default 3600)
  ALLOCATION_USAGE_CACHE_SIZE — max LRU entries  (0 = disabled, default 200)
  ALLOCATION_BURN_CACHE_TTL   — TTL in seconds (0 = disabled, default 43200)
  ALLOCATION_BURN_CACHE_SIZE  — max LRU entries  (0 = disabled, default 50)
"""

from datetime import datetime
from typing import Dict, List, Optional

import logging

from sam.caching import BucketedTTLCache, BucketSpec, CacheBase, norm
from sam.queries.allocations import (
    get_allocation_burn,
    get_allocation_summary_with_usage,
    get_allocation_usage_rows,
)
from sam.queries.charges import get_charges_by_facility_type

logger = logging.getLogger(__name__)


#: A burn entry is keyed on its as-of day and only that day's month still moves,
#: so it outlives a usage entry; both purge together under the 'usage' category.
_CACHE = BucketedTTLCache('usage_cache', 'usage', {
    'default': BucketSpec(
        name='allocation_usage',
        ttl_key='ALLOCATION_USAGE_CACHE_TTL', ttl_default=3600,
        size_key='ALLOCATION_USAGE_CACHE_SIZE', size_default=200,
    ),
    'burn': BucketSpec(
        name='allocation_burn',
        ttl_key='ALLOCATION_BURN_CACHE_TTL', ttl_default=43200,
        size_key='ALLOCATION_BURN_CACHE_SIZE', size_default=50,
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


def cached_allocation_usage_rows(
    session,
    *,
    resource_name,
    window_start: datetime,
    window_end: datetime,
    as_of: datetime,
    force_refresh: bool = False,
) -> List[Dict]:
    """Cached wrapper for get_allocation_usage_rows(), keyed at day granularity."""
    def _compute():
        return get_allocation_usage_rows(
            session, resource_name=resource_name, window_start=window_start,
            window_end=window_end, as_of=as_of,
        )

    key = ('rows', norm(resource_name), window_start.date(), window_end.date(),
           as_of.date())
    return _CACHE.get_or_compute('default', key, _compute,
                                 force_refresh=force_refresh)


def cached_allocation_burn(
    session,
    *,
    resource_name,
    window_start: datetime,
    window_end: datetime,
    as_of: datetime,
    force_refresh: bool = False,
) -> Dict[int, Dict[int, float]]:
    """Cached wrapper for get_allocation_burn(), keyed at day granularity."""
    def _compute():
        return get_allocation_burn(
            session, resource_name=resource_name, window_start=window_start,
            window_end=window_end, as_of=as_of,
        )

    key = ('burn', norm(resource_name), window_start.date(), window_end.date(),
           as_of.date())
    return _CACHE.get_or_compute('burn', key, _compute,
                                 force_refresh=force_refresh)


def cached_charges_by_facility_type(session, *, resource_names, start: datetime,
                                    end: datetime, force_refresh: bool = False) -> List[Dict]:
    """Cached wrapper for get_charges_by_facility_type(), keyed at day granularity."""
    def _compute():
        return get_charges_by_facility_type(session, resource_names, start, end)

    key = ('window', norm(resource_names), start.date(), end.date())
    return _CACHE.get_or_compute('default', key, _compute, force_refresh=force_refresh)


def purge_usage_cache() -> int:
    """Clear all cached usage data. Returns number of entries cleared."""
    return _CACHE.purge()


def usage_cache_info() -> Dict:
    """Return cache statistics for monitoring/admin display.

    Delegates to the adapter's `info()` (canonical CacheBase shape).
    Backwards-compatible: the legacy keys (`enabled`, `currsize`,
    `maxsize`, `ttl`) are still present; new fields (`hits`, `misses`,
    `bytes_approx`, `name`, `extras`) are additive. A dict rather than the
    per-bucket list the multi-bucket caches return: the Admin card renders the
    usage bucket as one row and the burn bucket (burn_cache_info) as another.
    """
    return _CACHE.info()[0]


def burn_cache_info() -> Dict:
    """The calendar-burn bucket's info dict, a second row on the Admin card."""
    return _CACHE.info()[1]
