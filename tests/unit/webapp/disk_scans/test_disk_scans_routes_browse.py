from __future__ import annotations

import types
from datetime import datetime

import pytest

from webapp.disk_scans.scope import ProjectScanScope
from _disk_scans_helpers import (
    _DRILL_MARKER,
    _DRILL_ROW,
    _RES,
    _enable_fs_scans,
    _wire_service,
)


def test_directories_owner_filter_and_form(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    captured_scope = None

    def fake_scan(scope, **kw):
        nonlocal captured_scope
        captured_scope = scope
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
    """Recursive subdir count is 0 for leaves — hide the Dirs column + its pill."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [{
        'path': '/glade/campaign/cisl/csg/leaf', 'depth': 5,
        'total_size_r': 1024 ** 4, 'file_count_r': 3, 'dir_count_r': 7,
        'max_atime_r': None, 'owner_uid': 1, 'owner_gid': 1, 'filesystem': 'cisl',
    }])

    base = f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    # Without the filter the Dirs column + # Subdirs pill are present (the row
    # has a non-zero dir_count_r).
    on = auth_client.get(base).get_data(as_text=True)
    assert 'Dirs' in on
    assert 'Subdirs' in on
    # With leaves-only, both are suppressed.
    off = auth_client.get(base + '&leaves_only=1').get_data(as_text=True)
    assert 'Dirs' not in off
    assert 'Subdirs' not in off


def test_directories_dirs_column_hidden_when_uniformly_zero(
        app, auth_client, active_project, monkeypatch):
    """All rows have dir_count_r == 0 (common in nr drill-downs) — fold the column."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [{
        'path': '/glade/campaign/cisl/csg/a', 'depth': 5,
        'total_size_nr': 1024 ** 3, 'file_count_nr': 9, 'dir_count_r': 0,
        'max_atime_nr': None, 'owner_uid': 1, 'owner_gid': 1, 'filesystem': 'cisl',
    }])

    # Non-recursive drill-down view: single row, dir_count_r 0 -> column folded
    # even though leaves_only is NOT set.
    base = (f'/dashboards/user/disk-scans/{active_project.projcode}'
            f'/directories?resource={_RES}&recursive=0')
    body = auth_client.get(base).get_data(as_text=True)
    assert 'Dirs' not in body
    assert 'Subdirs' not in body


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
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_entity_summary', lambda scope, *a, **kw: [{
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
    """benkirk holds VIEW_ALL_FILESYSTEM_DATA -> 200 (plugin off -> banner)."""
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


def test_resource_entities_drilldown_targets_resource_fragment(app, auth_client, monkeypatch):
    """Whole-FS owner rows drill into the *resource* directories fragment
    (regression: the drill must not be suppressed in resource mode)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_entity_summary', lambda scope, *a, **kw: [{
        'owner_uid': 4242, 'total_size': 1024 ** 4, 'total_files': 5,
        'directory_count': 2, 'filesystem': 'cisl', 'username': 'alice',
    }])

    resp = auth_client.get(f'/dashboards/user/disk-scans/resource/{_RES}/entities')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-bs-toggle="collapse"' in body          # drill chevron present
    assert 'owner_uid=4242' in body                      # carries the uid
    assert f'/disk-scans/resource/{_RES}/directories' in body   # -> resource fragment


@pytest.mark.parametrize('endpoint', ['entities', 'access-history', 'file-sizes'])
def test_resource_card_fragments_403_without_perm(non_admin_client, endpoint):
    resp = non_admin_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/{endpoint}'
    )
    assert resp.status_code == 403


@pytest.mark.parametrize('endpoint', ['entities', 'access-history', 'file-sizes'])
def test_resource_card_fragments_200_with_perm(auth_client, endpoint):
    """benkirk holds VIEW_ALL_FILESYSTEM_DATA -> 200 (plugin off -> banner)."""
    resp = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/{endpoint}'
    )
    assert resp.status_code == 200


def test_view_all_filesystem_data_grants():
    """Auto-granted to operator bundles via ALL_VIEW; NOT to facility tier."""
    from webapp.utils.rbac import (
        GROUP_PERMISSIONS, USER_FACILITY_PERMISSIONS, Permission,
    )
    p = Permission.VIEW_ALL_FILESYSTEM_DATA
    for bundle in ('nusd', 'csg', 'ssg'):
        assert p in GROUP_PERMISSIONS[bundle], bundle
    assert p not in USER_FACILITY_PERMISSIONS['sureshm']['WNA']


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
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store', '/cisl/csg'))
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
    svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store', '/cisl/csg/sub'))
    assert cap['list_kwargs']['path_prefixes'] == ['/cisl/csg/sub']


def test_scoped_out_of_scope_subpath_empty(monkeypatch):
    """A subpath neither ancestor nor descendant of a project prefix -> []."""
    cap = {}
    svc = _wire_service(
        monkeypatch,
        prefixes=['/glade/campaign/cisl/csg'],
        collections=['cisl'], warmed=['cisl'],
        collection_map={'/glade/campaign/cisl/csg': 'cisl'},
        capture=cap,
    )
    assert svc.scan_directories(ProjectScanScope(None, object(), 'Campaign_Store', '/mmm/foo')) == []
    assert 'list_kwargs' not in cap


def test_directories_browse_rows_drillable(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [_DRILL_ROW])

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
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [_DRILL_ROW])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories?resource={_RES}'
    )
    body = resp.get_data(as_text=True)
    assert _DRILL_MARKER not in body
    assert '/cisl/csg/sub' in body                      # path still shown


def test_directories_leaves_only_not_drillable(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [_DRILL_ROW])

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
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [])
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
    monkeypatch.setattr(service, 'scan_directories', lambda scope, *a, **kw: [])
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
