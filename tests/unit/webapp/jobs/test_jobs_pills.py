from __future__ import annotations

import pytest

from _jobs_helpers import (
    _card_url,
    _days_ago,
    _install_mock_plugin,
    _make_row,
    _rel,
)


@pytest.mark.parametrize('raw,expected', [
    ('30', 30), ('365', 365),
    ('7', None),        # not an offered window
    ('abc', None), ('', None), ('90.0', None), ('-90', None),
])
def test_parse_days_accepts_only_offered_windows(app, raw, expected):
    """A stale localStorage value must degrade to the default, never 400."""
    from webapp.jobs import routes

    with app.test_request_context(f'/?days={raw}'):
        assert routes._parse_days() == expected


def test_days_outranks_the_window_baked_into_the_url(app):
    """The pill wins over ?start=/?end= — the client can only append days."""
    from webapp.jobs import routes

    with app.test_request_context('/?start=2020-01-01&end=2020-06-01&days=60'):
        filters = routes._parse_job_filters()

    assert filters['start'] == _days_ago(60)
    assert filters['end'] is None


def test_start_and_end_survive_when_no_days_given(app):
    from datetime import date
    from webapp.jobs import routes

    with app.test_request_context('/?start=2020-01-01&end=2020-06-01'):
        filters = routes._parse_job_filters()

    assert filters['start'] == date(2020, 1, 1)
    assert filters['end'] == date(2020, 6, 1)


def test_roundtrip_params_normalize_days_to_a_plain_start(app):
    """`days` is confined to the fragment boundary: panels round-trip start."""
    from webapp.jobs import routes

    with app.test_request_context('/?days=365&end=2020-06-01'):
        params = routes._roundtrip_params('derecho', 'tgt')

    assert params['start'] == _days_ago(365).isoformat()
    assert 'end' not in params
    assert 'days' not in params


def test_panel_filters_default_window_follows_days(app, monkeypatch):
    """The explorer honors the pill the card handed over in its link."""
    _install_mock_plugin(app, monkeypatch)
    from webapp.jobs import routes

    with app.test_request_context('/?days=30'):
        panel = routes._panel_filters('derecho')
    assert panel['start'] == _days_ago(30).isoformat()

    with app.test_request_context('/'):
        panel = routes._panel_filters('derecho')
    assert panel['start'] == _days_ago(
        routes.service.DEFAULT_JOBS_WINDOW_DAYS).isoformat()


def test_panel_route_applies_days_over_baked_start(
    app, auth_client, active_project, monkeypatch,
):
    """End to end: a panel fetch carrying both uses the injected window."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&start=2020-01-01&days=365'
    )
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['start'] == _days_ago(365)


def test_card_fragment_bakes_the_window_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """The shell is how the six panels learn a new window."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(_card_url(active_project.projcode, days=365))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    import re
    start = _days_ago(365).isoformat()
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('', '/wait-times', '/job-sizes', '/durations'):
        path = f'/dashboards/user/jobs/{active_project.projcode}{suffix}?'
        matches = [u for u in panel_urls if u.startswith(path)]
        assert matches, suffix
        # Param order is url_for's business; what matters is that every
        # panel carries the machine and the pill's window.
        assert all('machine=derecho' in u and f'start={start}' in u
                   for u in matches), suffix
    # A pill is a lookback from today, so the page's own end date is gone.
    assert 'end=' not in body


def test_card_fragment_carries_scope_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """A re-rooted subtree has to narrow the aggregations, not just the link.

    The panels resolve ``?scope=`` through ``_tree_projcodes`` ->
    ``_scope_project``; until it rode ``panel_params`` only the page-level
    explore link carried it, so a scoped card would have widened its pies
    back to the whole tree.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=90, scope='CHILD0001')
    ).get_data(as_text=True)

    import re
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('', '/by-user', '/wait-times', '/job-sizes', '/durations'):
        path = f'/dashboards/user/jobs/{active_project.projcode}{suffix}?'
        matches = [u for u in panel_urls if u.startswith(path)]
        assert matches, suffix
        assert all('scope=CHILD0001' in u for u in matches), suffix


def test_card_fragment_marks_the_requested_pill_active(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=30)).get_data(as_text=True)

    # The selected pill is the one carrying `active` — every pill keeps the
    # same btn-outline-secondary base class (the theme inverts .btn-group so
    # `active` paints white). Attribute order isn't guaranteed, so match the
    # class list and the data attribute in either order.
    import re
    assert re.search(r'btn-outline-secondary active[^>]*data-days-value="30"', body) or \
        re.search(r'data-days-value="30"[^>]*btn-outline-secondary active', body)
    assert 'data-days-value="365"' in body      # the other pills still render
    # ...and it's the only active one — this fragment renders a single card,
    # so a second `active` would mean two windows look selected at once.
    assert len(re.findall(r'btn-outline-secondary active', body)) == 1


def test_card_fragment_unknown_days_falls_back_to_the_default(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=7)).get_data(as_text=True)

    from webapp.jobs.service import DEFAULT_JOBS_WINDOW_DAYS
    assert f'start={_days_ago(DEFAULT_JOBS_WINDOW_DAYS).isoformat()}' in body


@pytest.mark.parametrize('bad', ['jobs hist', 'a"b', '<script>', 'x' * 65])
def test_card_fragment_400_on_unsafe_element_ids(
    app, auth_client, active_project, monkeypatch, bad,
):
    """cid/tablist_id land in element ids and hx-target selectors."""
    _install_mock_plugin(app, monkeypatch)
    assert auth_client.get(
        _card_url(active_project.projcode, cid=bad)).status_code == 400
    assert auth_client.get(
        _card_url(active_project.projcode, tablist_id=bad)).status_code == 400


def test_card_fragment_404_on_unknown_projcode(auth_client):
    assert auth_client.get(_card_url('NOPE9999')).status_code == 404


def test_card_fragment_400_on_invalid_machine(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card?machine=gust')
    assert resp.status_code == 400


def test_card_fragment_persist_markers_are_opt_in(
    app, auth_client, active_project, monkeypatch,
):
    """No persist id (resource details) -> the window can't outlive the visit."""
    _install_mock_plugin(app, monkeypatch)

    plain = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)
    assert 'data-jobs-days-card' not in plain
    assert 'data-chart-persist-id' not in plain
    assert 'data-days-value' in plain            # pills still work

    kept = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)
    assert 'data-jobs-days-card' in kept
    assert 'data-chart-persist-id="jobs-days-status"' in kept
    assert 'data-chart-persist-keys="days"' in kept
    assert 'data-jobs-card-url' in kept


def test_card_wrapper_declares_no_inheritable_hx_target(
    app, auth_client, active_project, monkeypatch,
):
    """htmx inherits hx-target/hx-swap.

    A pair on the card wrapper would capture every descendant request that
    doesn't name its own target — a By Project bucket drill swapped the
    whole card away — so the sibling fan-out goes through htmx.ajax().
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)

    wrapper = body[body.index('<div id="jobs-hist-card"'):]
    wrapper = wrapper[:wrapper.index('>') + 1]
    assert 'hx-target' not in wrapper
    assert 'hx-swap' not in wrapper


def test_card_fragment_explore_link_hands_over_the_pill(
    app, auth_client, active_project, monkeypatch,
):
    """The link carries ?days=, never a date the JS would have to re-derive."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=365)).get_data(as_text=True)

    import re
    link = re.search(r'href="([^"]*/explore[^"]*)"', body)
    assert link, 'explorer link missing'
    assert 'days=365' in link.group(1)
    assert 'start=' not in link.group(1)


def test_card_machine_route_403_without_permission(non_admin_client):
    resp = non_admin_client.get('/dashboards/user/jobs/machine/derecho/card')
    assert resp.status_code == 403


def test_card_machine_route_404_unknown_machine(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/dashboards/user/jobs/machine/gust/card')
    assert resp.status_code == 404


def test_card_machine_route_renders_machine_mode_shell(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/card?days=60'
        '&cid=jobs-m1&tablist_id=jobHistCardTabs1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'start={_days_ago(60).isoformat()}' in body
    assert 'By User' in body                    # machine mode keeps the pie
    assert 'id="jobs-m1-card"' in body


def test_card_user_route_renders_without_elevated_permission(
    app, non_admin_client, monkeypatch,
):
    """The My Jobs shell is @login_required only — the username is pinned."""
    _install_mock_plugin(app, monkeypatch)
    resp = non_admin_client.get(
        '/dashboards/user/jobs/user/derecho/card?days=30&cid=my-jobs-m1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'start={_days_ago(30).isoformat()}' in body
    assert '>By User' not in body               # a pie of one, hidden here


def test_card_shell_panels_stay_lazy_after_a_refetch(
    app, auth_client, active_project, monkeypatch,
):
    """Only the visible panel refetches; the rest wait to be shown."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=30)).get_data(as_text=True)

    assert 'hx-trigger="intersect once"' in body
    assert body.count('hx-trigger="shown.bs.tab once"') >= 4


def test_relevance_machine_mode_varies_along_both_axes():
    r = _rel()
    assert r['show_by_user'] and r['show_by_project']
    assert r['owners_toggle'] and r['owners_enabled']
    assert r['default_group_by'] == 'user'


def test_relevance_user_mode_pins_the_user_axis():
    """By User would be a pie of one; the project axis takes the stack."""
    r = _rel(mode='user')
    assert not r['show_by_user']
    assert r['show_by_project']
    assert not r['owners_toggle']
    assert r['default_group_by'] == 'project'
    assert r['owners_enabled']


def test_relevance_user_filter_pins_the_user_axis_in_any_mode():
    r = _rel(mode='machine', user_filter='alice')
    assert not r['show_by_user']
    assert r['default_group_by'] == 'project'


def test_relevance_account_filter_pins_the_project_axis():
    r = _rel(mode='machine', account_filter='SCSG0001')
    assert r['show_by_user']
    assert not r['show_by_project']
    assert not r['owners_toggle']
    assert r['default_group_by'] == 'user'


def test_relevance_single_projcode_tree_pins_the_project_axis():
    assert not _rel(mode='project',
                    account_projcodes=['SCSG0001'])['show_by_project']
    assert _rel(mode='project',
                account_projcodes=['SCSG0001', 'SCSG0002'])['show_by_project']


def test_relevance_drops_owner_grouping_when_both_axes_are_pinned():
    r = _rel(mode='user', account_filter='SCSG0001')
    assert not r['show_by_user'] and not r['show_by_project']
    assert not r['owners_enabled']


def test_relevance_needs_no_request_context():
    """Purity is the point: relevance follows what reaches the PANELS, not
    request.args. A ?user= on a host *page* URL must not hide a tab whose
    panels were never filtered by it — so the rule can't read the request.
    Touching ``request`` here would raise outside a request context."""
    from flask import has_request_context
    assert not has_request_context()
    assert _rel(mode='machine')['show_by_user'] is True
