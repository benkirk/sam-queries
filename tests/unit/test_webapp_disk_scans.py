"""Tests for the fs-scans Flask integration (webapp/disk_scans/*).

Covers two layers:

1. ``service._scoped`` — the project-scoping + ``subpath`` (fileset
   drill-down) narrowing, exercised through the public ``scan_directories``
   wrapper with a fake plugin module. No app context / DB needed.

2. ``routes`` — the three HTMX fragment endpoints (directories, entities,
   access-history): disabled banner (not 404), 404 on unknown projcode,
   param whitelisting, fileset→subpath plumbing, and the enabled happy
   path. The service layer is monkeypatched so these stay independent of
   real project-directory data and the fs-scans backend.

The session-scoped ``app`` fixture (tests/conftest.py) uses
``TestingConfig`` with ``FS_SCANS_ENABLED = False``, so the plugin starts
disabled in every test; cases that need it enable it via
``monkeypatch.setitem`` on ``app.extensions['fs_scans']`` (restored at
teardown).
"""

from __future__ import annotations

import types
from datetime import datetime

import pytest


# Mirror the plugin's normalize_path / collection_for_path lexical helpers so the
# fake module behaves like the real one for scope matching (see
# project_fs_scans_paths_normalized).
_FAKE_MOUNTS = ('/glade/campaign', '/gpfs/csfs1', '/glade/derecho/scratch', '/lustre/desc1')


def _fake_normalize(p):
    p = (p or '').rstrip('/')
    for pre in _FAKE_MOUNTS:
        if p.startswith(pre):
            s = p[len(pre):]
            return s if s.startswith('/') else '/' + s
    return p


def _fake_collection_for_path(p):
    n = _fake_normalize(p).strip('/')
    return n.split('/', 1)[0].lower() if n else None


@pytest.fixture(autouse=True)
def _disable_fs_scans_cache():
    """Disable the scan-result cache for these tests by default.

    The scoping/route tests exercise the query path directly; the cache is
    covered explicitly by the test_cached_scan_* cases (which re-enable it).
    Reset to a clean, enabled state on teardown so other modules aren't
    affected by the process-wide adapter singleton.
    """
    from webapp.disk_scans import cache as _c
    # A stored None per bucket means "initialised but disabled".
    _c._adapters = {b: None for b in _c._BUCKETS}
    yield
    _c._adapters = {}   # clear → buckets re-init on next use


# ---------------------------------------------------------------------------
# service._scoped — project scoping + subpath narrowing
# ---------------------------------------------------------------------------

def _wire_service(monkeypatch, *, prefixes, collections, warmed,
                  collection_map, capture):
    """Patch the service module's scope/module/collection helpers + a fake
    FsScanQueries that captures the kwargs it's called with."""
    from webapp.disk_scans import service

    class _FakeQueries:
        def __init__(self, filesystems):
            capture['filesystems'] = list(filesystems)

        def list_directories(self, **kw):
            capture['list_kwargs'] = kw
            return [{'path': p} for p in (kw.get('path_prefixes') or [])]

        def owner_summary(self, **kw):
            capture['owner_kwargs'] = kw
            return []

        def resolve_usernames(self, uids):
            return {}

    mod = types.SimpleNamespace(
        FsScanQueries=_FakeQueries,
        # Explicit map wins; fall back to the computed (normalize + first
        # segment) form so normalized descent paths (/cisl/csg/sub) resolve too.
        collection_for_path=lambda p: collection_map.get(p) or _fake_collection_for_path(p),
        normalize_path=_fake_normalize,
    )
    monkeypatch.setattr(service, 'get_module', lambda: mod)
    monkeypatch.setattr(service, 'get_collections', lambda: list(warmed))
    monkeypatch.setattr(
        service, 'resolve_scan_scope',
        lambda session, project, resource_name: (list(prefixes), list(collections)),
    )
    return service


def test_scoped_no_subpath_keeps_all_prefixes(monkeypatch):
    """Without a subpath, every resolved prefix is handed to the facade."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg', '/glade/campaign/cisl/other'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={
            '/glade/campaign/cisl/csg': 'cisl',
            '/glade/campaign/cisl/other': 'cisl',
        },
        capture=cap,
    )
    rows = svc.scan_directories(None, object(), 'Campaign_Store')
    assert cap['filesystems'] == ['cisl']
    assert sorted(cap['list_kwargs']['path_prefixes']) == [
        '/glade/campaign/cisl/csg', '/glade/campaign/cisl/other',
    ]
    assert len(rows) == 2


def test_scoped_subpath_narrows_to_fileset(monkeypatch):
    """A subpath keeps only that prefix (and descendants) — the drill-down."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg', '/glade/campaign/cisl/other'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={
            '/glade/campaign/cisl/csg': 'cisl',
            '/glade/campaign/cisl/other': 'cisl',
        },
        capture=cap,
    )
    svc.scan_directories(None, object(), 'Campaign_Store',
                         subpath='/glade/campaign/cisl/csg')
    assert cap['list_kwargs']['path_prefixes'] == ['/glade/campaign/cisl/csg']


def test_scoped_unknown_subpath_yields_no_query(monkeypatch):
    """A subpath outside the project's resolved set returns [] (never widens)."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    rows = svc.scan_directories(None, object(), 'Campaign_Store',
                                subpath='/glade/campaign/other/elsewhere')
    assert rows == []
    # Facade must not have been constructed/queried for an unscoped path.
    assert 'list_kwargs' not in cap


def test_scoped_drops_unwarmed_collections(monkeypatch):
    """Collections not in the warmed set are dropped → no results."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/aiml/proj'],
        collections=['aiml'], warmed=['cisl'],   # aiml not reachable
        collection_map={'/glade/campaign/aiml/proj': 'aiml'},
        capture=cap,
    )
    assert svc.scan_directories(None, object(), 'Campaign_Store') == []
    assert 'list_kwargs' not in cap


def test_scoped_returns_empty_when_module_missing(monkeypatch):
    from webapp.disk_scans import service
    monkeypatch.setattr(service, 'get_module', lambda: None)
    assert service.scan_directories(None, object(), 'Campaign_Store') == []
    assert service.scan_access_history(None, object(), 'Campaign_Store') is None
    assert service.scan_file_sizes(None, object(), 'Campaign_Store') is None


# ---------------------------------------------------------------------------
# routes — HTMX fragment endpoints
# ---------------------------------------------------------------------------

def _enable_fs_scans(app, monkeypatch, collections=('cisl',)):
    """Mark the plugin enabled on app.extensions for is_enabled() checks."""
    state = {
        'module':      object(),
        'collections': list(collections),
        'engines':     {c: object() for c in collections},
        'enabled':     True,
    }
    monkeypatch.setitem(app.extensions, 'fs_scans', state)


_RES = 'Campaign_Store'


# -- directories ------------------------------------------------------------

def test_directories_disabled_banner(auth_client, active_project):
    """Plugin off → 200 with the 'unavailable' alert, not a 404."""
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    )
    assert resp.status_code == 200
    assert 'Filesystem-scan data is unavailable' in resp.get_data(as_text=True)


def test_directories_404_on_unknown_projcode(auth_client):
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/NOPE9999/directories?resource={_RES}'
    )
    assert resp.status_code == 404


def test_directories_renders_rows(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    def fake_scan(session, project, resource_name, **kw):
        captured.update(kw)
        return [{
            'path': '/glade/campaign/cisl/csg', 'depth': 4,
            'total_size_r': 2 * 1024 ** 4, 'file_count_r': 12345,
            'dir_count_r': 50, 'max_atime_r': '2026-05-01 10:00:00',
            'owner_uid': 1001, 'owner_gid': 2001, 'filesystem': 'cisl',
        }]
    monkeypatch.setattr(service, 'scan_directories', fake_scan)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '/glade/campaign/cisl/csg' in body
    assert 'TiB' in body                       # 2 TiB rendered via fmt_size
    assert 'Filesystem-scan data is unavailable' not in body
    assert captured['sort_by'] == 'size'       # default
    assert captured['subpath'] is None         # no fileset


def test_directories_sort_by_whitelisted(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda s, p, r, **kw: captured.update(kw) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&sort_by=bogus'
    )
    assert captured['sort_by'] == 'size'       # bogus coerced to default

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&sort_by=files'
    )
    assert captured['sort_by'] == 'files'


def test_directories_fileset_becomes_subpath(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda s, p, r, **kw: captured.update(kw) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&fileset=/glade/campaign/cisl/csg'
    )
    assert captured['subpath'] == '/glade/campaign/cisl/csg'


def test_directories_error_banner_on_exception(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError('backend down')
    monkeypatch.setattr(service, 'scan_directories', boom)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Could not load filesystem-scan data' in body
    assert 'backend down' in body


# -- entities ---------------------------------------------------------------

def test_entities_owner_renders(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_owner_summary', lambda s, p, r, **kw: [{
        'owner_uid': 1001, 'total_size': 1024 ** 4, 'total_files': 500,
        'directory_count': 10, 'filesystem': 'cisl', 'username': 'benkirk',
    }])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'benkirk' in body
    assert 'By group' in body          # the owner↔group toggle is present


def test_entities_group_renders(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    def fake_group(s, p, r, **kw):
        captured.update(kw)
        return [{
            'owner_gid': 2001, 'total_size': 1024 ** 4, 'total_files': 500,
            'directory_count': 10, 'filesystem': 'cisl', 'groupname': 'csgteam',
        }]
    monkeypatch.setattr(service, 'scan_group_summary', fake_group)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities'
        f'?resource={_RES}&kind=group'
    )
    assert resp.status_code == 200
    assert 'csgteam' in resp.get_data(as_text=True)


def test_entities_group_drilldown_and_pie(app, auth_client, active_project, monkeypatch):
    """By-group rows are now expandable (GID drill-down) and a clickable pie renders."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_group_summary', lambda s, p, r, **kw: [{
        'owner_gid': 2001, 'total_size': 1024 ** 4, 'total_files': 500,
        'directory_count': 10, 'filesystem': 'cisl', 'groupname': 'csgteam',
    }])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities'
        f'?resource={_RES}&kind=group'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'data-group-gid="2001"' in body     # row addressable from a pie wedge
    assert 'owner_gid=2001' in body            # collapse lazy-loads directories by GID
    assert '#disk-ent-group-2001' in body      # pie wedge/legend sentinel


def test_entities_kind_whitelisted(app, auth_client, active_project, monkeypatch):
    """A bogus kind falls back to owner (scan_owner_summary is used)."""
    from webapp.disk_scans import service
    called = {'owner': False, 'group': False}
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_owner_summary',
                        lambda *a, **k: called.update(owner=True) or [])
    monkeypatch.setattr(service, 'scan_group_summary',
                        lambda *a, **k: called.update(group=True) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities'
        f'?resource={_RES}&kind=bogus'
    )
    assert called['owner'] is True
    assert called['group'] is False


# -- access history ---------------------------------------------------------

def test_access_history_renders_svg(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    hist = {
        'bucket_labels': ['< 1 Month', '1-3 Months', '7+ Years'],
        'buckets': {
            '< 1 Month':   {'data': 2 * 1024 ** 4, 'files': 100,
                            'owners': {1001: {'data': 1024 ** 4, 'files': 60},
                                       1002: {'data': 1024 ** 4, 'files': 40}}},
            '1-3 Months':  {'data': 1024 ** 4, 'files': 50,
                            'owners': {1001: {'data': 1024 ** 4, 'files': 50}}},
            '7+ Years':    {'data': 512 * 1024 ** 3, 'files': 10, 'owners': {}},
        },
        'total_data': 3 * 1024 ** 4 + 512 * 1024 ** 3, 'total_files': 160,
        'directory': '/glade/campaign/cisl', 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
        'username_map': {1001: 'alice', 1002: 'bob'},
    }
    monkeypatch.setattr(service, 'scan_access_history', lambda s, p, r, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<svg' in body                 # matplotlib SVG actually rendered
    assert '7+ Years' in body             # bucket label in the table (no HTML-escaping)
    assert 'fast path' in body            # fast_path badge
    # Per-user breakdown rendered inside the bucket's collapse detail row.
    assert 'alice' in body                # username resolved via username_map
    assert 'bob' in body
    assert 'data-bs-toggle="collapse"' in body   # bucket rows are expandable
    # Chart bar → row drill-down wiring (svg-chart-links.js #ah-bar- branch):
    # buckets with owners get an SVG anchor and a matching row lookup attr.
    assert '#ah-bar-0' in body            # bar anchor for the first owned bucket
    assert 'data-ah-bucket="0"' in body   # row the anchor expands


def test_access_history_empty_when_none(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_access_history', lambda s, p, r, **kw: None)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    assert resp.status_code == 200
    assert 'No distribution data' in resp.get_data(as_text=True)


def test_file_sizes_renders_svg(app, auth_client, active_project, monkeypatch):
    """File-size tab is the access-history tab's twin: same shape, same
    template/chart, different service query (scan_file_sizes)."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    hist = {
        'bucket_labels': ['0 - 1 KiB', '1 KiB - 10 KiB', '100 GiB+'],
        'buckets': {
            '0 - 1 KiB':     {'data': 127 * 1024 ** 3, 'files': 4 * 10 ** 8,
                              'owners': {1001: {'data': 64 * 1024 ** 3, 'files': 3 * 10 ** 8},
                                         1002: {'data': 63 * 1024 ** 3, 'files': 10 ** 8}}},
            '1 KiB - 10 KiB': {'data': 693 * 1024 ** 3, 'files': 3 * 10 ** 8,
                               'owners': {1001: {'data': 693 * 1024 ** 3, 'files': 3 * 10 ** 8}}},
            '100 GiB+':      {'data': 8 * 1024 ** 5, 'files': 35000, 'owners': {}},
        },
        'total_data': 8 * 1024 ** 5, 'total_files': 7 * 10 ** 8,
        'directory': '/glade/campaign/cisl', 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
        'username_map': {1001: 'fasullo', 1002: 'schwartz'},
    }
    monkeypatch.setattr(service, 'scan_file_sizes', lambda s, p, r, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/file-sizes?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<svg' in body                 # matplotlib SVG rendered
    assert '100 GiB+' in body             # file-size bucket label in the table
    assert 'fasullo' in body              # per-user breakdown resolved
    assert '#ah-bar-0' in body            # bar→row drill-down anchor (shared scheme)
    assert 'data-ah-bucket="0"' in body
    # Data ↔ Files metric pill present (file-sizes only) and defaults to Data.
    assert 'metric=files' in body
    assert 'Top users by data' in body
    # Log-scale switch present and off by default.
    assert 'Log scale' in body
    assert 'disk-scans-log-' in body
    assert 'checked' not in body

    # Switching the pill re-renders the same fragment by file count.
    resp2 = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}'
        f'/file-sizes?resource={_RES}&metric=files'
    )
    assert resp2.status_code == 200
    body2 = resp2.get_data(as_text=True)
    assert '<svg' in body2
    assert 'Top users by files' in body2   # per-user table re-sorted by metric

    # Log scale on → still renders (solid bars), switch reflects checked state,
    # and the bar→row drill-down anchor survives.
    resp3 = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}'
        f'/file-sizes?resource={_RES}&log=1'
    )
    assert resp3.status_code == 200
    body3 = resp3.get_data(as_text=True)
    assert '<svg' in body3
    assert 'checked' in body3              # switch reflects log_on
    assert '#ah-bar-0' in body3            # drill-down preserved under log


def test_fragment_missing_resource_is_graceful(app, auth_client, active_project, monkeypatch):
    """No ?resource= → treated like disabled (no unscoped query)."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    called = {'hit': False}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda *a, **k: called.update(hit=True) or [])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
    )
    assert resp.status_code == 200
    assert called['hit'] is False


# ---------------------------------------------------------------------------
# cache.cached_scan — scan-date-keyed get / miss / weekly invalidation
# ---------------------------------------------------------------------------

class _FakeQ:
    """Minimal facade stand-in: only scan_dates() is needed for the cache key."""
    def __init__(self, iso):
        self.iso = iso

    def scan_dates(self, filesystems=None):
        return [datetime.fromisoformat(self.iso)] if self.iso else []


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

    # Different opts (a future filter selection) → distinct key → recompute.
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'sort_by': 'files', 'limit': 50}, compute)
    assert calls['n'] == 2

    # Different query type, same scope → its own entry.
    c.cached_scan('owner', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    assert calls['n'] == 3

    # A new weekly scan (later date) → key changes → recompute (auto-invalidation).
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


# ---------------------------------------------------------------------------
# Phase 3 — directory filters, two cache buckets, resource mode, drill-down
# ---------------------------------------------------------------------------

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
    svc.scan_directories(
        None, object(), 'Campaign_Store',
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
    svc.scan_directories(None, object(), 'Campaign_Store', owner_gid=2001)
    kw = cap['list_kwargs']
    assert kw['group_id'] == 2001            # facade param is group_id
    assert kw['owner_id'] is None            # mutually exclusive with owner


def test_directories_bucket_selection(monkeypatch):
    """Any filter routes to the 'filtered' bucket; the bare query to 'default'."""
    from webapp.disk_scans import service
    _wire_service(
        monkeypatch, prefixes=['/p'], collections=['c'], warmed=['c'],
        collection_map={'/p': 'c'}, capture={},
    )
    seen = []

    def fake_cached(qt, q, colls, pfx, opts, compute, bucket='default'):
        seen.append(bucket)
        return compute()

    monkeypatch.setattr(service, 'cached_scan', fake_cached)
    service.scan_directories(None, object(), 'R')                     # default
    service.scan_directories(None, object(), 'R', owner_uid=5)        # filtered
    service.scan_directories(None, object(), 'R', owner_gid=7)        # filtered
    service.scan_directories(None, object(), 'R', leaves_only=True)   # filtered
    service.scan_directories(None, object(), 'R',
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


# -- resource mode (service) -------------------------------------------------

def _wire_resource_service(monkeypatch, *, collections, capture):
    """Patch the service for resource mode: a fake module + collection map."""
    from webapp.disk_scans import service

    class _FakeQueries:
        def __init__(self, filesystems):
            capture['filesystems'] = list(filesystems)

        def list_directories(self, **kw):
            capture['list_kwargs'] = kw
            return [{'path': 'X'}]

    mod = types.SimpleNamespace(FsScanQueries=_FakeQueries)
    monkeypatch.setattr(service, 'get_module', lambda: mod)
    monkeypatch.setattr(service, 'collections_for_resource',
                        lambda r: list(collections))
    return service


def test_scan_directories_resource_unscoped(monkeypatch):
    """Resource mode queries the whole collection (path_prefixes=None)."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['campaign'], capture=cap)
    rows = svc.scan_directories_resource('Campaign_Store')
    assert cap['filesystems'] == ['campaign']
    assert cap['list_kwargs']['path_prefixes'] is None   # whole-collection fast path
    assert rows == [{'path': 'X'}]


def test_scan_directories_resource_subpath(monkeypatch):
    """A fileset narrows resource mode to that single sub-path."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['campaign'], capture=cap)
    svc.scan_directories_resource('Campaign_Store', subpath='/glade/campaign/cisl')
    assert cap['list_kwargs']['path_prefixes'] == ['/glade/campaign/cisl']


def test_scan_directories_resource_empty_when_plugin_off(monkeypatch):
    from webapp.disk_scans import service
    monkeypatch.setattr(service, 'get_module', lambda: None)
    assert service.scan_directories_resource('Campaign_Store') == []


def test_collections_for_resource_delegates(monkeypatch):
    """The resource→collections seam returns the warmed set (today)."""
    from webapp.disk_scans import session as sess
    monkeypatch.setattr(sess, 'get_collections', lambda app=None: ['campaign'])
    assert sess.collections_for_resource('Campaign_Store') == ['campaign']
    monkeypatch.setattr(sess, 'get_collections', lambda app=None: [])
    assert sess.collections_for_resource('Campaign_Store') == []


# -- routes: filters, explorer page, owner drill-down ------------------------

def test_directories_owner_filter_and_form(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    def fake_scan(session, project, resource_name, **kw):
        captured.update(kw)
        return [{
            'path': '/glade/campaign/cisl/csg', 'depth': 4,
            'total_size_r': 1024 ** 4, 'file_count_r': 1, 'dir_count_r': 1,
            'max_atime_r': None, 'owner_uid': 4242, 'owner_gid': 1,
            'filesystem': 'cisl',
        }]
    monkeypatch.setattr(service, 'scan_directories', fake_scan)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&owner_uid=4242&leaves_only=1&accessed_before=2026-01-01'
    )
    assert resp.status_code == 200
    assert captured['owner_uid'] == 4242
    assert captured['leaves_only'] is True
    assert captured['accessed_before'] == datetime(2026, 1, 1)
    body = resp.get_data(as_text=True)
    # Hidden params form round-trips the active filters across sort re-fetches.
    assert 'name="owner_uid" value="4242"' in body
    assert 'name="leaves_only"' in body
    assert 'name="accessed_before" value="2026-01-01"' in body


def test_directories_subdirs_column_hidden_under_leaves_only(
        app, auth_client, active_project, monkeypatch):
    """Recursive subdir count is 0 for leaves — hide the column + its pill."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda s, p, r, **kw: [{
        'path': '/glade/campaign/cisl/csg/leaf', 'depth': 5,
        'total_size_r': 1024 ** 4, 'file_count_r': 3, 'dir_count_r': 0,
        'max_atime_r': None, 'owner_uid': 1, 'owner_gid': 1, 'filesystem': 'cisl',
    }])

    base = f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    # Without the filter the Subdirectories column + # Subdirs pill are present.
    on = auth_client.get(base).get_data(as_text=True)
    assert 'Subdirectories' in on
    assert '# Subdirs' in on
    # With leaves-only, both are suppressed.
    off = auth_client.get(base + '&leaves_only=1').get_data(as_text=True)
    assert 'Subdirectories' not in off
    assert '# Subdirs' not in off


def test_directories_page_renders(auth_client, active_project):
    """The standalone explorer page shows the filters panel (project mode)."""
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories/explore'
        f'?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Accessed before' in body
    assert 'disk-scans-filters-' in body      # the filter form id
    assert 'Apply' in body


def test_entities_owner_drilldown_markup(app, auth_client, active_project, monkeypatch):
    """Owner rows are sortable-group tbodies with a lazy directory drill-down."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_owner_summary', lambda s, p, r, **kw: [{
        'owner_uid': 4242, 'total_size': 1024 ** 4, 'total_files': 5,
        'directory_count': 2, 'filesystem': 'cisl', 'username': 'alice',
    }])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'sortable-group' in body
    assert 'data-bs-toggle="collapse"' in body
    assert 'owner_uid=4242' in body              # drill-down hx-get carries the uid
    assert 'shown.bs.collapse' in body           # lazy-loads on expand


# NB: group drill-down is now SUPPORTED (fs_scans plugin group_id filter) —
# see test_entities_group_drilldown_and_pie above, which replaces the former
# test_entities_group_no_drilldown that asserted the single-tbody contract.


# -- resource mode (routes): RBAC gating -------------------------------------

def test_resource_fragment_403_without_perm(non_admin_client):
    resp = non_admin_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/directories'
    )
    assert resp.status_code == 403


def test_resource_page_403_without_perm(non_admin_client):
    resp = non_admin_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/explore'
    )
    assert resp.status_code == 403


def test_resource_fragment_200_with_perm(auth_client):
    """benkirk holds VIEW_ALL_FILESYSTEM_DATA → 200 (plugin off → banner)."""
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/directories'
    )
    assert resp.status_code == 200


def test_resource_page_200_with_perm(auth_client):
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/explore'
    )
    assert resp.status_code == 200
    assert 'Resource-wide' in resp.get_data(as_text=True)


def test_view_all_filesystem_data_grants():
    """Auto-granted to operator bundles via ALL_VIEW; NOT to facility tier."""
    from webapp.utils.rbac import (
        GROUP_PERMISSIONS, USER_FACILITY_PERMISSIONS, Permission,
    )
    p = Permission.VIEW_ALL_FILESYSTEM_DATA
    for bundle in ('nusd', 'csg', 'ssg'):
        assert p in GROUP_PERMISSIONS[bundle], bundle
    assert p not in USER_FACILITY_PERMISSIONS['sureshm']['WNA']


# ---------------------------------------------------------------------------
# File-browser drill-down — _scoped descent/normalized matching, browse rows,
# breadcrumb, shared macro
# ---------------------------------------------------------------------------

def test_scoped_normalized_subpath_selects_fileset(monkeypatch):
    """A normalized subpath selects the matching absolute project fileset."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg', '/glade/campaign/cisl/other'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl',
                        '/glade/campaign/cisl/other': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(None, object(), 'Campaign_Store', subpath='/cisl/csg')
    assert cap['list_kwargs']['path_prefixes'] == ['/glade/campaign/cisl/csg']


def test_scoped_descent_into_subdir(monkeypatch):
    """A subpath BELOW a registered fileset queries that deeper subtree."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    svc.scan_directories(None, object(), 'Campaign_Store', subpath='/cisl/csg/sub')
    assert cap['list_kwargs']['path_prefixes'] == ['/cisl/csg/sub']


def test_scoped_out_of_scope_subpath_empty(monkeypatch):
    """A subpath neither ancestor nor descendant of a project prefix → []."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    assert svc.scan_directories(None, object(), 'Campaign_Store',
                                subpath='/mmm/foo') == []
    assert 'list_kwargs' not in cap


_DRILL_ROW = {
    'path': '/cisl/csg/sub', 'depth': 5, 'total_size_r': 1024 ** 4,
    'file_count_r': 1, 'dir_count_r': 3, 'max_atime_r': None,
    'owner_uid': 1, 'owner_gid': 1, 'filesystem': 'cisl',
}
_DRILL_MARKER = 'fa-folder me-1'   # unique to a drillable row's link


def test_directories_browse_rows_drillable(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda s, p, r, **kw: [_DRILL_ROW])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&browse=1'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert _DRILL_MARKER in body                       # row is a drill link
    # The drill link carries the row path as the new fileset (Jinja's urlencode
    # may or may not %-escape slashes depending on version).
    assert ('fileset=/cisl/csg/sub' in body
            or 'fileset=%2Fcisl%2Fcsg%2Fsub' in body)


def test_directories_card_tab_rows_not_drillable(app, auth_client, active_project, monkeypatch):
    """Without browse (the resource-details card tab) rows stay plain text."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda s, p, r, **kw: [_DRILL_ROW])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    )
    body = resp.get_data(as_text=True)
    assert _DRILL_MARKER not in body
    assert '/cisl/csg/sub' in body                      # path still shown


def test_directories_leaves_only_not_drillable(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda s, p, r, **kw: [_DRILL_ROW])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&browse=1&leaves_only=1'
    )
    assert _DRILL_MARKER not in resp.get_data(as_text=True)


def test_resource_browse_breadcrumb_and_pill_fileset(app, auth_client, monkeypatch):
    """Resource-mode drill: breadcrumb (Home=resource + segments) + sort pill
    carries the active fileset."""
    from webapp.disk_scans import routes, service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories_resource', lambda r, **kw: [])
    monkeypatch.setattr(routes, 'get_module',
                        lambda: types.SimpleNamespace(normalize_path=lambda p: p))

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/directories'
        f'?browse=1&fileset=/cisl/csg'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'aria-label="breadcrumb"' in body
    assert _RES in body                                # Home crumb = resource name
    assert '>cisl<' in body and '>csg<' in body        # path segments
    assert ('fileset=/cisl/csg' in body
            or 'fileset=%2Fcisl%2Fcsg' in body)        # sort pill keeps the anchor


def test_project_breadcrumb_bounded_at_scan_root(app, auth_client, active_project, monkeypatch):
    """Project-mode breadcrumb collapses everything up to the project prefix:
    All / csg / sub — NOT All / cisl / csg / sub."""
    from webapp.disk_scans import routes, service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda s, p, r, **kw: [])
    monkeypatch.setattr(routes, 'get_module',
                        lambda: types.SimpleNamespace(normalize_path=lambda p: p))
    monkeypatch.setattr(routes, 'resolve_scan_scope',
                        lambda s, proj, res: (['/cisl/csg'], ['cisl']))

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&browse=1&fileset=/cisl/csg/sub'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert body.count('breadcrumb-item') == 3          # All, csg, sub (bounded)


def test_breadcrumb_macro_renders_href_and_htmx(app):
    """The shared macro renders href items, htmx items, and a plain active item."""
    from flask import render_template_string
    with app.test_request_context():
        out = render_template_string(
            "{% from 'dashboards/fragments/_breadcrumb.html' import breadcrumb %}"
            "{{ breadcrumb(["
            "{'label':'Admin','attrs':{'href':'/admin'}},"
            "{'label':'Go','attrs':{'hx-get':'/x','hx-target':'#t'}},"
            "{'label':'Here','active':True}]) }}"
        )
    assert 'href="/admin"' in out
    assert 'hx-get="/x"' in out
    assert 'aria-current="page"' in out
    assert '>Here<' in out


# -- disk-scans entity pie chart (By User / By Group) -----------------------

class TestDiskEntityPie:
    """charts.generate_disk_entity_pie_chart — cumulative ~90% trim + clickable
    wedge/legend sentinels that svg-chart-links.js routes to row expansion."""

    def test_cumulative_keep(self):
        from webapp.dashboards.charts import _pie_cumulative_keep
        assert _pie_cumulative_keep([90, 5, 3, 2]) == 1   # one dominant slice
        assert _pie_cumulative_keep([1] * 20) == 9        # hard cap (palette = 10)
        assert _pie_cumulative_keep([5, 4, 3]) == 3       # all fit → no "Other"
        assert _pie_cumulative_keep([0, 0]) == 2          # zero total, no crash

    def test_owner_wedges_clickable_other_inert(self):
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        data = [{'id': 1000 + i, 'name': f'u{i}', 'value': v}
                for i, v in enumerate([50, 20, 10, 6, 5, 3, 2, 1, 1, 1, 0.5, 0.5])]
        svg = generate_disk_entity_pie_chart(data, 'owner')
        assert '#disk-ent-owner-1000' in svg     # top kept entity is clickable
        assert 'Other (' in svg                  # long tail lumped into one slice
        assert '#disk-ent-owner-None' not in svg  # the Other slice has no sentinel

    def test_group_uses_group_prefix(self):
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        svg = generate_disk_entity_pie_chart(
            [{'id': 500, 'name': 'csg', 'value': 10},
             {'id': 501, 'name': None, 'value': 1}], 'group')
        assert '#disk-ent-group-500' in svg

    def test_empty_returns_placeholder(self):
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        assert 'No usage data' in generate_disk_entity_pie_chart([], 'owner')

    def test_decimal_values_do_not_crash(self):
        # Scan rollups arrive as decimal.Decimal from Postgres; the chart must
        # coerce to float (Decimal/float don't mix in cum += v / matplotlib).
        from decimal import Decimal
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        data = [{'id': 7, 'name': 'g7', 'value': Decimal('10')},
                {'id': 8, 'name': 'g8', 'value': Decimal('3')}]
        svg = generate_disk_entity_pie_chart(data, 'group')
        assert '#disk-ent-group-7' in svg
