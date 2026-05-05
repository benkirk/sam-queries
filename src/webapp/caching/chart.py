"""
ChartCache — bounded LRU for matplotlib SVG strings, with native hits/misses.

Lifted from webapp.dashboards.charts._ChartCache (which it now replaces).
Implements the CacheBase contract so chart caches show up in the unified
admin Caching card alongside Flask-Cache and the allocation usage cache.
"""

import hashlib
import json
import threading
from collections import OrderedDict, namedtuple
from typing import Any, Callable, Optional

from sam.caching import CacheBase


_CacheInfo = namedtuple('CacheInfo', ['hits', 'misses', 'maxsize', 'currsize'])


def content_hash(data: Any) -> str:
    """Stable MD5 hex digest of arbitrary JSON-serialisable data.

    O(n) compute, O(1) memory — suitable as a cache key for large inputs
    where materialising a hashable tuple would be prohibitive.
    """
    return hashlib.md5(
        json.dumps(data, default=str, sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


class ChartCache(CacheBase):
    """Thread-safe bounded LRU for rendered SVG strings."""

    def __init__(self, name: str, maxsize: int):
        self.name = name
        self._data: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._hits = self._misses = 0

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            else:
                if len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
                self._data[key] = value

    # ── functools.lru_cache-compatible accessors (existing-caller surface) ──

    def cache_info(self) -> _CacheInfo:
        with self._lock:
            return _CacheInfo(
                hits=self._hits,
                misses=self._misses,
                maxsize=self._maxsize,
                currsize=len(self._data),
            )

    def cache_clear(self) -> None:
        self.clear()

    def bytes_used(self) -> int:
        # SVG values are ASCII-dominant strings — len(str) is the character
        # count, which for ASCII content matches UTF-8 byte count within ~1%.
        # Avoids sys.getsizeof's Python-string-overhead noise (49 bytes per).
        with self._lock:
            return sum(len(v) for v in self._data.values())

    # ── CacheBase ───────────────────────────────────────────────────────

    def info(self) -> dict:
        with self._lock:
            return {
                'name':         self.name,
                'enabled':      True,
                'currsize':     len(self._data),
                'maxsize':      self._maxsize,
                'ttl':          None,
                'hits':         self._hits,
                'misses':       self._misses,
                'bytes_approx': sum(len(v) for v in self._data.values()),
                'extras':       {},
            }

    def clear(self) -> int:
        with self._lock:
            n = len(self._data)
            self._data.clear()
            self._hits = self._misses = 0
            return n


def chart_cached(cache: ChartCache, key_fn: Optional[Callable] = None):
    """Internal decorator factory used by the Caching facade.

    Wraps `fn` so calls hit `cache` first; on miss, computes the SVG and
    stores it. Exposes `cache_info` / `cache_clear` / `cache_bytes` on
    the wrapped callable to match the legacy functools.lru_cache surface
    (some callers and tests probe these attributes directly).
    """
    import functools

    _key = key_fn or (lambda *args, **kwargs: content_hash(args[0]))

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _key(*args, **kwargs)
            result = cache.get(key)
            if result is None:
                result = fn(*args, **kwargs)
                cache.put(key, result)
            return result

        wrapper.cache_info = cache.cache_info
        wrapper.cache_clear = cache.cache_clear
        wrapper.cache_bytes = cache.bytes_used
        return wrapper

    return decorator
