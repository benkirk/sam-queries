"""
RedisChartCache — drop-in replacement for ChartCache backed by Redis.

Mirrors `webapp.caching.chart.ChartCache` so the `chart_cached` decorator
needs no changes. Eviction is Redis-wide (allkeys-lru); per-cache
`maxsize` is dropped. Hits/misses survive worker rotation because the
counters live in Redis under `chart:hits:{name}` / `chart:misses:{name}`.
"""

from collections import namedtuple
from typing import Any, Callable, Optional

import redis

from sam.caching import CacheBase


_CacheInfo = namedtuple('CacheInfo', ['hits', 'misses', 'maxsize', 'currsize'])

_DEFAULT_TTL = 600  # seconds; mirrors flask-cache CACHE_DEFAULT_TIMEOUT


class RedisChartCache(CacheBase):
    """Redis-backed SVG cache with the same get/put surface as ChartCache."""

    def __init__(self,
                 name: str,
                 *,
                 client: redis.Redis,
                 ttl: int = _DEFAULT_TTL):
        self.name = name
        self._client = client
        self._ttl = int(ttl)
        self._key_prefix = f'chart:{name}:'.encode()
        self._hits_key = f'chart:hits:{name}'.encode()
        self._misses_key = f'chart:misses:{name}'.encode()
        # Recorded for legacy cache_info() consumers; not enforced.
        self._maxsize = None

    # ── helpers ──────────────────────────────────────────────────────────

    def _entry_key(self, key: str) -> bytes:
        return self._key_prefix + key.encode()

    def _scan_entries(self):
        return self._client.scan_iter(match=self._key_prefix + b'*', count=200)

    # ── ChartCache API surface ───────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        raw = self._client.get(self._entry_key(key))
        if raw is None:
            self._client.incr(self._misses_key)
            return None
        self._client.incr(self._hits_key)
        return raw.decode() if isinstance(raw, bytes) else raw

    def put(self, key: str, value: str) -> None:
        self._client.set(
            self._entry_key(key),
            value.encode() if isinstance(value, str) else value,
            ex=self._ttl,
        )

    def cache_info(self) -> _CacheInfo:
        return _CacheInfo(
            hits=self._read_counter(self._hits_key),
            misses=self._read_counter(self._misses_key),
            maxsize=self._maxsize,
            currsize=self._count_entries(),
        )

    def cache_clear(self) -> None:
        self.clear()

    def bytes_used(self) -> int:
        # Per-key MEMORY USAGE on every entry would be expensive on a
        # populated cache; sample the first 50 entries and extrapolate.
        sampled = 0
        sample_count = 0
        try:
            for key in self._scan_entries():
                try:
                    used = self._client.memory_usage(key) or 0
                except (redis.RedisError, AttributeError):
                    return 0
                sampled += int(used)
                sample_count += 1
                if sample_count >= 50:
                    break
            if sample_count == 0:
                return 0
            total_entries = self._count_entries()
            return int((sampled / sample_count) * total_entries)
        except redis.RedisError:
            return 0

    # ── CacheBase ───────────────────────────────────────────────────────

    def info(self) -> dict:
        return {
            'name':         self.name,
            'enabled':      True,
            'currsize':     self._count_entries(),
            'maxsize':      self._maxsize,
            'ttl':          float(self._ttl),
            'hits':         self._read_counter(self._hits_key),
            'misses':       self._read_counter(self._misses_key),
            'bytes_approx': self.bytes_used(),
            'extras':       {'backend': 'redis'},
        }

    def clear(self) -> int:
        n = 0
        try:
            for key in list(self._scan_entries()):
                if self._client.delete(key):
                    n += 1
            self._client.delete(self._hits_key, self._misses_key)
        except redis.RedisError:
            pass
        return n

    # ── internals ───────────────────────────────────────────────────────

    def _count_entries(self) -> int:
        try:
            return sum(1 for _ in self._scan_entries())
        except redis.RedisError:
            return 0

    def _read_counter(self, key: bytes) -> int:
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            return 0
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0


def chart_cached_redis(cache: RedisChartCache,
                       key_fn: Optional[Callable] = None):
    """Decorator factory mirroring `webapp.caching.chart.chart_cached`.

    Kept separate from the in-process variant so the import graph is
    explicit; the wrapped callable surface is identical.
    """
    import functools

    from webapp.caching.chart import content_hash
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


__all__ = ['RedisChartCache', 'chart_cached_redis']
