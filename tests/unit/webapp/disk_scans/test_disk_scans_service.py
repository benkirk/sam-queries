from __future__ import annotations

from webapp.disk_scans.scope import ProjectScanScope
from _disk_scans_helpers import _wire_service


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
    rows = svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store'))
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
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store', '/glade/campaign/cisl/csg'))
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
    rows = svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store', '/glade/campaign/other/elsewhere'))
    assert rows == []
    # Facade must not have been constructed/queried for an unscoped path.
    assert 'list_kwargs' not in cap


def test_scoped_drops_unwarmed_collections(monkeypatch):
    """Collections not in the warmed set are dropped -> no results."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/aiml/proj'],
        collections=['aiml'], warmed=['cisl'],   # aiml not reachable
        collection_map={'/glade/campaign/aiml/proj': 'aiml'},
        capture=cap,
    )
    assert svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store')) == []
    assert 'list_kwargs' not in cap


def test_scoped_returns_empty_when_module_missing(monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    monkeypatch.setattr(scope_mod, 'get_module', lambda: None)
    assert service.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store')) == []
    assert service.scan_distribution(ProjectScanScope(None, object(), 'Campaign_Store'), 'access_history') is None
    assert service.scan_distribution(ProjectScanScope(None, object(), 'Campaign_Store'), 'file_sizes') is None


def test_resolve_scan_scope_grouped_orders_root_first_and_groups(monkeypatch):
    """Pre-order from the scan root, one group per fileset-owning node."""
    from webapp.disk_scans import scope

    tree = {
        'projcode': 'ROOT0001',
        'fileset_paths': ['/glade/campaign/root/b', '/glade/campaign/root/a'],
        'children': [
            {'projcode': 'CHILD002', 'fileset_paths': ['/glade/campaign/child2'],
             'children': []},
            # a node that owns nothing is skipped entirely
            {'projcode': 'EMPTY003', 'fileset_paths': [], 'children': [
                {'projcode': 'GRAND004', 'fileset_paths': ['/glade/campaign/gc'],
                 'children': []},
            ]},
        ],
    }

    class _Proj:
        projcode = 'ROOT0001'

    monkeypatch.setattr(scope, 'build_disk_subtree',
                        lambda s, p, r: {'tree': tree})
    groups = scope.resolve_scan_scope_grouped(None, _Proj(), 'Campaign_Store')

    assert [g['projcode'] for g in groups] == ['ROOT0001', 'CHILD002', 'GRAND004']
    root = groups[0]
    assert root['is_root'] is True
    # paths sorted within a group
    assert root['paths'] == ['/glade/campaign/root/a', '/glade/campaign/root/b']
    assert all(g['is_root'] is False for g in groups[1:])
    # EMPTY003 (no filesets) contributes no group
    assert 'EMPTY003' not in [g['projcode'] for g in groups]


def test_resolve_scan_scope_grouped_empty_when_no_dirs(monkeypatch):
    from webapp.disk_scans import scope

    class _Proj:
        projcode = 'NONE0001'

    monkeypatch.setattr(
        scope, 'build_disk_subtree',
        lambda s, p, r: {'tree': {'projcode': 'NONE0001',
                                  'fileset_paths': [], 'children': []}})
    assert scope.resolve_scan_scope_grouped(None, _Proj(), 'Campaign_Store') == []
