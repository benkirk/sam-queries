"""
Tests for the Redis-backed cache adapters.

Uses fakeredis for an in-process Redis server so these tests run with no
external dependencies. Covers:

  - RedisTTLAdapter: dict-like API, TTL expiry, info() shape, clear()
  - RedisChartCache: get/put round-trip, hit/miss counter persistence,
    info() shape
  - usage_cache.get_cache_adapter() backend switching on env
"""

import time

import fakeredis
import pytest

from sam.caching import RedisTTLAdapter
from webapp.caching.redis_chart import RedisChartCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def redis_client():
    """Fresh in-memory Redis for each test."""
    return fakeredis.FakeRedis()


# ---------------------------------------------------------------------------
# RedisTTLAdapter
# ---------------------------------------------------------------------------

class TestRedisTTLAdapter:

    def test_setitem_getitem_roundtrip(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        key = ('Derecho', None, None, 'SCSG0001', True, None, True, False)
        adapter[key] = [{'allocated': 100, 'used': 50}]
        assert key in adapter
        assert adapter[key] == [{'allocated': 100, 'used': 50}]

    def test_contains_for_missing_key(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        assert ('missing',) not in adapter

    def test_getitem_missing_raises_keyerror(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        with pytest.raises(KeyError):
            _ = adapter[('missing',)]

    def test_pop_returns_value_and_deletes(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        adapter[('k',)] = 42
        assert adapter.pop(('k',)) == 42
        assert ('k',) not in adapter

    def test_pop_missing_returns_default(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        assert adapter.pop(('absent',), 'sentinel') == 'sentinel'

    def test_lock_is_noop_context_manager(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        with adapter.lock:
            adapter[('k',)] = 1
        assert adapter[('k',)] == 1

    def test_ttl_expiry(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=1)
        adapter[('k',)] = 'v'
        assert ('k',) in adapter
        # fakeredis honors TTLs based on system time.
        time.sleep(1.1)
        assert ('k',) not in adapter

    def test_info_shape(self, redis_client):
        adapter = RedisTTLAdapter(
            name='usage', client=redis_client, ttl=60, maxsize=200,
        )
        adapter[('a',)] = 1
        adapter[('b',)] = 2
        info = adapter.info()
        assert info['name'] == 'usage'
        assert info['enabled'] is True
        assert info['currsize'] == 2
        assert info['maxsize'] == 200
        assert info['ttl'] == 60.0
        assert info['hits'] is None
        assert info['misses'] is None
        assert info['extras']['backend'] == 'redis'

    def test_clear_removes_all_entries(self, redis_client):
        adapter = RedisTTLAdapter(name='usage', client=redis_client, ttl=60)
        adapter[('a',)] = 1
        adapter[('b',)] = 2
        cleared = adapter.clear()
        assert cleared == 2
        assert adapter.info()['currsize'] == 0

    def test_namespace_isolation(self, redis_client):
        """Two adapters with distinct prefixes must not see each other's keys."""
        a = RedisTTLAdapter(
            name='a', client=redis_client, ttl=60, prefix='a:',
        )
        b = RedisTTLAdapter(
            name='b', client=redis_client, ttl=60, prefix='b:',
        )
        a[('k',)] = 'va'
        b[('k',)] = 'vb'
        assert a[('k',)] == 'va'
        assert b[('k',)] == 'vb'
        assert a.info()['currsize'] == 1
        assert b.info()['currsize'] == 1


# ---------------------------------------------------------------------------
# RedisChartCache
# ---------------------------------------------------------------------------

class TestRedisChartCache:

    def test_get_miss_then_put_then_hit(self, redis_client):
        cache = RedisChartCache(name='facility_pie_chart', client=redis_client)
        assert cache.get('key1') is None
        cache.put('key1', '<svg>hello</svg>')
        assert cache.get('key1') == '<svg>hello</svg>'

    def test_hit_miss_counters_persist_in_redis(self, redis_client):
        # Two cache instances against the same Redis: counters must add up,
        # which is the property that "shared across workers" depends on.
        a = RedisChartCache(name='facility_pie_chart', client=redis_client)
        b = RedisChartCache(name='facility_pie_chart', client=redis_client)
        a.get('absent')           # miss on a
        b.get('absent')           # miss on b
        a.put('present', '<svg/>')
        b.get('present')          # hit on b
        info = a.info()
        assert info['hits'] == 1
        assert info['misses'] == 2

    def test_distinct_chart_names_do_not_share_keys(self, redis_client):
        a = RedisChartCache(name='pace_chart', client=redis_client)
        b = RedisChartCache(name='facility_pie_chart', client=redis_client)
        a.put('k', '<svg>A</svg>')
        b.put('k', '<svg>B</svg>')
        assert a.get('k') == '<svg>A</svg>'
        assert b.get('k') == '<svg>B</svg>'

    def test_clear_removes_entries_and_counters(self, redis_client):
        cache = RedisChartCache(name='pace_chart', client=redis_client)
        cache.put('k1', '<svg/>')
        cache.put('k2', '<svg/>')
        cache.get('k1')
        cleared = cache.clear()
        assert cleared == 2
        info = cache.info()
        assert info['currsize'] == 0
        assert info['hits'] == 0
        assert info['misses'] == 0

    def test_info_shape(self, redis_client):
        cache = RedisChartCache(name='pace_chart', client=redis_client, ttl=120)
        cache.put('k', '<svg>x</svg>')
        cache.get('k')
        info = cache.info()
        assert info['name'] == 'pace_chart'
        assert info['enabled'] is True
        assert info['currsize'] == 1
        assert info['ttl'] == 120.0
        assert info['hits'] == 1
        assert info['extras']['backend'] == 'redis'


# ---------------------------------------------------------------------------
# usage_cache backend selection
# ---------------------------------------------------------------------------

class TestUsageCacheBackendSwitch:

    def test_no_env_uses_inprocess_adapter(self, monkeypatch):
        monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
        import sam.queries.usage_cache as uc
        uc._adapter = None
        uc._disabled = False
        try:
            adapter = uc.get_cache_adapter()
            from sam.caching import TTLCacheAdapter
            assert isinstance(adapter, TTLCacheAdapter)
        finally:
            uc._adapter = None
            uc._disabled = False

    def test_unreachable_redis_falls_back_silently(self, monkeypatch, caplog):
        monkeypatch.setenv('CACHE_REDIS_URL', 'redis://127.0.0.1:1/0')  # port 1 = nothing listens
        import sam.queries.usage_cache as uc
        uc._adapter = None
        uc._disabled = False
        try:
            with caplog.at_level('WARNING'):
                adapter = uc.get_cache_adapter()
            from sam.caching import TTLCacheAdapter
            assert isinstance(adapter, TTLCacheAdapter)
            assert any('unreachable' in r.message for r in caplog.records)
        finally:
            uc._adapter = None
            uc._disabled = False

    def test_reachable_redis_returns_redis_adapter(self, monkeypatch):
        # Patch make_redis_client to return a fakeredis client so the
        # branch that calls redis is exercised without a real server.
        client = fakeredis.FakeRedis()
        monkeypatch.setenv('CACHE_REDIS_URL', 'redis://fake:6379/0')

        import sam.caching.redis_client as rc
        monkeypatch.setattr(rc, 'make_redis_client', lambda url=None, **kw: client)
        # The usage_cache module imports the symbol directly, so patch it
        # there as well.
        import sam.queries.usage_cache as uc
        monkeypatch.setattr(uc, 'make_redis_client', lambda url=None, **kw: client)

        uc._adapter = None
        uc._disabled = False
        try:
            adapter = uc.get_cache_adapter()
            assert isinstance(adapter, RedisTTLAdapter)
            # Verify it actually uses the fake client.
            adapter[('k',)] = 'v'
            assert client.exists(b'usage:' + __import__('pickle').dumps(('k',), protocol=4))
        finally:
            uc._adapter = None
            uc._disabled = False
