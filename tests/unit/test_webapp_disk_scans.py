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


@pytest.fixture(autouse=True)
def _disable_fs_scans_cache():
    """Disable the scan-result cache for these tests by default.

    The scoping/route tests exercise the query path directly; the cache is
    covered explicitly by the test_cached_scan_* cases (which re-enable it).
    Reset to a clean, enabled state on teardown so other modules aren't
    affected by the process-wide adapter singleton.
    """
    from webapp.disk_scans import cache as _c
    _c._adapter = None
    _c._disabled = True
    yield
    _c._adapter = None
    _c._disabled = False


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
        collection_for_path=lambda p: collection_map.get(p),
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
            '< 1 Month':   {'data': 2 * 1024 ** 4, 'files': 100, 'owners': {1001: {}}},
            '1-3 Months':  {'data': 1024 ** 4, 'files': 50, 'owners': {1001: {}, 1002: {}}},
            '7+ Years':    {'data': 512 * 1024 ** 3, 'files': 10, 'owners': {}},
        },
        'total_data': 3 * 1024 ** 4 + 512 * 1024 ** 3, 'total_files': 160,
        'directory': '/glade/campaign/cisl', 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
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


def test_access_history_empty_when_none(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_access_history', lambda s, p, r, **kw: None)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    assert resp.status_code == 200
    assert 'No access-history data' in resp.get_data(as_text=True)


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
    c._adapter = None
    c._disabled = False  # re-enable (the autouse fixture disabled it)

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
    c._adapter = None
    c._disabled = False

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
    c._adapter = None
    c._disabled = False  # force re-init under the TTL=0 env

    calls = {'n': 0}
    def compute():
        calls['n'] += 1
        return ['x']

    q = _FakeQ('2026-06-14T00:00:00')
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    c.cached_scan('directories', q, ['mmm'], ['/mmm'], {'limit': 50}, compute)
    assert calls['n'] == 2
    assert c.get_cache_adapter() is None
