"""Tests for the job-history TTL cache (webapp/jobs/cache.py) and the
cached aggregation wrappers in webapp/jobs/service.py.

The cache is a process-wide singleton (module-level ``_adapters``), so
every test starts from the autouse fixture's disabled state and the
cache-behavior cases re-enable explicitly — mirroring
``test_webapp_disk_scans.py``.
"""

from __future__ import annotations

import types
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_jobs_cache(monkeypatch):
    """Start each test with the cache disabled; reset the singleton after.

    A stored ``None`` per bucket means "initialised but disabled"; the
    cache-behavior tests below re-enable by clearing ``_adapters``.

    CACHE_REDIS_URL is dropped so re-init always builds the in-process
    TTLCacheAdapter: the bucket-policy logic under test is backend-
    independent, and a real Redis (CI runs with one) is shared across
    xdist workers — concurrent cache tests would corrupt each other's
    hit counts and purge totals.
    """
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    from webapp.jobs import cache as _c
    _c._CACHE.reset_for_tests()
    yield
    # disabled=False → drop the memo so buckets re-init on next use
    _c._CACHE.reset_for_tests(disabled=False)


# ---------------------------------------------------------------------------
# bucket_for_window
# ---------------------------------------------------------------------------

def test_bucket_for_window_closed_is_historical():
    from webapp.jobs.cache import bucket_for_window
    assert bucket_for_window(date.today() - timedelta(days=1)) == 'historical'


def test_bucket_for_window_today_is_recent():
    """A window ending today is still collecting jobs → recent bucket."""
    from webapp.jobs.cache import bucket_for_window
    assert bucket_for_window(date.today()) == 'recent'


def test_bucket_for_window_open_end_is_recent():
    from webapp.jobs.cache import bucket_for_window
    assert bucket_for_window(None) == 'recent'


# ---------------------------------------------------------------------------
# cached_jobs_aggregation — hit / miss / key discrimination
# ---------------------------------------------------------------------------

def _counting_compute(results=None):
    calls = {'n': 0}

    def compute():
        calls['n'] += 1
        return results if results is not None else {'n': calls['n']}
    return calls, compute


def test_cached_aggregation_hit_and_miss():
    from webapp.jobs import cache as c
    c._adapters.clear()  # re-enable (autouse fixture disabled all buckets)

    calls, compute = _counting_compute({'buckets': []})
    opts = {'dimension': 'wait', 'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    r1 = c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'historical')
    r2 = c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'historical')
    assert calls['n'] == 1          # second call was a hit
    assert r1 == r2 == {'buckets': []}

    # Different machine → different key.
    c.cached_jobs_aggregation('histogram', 'casper', opts, compute, 'historical')
    assert calls['n'] == 2

    # Different opts (dimension) → different key.
    opts2 = dict(opts, dimension='nodes')
    c.cached_jobs_aggregation('histogram', 'derecho', opts2, compute, 'historical')
    assert calls['n'] == 3

    # Different query_type → different key.
    c.cached_jobs_aggregation('usage_by_user', 'derecho', opts, compute, 'historical')
    assert calls['n'] == 4


def test_cached_aggregation_normalizes_dates_and_lists():
    """date objects and account lists hash stably — equal values hit."""
    from webapp.jobs import cache as c
    c._adapters.clear()

    calls, compute = _counting_compute()
    opts_a = {'end': date(2026, 6, 30), 'account': ['B', 'A']}
    opts_b = {'end': date(2026, 6, 30), 'account': ['A', 'B']}  # order-insensitive

    c.cached_jobs_aggregation('histogram', 'derecho', opts_a, compute, 'historical')
    c.cached_jobs_aggregation('histogram', 'derecho', opts_b, compute, 'historical')
    assert calls['n'] == 1


def test_cached_aggregation_buckets_are_independent():
    """Same key in different buckets → separate entries (recent vs historical)."""
    from webapp.jobs import cache as c
    c._adapters.clear()

    calls, compute = _counting_compute()
    opts = {'dimension': 'wait'}
    c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'historical')
    c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'recent')
    assert calls['n'] == 2


def test_cached_aggregation_disabled_bucket_computes_every_time():
    """Autouse fixture leaves every bucket disabled → no caching at all."""
    from webapp.jobs import cache as c

    calls, compute = _counting_compute()
    opts = {'dimension': 'wait'}
    c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'recent')
    c.cached_jobs_aggregation('histogram', 'derecho', opts, compute, 'recent')
    assert calls['n'] == 2


# ---------------------------------------------------------------------------
# purge + info
# ---------------------------------------------------------------------------

def test_purge_jobs_cache_clears_all_buckets():
    from webapp.jobs import cache as c
    c._adapters.clear()

    calls, compute = _counting_compute()
    c.cached_jobs_aggregation('histogram', 'derecho', {'d': 1}, compute, 'historical')
    c.cached_jobs_aggregation('histogram', 'derecho', {'d': 2}, compute, 'recent')

    assert c.purge_jobs_cache() == 2

    # Entries gone → both recompute.
    c.cached_jobs_aggregation('histogram', 'derecho', {'d': 1}, compute, 'historical')
    c.cached_jobs_aggregation('histogram', 'derecho', {'d': 2}, compute, 'recent')
    assert calls['n'] == 4


def test_jobs_cache_info_shape_enabled():
    from webapp.jobs import cache as c
    c._adapters.clear()

    infos = c.jobs_cache_info()
    assert [i['name'] for i in infos] == ['jobs', 'jobs_recent']


def test_jobs_cache_defaults_cap_staleness_at_thirty_minutes():
    """Job records keep arriving for windows already closed, so even the
    'historical' bucket is only nearly-immutable. These TTLs are the ONLY
    freshness lever — the chart SVG caches are keyed by a content hash, so
    they can never serve a stale panel on their own."""
    from webapp.jobs import cache as c

    ttls = {b: spec.ttl_default for b, spec in c._BUCKETS.items()}
    assert ttls['historical'] == 1800
    assert ttls['recent'] == 900
    assert max(ttls.values()) <= 1800


def test_jobs_cache_sizes_fit_the_explorer_fan_out():
    """One explorer filter set is ~22 aggregation keys (8 histogram
    dimensions x 2 owner axes + 2 rollups x 3 sort orders), so a
    cards-era 128 held about six of them."""
    from webapp.jobs import cache as c

    sizes = {b: spec.size_default for b, spec in c._BUCKETS.items()}
    assert min(sizes.values()) >= 512


def test_jobs_cache_info_shape_disabled():
    """With buckets disabled, info still reports both (disabled_info shape)."""
    from webapp.jobs import cache as c

    infos = c.jobs_cache_info()
    assert [i['name'] for i in infos] == ['jobs', 'jobs_recent']


def test_caching_facade_reports_and_clears_jobs_category(app):
    """The webapp caching facade includes the jobs buckets in stats()/clear()."""
    from webapp.jobs import cache as c
    from webapp.caching import caching

    c._adapters.clear()
    calls, compute = _counting_compute()
    c.cached_jobs_aggregation('histogram', 'derecho', {'d': 1}, compute, 'historical')

    with app.app_context():
        stats = caching.stats()
    assert [i['name'] for i in stats['jobs']] == ['jobs', 'jobs_recent']

    cleared = caching.clear('jobs')
    assert cleared == {'jobs': 1}


# ---------------------------------------------------------------------------
# service.jobs_histogram / jobs_usage_by_user — cached wrappers
# ---------------------------------------------------------------------------

def _install_agg_plugin(app, monkeypatch):
    """Mock plugin capturing jobs_histogram / jobs_usage_by / jobs_facets."""
    captured = {'histogram': [], 'usage_by': [], 'facets': []}

    class FakeJobQueries:
        def __init__(self, session, machine='derecho'):
            self.machine = machine

        def jobs_histogram(self, dimension, **kwargs):
            captured['histogram'].append((dimension, kwargs))
            return {'dimension': dimension, 'buckets': [], 'null_count': 0,
                    'total_count': 0}

        def jobs_usage_by(self, dimension, **kwargs):
            captured['usage_by'].append((dimension, kwargs))
            return {'dimension': dimension, 'rows': [],
                    'totals': {'job_count': 0, 'cpu_hours': 0.0, 'gpu_hours': 0.0}}

        def jobs_facets(self, **kwargs):
            captured['facets'].append(kwargs)
            return {d: [] for d in kwargs.get('facets', ())}

    fake_mod = types.SimpleNamespace(
        get_engine=lambda machine, pool_kwargs=None: MagicMock(),
        get_session=lambda machine, engine=None: MagicMock(name='jh_session'),
        JobQueries=FakeJobQueries,
    )
    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {
        'module': fake_mod,
        'engines': {'derecho': MagicMock(), 'casper': MagicMock()},
        'enabled': True,
    })
    return captured


def test_service_jobs_histogram_caches_closed_window(app, monkeypatch):
    """Two identical closed-window calls → one plugin query."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_histogram('derecho', 'wait', account_projcodes=['SCSG0001'], **win)
        service.jobs_histogram('derecho', 'wait', account_projcodes=['SCSG0001'], **win)

    assert len(captured['histogram']) == 1
    dimension, kwargs = captured['histogram'][0]
    assert dimension == 'wait'
    assert kwargs['account'] == ['SCSG0001']


def test_service_jobs_histogram_username_pin_overwrites_user_filter(app, monkeypatch):
    """user-mode pin always wins over a client-supplied user filter."""
    from webapp.jobs import service

    captured = _install_agg_plugin(app, monkeypatch)

    with app.app_context():
        service.jobs_histogram('derecho', 'duration',
                               username='benkirk', user='mallory')

    _, kwargs = captured['histogram'][0]
    assert kwargs['user'] == 'benkirk'


def test_service_jobs_histogram_machine_wide_has_no_account(app, monkeypatch):
    """No account_projcodes → no account key (machine-wide, caller gated)."""
    from webapp.jobs import service

    captured = _install_agg_plugin(app, monkeypatch)

    with app.app_context():
        service.jobs_histogram('derecho', 'nodes')

    _, kwargs = captured['histogram'][0]
    assert 'account' not in kwargs


def test_service_jobs_usage_by_user_forwards_limit_and_account(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_agg_plugin(app, monkeypatch)

    with app.app_context():
        out = service.jobs_usage_by_user(
            'derecho', limit=7, account_projcodes=['SCSG0001', 'SCSG0002'],
        )

    dimension, kwargs = captured['usage_by'][0]
    assert dimension == 'user'
    assert kwargs['limit'] == 7
    assert kwargs['account'] == ['SCSG0001', 'SCSG0002']
    assert 'rows' in out and 'totals' in out


def test_service_jobs_histogram_owners_limit_in_key_and_forwarded(app, monkeypatch):
    """owners_limit reaches the plugin and discriminates the cache key —
    an enriched envelope must never be served for a plain request (or
    vice versa)."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_histogram('derecho', 'wait', **win)
        service.jobs_histogram('derecho', 'wait', owners_limit=10, **win)
        service.jobs_histogram('derecho', 'wait', owners_limit=10, **win)

    assert len(captured['histogram']) == 2      # 3rd call served from cache
    _, plain_kwargs = captured['histogram'][0]
    _, rich_kwargs = captured['histogram'][1]
    assert 'owners_limit' not in plain_kwargs   # None → omitted entirely
    assert rich_kwargs['owners_limit'] == 10


def test_service_jobs_histogram_owners_sort_in_key_and_forwarded(app, monkeypatch):
    """owners_sort_by discriminates the cache key — a jobs-ranked owner set
    must never be served for a GPU-hours request."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_histogram('derecho', 'wait', owners_limit=10,
                               owners_sort_by='job_count', **win)
        service.jobs_histogram('derecho', 'wait', owners_limit=10,
                               owners_sort_by='gpu_hours', **win)
        service.jobs_histogram('derecho', 'wait', owners_limit=10,
                               owners_sort_by='gpu_hours', **win)

    assert len(captured['histogram']) == 2
    _, jobs_kwargs = captured['histogram'][0]
    _, gpu_kwargs = captured['histogram'][1]
    assert jobs_kwargs['owners_sort_by'] == 'job_count'
    assert gpu_kwargs['owners_sort_by'] == 'gpu_hours'


def test_service_jobs_histogram_owners_by_in_key_and_forwarded(app, monkeypatch):
    """owners_by='account' discriminates the cache key; the 'user' default
    is normalized to the omitted form (same key, never forwarded) so a
    pre-owners_by plugin keeps working on the default path."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_histogram('derecho', 'wait', **win)
        service.jobs_histogram('derecho', 'wait', owners_by='user', **win)
        service.jobs_histogram('derecho', 'wait', owners_by='account', **win)
        service.jobs_histogram('derecho', 'wait', owners_by='account', **win)

    # call 2 aliases call 1 (explicit default == omitted); call 4 hits 3.
    assert len(captured['histogram']) == 2
    _, default_kwargs = captured['histogram'][0]
    _, account_kwargs = captured['histogram'][1]
    assert 'owners_by' not in default_kwargs
    assert account_kwargs['owners_by'] == 'account'


def test_service_jobs_usage_by_sort_in_key_and_forwarded(app, monkeypatch):
    """sort_by reaches the plugin and discriminates the cache key —
    different rankings are different result sets."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_usage_by_user('derecho', **win)
        service.jobs_usage_by_user('derecho', sort_by='gpu_hours', **win)
        service.jobs_usage_by_user('derecho', sort_by='gpu_hours', **win)

    assert len(captured['usage_by']) == 2
    _, default_kwargs = captured['usage_by'][0]
    _, gpu_kwargs = captured['usage_by'][1]
    assert 'sort_by' not in default_kwargs      # None → plugin default
    assert gpu_kwargs['sort_by'] == 'gpu_hours'


def test_service_jobs_usage_by_project_forwards_sort(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_agg_plugin(app, monkeypatch)

    with app.app_context():
        service.jobs_usage_by_project(
            'derecho', username='benkirk', sort_by='job_count',
        )

    dimension, kwargs = captured['usage_by'][0]
    assert dimension == 'account'
    assert kwargs['sort_by'] == 'job_count'
    assert kwargs['user'] == 'benkirk'


def test_service_jobs_usage_by_project_scopes_in_key_and_forwarded(
    app, monkeypatch,
):
    """The non-user-mode scopes: account_projcodes reaches the plugin as
    ``account`` and discriminates the cache key; the unpinned machine-mode
    call sends neither ``user`` nor ``account``. Different scopes must
    never satisfy each other from cache."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_usage_by_project('derecho', **win)                 # machine
        service.jobs_usage_by_project('derecho', **win)                 # cached
        service.jobs_usage_by_project(
            'derecho', account_projcodes=['SCSG0001', 'SCSG0002'], **win)  # tree

    assert len(captured['usage_by']) == 2
    _, machine_kwargs = captured['usage_by'][0]
    assert machine_kwargs.get('user') is None   # unpinned (filter None)
    assert 'account' not in machine_kwargs
    _, tree_kwargs = captured['usage_by'][1]
    assert tree_kwargs['account'] == ['SCSG0001', 'SCSG0002']


def test_service_jobs_facets_caches_closed_window(app, monkeypatch):
    """Two identical closed-window facet calls → one plugin query; a
    different facet tuple or limit is a different key."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_facets('derecho', account_projcodes=['SCSG0001'], **win)
        service.jobs_facets('derecho', account_projcodes=['SCSG0001'], **win)
        service.jobs_facets('derecho', account_projcodes=['SCSG0001'],
                            limit=3, **win)

    assert len(captured['facets']) == 2
    kwargs = captured['facets'][0]
    assert kwargs['facets'] == ('queue', 'qos', 'exit_status')
    assert kwargs['limit'] == 8
    assert kwargs['account'] == ['SCSG0001']


def test_service_jobs_facets_username_pin_overwrites_user_filter(
    app, monkeypatch,
):
    from webapp.jobs import service

    captured = _install_agg_plugin(app, monkeypatch)

    with app.app_context():
        service.jobs_facets('derecho', username='benkirk', user='mallory')

    assert captured['facets'][0]['user'] == 'benkirk'


def test_service_jobs_usage_by_user_caches(app, monkeypatch):
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_usage_by_user('derecho', account_projcodes=['SCSG0001'], **win)
        service.jobs_usage_by_user('derecho', account_projcodes=['SCSG0001'], **win)
        # A different limit is a different result → recompute.
        service.jobs_usage_by_user('derecho', limit=3,
                                   account_projcodes=['SCSG0001'], **win)

    assert len(captured['usage_by']) == 2


def test_service_jobs_usage_by_project_caches_under_own_query_type(
    app, monkeypatch,
):
    """usage_by_account is its own cache key family — a by-project call
    never satisfies (or is satisfied by) a by-user call with the same
    filter set."""
    from webapp.jobs import cache as c, service
    c._adapters.clear()

    captured = _install_agg_plugin(app, monkeypatch)
    win = {'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}

    with app.app_context():
        service.jobs_usage_by_project('derecho', username='benkirk', **win)
        service.jobs_usage_by_project('derecho', username='benkirk', **win)
        # Same window as a by-user call → still a fresh plugin query.
        service.jobs_usage_by_user('derecho', user='benkirk', limit=25, **win)

    assert len(captured['usage_by']) == 2
    dim, kwargs = captured['usage_by'][0]
    assert dim == 'account'
    assert kwargs['user'] == 'benkirk'
