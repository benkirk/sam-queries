from __future__ import annotations

import types
from datetime import datetime, timedelta

from webapp.disk_scans.scope import ProjectScanScope
from _disk_scans_helpers import (
    _FakeQ,
    _wire_service,
)


def test_cached_scan_hit_miss_and_scan_date_invalidation(monkeypatch):
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    c._adapters.clear()  # re-enable (the autouse fixture disabled all buckets)

    calls = {'n': 0}
    def compute():
        calls['n'] += 1
        return [{'v': calls['n']}]

    q = _FakeQ('2026-06-14T00:00:00')
    opts = {'sort_by': 'size', 'limit': 50}

    r1 = c.cached_scan('directories', q, ['mmm'], ['/mmm'], opts, compute)
    r2 = c.cached_scan('directories', q, ['mmm'], ['/mmm'], opts, compute)
    assert r1 == r2 == [{'v': 1}]
    assert calls['n'] == 1                               # 2nd call served from cache

    # Different opts (a future filter selection) -> distinct key -> recompute.
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'sort_by': 'files', 'limit': 50}, compute)
    assert calls['n'] == 2

    # Different query type, same scope -> its own entry.
    c.cached_scan('owner', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    assert calls['n'] == 3

    # A new weekly scan (later date) -> key changes -> recompute (auto-invalidation).
    q2 = _FakeQ('2026-06-21T00:00:00')
    c.cached_scan('directories', q2, ['mmm'], ['/mmm'], opts, compute)
    assert calls['n'] == 4


def test_cached_scan_skips_when_no_scan_dates(monkeypatch):
    """Without a scan date there's no freshness to key on — never cache."""
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    c._adapters.clear()

    calls = {'n': 0}
    def compute():
        calls['n'] += 1
        return ['x']

    q = _FakeQ(None)  # scan_dates() -> []
    c.cached_scan('owner', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    c.cached_scan('owner', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    assert calls['n'] == 2


def test_cached_scan_disabled_passes_through(monkeypatch):
    """TTL/SIZE == 0 disables the cache; every call recomputes."""
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    monkeypatch.setenv('FS_SCANS_CACHE_TTL', '0')
    c._adapters.clear()  # force re-init under the TTL=0 env

    calls = {'n': 0}
    def compute():
        calls['n'] += 1
        return ['x']

    q = _FakeQ('2026-06-14T00:00:00')
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    assert calls['n'] == 2
    assert c.get_cache_adapter() is None


def test_scan_directories_forwards_filters(monkeypatch):
    """The four new filters reach the facade with the right kwarg names."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'),
        owner_uid=4242, leaves_only=True,
        accessed_before=datetime(2026, 1, 1), accessed_after=datetime(2025, 1, 1),
    )
    kw = cap['list_kwargs']
    assert kw['owner_id'] == 4242            # facade param is owner_id
    assert kw['leaves_only'] is True
    assert kw['accessed_before'] == datetime(2026, 1, 1)
    assert kw['accessed_after'] == datetime(2025, 1, 1)


def test_scan_directories_forwards_group_id(monkeypatch):
    """A 'By group' drill-down reaches the facade as group_id (not owner_id)."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'), owner_gid=2001)
    kw = cap['list_kwargs']
    assert kw['group_id'] == 2001            # facade param is group_id
    assert kw['owner_id'] is None            # mutually exclusive with owner


def test_scan_directories_forwards_atime_recursive(monkeypatch):
    """The recursive/non-recursive atime choice reaches the facade verbatim."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'))          # default
    assert cap['list_kwargs']['atime_recursive'] is True
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'), atime_recursive=False)
    assert cap['list_kwargs']['atime_recursive'] is False


def test_scan_directories_outermost_drops_nested(monkeypatch):
    """outermost_only keeps the topmost tree, dropping nested descendants.

    Rows arrive size-sorted (ancestor first); the recursive drill-down wants
    the removable tree, not every directory inside it.
    """
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    rows = [
        {'path': '/glade/campaign/cisl/csg'},
        {'path': '/glade/campaign/cisl/csg/sub'},      # nested under the above
        {'path': '/glade/campaign/cisl/csg/sub/deep'}, # nested deeper
        {'path': '/glade/campaign/cisl/other'},        # sibling — kept
    ]
    # Stub the scope resolution + the cache-wrapped facade call; the flag
    # under test is applied after both.
    monkeypatch.setattr(
        ProjectScanScope, 'resolve',
        lambda self: (types.SimpleNamespace(FsScanQueries=lambda **kw: object()),
                      ['/glade/campaign/cisl'], ['cisl']),
    )
    monkeypatch.setattr(service, 'cached_scan', lambda *a, **k: list(rows))
    kept = service.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'),
                                    outermost_only=True)
    assert [r['path'] for r in kept] == [
        '/glade/campaign/cisl/csg', '/glade/campaign/cisl/other',
    ]
    # Without the flag, every directory is returned untouched.
    allrows = service.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'))
    assert len(allrows) == 4


def test_atime_band_bounds_maps_bands_to_dates():
    """Each band maps to (accessed_after, accessed_before) by its ATIME_BUCKETS
    day window, relative to the scan date; the open-ended oldest band has no
    lower (after) bound. Mapping is by label, not list position."""
    from webapp.disk_scans.service import _atime_band_bounds

    scan = datetime(2026, 6, 1)
    bounds = _atime_band_bounds(scan, ['< 1 Month', '7+ Years'])
    # Band 0: ages [0, 30) days -> before = scan, after = scan - 30 days.
    assert bounds['< 1 Month']['accessed_before'] == '2026-06-01'
    assert bounds['< 1 Month']['accessed_after'] == '2026-05-02'
    # Oldest band: open-ended -> no after bound, before = scan - 2555 days.
    assert bounds['7+ Years']['accessed_after'] is None
    assert bounds['7+ Years']['accessed_before'] == (
        (scan - timedelta(days=2555)).strftime('%Y-%m-%d'))


def test_atime_band_bounds_empty_without_scan_date():
    from webapp.disk_scans.service import _atime_band_bounds
    assert _atime_band_bounds(None, ['< 1 Month']) == {}


def test_scan_access_history_tags_band_bounds(monkeypatch):
    """scan_access_history stamps each band with its date window so the
    drill-down can scope directories to the clicked band."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    hist = {
        'bucket_labels': ['< 1 Month', '7+ Years'],
        'buckets': {
            '< 1 Month': {'data': 1, 'files': 1, 'owners': {1001: {'data': 1, 'files': 1}}},
            '7+ Years':  {'data': 1, 'files': 1, 'owners': {}},
        },
        'reference_scan_date': datetime(2026, 6, 1),
    }

    class _Q:
        def __init__(self, filesystems, database=None):
            pass

        def access_history(self, **kw):
            return hist

    mod = types.SimpleNamespace(FsScanQueries=_Q,
                                collection_for_path=lambda p: 'cisl',
                                normalize_path=lambda p: p)
    monkeypatch.setattr(scope_mod, 'get_module', lambda: mod)
    monkeypatch.setattr(scope_mod, 'collections_for_resource', lambda r, app=None: ['cisl'])
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: None)
    monkeypatch.setattr(scope_mod, 'resolve_scan_scope',
                        lambda s, p, r: (['/glade/campaign/cisl/csg'], ['cisl']))
    monkeypatch.setattr(
        service, 'cached_scan',
        lambda qt, q, colls, pfx, opts, compute, bucket='default', database=None: compute(),
    )

    out = service.scan_distribution(ProjectScanScope(None, object(), 'Campaign_Store'), 'access_history')
    assert out['buckets']['< 1 Month']['accessed_before'] == '2026-06-01'
    assert out['buckets']['< 1 Month']['accessed_after'] == '2026-05-02'
    assert out['buckets']['7+ Years']['accessed_after'] is None


def test_size_band_bounds_maps_bands_to_byte_ranges():
    """Each file-size band maps to its (size_min, size_max) byte range; the
    largest band is open-ended (max None)."""
    from webapp.disk_scans.service import _size_band_bounds

    b = _size_band_bounds(['0 - 1 KiB', '100 GiB+'])
    assert b['0 - 1 KiB'] == {'size_min': 0, 'size_max': 1024}
    assert b['100 GiB+']['size_min'] == 100 * 1024 ** 3
    assert b['100 GiB+']['size_max'] is None


def test_scan_directories_forwards_avg_size(monkeypatch):
    """The average-file-size band reaches the facade as min/max_avg_size."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'),
                         min_avg_size=1024, max_avg_size=10240)
    kw = cap['list_kwargs']
    assert kw['min_avg_size'] == 1024
    assert kw['max_avg_size'] == 10240


def test_scan_file_sizes_tags_band_bounds(monkeypatch):
    """scan_file_sizes stamps each band with its avg-file-size window so the
    drill-down can scope directories to the clicked size band."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    hist = {
        'bucket_labels': ['0 - 1 KiB', '100 GiB+'],
        'buckets': {
            '0 - 1 KiB': {'data': 1, 'files': 1, 'owners': {1001: {'data': 1, 'files': 1}}},
            '100 GiB+':  {'data': 1, 'files': 1, 'owners': {}},
        },
    }

    class _Q:
        def __init__(self, filesystems, database=None):
            pass

        def file_size_histogram(self, **kw):
            return hist

    mod = types.SimpleNamespace(FsScanQueries=_Q,
                                collection_for_path=lambda p: 'cisl',
                                normalize_path=lambda p: p)
    monkeypatch.setattr(scope_mod, 'get_module', lambda: mod)
    monkeypatch.setattr(scope_mod, 'collections_for_resource', lambda r, app=None: ['cisl'])
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: None)
    monkeypatch.setattr(scope_mod, 'resolve_scan_scope',
                        lambda s, p, r: (['/glade/campaign/cisl/csg'], ['cisl']))
    monkeypatch.setattr(
        service, 'cached_scan',
        lambda qt, q, colls, pfx, opts, compute, bucket='default', database=None: compute(),
    )

    out = service.scan_distribution(ProjectScanScope(None, object(), 'Campaign_Store'), 'file_sizes')
    assert out['buckets']['0 - 1 KiB']['size_min'] == 0
    assert out['buckets']['0 - 1 KiB']['size_max'] == 1024
    assert out['buckets']['100 GiB+']['size_max'] is None


def test_directories_bucket_selection(monkeypatch):
    """Any filter routes to the 'filtered' bucket; the bare query to 'default'."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _wire_service(
        monkeypatch, prefixes=['/p'], collections=['c'], warmed=['c'],
        collection_map={'/p': 'c'}, capture={},
    )
    seen = []

    def fake_cached(qt, q, colls, pfx, opts, compute, bucket='default', database=None):
        seen.append(bucket)
        return compute()

    monkeypatch.setattr(service, 'cached_scan', fake_cached)
    service.scan_directories(ProjectScanScope(None, object(), 'R'))                     # default
    service.scan_directories(ProjectScanScope(None, object(), 'R'), owner_uid=5)        # filtered
    service.scan_directories(ProjectScanScope(None, object(), 'R'), owner_gid=7)        # filtered
    service.scan_directories(ProjectScanScope(None, object(), 'R'), leaves_only=True)   # filtered
    service.scan_directories(ProjectScanScope(None, object(), 'R'),
                             accessed_before=datetime(2026, 1, 1))    # filtered
    assert seen == ['default', 'filtered', 'filtered', 'filtered', 'filtered']


def test_filtered_bucket_has_short_ttl(monkeypatch):
    """The two buckets carry distinct TTLs (8 days vs 30 minutes)."""
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    c._adapters.clear()
    assert c.get_cache_adapter('default').info()['ttl'] == 691200
    assert c.get_cache_adapter('filtered').info()['ttl'] == 1800


def test_fs_scans_cache_info_lists_both_buckets(monkeypatch):
    """Admin card data: one info() dict per bucket, default first."""
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    c._adapters.clear()
    infos = c.fs_scans_cache_info()
    assert [i['name'] for i in infos] == ['fs_scans', 'fs_scans_filtered']
