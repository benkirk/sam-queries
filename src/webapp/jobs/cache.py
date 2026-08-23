"""TTL cache for the job-history aggregation queries.

Unlike fs-scans, job data appends continuously -- there is no freshness
signature to key on, so this is a plain TTL cache. Two buckets, picked by
whether the requested window can still change: ``historical`` (window ends
before today) and ``recent`` (touches today, or unbounded). Historical caps at
30 minutes rather than hours because records keep arriving for closed windows.

WARNING: the chart SVG caches (``webapp.caching.chart_cached``) are NOT the
freshness lever they look like -- their keys are content hashes of the plotted
data, so a stale entry can only be served for data that has not changed. These
TTLs are the only thing deciding how fresh a panel is.

Only aggregations go through here (~0.5-0.6 s warm per month-window, on every
card tab). Paged search and counts stay uncached: cheaper, highly
parameterized, and users expect row-level freshness.

Mechanism, backend and facade registration come from
:class:`sam.caching.BucketedTTLCache`, shared with ``disk_scans/cache.py``.
Sizes are load-bearing only for the in-process fallback -- under Redis,
eviction is instance-global and maxsize is advisory.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from sam.caching import BucketedTTLCache, BucketSpec, CacheBase, norm

logger = logging.getLogger(__name__)


#: Bucket specs: the Redis prefix / Admin-card label plus the config knobs.
#: Both buckets use the same backend, differing only here. Declaration order
#: is the Admin card's display order — historical first.
_CACHE = BucketedTTLCache('jobs', 'jobs', {
    'historical': BucketSpec(
        name='jobs',
        ttl_key='JOBS_CACHE_TTL', ttl_default=1800,          # 30 minutes
        size_key='JOBS_CACHE_SIZE', size_default=512,
    ),
    'recent': BucketSpec(
        name='jobs_recent',
        ttl_key='JOBS_RECENT_CACHE_TTL', ttl_default=900,    # 15 minutes
        size_key='JOBS_RECENT_CACHE_SIZE', size_default=512,
    ),
})

#: Test seams. ``_BUCKETS`` enumerates the bucket keys; ``_adapters`` IS the
#: cache's memo dict (same object), so a test that clears it re-initializes
#: this cache — the pre-existing idiom, preserved through the extraction.
_BUCKETS = _CACHE.buckets
_adapters = _CACHE._adapters


def get_cache_adapter(bucket: str = 'historical') -> Optional[CacheBase]:
    """Return the shared CacheBase adapter for *bucket* (``None`` if disabled)."""
    return _CACHE.adapter(bucket)


def bucket_for_window(end: Optional[date]) -> str:
    """Pick the cache bucket for a query window ending at *end*.

    ``historical`` only when the window is provably closed: an explicit
    ``end`` strictly before today. An open end (None) or a window that
    touches today keeps collecting jobs -> ``recent``.
    """
    if end is not None and end < date.today():
        return 'historical'
    return 'recent'


def cached_jobs_aggregation(
    query_type: str,
    machine: str,
    opts: Dict[str, Any],
    compute: Callable[[], Any],
    bucket: str,
) -> Any:
    """Return a cached aggregation result or compute + store it.

    *compute* must produce the FINAL caller-facing result (the plugin's
    self-describing envelope), so a cache hit reproduces it exactly
    without re-querying. *opts* must carry every parameter that shapes
    the result — the caller passes the exact plugin kwargs plus any
    SAM-side knobs (dimension, limit).
    """
    key = (
        query_type,
        machine,
        tuple(sorted((k, norm(v)) for k, v in opts.items())),
    )
    return _CACHE.get_or_compute(bucket, key, compute)


# ---------------------------------------------------------------------------
# Admin / facade hooks
# ---------------------------------------------------------------------------

def purge_jobs_cache() -> int:
    """Clear every jobs-cache bucket. Returns the total entries cleared."""
    return _CACHE.purge()


def jobs_cache_info() -> List[Dict]:
    """One uniform CacheBase ``info()`` dict per bucket, for the Admin card.

    Returns a list (historical bucket first) so the Configuration card can
    loop and surface each bucket's TTL — making the 15-min recent TTL
    visible alongside the 30-min historical one.
    """
    return _CACHE.info()
