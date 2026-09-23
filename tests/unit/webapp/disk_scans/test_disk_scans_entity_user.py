from __future__ import annotations

import pytest

from _disk_scans_helpers import (
    _RES,
    _benkirk_uid,
    _enable_fs_scans,
    _explore,
    _render_distribution_partial,
)


class TestDiskEntityPie:
    """charts.generate_disk_entity_pie_chart — cumulative ~90% trim + clickable
    wedge/legend sentinels that svg-chart-links.js routes to row expansion."""

    def test_cumulative_keep(self):
        from webapp.dashboards.charts import _pie_cumulative_keep
        assert _pie_cumulative_keep([90, 5, 3, 2]) == 1   # one dominant slice
        assert _pie_cumulative_keep([1] * 20) == 9        # hard cap (palette = 10)
        assert _pie_cumulative_keep([5, 4, 3]) == 3       # all fit -> no "Other"
        assert _pie_cumulative_keep([0, 0]) == 2          # zero total, no crash

    def test_owner_wedges_clickable_other_inert(self):
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        data = [{'id': 1000 + i, 'name': f'u{i}', 'value': v}
                for i, v in enumerate([50, 20, 10, 6, 5, 3, 2, 1, 1, 1, 0.5, 0.5])]
        svg = generate_disk_entity_pie_chart(data, 'owner')
        assert '#sam/row/data-owner-uid/1000' in svg     # top kept entity is clickable
        assert 'Other (' in svg                  # long tail lumped into one slice
        assert '#sam/row/data-owner-uid/None' not in svg  # the Other slice has no sentinel

    def test_group_uses_group_prefix(self):
        from webapp.dashboards.charts import generate_disk_entity_pie_chart
        svg = generate_disk_entity_pie_chart(
            [{'id': 500, 'name': 'csg', 'value': 10},
             {'id': 501, 'name': None, 'value': 1}], 'group')
        assert '#sam/row/data-group-gid/500' in svg

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
        assert '#sam/row/data-group-gid/7' in svg


def test_user_directories_pins_owner_ignoring_query(
        app, auth_client, session, monkeypatch):
    """SECURITY: ?owner_uid / ?owner_user_id are ignored; the directory scan is
    pinned to the logged-in user's unix_uid, never the client-supplied value."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    uid = _benkirk_uid(session)
    captured = {}

    def fake(scope, **kw):
        captured.update(kw)
        captured['resource_name'] = scope.resource_name
        return []
    monkeypatch.setattr(service, 'scan_directories', fake)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/user/{_RES}/directories'
        f'?owner_uid=999999&owner_user_id=999999'
    )
    assert resp.status_code == 200
    assert captured['resource_name'] == _RES
    assert captured['owner_uid'] == uid          # pinned to me
    assert captured['owner_uid'] != 999999       # NOT the tampered value


def test_directories_take_unpicked_search_text_as_the_owner(
        app, auth_client, session, monkeypatch):
    """Enter in the owner picker submits its text (``q``) with no id; it must
    resolve to that user's uid, not silently widen to every owner."""
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    captured = {}

    def fake(scope, **kw):
        captured.update(kw)
        return []
    monkeypatch.setattr(service, 'scan_directories', fake)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/directories'
        '?owner_user_id=&q=benkirk')
    assert resp.status_code == 200
    assert captured['owner_uid'] == _benkirk_uid(session)


def test_directories_unknown_search_text_does_not_scan(
        app, auth_client, monkeypatch):
    from webapp.disk_scans import service
    _enable_fs_scans(app, monkeypatch)
    calls = []
    monkeypatch.setattr(service, 'scan_directories',
                        lambda scope, **kw: calls.append(kw) or [])

    body = auth_client.get(
        f'/dashboards/user/disk-scans/resource/{_RES}/directories'
        '?q=no_such_user_xyz').get_data(as_text=True)
    assert calls == []
    assert "no user &#39;no_such_user_xyz&#39;" in body


@pytest.mark.parametrize('endpoint', ['access-history', 'file-sizes'])
def test_user_distribution_pins_owner_ignoring_query(
        app, auth_client, session, monkeypatch, endpoint):
    """SECURITY: the histogram scans are pinned to the logged-in user too."""
    from webapp.disk_scans import service
    from webapp.disk_scans import scope as scope_mod
    _enable_fs_scans(app, monkeypatch)
    uid = _benkirk_uid(session)
    captured = {}

    def fake(scope, kind, **kw):
        captured.update(kw)
        return None      # falsy -> no chart generation; the call is what we check
    monkeypatch.setattr(service, 'scan_distribution', fake)

    resp = auth_client.get(
        f'/dashboards/user/disk-scans/user/{_RES}/{endpoint}?owner_uid=999999'
    )
    assert resp.status_code == 200
    assert captured['owner_uid'] == uid
    assert captured['owner_uid'] != 999999


@pytest.mark.parametrize(
    'endpoint', ['directories', 'access-history', 'file-sizes', 'explore'])
def test_user_routes_reachable_without_view_all(non_admin_client, endpoint):
    """User routes are @login_required only — a user WITHOUT
    VIEW_ALL_FILESYSTEM_DATA gets 200 (contrast the resource routes' 403)."""
    resp = non_admin_client.get(
        f'/dashboards/user/disk-scans/user/{_RES}/{endpoint}'
    )
    assert resp.status_code == 200


def test_user_directories_no_identity_empty_state(app, auth_client, monkeypatch):
    """Account with no unix_uid -> info message, and NO scan is run (an absent
    owner filter would otherwise scan the whole resource)."""
    from types import SimpleNamespace
    from webapp.disk_scans import service, routes
    _enable_fs_scans(app, monkeypatch)
    calls = {'n': 0}

    def fake(*a, **k):
        calls['n'] += 1
        return []
    monkeypatch.setattr(service, 'scan_directories', fake)
    # _user_ctx reads the owner via routes' module-level current_user.
    monkeypatch.setattr(routes, 'current_user', SimpleNamespace(unix_uid=None))

    resp = auth_client.get(f'/dashboards/user/disk-scans/user/{_RES}/directories')
    assert resp.status_code == 200
    assert calls['n'] == 0                                   # never scanned
    assert 'No filesystem identity' in resp.get_data(as_text=True)


def test_no_user_entities_route(app):
    """User mode hides the User/group counts tab — there is no entities_user
    endpoint — but the other three fragments + the explorer page do exist."""
    from flask import url_for
    from werkzeug.routing import BuildError
    with app.test_request_context():
        with pytest.raises(BuildError):
            url_for('disk_scans.entities_user_fragment', resource=_RES)
        for ep in ('directories_user_fragment', 'access_history_user_fragment',
                   'file_sizes_user_fragment', 'directories_user_page'):
            url_for(f'disk_scans.{ep}', resource=_RES)   # no raise


def test_user_explore_hides_owner_picker(auth_client, session):
    """The explorer 'full view' omits the owner picker in user mode (a user must
    not be able to re-filter to another owner)."""
    _benkirk_uid(session)
    resp = auth_client.get(f'/dashboards/user/disk-scans/user/{_RES}/explore')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My Data' in body
    assert 'Search users…' not in body          # owner fk-picker omitted


def test_my_data_page_shown(auth_client, monkeypatch):
    """benkirk has a unix_uid -> My Data tab link + one subtab per warmed
    resource on the /user/data page."""
    monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                        lambda app=None: ['Campaign_Store'])
    # Tab strip on any user-dashboard page links to the My Data page
    accounts = auth_client.get('/user/accounts')
    assert accounts.status_code == 200
    assert 'href="/user/data"' in accounts.get_data(as_text=True)
    # The page itself renders the per-resource subtab strip
    resp = auth_client.get('/user/data')
    assert resp.status_code == 200
    assert 'id="my-data-subtab-1"' in resp.get_data(as_text=True)


def test_my_data_page_hidden_when_no_resources(auth_client, monkeypatch):
    """Plugin off / no warmed resource -> no My Data tab link even with a
    unix_uid, and the page itself 404s."""
    monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                        lambda app=None: [])
    resp = auth_client.get('/user/accounts')
    assert resp.status_code == 200
    assert 'href="/user/data"' not in resp.get_data(as_text=True)
    assert auth_client.get('/user/data').status_code == 404


def test_single_owner_band_drills_straight_to_directories(app):
    """One owner -> no per-user table; the band row drills to their directories."""
    body = _render_distribution_partial(app, {7: {'data': 100, 'files': 10}})
    assert 'owner_uid=7' in body                       # directory drill present
    assert 'Top users by' not in body                  # per-user table skipped
    assert 'Show directories in this band' in body     # band row title
    assert '>Owners<' not in body                      # uniform-1 column folded


def test_multi_owner_band_keeps_per_user_table(app):
    """Two+ owners -> the per-user aggregation table is retained."""
    body = _render_distribution_partial(
        app, {7: {'data': 60, 'files': 6}, 8: {'data': 40, 'files': 4}})
    assert 'Top users by' in body                       # per-user table kept
    assert 'Show top users in this bucket' in body
    assert 'owner_uid=7' in body and 'owner_uid=8' in body   # each user drills
    assert '>Owners<' in body                            # column shown (≥2 owners)


def test_single_owner_band_without_window_keeps_table(app):
    """The shortcut only applies when there's a drill window. A lone owner in a
    window-less band keeps the per-user table (listed, but not drillable) —
    behavior unchanged from before the shortcut."""
    from flask import render_template
    hist = {
        'bucket_labels': ['unknown'],
        'buckets': {'unknown': {'data': 5, 'files': 1, 'owners': {7: {'data': 5, 'files': 1}}}},
        'total_data': 5, 'total_files': 1, 'username_map': {7: 'benkirk'},
    }
    with app.test_request_context():
        body = render_template(
            'dashboards/user/partials/disk_scans_distribution.html',
            hist=hist, chart_svg='<svg/>', enabled=True, error=None,
            resource_name=_RES, scope='', fileset=None, target_id='t',
            bucket_header='Last accessed', metric='data', metric_toggle=False,
            log_toggle=False, log_on=False, fragment_url='/frag',
            dir_fragment_url='/dirs')
    assert 'benkirk' in body                            # user still listed
    assert 'Top users by' in body                       # per-user table kept
    assert 'owner_uid=7' not in body                    # but no directory drill


def test_age_bands_render_with_the_ladder_as_a_data_block(
        auth_client, active_project, _anchored_scan):
    """The ladder travels as JSON so the browser only indexes it — no date
    arithmetic, and therefore no timezone reasoning, in JavaScript."""
    body = _explore(auth_client, active_project)
    assert 'ladder-range-bands' in body
    assert '&lt; 1 Month' in body or '< 1 Month' in body
    # Both thumbs present, and each announces a band name rather than an index.
    # Scoped by the control's own id namespace: the panel carries a second
    # ladder (average file size), so a page-wide count would pass on the
    # wrong control's markup.
    import re
    assert len(re.findall(r'id="[^"]*-age-lo"', body)) == 1
    assert len(re.findall(r'id="[^"]*-age-hi"', body)) == 1
    assert 'aria-valuetext=' in body


def test_the_two_date_inputs_remain_the_only_named_fields(
        auth_client, active_project, _anchored_scan):
    """Everything else in the control is unnamed UI that writes into these two.
    Two same-named controls would make form.elements[name] a RadioNodeList and
    silently break assignment — the trap window_pills.html documents."""
    body = _explore(auth_client, active_project)
    assert body.count('name="accessed_after"') == 1
    assert body.count('name="accessed_before"') == 1
    # The range inputs carry no name, so they submit nothing.
    assert 'type="range"' in body
    assert 'name="age' not in body


def test_typing_an_exact_bound_marks_the_control_custom(
        auth_client, active_project, _anchored_scan):
    """Every named field carries the hook, on both ladders.

    Without it the readout keeps naming the last span while the fields hold
    something else, so the control misdescribes the filter it is about to
    submit. `span=None` is exactly what the server renders for a hand-typed
    range; this is the client-side half of the same state.
    """
    import re
    body = _explore(auth_client, active_project)
    named = re.findall(r'<input[^>]*data-ladder-field[^>]*>', body)
    assert named, 'no named ladder fields on the page'
    assert all('data-action-change="ladder-range-typed"' in f for f in named)


def test_a_filter_on_band_edges_marks_that_span(
        auth_client, active_project, _anchored_scan):
    """Dates that ARE band edges put the thumbs on those bands: '< 1 Month' is
    ages [0, 30) from the 2026-06-01 scan."""
    body = _explore(auth_client, active_project,
                    '&accessed_after=2026-05-02&accessed_before=2026-06-01')
    assert 'value="0"' in body
    assert 'Custom range' not in body


def test_a_hand_typed_range_renders_the_custom_state(
        auth_client, active_project, _anchored_scan):
    """Dates that describe no whole band must not snap the handles somewhere
    the filter isn't."""
    body = _explore(auth_client, active_project,
                    '&accessed_after=2026-03-17&accessed_before=2026-04-02')
    assert 'Custom range' in body
    # The typed values still round-trip into the exact-date inputs.
    assert 'value="2026-03-17"' in body
    assert 'value="2026-04-02"' in body


def test_mobile_gets_selects_instead_of_thumbs(
        auth_client, active_project, _anchored_scan):
    """Two thumbs a few pixels apart is the worst case at 390px, so the layout
    axis picks the presentation server-side rather than rendering both."""
    body = _explore(auth_client, active_project, '&layout=mobile')
    assert 'type="range"' not in body
    # The two <select>s stand in for the two thumbs, under the same ids.
    # Matched on `id=` rather than the bare suffix: on this layout each also
    # has a <label for>, so a substring count would see two of everything.
    import re
    assert len(re.findall(r'id="[^"]*-age-lo"', body)) == 1
    assert len(re.findall(r'id="[^"]*-age-hi"', body)) == 1
    assert body.count('name="accessed_before"') == 1


def test_no_scan_date_falls_back_to_the_bare_date_pair(
        auth_client, active_project, monkeypatch):
    """No anchor means no bands can be computed. The panel must still filter —
    the same degrade-not-500 contract every fs-scans surface has."""
    from webapp.disk_scans import service
    monkeypatch.setattr(service, 'scan_reference_date', lambda scope: None)
    body = _explore(auth_client, active_project)
    # Only the AGE ladder degrades. The size ladder is anchor-free, so it is
    # still there — asserting on the age control's own ids rather than on the
    # shared class is what keeps this test about the thing it names.
    import re
    assert not re.search(r'id="[^"]*-age-lo"', body)
    assert 'Accessed after' in body and 'Accessed before' in body
    assert body.count('name="accessed_before"') == 1


def test_from_and_to_label_the_opposite_ends_from_the_slider(
        auth_client, active_project, _anchored_scan):
    """The exact-date pair reads in the OPPOSITE direction to the slider above
    it: the slider's axis is age (youngest left), these are calendar dates
    (earliest left). So `From` binds the OLDER bound — `accessed_after` — even
    though it holds the smaller date. Pinning it because the two controls look
    like they disagree without the labels, and a future 'tidy-up' that swaps
    them would be silently wrong.
    """
    import re
    body = _explore(auth_client, active_project)

    def field_for(label_text):
        m = re.search(r'<label[^>]*for="([^"]+)"[^>]*>\s*' + label_text, body)
        assert m, f'no <label> for {label_text}'
        m2 = re.search(r'<input[^>]*id="' + re.escape(m.group(1)) + r'"[^>]*>', body)
        assert m2, f'no input carrying id {m.group(1)}'
        return m2.group(0)

    assert 'name="accessed_after"' in field_for('From')
    assert 'name="accessed_before"' in field_for('To')


def test_exact_inputs_hide_behind_the_axis_ends(
        auth_client, active_project, _anchored_scan):
    """The escape hatch costs no vertical space until asked for. Both axis
    end-labels are buttons onto the same panel, which starts collapsed while a
    whole span is in force."""
    import re
    body = _explore(auth_client, active_project)
    panel = re.search(
        r'<div class="ladder-range-exact([^"]*)"\s+id="([^"]*-age-exact)"', body)
    assert panel, 'no exact panel for the age control'
    assert 'd-none' in panel.group(1)
    ends = [e for e in re.findall(r'<button[^>]*class="ladder-range-end"[^>]*>', body)
            if f'data-target="#{panel.group(2)}"' in e]
    assert len(ends) == 2, 'each control gets exactly two ends, both its own'
    for end in ends:
        assert 'aria-expanded="false"' in end


def test_each_axis_end_focuses_its_own_bound(
        auth_client, active_project, _anchored_scan):
    """The reveal is direction-aware, and on an age ladder that is CROSSED: the
    left (newest) end of the axis opens onto `accessed_before`, which is the
    box on the RIGHT. Resolving by declared thumb rather than by position is
    the whole reason `thumb` is a per-field value — a tidy-up that paired them
    left-to-left would be silently wrong in exactly the way the From/To labels
    above already guard against."""
    import re
    body = _explore(auth_client, active_project)
    ends = re.findall(r'<button[^>]*class="ladder-range-end"[^>]*>', body)
    focus = [re.search(r'data-focus="#([^"]+)"', e).group(1) for e in ends]

    def name_of(element_id):
        m = re.search(r'<input[^>]*id="' + re.escape(element_id) + r'"[^>]*>', body)
        assert m, f'no input carrying id {element_id}'
        return re.search(r'name="([^"]+)"', m.group(0)).group(1)

    # Left end == newest == the NEWER bound; right end == oldest == the older.
    assert name_of(focus[0]) == 'accessed_before'
    assert name_of(focus[1]) == 'accessed_after'


def test_a_custom_range_renders_the_exact_inputs_open(
        auth_client, active_project, _anchored_scan):
    """A hand-typed range came from that panel, so collapsing it on reload
    would hide the only control that explains the filter in force."""
    import re
    body = _explore(auth_client, active_project,
                    '&accessed_after=2026-03-17&accessed_before=2026-04-02')
    panel = re.search(r'<div class="ladder-range-exact([^"]*)"\s+id="', body)
    assert panel and 'd-none' not in panel.group(1)
    assert body.count('aria-expanded="true"') >= 2


def test_mobile_keeps_the_exact_inputs_visible(
        auth_client, active_project, _anchored_scan):
    """The mobile presentation is two selects with no axis end-labels, so
    there is nothing to hang the reveal on. Hiding the panel there would leave
    it unreachable rather than merely tucked away."""
    import re
    body = _explore(auth_client, active_project, '&layout=mobile')
    assert 'ladder-range-end' not in body
    panel = re.search(r'<div class="ladder-range-exact([^"]*)"\s+id="', body)
    assert panel and 'd-none' not in panel.group(1)


def test_average_file_size_gets_its_own_ladder(auth_client, active_project):
    """`min_avg_size`/`max_avg_size` have always reached the service, but until
    now the only way to set them was clicking a file-size histogram bar — so a
    viewer who arrived that way could not adjust or clear the filter. The panel
    now carries the same ladder the chart plots."""
    import re
    body = _explore(auth_client, active_project)
    assert len(re.findall(r'id="[^"]*-size-lo"', body)) == 1
    assert len(re.findall(r'id="[^"]*-size-hi"', body)) == 1
    assert body.count('name="min_avg_size"') == 1
    assert body.count('name="max_avg_size"') == 1


def test_the_size_ladder_is_the_charts_own_vocabulary(auth_client, active_project):
    """Not a second vocabulary: the band edges the control offers are exactly
    the plugin's SIZE_BUCKETS, which is what the file-size histogram bins on and
    what its band clicks drill with. A slider position and the equivalent bar
    click therefore select the same directories."""
    import json
    import re
    from fs_scans.core.models import SIZE_BUCKETS
    body = _explore(auth_client, active_project)
    blocks = re.findall(
        r'<script type="application/json" class="ladder-range-bands">(.*?)</script>',
        body, re.S)
    payloads = [json.loads(b) for b in blocks]
    size = next(p for p in payloads if 'min_avg_size' in p[0])
    assert [(r['label'], r['min_avg_size'], r['max_avg_size']) for r in size] == \
        [tuple(b) for b in SIZE_BUCKETS]


def test_the_size_ladders_floor_is_a_real_zero(auth_client, active_project):
    """The bottom band's lower edge is 0, and it has to survive as 0 into the
    JSON the browser indexes. A falsy check anywhere on this path would submit
    "no lower bound" instead — see the `|| ''` note in actions.js."""
    import json
    import re
    body = _explore(auth_client, active_project)
    blocks = re.findall(
        r'<script type="application/json" class="ladder-range-bands">(.*?)</script>',
        body, re.S)
    size = next(p for p in (json.loads(b) for b in blocks) if 'min_avg_size' in p[0])
    assert size[0]['min_avg_size'] == 0
    assert size[-1]['max_avg_size'] is None       # open-ended top band


def test_a_size_band_drill_puts_the_slider_on_that_band(auth_client, active_project):
    """The round-trip that proves the two entry points share one vocabulary:
    arrive with the bounds a histogram band click produces, and the control
    comes back showing that span rather than its custom state."""
    body = _explore(auth_client, active_project,
                    '&min_avg_size=1048576&max_avg_size=10485760')
    assert 'value="1048576"' in body
    assert body.count('Custom range') == 0 or 'Avg file size' in body
