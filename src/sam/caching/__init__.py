"""
Framework-agnostic cache primitives shared by sam (CLI/ORM) and webapp.

Every cache layer in the SAM ecosystem implements the `CacheBase` contract:
a uniform `info() -> dict` and `clear() -> int`. That gives operators a
single stats shape across the admin Configuration card and lets future
adapters (Redis, file-system, etc.) plug in without rewriting consumers.

Subclasses live both here (`TTLCacheAdapter` — used by the allocation usage
cache) and in `webapp.caching` (`ChartCache`, `FlaskCacheAdapter`).

`BucketedTTLCache` sits one level up from the adapters: it owns the
lazy-init + Redis-or-in-process selection + get/compute/store skeleton that
the allocation-usage, fs-scans and job-history caches all need, leaving each
of them only its cache-key construction.
"""

from sam.caching.base import CacheBase, approx_bytes
from sam.caching.buckets import (
    BucketedTTLCache,
    BucketSpec,
    norm,
    registered_caches,
)
from sam.caching.redis_client import make_redis_client
from sam.caching.redis_ttl import RedisTTLAdapter
from sam.caching.ttl import TTLCacheAdapter

__all__ = [
    'BucketSpec',
    'BucketedTTLCache',
    'CacheBase',
    'RedisTTLAdapter',
    'TTLCacheAdapter',
    'approx_bytes',
    'make_redis_client',
    'norm',
    'registered_caches',
]
