"""
TTLCacheAdapter — wraps cachetools.TTLCache to conform to CacheBase.
"""

import threading
from typing import Any, Hashable, Optional

from cachetools import TTLCache

from sam.caching.base import CacheBase, approx_bytes


class TTLCacheAdapter(CacheBase):
    """`cachetools.TTLCache` wrapped to match the CacheBase contract.

    Used by `sam.queries.usage_cache` for the allocation summary cache.
    Provides the standard get/pop/setitem operations the existing
    consumer expects, plus the `info()`/`clear()` surface every adapter
    exposes.

    Hits/misses are not tracked — cachetools doesn't expose counters
    natively and instrumenting that is out of scope. Operators see
    `hits=None, misses=None` in the admin card.
    """

    def __init__(self, name: str, *, maxsize: int, ttl: float):
        self.name = name
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()

    # ── thin operations the wrapping query function uses directly ────────

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def __contains__(self, key: Hashable) -> bool:
        return key in self._cache

    def __getitem__(self, key: Hashable) -> Any:
        return self._cache[key]

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self._cache[key] = value

    def pop(self, key: Hashable, default: Any = None) -> Any:
        return self._cache.pop(key, default)

    @property
    def maxsize(self) -> int:
        return self._cache.maxsize

    @property
    def ttl(self) -> float:
        return self._cache.ttl

    # ── CacheBase ───────────────────────────────────────────────────────

    def info(self) -> dict:
        with self._lock:
            currsize = len(self._cache)
            # Approximate bytes by walking entries. For the allocation
            # usage cache the values are list[dict] — approx_bytes
            # captures container overhead + leaf sizes (lower bound).
            bytes_approx = approx_bytes(dict(self._cache))
        return {
            'name':         self.name,
            'enabled':      True,
            'currsize':     currsize,
            'maxsize':      self._cache.maxsize,
            'ttl':          self._cache.ttl,
            'hits':         None,
            'misses':       None,
            'bytes_approx': bytes_approx,
            'extras':       {},
        }

    def clear(self) -> int:
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
        return n


def disabled_info(name: str, *, maxsize: Optional[int] = None,
                  ttl: Optional[float] = None) -> dict:
    """Stats dict for a cache that's been turned off via config (TTL=0 or size=0)."""
    return {
        'name':         name,
        'enabled':      False,
        'currsize':     0,
        'maxsize':      maxsize,
        'ttl':          ttl,
        'hits':         None,
        'misses':       None,
        'bytes_approx': 0,
        'extras':       {},
    }
