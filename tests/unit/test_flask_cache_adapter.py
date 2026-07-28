"""
Tests for FlaskCacheAdapter's Redis introspection and its foreign-prefix
skip list.

The flask adapter SCANs the whole Redis DB and must exclude every keyspace
owned by the other adapters (chart + the five RedisTTLAdapter constructions),
else their entries miscount into the flask card's 'other' group. The
cross-check test here is the guard that a future sixth cache cannot be added
without extending ``_FOREIGN_PREFIXES``.

Uses fakeredis (no external dependencies); factory module state is
saved/restored around each test so the shared CI Redis and other tests are
unaffected.
"""

import pickle

import fakeredis
import pytest

from webapp.caching.flask_adapter import (
    _FOREIGN_PREFIXES,
    _FOREIGN_PREFIXES_B,
    FlaskCacheAdapter,
)


@pytest.fixture
def redis_client():
    """Fresh in-memory Redis for each test."""
    return fakeredis.FakeRedis()


def _pickled(key):
    return pickle.dumps(key, protocol=4)


# ---------------------------------------------------------------------------
# Cross-check: the skip list must cover every live non-flask adapter
# ---------------------------------------------------------------------------

class TestForeignPrefixCrossCheck:

    def test_bytes_tuple_mirrors_str_tuple(self):
        assert _FOREIGN_PREFIXES_B == tuple(p.encode() for p in _FOREIGN_PREFIXES)

    def test_every_ttl_adapter_prefix_is_listed(self, monkeypatch, redis_client):
        """Init all five TTL adapters via their real factories and assert each
        prefix appears in _FOREIGN_PREFIXES — a sixth cache added to
        ``caching.adapters()`` without extending the tuple fails here."""
        monkeypatch.setenv('CACHE_REDIS_URL', 'redis://fake:6379/0')

        import sam.caching.redis_client as rc
        import sam.queries.usage_cache as uc
        import webapp.disk_scans.cache as dc
        import webapp.jobs.cache as jc
        for mod in (rc, uc, dc, jc):
            monkeypatch.setattr(mod, 'make_redis_client',
                                lambda url=None, **kw: redis_client)

        saved_usage = (uc._adapter, uc._disabled)
        saved_scans = dict(dc._adapters)
        saved_jobs = dict(jc._adapters)
        uc._adapter, uc._disabled = None, False
        dc._adapters.clear()
        jc._adapters.clear()
        try:
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
            for adapter in ttl_adapters:
                assert adapter._prefix in _FOREIGN_PREFIXES, (
                    f"adapter '{adapter.name}' prefix '{adapter._prefix}' "
                    f"missing from flask_adapter._FOREIGN_PREFIXES — its keys "
                    f"would miscount into the flask card's 'other' group"
                )
        finally:
            uc._adapter, uc._disabled = saved_usage
            dc._adapters.clear()
            dc._adapters.update(saved_scans)
            jc._adapters.clear()
            jc._adapters.update(saved_jobs)

    def test_chart_prefix_listed(self, redis_client):
        """RedisChartCache keys (chart:<name>:, chart:hits/misses:<name>) are
        covered by the single 'chart:' entry."""
        from webapp.caching.redis_chart import RedisChartCache
        cache = RedisChartCache(name='pace_chart', client=redis_client)
        cache.put('k', '<svg/>')
        cache.get('k')      # bump the hit counter key too
        for raw_key in redis_client.scan_iter(match='*'):
            assert raw_key.startswith(b'chart:')
        assert 'chart:' in _FOREIGN_PREFIXES


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
