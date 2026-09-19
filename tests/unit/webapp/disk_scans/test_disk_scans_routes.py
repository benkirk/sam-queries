from __future__ import annotations

from datetime import datetime

from _disk_scans_helpers import (
    _RES,
    _enable_fs_scans,
)


def test_directories_disabled_banner(auth_client, active_project):
    """Plugin off -> 200 with the 'unavailable' alert, not a 404."""
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
    assert captured_scope.subpath is None      # no fileset


def test_directories_sort_by_whitelisted(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    captured = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda scope, *a, **kw: captured.update(kw) or [])

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
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    seen = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda scope, *a, **kw: seen.update(scope=scope) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&fileset=/glade/campaign/cisl/csg'
    )
    assert seen['scope'].subpath == '/glade/campaign/cisl/csg'


def test_directories_recursive_flag(app, auth_client, active_project, monkeypatch):
    """?recursive defaults True; recursive=0 + outermost=1 reach the service."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    captured = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda scope, *a, **kw: captured.update(kw) or [])

    base = (f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
            f'?resource={_RES}')
    auth_client.get(base)
    assert captured['atime_recursive'] is True       # default — existing callers
    assert captured['outermost_only'] is False

    auth_client.get(base + '&recursive=0&outermost=1&sort_by=size_nr')
    assert captured['atime_recursive'] is False
    assert captured['outermost_only'] is True
    assert captured['sort_by'] == 'size_nr'          # _nr sort key whitelisted


def test_directories_error_banner_on_exception(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
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


def test_entities_owner_renders(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_entity_summary', lambda scope, *a, **kw: [{
        'owner_uid': 1001, 'total_size': 1024 ** 4, 'total_files': 500,
        'directory_count': 10, 'filesystem': 'cisl', 'username': 'benkirk',
    }])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'benkirk' in body
    assert 'By group' in body          # the owner<->group toggle is present


def test_entities_group_renders(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    def fake_group(scope, kind, **kw):
        captured.update(kw)
        return [{
            'owner_gid': 2001, 'total_size': 1024 ** 4, 'total_files': 500,
            'directory_count': 10, 'filesystem': 'cisl', 'groupname': 'csgteam',
        }]
    monkeypatch.setattr(service, 'scan_entity_summary', fake_group)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities'
        f'?resource={_RES}&kind=group'
    )
    assert resp.status_code == 200
    assert 'csgteam' in resp.get_data(as_text=True)


def test_entities_group_drilldown_and_pie(app, auth_client, active_project, monkeypatch):
    """By-group rows are now expandable (GID drill-down) and a clickable pie renders."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_entity_summary', lambda scope, *a, **kw: [{
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
    assert '#sam/row/data-group-gid/2001' in body      # pie wedge/legend sentinel


def test_entities_kind_whitelisted(app, auth_client, active_project, monkeypatch):
    """A bogus kind falls back to owner (scan_owner_summary is used)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    called = {'owner': False, 'group': False}
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_entity_summary',
                        lambda *a, **k: called.update(owner=True) or [])
    monkeypatch.setattr(
        service, 'scan_entity_summary',
        lambda scope, kind, **k: called.update({kind: True}) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/entities'
        f'?resource={_RES}&kind=bogus'
    )
    assert called['owner'] is True
    assert called['group'] is False


def test_access_history_renders_svg(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
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
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: hist)

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
    # Chart bar -> row drill-down wiring (svg-chart-links.js #sam/row/data-ah-bucket/ branch):
    # buckets with owners get an SVG anchor and a matching row lookup attr.
    assert '#sam/row/data-ah-bucket/0' in body            # bar anchor for the first owned bucket
    assert 'data-ah-bucket="0"' in body   # row the anchor expands


def test_access_history_user_drilldown_rows(app, auth_client, active_project, monkeypatch):
    """Bands carrying a date window render expandable per-user rows whose
    collapse lazy-loads that user's directories scoped to the band (owner_uid
    + date window + recursive=0 by default)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    hist = {
        'bucket_labels': ['1-2 Years'],
        'buckets': {
            '1-2 Years': {
                'data': 1024 ** 4, 'files': 100,
                'owners': {1001: {'data': 1024 ** 4, 'files': 100}},
                # date window stamped by the service (_atime_band_bounds)
                'accessed_after': '2024-06-01', 'accessed_before': '2025-06-01',
            },
        },
        'total_data': 1024 ** 4, 'total_files': 100,
        'directory': '/glade/campaign/cisl', 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
        'username_map': {1001: 'alice'},
    }
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The user row is expandable and lazy-loads the directories fragment for
    # that user, scoped to the band's date window, non-recursive by default.
    assert 'owner_uid=1001' in body
    assert 'accessed_after=2024-06-01' in body
    assert 'accessed_before=2025-06-01' in body
    assert 'recursive=0' in body
    assert 'sort_by=size_nr' in body
    assert 'shown.bs.collapse' in body            # lazy-load trigger
    # Each tab carries its own caveat: access-history's, not the file-size one.
    assert 'grouped by most recent' in body
    assert 'computed as average file sizes per' not in body


def test_access_history_no_drilldown_without_bounds(app, auth_client, active_project, monkeypatch):
    """A band with no date window (e.g. the file-size histogram shape) does not
    sprout a per-user drill-down — there's no range to scope directories by."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    hist = {
        'bucket_labels': ['1-2 Years'],
        'buckets': {'1-2 Years': {'data': 1, 'files': 1,
                                  'owners': {1001: {'data': 1, 'files': 1}}}},
        'total_data': 1, 'total_files': 1, 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
        'username_map': {1001: 'alice'},
    }
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    body = resp.get_data(as_text=True)
    assert 'alice' in body                        # user still listed
    assert 'owner_uid=1001' not in body           # but not expandable to dirs


def test_file_sizes_user_drilldown_rows(app, auth_client, active_project, monkeypatch):
    """File-size bands carrying an avg-size window render expandable per-user
    rows whose collapse lazy-loads that user's directories filtered by average
    own-file size (owner_uid + min/max_avg_size + recursive=0)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    hist = {
        'bucket_labels': ['1 MiB - 10 MiB'],
        'buckets': {
            '1 MiB - 10 MiB': {
                'data': 1024 ** 3, 'files': 100,
                'owners': {1001: {'data': 1024 ** 3, 'files': 100}},
                'size_min': 1048576, 'size_max': 10485760,
            },
        },
        'total_data': 1024 ** 3, 'total_files': 100,
        'directory': '/glade/campaign/cisl', 'fast_path': True,
        'reference_scan_date': datetime(2026, 6, 1),
        'username_map': {1001: 'alice'},
    }
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/file-sizes?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'owner_uid=1001' in body
    assert 'min_avg_size=1048576' in body
    assert 'max_avg_size=10485760' in body
    assert 'recursive=0' in body
    assert 'shown.bs.collapse' in body
    # File-size tab carries its own caveat, not the access-history one.
    assert 'computed as average file sizes per' in body
    assert 'grouped by most recent' not in body


def test_directories_avg_size_flag(app, auth_client, active_project, monkeypatch):
    """?min_avg_size/max_avg_size reach the service as ints for the size drill."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    captured = {}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda scope, *a, **kw: captured.update(kw) or [])

    auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
        f'?resource={_RES}&min_avg_size=1048576&max_avg_size=10485760&recursive=0&sort_by=size_nr'
    )
    assert captured['min_avg_size'] == 1048576
    assert captured['max_avg_size'] == 10485760


def test_access_history_empty_when_none(app, auth_client, active_project, monkeypatch):
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: None)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/access-history?resource={_RES}'
    )
    assert resp.status_code == 200
    assert 'No distribution data' in resp.get_data(as_text=True)


def test_file_sizes_renders_svg(app, auth_client, active_project, monkeypatch):
    """File-size tab is the access-history tab's twin: same shape, same
    template/chart, different service query (scan_file_sizes)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
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
    monkeypatch.setattr(service, 'scan_distribution', lambda scope, *a, **kw: hist)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/file-sizes?resource={_RES}'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<svg' in body                 # matplotlib SVG rendered
    assert '100 GiB+' in body             # file-size bucket label in the table
    assert 'fasullo' in body              # per-user breakdown resolved
    assert '#sam/row/data-ah-bucket/0' in body            # bar->row drill-down anchor (shared scheme)
    assert 'data-ah-bucket="0"' in body
    # Data <-> Files metric pill present (file-sizes only) and defaults to Data.
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

    # Log scale on -> still renders (solid bars), switch reflects checked state,
    # and the bar->row drill-down anchor survives.
    resp3 = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}'
        f'/file-sizes?resource={_RES}&log=1'
    )
    assert resp3.status_code == 200
    body3 = resp3.get_data(as_text=True)
    assert '<svg' in body3
    assert 'checked' in body3              # switch reflects log_on
    assert '#sam/row/data-ah-bucket/0' in body3            # drill-down preserved under log


def test_fragment_missing_resource_is_graceful(app, auth_client, active_project, monkeypatch):
    """No ?resource= -> treated like disabled (no unscoped query)."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    called = {'hit': False}
    monkeypatch.setattr(service, 'scan_directories',
                        lambda *a, **k: called.update(hit=True) or [])

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/{active_project.projcode}/directories'
    )
    assert resp.status_code == 200
    assert called['hit'] is False
