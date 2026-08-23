"""BucketedTTLCache — one lazily-initialized TTL cache with named buckets.

Three call sites grew the same ~120-line skeleton independently
(``sam.queries.usage_cache``, ``webapp.disk_scans.cache``,
``webapp.jobs.cache``): read TTL/size from Flask config or env, lazily build
a ``RedisTTLAdapter`` when ``CACHE_REDIS_URL`` is reachable and a per-worker
``TTLCacheAdapter`` otherwise, then get-under-lock / compute-outside-lock /
store-under-lock. This is that skeleton, once.

What each call site still owns — because it is what genuinely differs — is
the **cache key**:

* fs-scans keys embed the per-collection scan dates, so invalidation is
  content-addressed and the TTL is only a memory backstop.
* jobs have no freshness signature (records append continuously), so the TTL
  *is* the staleness bound.
* allocation usage keys on the query parameters at day granularity.

Buckets exist so one mechanism can serve two populations with different
staleness tolerance under a single Redis instance — e.g. fs-scans' long-lived
passive queries vs the explorer's volatile filter permutations. A bucket is
disabled by config when either its TTL or its size is 0.

Registry
--------
Instances self-register at construction so the webapp's ``Caching`` facade can
enumerate every bucketed cache (for the Admin -> Configuration card, for
``caching.clear(category)``, and for deriving the flask adapter's
foreign-keyspace skip list) without hand-maintaining parallel lists.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, List, Mapping, Optional, Tuple

from sam.caching.base import CacheBase
from sam.caching.redis_client import make_redis_client
from sam.caching.redis_ttl import RedisTTLAdapter
from sam.caching.ttl import TTLCacheAdapter, disabled_info

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BucketSpec:
    """One bucket's identity and config knobs.

    ``name`` is load-bearing twice over: it is the Redis key prefix
    (``RedisTTLAdapter`` derives ``f'{name}:'``) and the label the Admin
    Configuration card renders, so bucket names must stay unique across every
    cache sharing the Redis instance.
    """

    name: str
    ttl_key: str
    ttl_default: int
    size_key: str
    size_default: int


def _config_int(key: str, default: int) -> int:
    """Read an int from Flask app config if we're in one, else env, else default."""
    try:
        from flask import current_app
        return int(current_app.config.get(key, default))
    except RuntimeError:
        return int(os.environ.get(key, default))


class BucketedTTLCache:
    """A named family of TTL cache buckets sharing one backend policy.

    Adapters are built on first use and memoised; a stored ``None`` means
    "initialized but disabled by config", so a disabled bucket costs one
    config read for the process lifetime rather than one per call.
    """

    def __init__(self, label: str, category: str,
                 buckets: Mapping[str, BucketSpec]) -> None:
        """
        Args:
            label: short name used in log lines (e.g. ``'jobs'``).
            category: the ``caching.clear(category)`` bucket this cache
                answers to (``'usage'`` / ``'scans'`` / ``'jobs'``). Also the
                key under which :meth:`info` output appears in the Admin card.
            buckets: ``{bucket_key: BucketSpec}``. Iteration order is the
                display order in the Admin card, so declare the primary
                bucket first.
        """
        self.label = label
        self.category = category
        self.buckets: Dict[str, BucketSpec] = dict(buckets)
        self._adapters: Dict[str, Optional[CacheBase]] = {}
        self._lock = threading.RLock()
        _register(self)

    # Adapters

    def adapter(self, bucket: str) -> Optional[CacheBase]:
        """Return the shared adapter for *bucket*, initializing on first call.

        ``None`` when the bucket is disabled by config (TTL or size == 0).
        Prefers a ``RedisTTLAdapter`` so all gunicorn workers share one cache;
        falls back to a per-worker ``TTLCacheAdapter`` when ``CACHE_REDIS_URL``
        is unset or unreachable. The fallback is load-bearing — an unreachable
        Redis must never take the app down.
        """
        spec = self.buckets[bucket]

        with self._lock:
            if bucket in self._adapters:
                return self._adapters[bucket]

            ttl = _config_int(spec.ttl_key, spec.ttl_default)
            size = _config_int(spec.size_key, spec.size_default)
            if ttl <= 0 or size <= 0:
                self._adapters[bucket] = None
                return None

            redis_url = os.environ.get('CACHE_REDIS_URL')
            if redis_url:
                try:
                    client = make_redis_client(redis_url)
                    if client is not None:
                        adapter: CacheBase = RedisTTLAdapter(
                            name=spec.name, client=client, ttl=ttl, maxsize=size,
                        )
                        self._adapters[bucket] = adapter
                        return adapter
                except Exception as exc:
                    logger.warning(
                        "%s cache: CACHE_REDIS_URL=%s set but unreachable (%s); "
                        "falling back to per-worker TTLCacheAdapter.",
                        self.label, redis_url, exc,
                    )

            adapter = TTLCacheAdapter(name=spec.name, maxsize=size, ttl=ttl)
            self._adapters[bucket] = adapter
            return adapter

    def live_adapters(self) -> List[CacheBase]:
        """Every enabled adapter, in declaration order (for the facade)."""
        out: List[CacheBase] = []
        for bucket in self.buckets:
            adapter = self.adapter(bucket)
            if adapter is not None:
                out.append(adapter)
        return out

    # The memoisation dance

    def get_or_compute(self, bucket: str, key: Hashable,
                       compute: Callable[[], Any], *,
                       force_refresh: bool = False) -> Any:
        """Return the cached value for *key* in *bucket*, or compute and store it.

        *compute* must produce the FINAL caller-facing result, so a cache hit
        reproduces it exactly without re-querying. *key* must already carry
        every parameter that shapes that result — key construction stays with
        the caller because it is the part that differs per cache (see the
        module docstring).

        The compute runs OUTSIDE the adapter lock: these are multi-second
        plugin queries, and holding the lock across one would serialize every
        other reader of the bucket.
        """
        adapter = self.adapter(bucket)
        if adapter is None:
            return compute()

        with adapter.lock:
            if not force_refresh and key in adapter:
                return adapter[key]
            # Drop any stale entry so the post-compute store can re-insert.
            adapter.pop(key, None)

        result = compute()

        with adapter.lock:
            try:
                adapter[key] = result
            except ValueError:
                # Cache full and no expired entries to evict — skip the store
                # rather than fail the request.
                pass
        return result

    # Admin / facade hooks

    def purge(self) -> int:
        """Clear every bucket. Returns the total entries cleared."""
        total = 0
        for bucket in self.buckets:
            adapter = self.adapter(bucket)
            if adapter is not None:
                total += adapter.clear()
        return total

    def info(self) -> List[Dict]:
        """One canonical ``CacheBase.info()`` dict per bucket, in declaration order.

        A disabled bucket still reports its configured TTL/size via
        ``disabled_info`` so an operator can see *why* it is off.
        """
        infos: List[Dict] = []
        for bucket, spec in self.buckets.items():
            adapter = self.adapter(bucket)
            if adapter is None:
                infos.append(disabled_info(
                    spec.name,
                    maxsize=_config_int(spec.size_key, spec.size_default),
                    ttl=_config_int(spec.ttl_key, spec.ttl_default),
                ))
            else:
                infos.append(adapter.info())
        return infos

    @property
    def prefixes(self) -> Tuple[str, ...]:
        """Redis key prefixes this cache owns, enabled or not.

        Config-independent by construction: the flask adapter needs to skip
        these keyspaces whether or not *this* worker happens to have the
        bucket enabled.
        """
        return tuple(f'{spec.name}:' for spec in self.buckets.values())

    # Test hook

    def reset_for_tests(self, *, disabled: bool = True) -> None:
        """Force every bucket to a known adapter state.

        ``disabled=True`` (the default) pins all buckets off, so a test
        exercising a service function sees real computes instead of a
        neighbor test's cached value. ``disabled=False`` drops the memo so
        the next call re-reads config — used by tests that flip TTL/size
        config and expect it to take effect.

        Mutates the memo dict in place rather than rebinding it, so a caller
        holding a reference to ``_adapters`` (the cache modules expose one as
        a test seam) keeps seeing this cache's real state.
        """
        with self._lock:
            self._adapters.clear()
            if disabled:
                self._adapters.update({b: None for b in self.buckets})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_INSTANCES: List[BucketedTTLCache] = []
_REGISTRY_LOCK = threading.RLock()


def _register(cache: BucketedTTLCache) -> None:
    """Record *cache* for facade enumeration. Idempotent per name."""
    with _REGISTRY_LOCK:
        if not any(c.label == cache.label for c in _INSTANCES):
            _INSTANCES.append(cache)


def registered_caches() -> List[BucketedTTLCache]:
    """Every BucketedTTLCache constructed so far, in construction order.

    Construction happens at module import, so callers must import the owning
    modules first — see ``webapp.caching.Caching._bucketed_caches``.
    """
    with _REGISTRY_LOCK:
        return list(_INSTANCES)


def norm(value: Any) -> Any:
    """Make a cache-key component hashable and stable across calls.

    Collections become sorted tuples of strings (so ``['b','a']`` and
    ``['a','b']`` share an entry — every collection in these keys is a set of
    filters, where order carries no meaning), and anything date-like becomes
    its ISO string (so a ``date`` and the ``datetime`` at its midnight do not
    hash apart).
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(sorted(str(v) for v in value))
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value
