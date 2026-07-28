"""TTL cache for the job-history aggregation queries.

Unlike fs-scans (weekly refreshes → content-addressed scan-date keys),
job data appends continuously — there is no freshness signature to key
on, so this is a plain TTL cache. Two buckets share the mechanism,
picked by whether the requested window can still change:

  * ``historical`` (``jobs``) — the window's ``end`` date is strictly
    before today, so its aggregation is *nearly* immutable. Not entirely:
    job records keep arriving for windows already closed, which is why
    this caps out at 30 minutes rather than hours.
  * ``recent`` (``jobs_recent``) — the window touches today (or has no
    end bound), so new jobs keep landing in it. Short TTL keeps the
    staleness window at ~15 minutes.

Note the chart SVG caches (``webapp.caching.chart_cached``) are NOT the
freshness lever they look like: their keys are content hashes of the data
being plotted, so a stale entry can only be served for data that has not
changed. These TTLs are the only thing that decides how fresh a panel is.

Only the aggregations (histograms, usage-by rollups) go through here —
they cost ~0.5-0.6 s warm per month-window against the plugin PG and
back every card tab. Paged search + counts stay uncached: they're
cheaper, highly parameterized, and users expect row-level freshness.

Backend mirrors ``disk_scans/cache.py`` / ``sam.queries.usage_cache``:
Redis-backed adapter shared across gunicorn workers when
``CACHE_REDIS_URL`` is set, per-worker in-process TTL cache otherwise.
Registered with the ``webapp.caching`` facade (category ``jobs``) so it
appears in Admin → Configuration and clears via the same surfaces.

Config (Flask app.config or env; 0 disables the corresponding bucket):
  JOBS_CACHE_TTL          — historical TTL seconds (default 1800 = 30 min)
  JOBS_CACHE_SIZE         — historical max LRU entries (default 512)
  JOBS_RECENT_CACHE_TTL   — recent TTL seconds (default 900 = 15 min)
  JOBS_RECENT_CACHE_SIZE  — recent max LRU entries (default 512)

The sizes are set for the explorer, which fans out far more distinct keys
than the cards ever did: per filter combination, up to 8 histogram
dimensions x 2 owner axes, plus 2 usage rollups x 3 sort orders — roughly
22 entries. (Under Redis eviction is instance-global ``allkeys-lru`` and
maxsize is advisory; the sizes are load-bearing for the in-process
fallback used in local dev and any Redis-less deploy.)

Key shape (hashable tuple):
  (query_type, machine, sorted(normalized opts))
``opts`` carries every parameter that shapes the result — the flat
filter set plus dimension/limit — normalized so dates and lists hash
stably.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from sam.caching import CacheBase, RedisTTLAdapter, TTLCacheAdapter, make_redis_client
from sam.caching.ttl import disabled_info

logger = logging.getLogger(__name__)


def _get_config(key: str, default: int) -> int:
    """Read config from Flask app context if available, else env, else default."""
    try:
        from flask import current_app
        return int(current_app.config.get(key, default))
    except RuntimeError:
        return int(os.environ.get(key, default))


def _norm(value: Any):
    """Make list/date values hashable + stable for use as a key component."""
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted(str(v) for v in value))
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# Lazy-initialised adapters (Redis shared / in-process fallback), one per bucket
# ---------------------------------------------------------------------------

# Bucket specs: name shown in the Admin card + the (config_key, default) pairs
# for TTL and size. Both buckets use the same backend, differing only here.
_BUCKETS: Dict[str, Dict[str, Any]] = {
    'historical': {
        'name': 'jobs',
        'ttl':  ('JOBS_CACHE_TTL', 1800),   # 30 minutes
        'size': ('JOBS_CACHE_SIZE', 512),
    },
    'recent': {
        'name': 'jobs_recent',
        'ttl':  ('JOBS_RECENT_CACHE_TTL', 900),   # 15 minutes
        'size': ('JOBS_RECENT_CACHE_SIZE', 512),
    },
}

# bucket -> adapter once initialised; a stored ``None`` means "initialised but
# disabled by config" (so we don't re-probe on every call).
_adapters: Dict[str, Optional[CacheBase]] = {}
_init_lock = threading.RLock()


def get_cache_adapter(bucket: str = 'historical') -> Optional[CacheBase]:
    """Return the shared CacheBase adapter for *bucket*, init on first call.

    Returns ``None`` when that bucket is disabled by config (TTL or SIZE == 0).
    Backend mirrors ``disk_scans/cache.py``: ``RedisTTLAdapter`` when
    ``CACHE_REDIS_URL`` is reachable (all workers share one cache), else a
    per-worker ``TTLCacheAdapter``.
    """
    spec = _BUCKETS[bucket]

    with _init_lock:
        if bucket in _adapters:
            return _adapters[bucket]

        ttl  = _get_config(*spec['ttl'])
        size = _get_config(*spec['size'])
        if ttl <= 0 or size <= 0:
            _adapters[bucket] = None
            return None

        name = spec['name']
        redis_url = os.environ.get('CACHE_REDIS_URL')
        if redis_url:
            try:
                client = make_redis_client(redis_url)
                if client is not None:
                    adapter = RedisTTLAdapter(
                        name=name, client=client, ttl=ttl, maxsize=size,
                    )
                    _adapters[bucket] = adapter
                    return adapter
            except Exception as exc:
                logger.warning(
                    "jobs cache: CACHE_REDIS_URL=%s set but unreachable (%s); "
                    "falling back to per-worker TTLCacheAdapter.",
                    redis_url, exc,
                )

        adapter = TTLCacheAdapter(name=name, maxsize=size, ttl=ttl)
        _adapters[bucket] = adapter
        return adapter


def bucket_for_window(end: Optional[date]) -> str:
    """Pick the cache bucket for a query window ending at *end*.

    ``historical`` only when the window is provably closed: an explicit
    ``end`` strictly before today. An open end (None) or a window that
    touches today keeps collecting jobs → ``recent``.
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
    adapter = get_cache_adapter(bucket)
    if adapter is None:
        return compute()

    key = (
        query_type,
        machine,
        tuple(sorted((k, _norm(v)) for k, v in opts.items())),
    )

    with adapter.lock:
        if key in adapter:
            return adapter[key]
        adapter.pop(key, None)

    result = compute()

    with adapter.lock:
        try:
            adapter[key] = result
        except ValueError:
            # Cache full and no expired entries to evict — skip the store.
            pass
    return result


# ---------------------------------------------------------------------------
# Admin / facade hooks
# ---------------------------------------------------------------------------

def purge_jobs_cache() -> int:
    """Clear every jobs-cache bucket. Returns the total entries cleared."""
    total = 0
    for bucket in _BUCKETS:
        adapter = get_cache_adapter(bucket)
        if adapter is not None:
            total += adapter.clear()
    return total


def jobs_cache_info() -> List[Dict]:
    """One uniform CacheBase ``info()`` dict per bucket, for the Admin card.

    Returns a list (historical bucket first) so the Configuration card can
    loop and surface each bucket's TTL — making the 15-min recent TTL
    visible alongside the 30-min historical one.
    """
    infos: List[Dict] = []
    for bucket, spec in _BUCKETS.items():
        adapter = get_cache_adapter(bucket)
        if adapter is None:
            ttl  = _get_config(*spec['ttl'])
            size = _get_config(*spec['size'])
            infos.append(disabled_info(spec['name'], maxsize=size, ttl=ttl))
        else:
            infos.append(adapter.info())
    return infos
