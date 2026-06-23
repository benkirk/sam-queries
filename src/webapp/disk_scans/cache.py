"""Scan-date-keyed cache for the slow fs-scans service queries.

Filesystem scans refresh ~weekly, so a query's result is valid until the
collection is re-scanned. We exploit that: every cache key embeds the
per-collection scan dates, so when a new scan lands the key changes and
the stale entry is simply never read again (and TTL-evicted later). No
manual flush, no stale-data window — invalidation is content-addressed.

Why this matters: a project whose directories include a *sub-path* of a
collection (e.g. ``/ncar/USGS_Water``) takes the on-the-fly path, which is
30-120s for the large collections (see the per-collection fast-path notes
in ``facade.py``). Whole-collection-root projects hit the pre-computed
tables and are sub-second — caching them is cheap insurance, not the win.

Mirrors ``sam.queries.usage_cache``: a lazily-initialised, Redis-backed
adapter shared across gunicorn workers when ``CACHE_REDIS_URL`` is set,
falling back to a per-worker in-process TTL cache otherwise. Registered
with the ``webapp.caching`` facade so it appears in Admin → Configuration.

Two buckets share this mechanism, differing only in name / size / TTL:

  * ``default`` (``fs_scans``) — passive/landing + tab-pill queries
    (no-filter + sort_by + limit). High reuse, long-lived.
  * ``filtered`` (``fs_scans_filtered``) — the explorer's owner / date /
    leaves-only permutations. Short TTL so the volatile permutations stay a
    small, transient footprint and self-expire rather than crowding the
    long-lived default entries. (A *soft* protection: ``allkeys-lru`` is
    instance-global, so under genuine Redis memory pressure a filtered write
    could still evict a default entry — the short TTL just keeps that window
    small. Chosen over an off-Redis bucket to keep cross-worker sharing.)

The service picks ``bucket='filtered'`` whenever any of owner_uid /
accessed_before / accessed_after / leaves_only is set, else ``'default'``.

Config (Flask app.config or env; 0 disables the corresponding bucket):
  FS_SCANS_CACHE_TTL            — default TTL seconds (default 691200 = 8 days,
                                  a memory backstop slightly longer than the
                                  weekly refresh; correctness comes from the
                                  scan-date key, not TTL)
  FS_SCANS_CACHE_SIZE           — default max LRU entries (default 256)
  FS_SCANS_FILTERED_CACHE_TTL   — filtered TTL seconds (default 1800 = 30 min)
  FS_SCANS_FILTERED_CACHE_SIZE  — filtered max LRU entries (default 128)

Key shape (hashable tuple):
  (query_type, collections, path_prefixes, opts, scan_date_signature)
``opts`` carries every query parameter NOT already captured by the resolved
scope — sort_by/limit/owner_uid today, and any Phase-3 filter kwargs
(owner, leaves-only, accessed-before, …) automatically as they're added to
the call. The default (no-filter) path is just ``opts`` at its defaults, so
one mechanism caches both the default and any filter selection.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    """Make list values hashable for use as a key component."""
    if isinstance(value, list):
        return tuple(sorted(str(v) for v in value))
    return value


# ---------------------------------------------------------------------------
# Lazy-initialised adapters (Redis shared / in-process fallback), one per bucket
# ---------------------------------------------------------------------------

# Bucket specs: name shown in the Admin card + the (config_key, default) pairs
# for TTL and size. Both buckets use the same backend, differing only here.
_BUCKETS: Dict[str, Dict[str, Any]] = {
    'default': {
        'name': 'fs_scans',
        'ttl':  ('FS_SCANS_CACHE_TTL', 691200),   # 8 days
        'size': ('FS_SCANS_CACHE_SIZE', 256),
    },
    'filtered': {
        'name': 'fs_scans_filtered',
        'ttl':  ('FS_SCANS_FILTERED_CACHE_TTL', 1800),   # 30 minutes
        'size': ('FS_SCANS_FILTERED_CACHE_SIZE', 128),
    },
}

# bucket -> adapter once initialised; a stored ``None`` means "initialised but
# disabled by config" (so we don't re-probe on every call).
_adapters: Dict[str, Optional[CacheBase]] = {}
_init_lock = threading.RLock()


def get_cache_adapter(bucket: str = 'default') -> Optional[CacheBase]:
    """Return the shared CacheBase adapter for *bucket*, init on first call.

    Returns ``None`` when that bucket is disabled by config (TTL or SIZE == 0).
    Backend mirrors ``usage_cache``: ``RedisTTLAdapter`` when ``CACHE_REDIS_URL``
    is reachable (all workers share one cache), else a per-worker
    ``TTLCacheAdapter``.
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
                    "fs_scans cache: CACHE_REDIS_URL=%s set but unreachable (%s); "
                    "falling back to per-worker TTLCacheAdapter.",
                    redis_url, exc,
                )

        adapter = TTLCacheAdapter(name=name, maxsize=size, ttl=ttl)
        _adapters[bucket] = adapter
        return adapter


def _scan_date_signature(q, collections) -> Optional[Tuple]:
    """Per-collection latest scan date, for the cache key.

    Returns ``None`` (→ skip caching) when no collection has a scan date —
    we can't key on freshness we don't have. Otherwise a sorted tuple of
    ``(collection, iso-date-or-None)``: when ANY collection is re-scanned
    the signature changes, busting every entry that depended on it.
    """
    sig = []
    for c in sorted(collections):
        dates = q.scan_dates(filesystems=[c])
        sig.append((c, max(dates).isoformat() if dates else None))
    if all(d is None for _, d in sig):
        return None
    return tuple(sig)


def cached_scan(
    query_type: str,
    q,
    collections: List[str],
    path_prefixes: List[str],
    opts: Dict[str, Any],
    compute: Callable[[], Any],
    bucket: str = 'default',
    database: Optional[str] = None,
) -> Any:
    """Return a cached scan result or compute + store it.

    *compute* must produce the FINAL caller-facing result (e.g. owner rows
    with resolved usernames already attached), so a cache hit reproduces it
    exactly without re-querying. The cache is keyed on the resolved scope +
    *opts* + the per-collection scan dates + *database*.

    *bucket* selects which adapter stores the entry — ``'filtered'`` for the
    short-TTL explorer permutations, ``'default'`` (the passive/landing path)
    otherwise. *database* is the CNPG database the query targets; it's part of
    the key so collection-name collisions across databases (e.g. a schema named
    the same in ``campaign`` and ``desc1``) never share a cache entry.
    """
    adapter = get_cache_adapter(bucket)
    if adapter is None:
        return compute()

    sig = _scan_date_signature(q, collections)
    if sig is None:
        return compute()  # no scan dates to key on — don't cache

    key = (
        query_type,
        database,
        tuple(sorted(collections)),
        tuple(sorted(path_prefixes)),
        tuple(sorted((k, _norm(v)) for k, v in opts.items())),
        sig,
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

def purge_fs_scans_cache() -> int:
    """Clear every scan-cache bucket. Returns the total entries cleared."""
    total = 0
    for bucket in _BUCKETS:
        adapter = get_cache_adapter(bucket)
        if adapter is not None:
            total += adapter.clear()
    return total


def fs_scans_cache_info() -> List[Dict]:
    """One uniform CacheBase ``info()`` dict per bucket, for the Admin card.

    Returns a list (default bucket first) so the Configuration card can loop
    and surface each bucket's TTL — making the 30-min explorer TTL visible
    alongside the 8-day default.
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
