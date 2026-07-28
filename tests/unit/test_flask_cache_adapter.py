"""
Tests for FlaskCacheAdapter's Redis introspection and its foreign-prefix
skip list.

The flask adapter SCANs the whole Redis DB and must exclude every keyspace
owned by the other adapters (chart + the five RedisTTLAdapter constructions),
else their entries miscount into the flask card's 'other' group.

That skip list is DERIVED from the BucketedTTLCache registry, so a sixth
bucketed cache cannot be added without its prefix appearing. The cross-check
test here is the guard that the derivation actually covers every live
adapter — i.e. that ``_foreign_prefixes()`` and ``caching.adapters()`` are
reading the same registry.

Uses fakeredis (no external dependencies); factory module state is
saved/restored around each test so the shared CI Redis and other tests are
unaffected.
"""

import pickle
from contextlib import contextmanager

import fakeredis
import pytest

from webapp.caching.flask_adapter import (
    _CHART_PREFIX,
    _foreign_prefixes,
    FlaskCacheAdapter,
)


@pytest.fixture
def redis_client():
    """Fresh in-memory Redis for each test."""
    return fakeredis.FakeRedis()


def _pickled(key):
    return pickle.dumps(key, protocol=4)


@contextmanager
def _fresh_bucketed_caches(*, disabled=False):
    """Drop every bucketed cache's memoised adapters, restoring them after.

    These caches are module-level singletons shared across the whole test
    session, so a test that forces them to (re)initialise against fakeredis
    has to hand back what it found or it poisons every later test.
    """
    from webapp.caching import caching
    caches = caching.bucketed_caches()
    saved = [dict(c._adapters) for c in caches]
    for cache in caches:
        cache.reset_for_tests(disabled=disabled)
    try:
        yield caches
    finally:
        # Restore IN PLACE — the cache modules expose `_adapters` as a
        # module-level alias to this same dict, and rebinding would leave
        # every later test in this worker poking a detached copy.
        for cache, state in zip(caches, saved):
            cache._adapters.clear()
            cache._adapters.update(state)


# ---------------------------------------------------------------------------
# Cross-check: the skip list must cover every live non-flask adapter
# ---------------------------------------------------------------------------

class TestForeignPrefixCrossCheck:

    def test_every_ttl_adapter_prefix_is_listed(self, monkeypatch, redis_client):
        """Init all five TTL adapters via their real factories and assert each
        prefix is skipped — a sixth bucketed cache whose keyspace escaped the
        derivation would fail here."""
        monkeypatch.setenv('CACHE_REDIS_URL', 'redis://fake:6379/0')

        # One patch point: every bucketed cache builds its Redis client
        # through sam.caching.buckets.
        import sam.caching.buckets as buckets
        monkeypatch.setattr(buckets, 'make_redis_client',
                            lambda url=None, **kw: redis_client)

        with _fresh_bucketed_caches():
            from webapp.caching import Caching
            facade = Caching()
            ttl_adapters = [a for a in facade.adapters()
                            if getattr(a, '_prefix', None) is not None]
            # All five constructions must be live under fakeredis…
            assert sorted(a.name for a in ttl_adapters) == [
                'allocation_usage', 'fs_scans', 'fs_scans_filtered',
                'jobs', 'jobs_recent',
            ]
            # …and every one of their keyspaces must be in the skip list.
            skipped = _foreign_prefixes()
            for adapter in ttl_adapters:
                assert adapter._prefix in skipped, (
                    f"adapter '{adapter.name}' prefix '{adapter._prefix}' "
                    f"missing from flask_adapter._foreign_prefixes() — its keys "
                    f"would miscount into the flask card's 'other' group"
                )

    def test_prefixes_are_config_independent(self, monkeypatch):
        """A bucket disabled in THIS worker still owns its keyspace in a
        shared Redis, so its prefix must stay in the skip list."""
        monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
        with _fresh_bucketed_caches(disabled=True):
            skipped = _foreign_prefixes()
            for prefix in ('allocation_usage:', 'fs_scans:', 'fs_scans_filtered:',
                           'jobs:', 'jobs_recent:'):
                assert prefix in skipped

    def test_chart_prefix_listed(self, redis_client):
        """RedisChartCache keys (chart:<name>:, chart:hits/misses:<name>) are
        covered by the single 'chart:' entry."""
        from webapp.caching.redis_chart import RedisChartCache
        cache = RedisChartCache(name='pace_chart', client=redis_client)
        cache.put('k', '<svg/>')
        cache.get('k')      # bump the hit counter key too
        for raw_key in redis_client.scan_iter(match='*'):
            assert raw_key.startswith(b'chart:')
        assert _CHART_PREFIX in _foreign_prefixes()


# ---------------------------------------------------------------------------
# Introspection: foreign keys stay out of the flask groups
# ---------------------------------------------------------------------------

class TestRedisIntrospection:

    def _adapter(self, client):
        # A flask_cache object with no usable backend forces info() down the
        # Redis-introspection path.
        return FlaskCacheAdapter(object(), redis_client=client)

    def test_foreign_keys_excluded_from_counts(self, redis_client):
        # Foreign keys: one per adapter keyspace, pickle-suffixed (not UTF-8)
        # like the real RedisTTLAdapter writes them.
        for prefix in (b'allocation_usage:', b'fs_scans:', b'fs_scans_filtered:',
                       b'jobs:', b'jobs_recent:'):
            redis_client.set(prefix + _pickled(('k',)), b'v')
        redis_client.set(b'chart:pace_chart:abc', b'<svg/>')
        # Flask-cache keys: one groupable, one 'other'.
        redis_client.set(b'flask_cache_view//api/v1/directory_access/foo', b'x')
        redis_client.set(b'flask_cache_view//dashboards/user', b'y')

        info = self._adapter(redis_client).info()
        assert info['currsize'] == 2
        groups = info['extras']['groups']
        assert groups['directory_access']['entries'] == 1
        assert groups['other']['entries'] == 1

    def test_str_keys_also_skipped(self):
        """decode_responses=True clients hand back str keys — same skip."""
        client = fakeredis.FakeRedis(decode_responses=True)
        client.set('jobs:notpickled', 'v')
        client.set('chart:pace_chart:abc', '<svg/>')
        client.set('flask_cache_view//api/v1/project_access/p', 'x')

        info = self._adapter(client).info()
        assert info['currsize'] == 1
        assert info['extras']['groups']['project_access']['entries'] == 1
        assert info['extras']['groups']['other']['entries'] == 0
