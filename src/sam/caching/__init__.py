"""
Framework-agnostic cache primitives shared by sam (CLI/ORM) and webapp.

Every cache layer in the SAM ecosystem implements the `CacheBase` contract:
a uniform `info() -> dict` and `clear() -> int`. That gives operators a
single stats shape across the admin Configuration card and lets future
adapters (Redis, file-system, etc.) plug in without rewriting consumers.

Subclasses live both here (`TTLCacheAdapter` — used by the allocation usage
cache) and in `webapp.caching` (`ChartCache`, `FlaskCacheAdapter`).
"""

from sam.caching.base import CacheBase, approx_bytes
from sam.caching.ttl import TTLCacheAdapter

__all__ = ['CacheBase', 'TTLCacheAdapter', 'approx_bytes']
