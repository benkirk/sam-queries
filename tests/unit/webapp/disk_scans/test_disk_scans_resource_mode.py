from __future__ import annotations

from webapp.disk_scans.scope import (
    ProjectScanScope,
    ResourceScanScope,
)
from _disk_scans_helpers import (
    _FakeQ,
    _WarmEngine,
    _wire_resource_entities,
    _wire_resource_service,
    _wire_service,
)


def test_scan_directories_resource_unscoped(monkeypatch):
    """Resource mode queries the whole collection (path_prefixes=None)."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['campaign'], capture=cap)
    rows = svc.scan_directories(ResourceScanScope('Campaign_Store'))
    assert cap['filesystems'] == ['campaign']
    assert cap['list_kwargs']['path_prefixes'] is None   # whole-collection fast path
    assert rows == [{'path': 'X'}]


def test_scan_directories_resource_subpath(monkeypatch):
    """A fileset narrows resource mode to that single sub-path."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['campaign'], capture=cap)
    svc.scan_directories(ResourceScanScope('Campaign_Store', '/glade/campaign/cisl'))
    assert cap['list_kwargs']['path_prefixes'] == ['/glade/campaign/cisl']


def test_scan_directories_resource_forwards_full_filters(monkeypatch):
    """Resource mode accepts the same filters as project mode — so the card's
    per-user/group + histogram-band drill-downs filter identically."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['campaign'], capture=cap)
    svc.scan_directories(
        ResourceScanScope('Campaign_Store'),
        owner_gid=2001, atime_recursive=False,
        min_avg_size=10, max_avg_size=20, sort_by='size_nr')
    kw = cap['list_kwargs']
    assert kw['group_id'] == 2001
    assert kw['atime_recursive'] is False
    assert kw['min_avg_size'] == 10
    assert kw['max_avg_size'] == 20
    assert kw['sort_by'] == 'size_nr'


def test_scan_directories_resource_empty_when_plugin_off(monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(scope_mod, 'get_module', lambda: None)
    assert service.scan_directories(ResourceScanScope('Campaign_Store')) == []


def test_collections_for_resource_maps_via_database(monkeypatch):
    """The seam resolves the resource's database, then returns THAT database's
    warmed collections — so Campaign_Store and Destor see disjoint sets."""
    from webapp.disk_scans import session as sess
    # Patch the extension instance: the module-level names are its bound
    # methods, and the seam calls its siblings through self.
    ext = sess.extension
    monkeypatch.setattr(
        ext, 'database_for_resource',
        lambda r, app=None: {'Campaign_Store': 'campaign', 'Destor': 'destor'}.get(r))
    monkeypatch.setattr(ext, 'get_databases', lambda app=None: {
        'campaign': {'collections': ['cisl', 'mmm'], 'engines': {}},
        'destor':    {'collections': ['gdex'], 'engines': {}},
    })
    assert ext.collections_for_resource('Campaign_Store') == ['cisl', 'mmm']
    assert ext.collections_for_resource('Destor') == ['gdex']
    # Unmapped resource -> no database -> no collections (never unscoped).
    assert ext.collections_for_resource('Nope') == []
    # Mapped but unwarmed database -> [].
    monkeypatch.setattr(ext, 'get_databases', lambda app=None: {})
    assert ext.collections_for_resource('Campaign_Store') == []


def test_database_for_resource_reads_config_map(app, monkeypatch):
    """database_for_resource reads the FS_SCAN_RESOURCE_DATABASES config map,
    and is safe (None) outside an app context."""
    from webapp.disk_scans import session as sess
    monkeypatch.setitem(app.config, 'FS_SCAN_RESOURCE_DATABASES',
                        {'Campaign_Store': 'campaign', 'Destor': 'destor'})
    with app.app_context():
        assert sess.database_for_resource('Campaign_Store') == 'campaign'
        assert sess.database_for_resource('Destor') == 'destor'
        assert sess.database_for_resource('Unknown') is None
    # No app context -> None (lets service helpers resolve unconditionally).
    assert sess.database_for_resource('Campaign_Store') is None


def test_scan_directories_threads_resource_database(monkeypatch):
    """The resource's database (Destor -> destor) reaches FsScanQueries."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/lustre/desc1/gdex/proj'],
        collections=['gdex'], warmed=['gdex'],
        collection_map={'/lustre/desc1/gdex/proj': 'gdex'},
        capture=cap,
    )
    from webapp.disk_scans import scope as _sm
    monkeypatch.setattr(_sm, 'database_for_resource',
                        lambda r, app=None: 'destor' if r == 'Destor' else None)
    svc.scan_directories(ProjectScanScope(None, object(), 'Destor'))
    assert cap['filesystems'] == ['gdex']
    assert cap['database'] == 'destor'        # threaded to the facade


def test_scan_directories_resource_threads_database(monkeypatch):
    """Resource mode threads the resource's database to the facade too."""
    cap = {}
    svc = _wire_resource_service(monkeypatch, collections=['gdex'], capture=cap)
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(scope_mod, 'database_for_resource', lambda r, app=None: 'destor')
    svc.scan_directories(ResourceScanScope('Destor'))
    assert cap['database'] == 'destor'


def test_cached_scan_database_is_in_key(monkeypatch):
    """Same scope + opts but a different database -> distinct cache entries, so a
    collection name shared across databases can't collide."""
    from webapp.disk_scans import cache as c
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    c._adapters.clear()

    calls = {'n': 0}
    def compute():
        calls['n'] += 1
        return [{'v': calls['n']}]

    q = _FakeQ('2026-06-14T00:00:00')
    opts = {'limit': 50}
    r1 = c.cached_scan('owner', q, ['gdex'], ['/gdex'], opts, compute, database='campaign')
    r2 = c.cached_scan('owner', q, ['gdex'], ['/gdex'], opts, compute, database='destor')
    assert r1 == [{'v': 1}] and r2 == [{'v': 2}]      # different db -> recompute
    assert calls['n'] == 2
    # Same (db, scope, opts) repeats -> served from cache.
    c.cached_scan('owner', q, ['gdex'], ['/gdex'], opts, compute, database='destor')
    assert calls['n'] == 2


def test_init_fs_scans_warms_each_database_separately(app, monkeypatch):
    """init_fs_scans discovers + warms each configured database independently,
    keys state by database, and wires resource -> database -> collections."""
    from webapp import disk_scans
    from webapp.disk_scans import session as sess

    schemas = {'campaign': ['cisl', 'mmm'], 'destor': ['gdex']}

    class _FakeMod:
        def list_pg_schemas(self, database=None):
            return list(schemas.get(database, []))

        def get_engine(self, collection, database=None):
            return _WarmEngine(database)

    from sam.plugins import FS_SCANS
    monkeypatch.setattr(FS_SCANS, 'load', lambda: _FakeMod())
    monkeypatch.setitem(app.config, 'FS_SCANS_ENABLED', True)
    monkeypatch.setitem(app.config, 'FS_SCAN_RESOURCE_DATABASES',
                        {'Campaign_Store': 'campaign', 'Destor': 'destor'})

    orig = app.extensions.get('fs_scans')
    try:
        disk_scans.init_fs_scans(app)
        dbs = sess.get_databases(app)
        assert set(dbs) == {'campaign', 'destor'}
        assert dbs['campaign']['collections'] == ['cisl', 'mmm']
        assert dbs['destor']['collections'] == ['gdex']
        assert sess.is_enabled(app) is True
        # resource -> database -> collections, end to end
        assert sess.collections_for_resource('Campaign_Store', app) == ['cisl', 'mmm']
        assert sess.collections_for_resource('Destor', app) == ['gdex']
        # union view spans both databases
        assert sess.get_collections(app) == ['cisl', 'gdex', 'mmm']
    finally:
        if orig is not None:
            app.extensions['fs_scans'] = orig
        else:
            app.extensions.pop('fs_scans', None)


def test_init_fs_scans_survives_one_unreachable_database(app, monkeypatch):
    """If one database is unreachable (destor not provisioned), the other still
    warms and the feature stays enabled for it."""
    from webapp import disk_scans
    from webapp.disk_scans import session as sess

    class _FakeMod:
        def list_pg_schemas(self, database=None):
            if database == 'destor':
                raise RuntimeError('destor not reachable')
            return ['cisl']

        def get_engine(self, collection, database=None):
            return _WarmEngine(database)

    from sam.plugins import FS_SCANS
    monkeypatch.setattr(FS_SCANS, 'load', lambda: _FakeMod())
    monkeypatch.setitem(app.config, 'FS_SCANS_ENABLED', True)
    monkeypatch.setitem(app.config, 'FS_SCAN_RESOURCE_DATABASES',
                        {'Campaign_Store': 'campaign', 'Destor': 'destor'})

    orig = app.extensions.get('fs_scans')
    try:
        disk_scans.init_fs_scans(app)
        dbs = sess.get_databases(app)
        assert set(dbs) == {'campaign'}                  # destor skipped, not fatal
        assert sess.is_enabled(app) is True
        assert sess.collections_for_resource('Destor', app) == []
        assert sess.collections_for_resource('Campaign_Store', app) == ['cisl']
    finally:
        if orig is not None:
            app.extensions['fs_scans'] = orig
        else:
            app.extensions.pop('fs_scans', None)


def test_scan_owner_summary_resource_unscoped(monkeypatch):
    """Resource mode queries the whole collection (path_prefixes=None)."""
    cap = {}
    svc = _wire_resource_entities(monkeypatch, collections=['campaign'], capture=cap)
    rows = svc.scan_entity_summary(
        ResourceScanScope('Campaign_Store'), 'owner', limit=10)
    assert cap['filesystems'] == ['campaign']
    assert cap['owner_kwargs']['path_prefixes'] is None   # whole-collection fast path
    assert cap['owner_kwargs']['limit'] == 10
    assert rows[0]['username'] == 'user1001'              # enrichment ran


def test_scan_group_summary_resource_subpath(monkeypatch):
    """A fileset narrows resource mode to that single sub-path."""
    cap = {}
    svc = _wire_resource_entities(monkeypatch, collections=['campaign'], capture=cap)
    rows = svc.scan_entity_summary(
        ResourceScanScope('Campaign_Store', '/glade/campaign/cisl'), 'group')
    assert cap['group_kwargs']['path_prefixes'] == ['/glade/campaign/cisl']
    assert rows[0]['groupname'] == 'grp2001'


def test_scan_access_history_resource_unscoped(monkeypatch):
    cap = {}
    svc = _wire_resource_entities(monkeypatch, collections=['campaign'], capture=cap)
    hist = svc.scan_distribution(ResourceScanScope('Campaign_Store'), 'access_history')
    assert cap['access_kwargs']['path_prefixes'] is None
    assert cap['access_kwargs']['owner_uid'] is None
    assert hist == {'bucket_labels': [], 'buckets': {}}


def test_scan_file_sizes_resource_owner_uid(monkeypatch):
    cap = {}
    svc = _wire_resource_entities(monkeypatch, collections=['campaign'], capture=cap)
    svc.scan_distribution(
        ResourceScanScope('Campaign_Store'), 'file_sizes', owner_uid=4242)
    assert cap['files_kwargs']['path_prefixes'] is None   # whole-collection fast path
    assert cap['files_kwargs']['owner_uid'] == 4242


def test_resource_entity_fns_empty_when_plugin_off(monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(scope_mod, 'get_module', lambda: None)
    assert service.scan_entity_summary(ResourceScanScope('Campaign_Store'), 'owner') == []
    assert service.scan_entity_summary(ResourceScanScope('Campaign_Store'), 'group') == []
    assert service.scan_distribution(ResourceScanScope('Campaign_Store'), 'access_history') is None
    assert service.scan_distribution(ResourceScanScope('Campaign_Store'), 'file_sizes') is None


def test_resource_entity_fns_empty_when_no_collections(monkeypatch):
    """An off-map / unwarmed resource yields no results (never unscoped)."""
    cap = {}
    svc = _wire_resource_entities(monkeypatch, collections=[], capture=cap)
    assert svc.scan_entity_summary(ResourceScanScope('Campaign_Store'), 'owner') == []
    assert svc.scan_distribution(ResourceScanScope('Campaign_Store'), 'access_history') is None


def test_scan_capable_resources_filters_unwarmed(app, monkeypatch):
    """Keeps only configured resources that currently have warmed collections."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(service, 'is_enabled', lambda a=None: True)
    monkeypatch.setattr(
        service, 'collections_for_resource',
        lambda n, app=None: ['campaign'] if n == 'Campaign_Store' else [])
    monkeypatch.setitem(app.config, 'FS_SCAN_RESOURCES', ['Campaign_Store', 'Destor'])
    with app.app_context():
        assert service.scan_capable_resources() == ['Campaign_Store']


def test_scan_capable_resources_empty_when_disabled(app, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(service, 'is_enabled', lambda a=None: False)
    monkeypatch.setitem(app.config, 'FS_SCAN_RESOURCES', ['Campaign_Store'])
    with app.app_context():
        assert service.scan_capable_resources() == []
