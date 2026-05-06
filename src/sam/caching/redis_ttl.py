"""
RedisTTLAdapter — drop-in replacement for TTLCacheAdapter backed by Redis.

Exposes the same dict-like API used by `sam.queries.usage_cache` so call
sites are unchanged. Eviction is handled Redis-wide via `allkeys-lru`,
not per-cache `maxsize`. Keys are namespaced under a fixed prefix so a
single Redis DB can host this adapter and `RedisChartCache` without
collisions.
"""

import contextlib
import os
import pickle
import threading
from typing import Any, Hashable, Optional

import redis

from sam.caching.base import CacheBase


_DEFAULT_PREFIX = 'usage:'


@contextlib.contextmanager
def _noop_lock():
    """No-op context manager.

    Why: Redis ops are atomic, and a per-process lock cannot coordinate
    state across gunicorn workers anyway. Keeping the `.lock` attribute
    keeps usage_cache.py call sites identical between adapters.
    """
    yield


class RedisTTLAdapter(CacheBase):
    """Redis-backed cache conforming to the dict-like API of TTLCacheAdapter.

    Keys are pickled (call-sites use tuple keys) and namespaced under
    `prefix:`. Per-key TTL is set on `__setitem__` so entries expire in
    Redis the same way `cachetools.TTLCache` would.

    `maxsize` is recorded for `info()` reporting only — bounding is
    handled by Redis-wide `allkeys-lru`.
    """

    def __init__(self,
                 name: str,
                 *,
                 client: redis.Redis,
                 ttl: float,
                 maxsize: Optional[int] = None,
                 prefix: str = _DEFAULT_PREFIX):
        self.name = name
        self._client = client
        self._ttl = float(ttl)
        self._maxsize = maxsize
        self._prefix = prefix
        # Internal lock guards only Python-side bookkeeping; cross-worker
        # consistency is provided by Redis itself.
        self._py_lock = threading.RLock()

    # ── helpers ──────────────────────────────────────────────────────────

    def _encode_key(self, key: Hashable) -> bytes:
        # protocol=4 is universally available on Python ≥ 3.4 and yields
        # a stable byte representation across processes.
        return self._prefix.encode() + pickle.dumps(key, protocol=4)

    # ── lock surface (no-op; see module docstring) ───────────────────────

    @property
    def lock(self):
        return _noop_lock()

    # ── dict-like API used by usage_cache.py:138-163 ─────────────────────

    def __contains__(self, key: Hashable) -> bool:
        return bool(self._client.exists(self._encode_key(key)))

    def __getitem__(self, key: Hashable) -> Any:
        raw = self._client.get(self._encode_key(key))
        if raw is None:
            raise KeyError(key)
        return pickle.loads(raw)

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self._client.set(
            self._encode_key(key),
            pickle.dumps(value, protocol=4),
            ex=int(self._ttl) if self._ttl > 0 else None,
        )

    def pop(self, key: Hashable, default: Any = None) -> Any:
        encoded = self._encode_key(key)
        raw = self._client.get(encoded)
        if raw is None:
            return default
        self._client.delete(encoded)
        return pickle.loads(raw)

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def maxsize(self) -> Optional[int]:
        return self._maxsize

    # ── CacheBase ───────────────────────────────────────────────────────

    def _scan_keys(self):
        # SCAN avoids the O(N) blocking behaviour of KEYS.
        return self._client.scan_iter(match=f'{self._prefix}*', count=200)

    def info(self) -> dict:
        try:
            currsize = sum(1 for _ in self._scan_keys())
        except redis.RedisError:
            currsize = None
        return {
            'name':         self.name,
            'enabled':      True,
            'currsize':     currsize,
            'maxsize':      self._maxsize,
            'ttl':          self._ttl,
            'hits':         None,    # not tracked at this layer
            'misses':       None,
            'bytes_approx': None,    # MEMORY USAGE on every key is too costly
            'extras':       {'backend': 'redis', 'prefix': self._prefix},
        }

    def clear(self) -> int:
        n = 0
        try:
            for key in list(self._scan_keys()):
                if self._client.delete(key):
                    n += 1
        except redis.RedisError:
            pass
        return n
