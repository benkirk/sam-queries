"""Tests for the fs-scans Flask integration (webapp/disk_scans/*).

Covers two layers:

1. ``service._scoped`` — the project-scoping + ``subpath`` (fileset
   drill-down) narrowing, exercised through the public ``scan_directories``
   wrapper with a fake plugin module. No app context / DB needed.

2. ``routes`` — the three HTMX fragment endpoints (directories, entities,
   access-history): disabled banner (not 404), 404 on unknown projcode,
   param whitelisting, fileset->subpath plumbing, and the enabled happy
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


__all__ = [
    '_DRILL_MARKER',
    '_DRILL_ROW',
    '_FAKE_MOUNTS',
    '_FakeQ',
    '_RES',
    '_WarmConn',
    '_WarmEngine',
    '_WarmURL',
    '_benkirk_uid',
    '_enable_fs_scans',
    '_explore',
    '_fake_collection_for_path',
    '_fake_normalize',
    '_render_distribution_partial',
    '_wire_resource_entities',
    '_wire_resource_service',
    '_wire_service',
]


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


def _wire_service(monkeypatch, *, prefixes, collections, warmed,
                  collection_map, capture):
    """Patch the service module's scope/module/collection helpers + a fake
    FsScanQueries that captures the kwargs it's called with."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    class _FakeQueries:
        def __init__(self, filesystems, database=None):
            capture['filesystems'] = list(filesystems)
            capture['database'] = database

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
    # The plugin + collection seams live on the scope module now: it is
    # ScanScope.resolve() that reaches for them, not the service.
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(scope_mod, 'get_module', lambda: mod)
    # resolve() intersects reachability against the resource's database, so
    # stub the database-aware seam (not get_collections) with the warmed set.
    monkeypatch.setattr(scope_mod, 'collections_for_resource',
                        lambda r, app=None: list(warmed))
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: None)
    monkeypatch.setattr(
        scope_mod, 'resolve_scan_scope',
        lambda session, project, resource_name: (list(prefixes), list(collections)),
    )
    return service


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


class _FakeQ:
    """Minimal facade stand-in: only scan_dates() is needed for the cache key."""
    def __init__(self, iso):
        self.iso = iso

    def scan_dates(self, filesystems=None):
        return [datetime.fromisoformat(self.iso)] if self.iso else []


def _wire_resource_service(monkeypatch, *, collections, capture):
    """Patch the service for resource mode: a fake module + collection map."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    class _FakeQueries:
        def __init__(self, filesystems, database=None):
            capture['filesystems'] = list(filesystems)
            capture['database'] = database

        def list_directories(self, **kw):
            capture['list_kwargs'] = kw
            return [{'path': 'X'}]

    mod = types.SimpleNamespace(FsScanQueries=_FakeQueries)
    monkeypatch.setattr(scope_mod, 'get_module', lambda: mod)
    monkeypatch.setattr(scope_mod, 'collections_for_resource',
                        lambda r, app=None: list(collections))
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: None)
    return service


class _WarmConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): return None


class _WarmURL:
    drivername = 'sqlite'     # skip the postgres connect-listener in _warm

    def __init__(self, db):
        self.database = db
        self.username = self.host = self.port = None


class _WarmEngine:
    def __init__(self, db):
        self.url = _WarmURL(db)

    def connect(self):
        return _WarmConn()


def _wire_resource_entities(monkeypatch, *, collections, capture):
    """Patch the service for resource-mode owner/group/histogram queries.

    Mirrors ``_wire_resource_service`` but the fake ``FsScanQueries`` captures
    the kwargs of the four facade methods the entity/histogram cores call, and
    resolves names so the enrichment paths run.
    """
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod

    class _FakeQueries:
        def __init__(self, filesystems, database=None):
            capture['filesystems'] = list(filesystems)
            capture['database'] = database

        def owner_summary(self, **kw):
            capture['owner_kwargs'] = kw
            return [{'owner_uid': 1001, 'total_size': 5,
                     'total_files': 2, 'directory_count': 1}]

        def group_summary(self, **kw):
            capture['group_kwargs'] = kw
            return [{'owner_gid': 2001, 'total_size': 5,
                     'total_files': 2, 'directory_count': 1}]

        def access_history(self, **kw):
            capture['access_kwargs'] = kw
            return {'bucket_labels': [], 'buckets': {}}

        def file_size_histogram(self, **kw):
            capture['files_kwargs'] = kw
            return {'bucket_labels': [], 'buckets': {}}

        def resolve_usernames(self, uids):
            return {u: f'user{u}' for u in uids}

        def resolve_groupnames(self, gids):
            return {g: f'grp{g}' for g in gids}

    mod = types.SimpleNamespace(FsScanQueries=_FakeQueries)
    monkeypatch.setattr(scope_mod, 'get_module', lambda: mod)
    monkeypatch.setattr(scope_mod, 'collections_for_resource',
                        lambda r, app=None: list(collections))
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: None)
    return service


_DRILL_ROW = {
    'path': '/cisl/csg/sub', 'depth': 5, 'total_size_r': 1024 ** 4,
    'file_count_r': 1, 'dir_count_r': 3, 'max_atime_r': None,
    'owner_uid': 1, 'owner_gid': 1, 'filesystem': 'cisl',
}


_DRILL_MARKER = 'fa-folder me-1'   # unique to a drillable row's link


def _benkirk_uid(session):
    from sam import User
    u = User.get_by_username(session, 'benkirk')
    assert u is not None and u.unix_uid is not None, (
        'benkirk must be preserved with a unix_uid in the snapshot — '
        'see project_test_db_fixtures.md'
    )
    return u.unix_uid


def _render_distribution_partial(app, owners):
    from flask import render_template
    hist = {
        'bucket_labels': ['> 1 year'],
        'buckets': {'> 1 year': {
            'data': 100, 'files': 10, 'owners': owners,
            'accessed_before': '2025-01-01', 'accessed_after': '2024-01-01',
        }},
        'total_data': 100, 'total_files': 10,
        'username_map': {7: 'benkirk', 8: 'alice'},
    }
    with app.test_request_context():
        return render_template(
            'dashboards/user/partials/disk_scans_distribution.html',
            hist=hist, chart_svg='<svg/>', enabled=True, error=None,
            resource_name=_RES, scope='', fileset=None, target_id='t',
            bucket_header='Last accessed', metric='data', metric_toggle=False,
            log_toggle=False, log_on=False, fragment_url='/frag',
            dir_fragment_url='/dirs')


def _explore(auth_client, project, query=''):
    return auth_client.get(
        f'/dashboards/user/disk-scans/{project.projcode}/directories/explore'
        f'?resource={_RES}{query}'
    ).get_data(as_text=True)
