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

Config (Flask app.config or env; 0 disables):
  FS_SCANS_CACHE_TTL   — TTL seconds (default 691200 = 8 days, a memory
                         backstop slightly longer than the weekly refresh;
                         correctness comes from the scan-date key, not TTL)
  FS_SCANS_CACHE_SIZE  — max LRU entries (default 256)

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
# Lazy-initialised adapter (Redis shared / in-process fallback)
# ---------------------------------------------------------------------------

_adapter: Optional[CacheBase] = None
_init_lock = threading.RLock()
_disabled = False


def get_cache_adapter() -> Optional[CacheBase]:
    """Return the shared CacheBase adapter, initialising on first call.

    Returns ``None`` when disabled by config (TTL or SIZE == 0). Backend
    mirrors ``usage_cache``: ``RedisTTLAdapter`` when ``CACHE_REDIS_URL`` is
    reachable (all workers share one cache), else a per-worker
    ``TTLCacheAdapter``.
    """
    global _adapter, _disabled

    with _init_lock:
        if _adapter is not None or _disabled:
            return None if _disabled else _adapter

        ttl  = _get_config('FS_SCANS_CACHE_TTL', 691200)
        size = _get_config('FS_SCANS_CACHE_SIZE', 256)
        if ttl <= 0 or size <= 0:
            _disabled = True
            return None

        redis_url = os.environ.get('CACHE_REDIS_URL')
        if redis_url:
            try:
                client = make_redis_client(redis_url)
                if client is not None:
                    _adapter = RedisTTLAdapter(
                        name='fs_scans', client=client, ttl=ttl, maxsize=size,
                    )
                    return _adapter
            except Exception as exc:
                logger.warning(
                    "fs_scans cache: CACHE_REDIS_URL=%s set but unreachable (%s); "
                    "falling back to per-worker TTLCacheAdapter.",
                    redis_url, exc,
                )

        _adapter = TTLCacheAdapter(name='fs_scans', maxsize=size, ttl=ttl)
        return _adapter


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
) -> Any:
    """Return a cached scan result or compute + store it.

    *compute* must produce the FINAL caller-facing result (e.g. owner rows
    with resolved usernames already attached), so a cache hit reproduces it
    exactly without re-querying. The cache is keyed on the resolved scope +
    *opts* + the per-collection scan dates.
    """
    adapter = get_cache_adapter()
    if adapter is None:
        return compute()

    sig = _scan_date_signature(q, collections)
    if sig is None:
        return compute()  # no scan dates to key on — don't cache

    key = (
        query_type,
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
    """Clear all cached scan results. Returns the number of entries cleared."""
    adapter = get_cache_adapter()
    return adapter.clear() if adapter is not None else 0


def fs_scans_cache_info() -> Dict:
    """Uniform CacheBase ``info()`` dict for the Admin → Configuration card."""
    adapter = get_cache_adapter()
    if adapter is None:
        ttl  = _get_config('FS_SCANS_CACHE_TTL', 691200)
        size = _get_config('FS_SCANS_CACHE_SIZE', 256)
        return disabled_info('fs_scans', maxsize=size, ttl=ttl)
    return adapter.info()
