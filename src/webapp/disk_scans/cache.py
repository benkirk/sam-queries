"""Scan-date-keyed cache for the slow fs-scans service queries.

Filesystem scans refresh ~weekly, so a result is valid until the collection is
re-scanned. Every cache key embeds the per-collection scan dates, so a new scan
changes the key and the stale entry is never read again. Invalidation is
content-addressed: no manual flush, no stale-data window. The TTL is only a
memory backstop.

Worth caching because a project including a *sub-path* of a collection takes
the on-the-fly path -- 30-120 s for the large collections. Whole-collection-root
projects hit pre-computed tables and are sub-second.

Two buckets: ``default`` for the passive/landing and tab-pill queries, and
``filtered`` for the explorer's owner/date/leaves-only permutations, on a short
TTL so volatile keys self-expire rather than crowding the long-lived ones.
That is SOFT protection -- ``allkeys-lru`` is instance-global, so a filtered
write can still evict a default entry under real memory pressure.

Mechanism, backend and facade registration come from
:class:`sam.caching.BucketedTTLCache`, shared with ``jobs/cache.py``. Config
constants and the key shape are below.
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
