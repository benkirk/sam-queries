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

Backend, lazy init and the get/compute/store dance all come from
:class:`sam.caching.BucketedTTLCache` (shared with ``jobs/cache.py`` and
``sam.queries.usage_cache``): a Redis-backed adapter shared across gunicorn
workers when ``CACHE_REDIS_URL`` is set, falling back to a per-worker
in-process TTL cache otherwise. Registered with the ``webapp.caching``
facade so it appears in Admin -> Configuration. All that is left here is the
cache key — which is the interesting part, see below.

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
  (query_type, database, collections, path_prefixes, opts, scan_date_signature)
``opts`` carries every query parameter NOT already captured by the resolved
scope — sort_by/limit/owner_uid today, and any Phase-3 filter kwargs
(owner, leaves-only, accessed-before, …) automatically as they're added to
the call. The default (no-filter) path is just ``opts`` at its defaults, so
one mechanism caches both the default and any filter selection.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from sam.caching import BucketedTTLCache, BucketSpec, CacheBase, norm

logger = logging.getLogger(__name__)


#: Bucket specs: the Redis prefix / Admin-card label plus the config knobs.
#: Both buckets use the same backend, differing only here. Declaration order
#: is the Admin card's display order — default first.
_CACHE = BucketedTTLCache('fs_scans', 'scans', {
    'default': BucketSpec(
        name='fs_scans',
        ttl_key='FS_SCANS_CACHE_TTL', ttl_default=691200,           # 8 days
        size_key='FS_SCANS_CACHE_SIZE', size_default=256,
    ),
    'filtered': BucketSpec(
        name='fs_scans_filtered',
        ttl_key='FS_SCANS_FILTERED_CACHE_TTL', ttl_default=1800,    # 30 minutes
        size_key='FS_SCANS_FILTERED_CACHE_SIZE', size_default=128,
    ),
})

#: Test seams. ``_BUCKETS`` enumerates the bucket keys; ``_adapters`` IS the
#: cache's memo dict (same object), so a test that clears it re-initializes
#: this cache — the pre-existing idiom, preserved through the extraction.
_BUCKETS = _CACHE.buckets
_adapters = _CACHE._adapters


def get_cache_adapter(bucket: str = 'default') -> Optional[CacheBase]:
    """Return the shared CacheBase adapter for *bucket* (``None`` if disabled)."""
    return _CACHE.adapter(bucket)


def _scan_date_signature(q, collections) -> Optional[Tuple]:
    """Per-collection latest scan date, for the cache key.

    Returns ``None`` (-> skip caching) when no collection has a scan date —
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
    if _CACHE.adapter(bucket) is None:
        return compute()

    sig = _scan_date_signature(q, collections)
    if sig is None:
        return compute()  # no scan dates to key on — don't cache

    key = (
        query_type,
        database,
        tuple(sorted(collections)),
        tuple(sorted(path_prefixes)),
        tuple(sorted((k, norm(v)) for k, v in opts.items())),
        sig,
    )
    return _CACHE.get_or_compute(bucket, key, compute)


# ---------------------------------------------------------------------------
# Admin / facade hooks
# ---------------------------------------------------------------------------

def purge_fs_scans_cache() -> int:
    """Clear every scan-cache bucket. Returns the total entries cleared."""
    return _CACHE.purge()


def fs_scans_cache_info() -> List[Dict]:
    """One uniform CacheBase ``info()`` dict per bucket, for the Admin card.

    Returns a list (default bucket first) so the Configuration card can loop
    and surface each bucket's TTL — making the 30-min explorer TTL visible
    alongside the 8-day default.
    """
    return _CACHE.info()
