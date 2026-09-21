from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _jobs_helpers import (
    _EXPLORE_URLS,
    _MACHINE_FRAGMENTS,
    _PROJECT_USAGE,
    _SAMPLE_FACETS,
    _card_url,
    _days_ago,
    _install_mock_plugin,
    _make_row,
    _sample_hist,
    _sample_usage,
)


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_403_without_permission(non_admin_client, suffix):
    resp = non_admin_client.get(f'/dashboards/user/jobs/machine/derecho{suffix}')
    assert resp.status_code == 403


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_disabled_banner_with_permission(auth_client, suffix):
    """benkirk holds VIEW_ALL_JOB_DATA -> 200 (plugin off -> banner)."""
    resp = auth_client.get(f'/dashboards/user/jobs/machine/derecho{suffix}')
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_404_unknown_machine(app, auth_client, monkeypatch, suffix):
    """With the plugin up for derecho only, /machine/gust -> 404."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get(f'/dashboards/user/jobs/machine/gust{suffix}')
    assert resp.status_code == 404


def test_machine_jobs_fragment_unscoped(app, auth_client, monkeypatch):
    """The machine table issues no account filter and renders rows."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '500.desched1' in body
    kw = captured['last_jobs_search_kwargs']
    assert 'account' not in kw
    ckw = captured['last_jobs_count_kwargs']
    assert 'account' not in ckw


def test_machine_by_user_fragment_unscoped(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_sample_usage())
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/by-user')
    assert resp.status_code == 200
    assert 'data-job-user="alice"' in resp.get_data(as_text=True)
    _dim, kwargs = captured['last_jobs_usage_by']
    assert 'account' not in kwargs


def test_machine_by_project_fragment_unscoped(app, auth_client, monkeypatch):
    """Machine-wide By Project: dimension 'account', no user pin, no
    account scoping; rows drill into the machine jobs fragment narrowed
    by account=."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/by-project')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs.get('user') is None      # no pin (filter dict carries None)
    assert 'account' not in kwargs
    assert 'data-job-project="SCSG0001"' in body
    assert ('/dashboards/user/jobs/machine/derecho'
            '?machine=derecho&account=SCSG0001') in body


def test_machine_by_project_threads_metric_sort(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/by-project?metric=gpu_hours')
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'gpu_hours'


def test_machine_jobs_fragment_account_narrows(app, auth_client, monkeypatch):
    """?account= narrows the machine-wide table (the By Project drill) —
    rows and count both see it, and it surfaces as the project: badge."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?account=SCSG0001')
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['account'] == 'SCSG0001'
    assert captured['last_jobs_count_kwargs']['account'] == 'SCSG0001'
    assert 'project: SCSG0001' in resp.get_data(as_text=True)


def test_machine_histogram_fragment_unscoped(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/wait-times')
    assert resp.status_code == 200
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'account' not in kwargs


def test_explore_machine_page_renders_filter_panel(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/explore')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'filter-sidebar' in body
    assert 'Machine-wide (operator view)' in body
    # Panel fields present.
    assert 'name="min_nodes"' in body
    assert 'name="exit_status"' in body


def test_explore_page_project_mode(app, auth_client, active_project, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'filter-sidebar' in body
    assert active_project.projcode in body
    # Machine-wide badge must NOT show in project mode.
    assert 'Machine-wide (operator view)' not in body


def test_explore_page_carries_filters_into_initial_url(
    app, auth_client, active_project, monkeypatch,
):
    """Deep-link with filters -> the lazy-load URL reproduces them."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=main&min_nodes=4&min_wait_hours=1.5'
    )
    body = resp.get_data(as_text=True)
    assert 'queue=main' in body
    assert 'min_nodes=4' in body
    assert 'min_wait_hours=1.5' in body


def test_explore_page_elapsed_reqmem_panel_roundtrip(
    app, auth_client, active_project, monkeypatch,
):
    """The panel renders the elapsed/req-mem inputs, echoes deep-linked
    values back into them, and carries them into the lazy-load URL."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&min_elapsed_hours=2.5&max_reqmem_gb=128'
    )
    body = resp.get_data(as_text=True)
    # Panel inputs exist (all four names) with the deep-linked values.
    for field in ('min_elapsed_hours', 'max_elapsed_hours',
                  'min_reqmem_gb', 'max_reqmem_gb'):
        assert f'name="{field}"' in body
    assert 'value="2.5"' in body
    assert 'value="128' in body
    # …and the initial fragment URL reproduces them.
    assert 'min_elapsed_hours=2.5' in body
    assert 'max_reqmem_gb=128' in body


def test_fragment_converts_elapsed_hours_and_reqmem_gb(
    app, auth_client, active_project, monkeypatch,
):
    """Panel units convert at the route boundary: hours -> seconds for
    elapsed, GB -> bytes (1024³) for requested memory."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&min_elapsed_hours=1.5&max_elapsed_hours=24'
        '&min_reqmem_gb=0.5&max_reqmem_gb=128'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['min_elapsed'] == 5400
    assert skw['max_elapsed'] == 86400
    assert skw['min_reqmem'] == 512 * 1024 ** 2      # 0.5 GB
    assert skw['max_reqmem'] == 128 * 1024 ** 3


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_renders_the_jobs_card(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """The charts live on the full view too — same card, driven by the
    filter panel instead of a baked window."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert 'id="jobs-explore-card"' in body
    assert 'id="jobsExploreTabs"' in body
    for tab in ('Wait Times', 'Job Sizes', 'Durations'):
        assert tab in body, tab
    # By User follows the relevance rule, not the surface.
    assert ('By User' in body) is (mode != 'user')


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_ships_the_entity_modal_shells(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """By User, By Project and the histograms' owner tier all open
    quick-view modals whose shells live on the host page. This one
    extends dashboards/base.html (which includes neither), so without
    the includes every one of those links is a silent no-op — exactly
    once each, since duplicate ids break Bootstrap's lookup.

    Pinned per mode: the suppressed panel still leaves the other panels'
    links, and the histogram owner tier can name either entity.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert body.count('id="userDetailsModal"') == 1
    assert body.count('id="projectDetailsModal"') == 1
    # The project shell pulls this in for the per-allocation pencils
    # inside it — see tests/unit/gates/test_modal_shell_contract.py.
    assert body.count('id="editAllocationModal"') == 1


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_suppresses_pills_and_the_explore_link(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """The panel's date fields own the window here, and this IS the full
    view — a period pill group and an "Open full view" link would both be
    second controls for something already on screen."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert 'aria-label="Time window"' not in body
    assert 'data-jobs-explore-link' not in body


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_filter_form_re_renders_the_card(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """Apply swaps the whole card: that is the only way six panels whose
    URLs are baked at render time pick up a new filter set."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    form = re.search(r'<form id="jobs-filters-panel-jobs-explore-jobs"(.*?)>',
                     body, re.S)
    assert form, 'filter form missing'
    attrs = form.group(1)
    assert 'hx-target="#jobs-explore-card"' in attrs
    assert 'hx-swap="outerHTML"' in attrs
    assert '/card?' in attrs and 'surface=explorer' in attrs.replace('&amp;', '&')


def test_explore_card_opens_the_tab_the_form_reports(
    app, auth_client, active_project, monkeypatch,
):
    """Apply re-renders the whole card, so the server has to be told which
    tab is open. Otherwise it always comes back on Jobs and an Apply from
    a chart fetches that chart AND a per-job table nobody asked for."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&active_tab=sizes'
    ).get_data(as_text=True)

    sizes_btn = re.search(r'<button[^>]*data-jobs-tab="sizes".*?>', body, re.S)
    assert sizes_btn and 'active' in sizes_btn.group(0)
    # …and it is the one that fires on render; the rest wait to be shown.
    assert 'hx-trigger="load once"' in sizes_btn.group(0)
    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' not in jobs_btn.group(0)
    assert 'hx-trigger="shown.bs.tab once"' in jobs_btn.group(0)
    # The pane follows the button.
    assert re.search(r'class="tab-pane fade show active" id="tab-jobs-explore-sizes"',
                     body)


def test_explore_card_rejects_an_unknown_active_tab(
    app, auth_client, active_project, monkeypatch,
):
    """The value picks which panel fires a query, so it is whitelisted."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&active_tab=../evil'
    ).get_data(as_text=True)

    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' in jobs_btn.group(0)


def test_explore_page_round_trips_the_active_tab_through_the_form(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&active_tab=wait'
    ).get_data(as_text=True)

    assert ('<input type="hidden" name="active_tab" value="wait"'
            in body)
    assert 'data-jobs-active-tab-input' in body


def test_cards_still_open_on_jobs_by_default(
    app, auth_client, active_project, monkeypatch,
):
    """Nothing changes for the embedded cards: Jobs is open and owns the
    host's load_trigger, every other tab waits to be shown."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=90)).get_data(as_text=True)

    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' in jobs_btn.group(0)
    assert 'hx-trigger="intersect once"' in jobs_btn.group(0)
    assert body.count('hx-trigger="shown.bs.tab once"') >= 4


def test_explore_page_bakes_filters_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """Every panel — not just the table — answers the current filters."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=main&min_nodes=4&min_wait_hours=1.5'
    ).get_data(as_text=True)

    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('/by-user', '/wait-times', '/job-sizes', '/durations'):
        matches = [u for u in panel_urls
                   if f'/{active_project.projcode}{suffix}?' in u]
        assert matches, suffix
        assert all('queue=main' in u and 'min_nodes=4' in u
                   and 'min_wait_hours=1.5' in u for u in matches), suffix


def test_explore_card_route_rebuilds_from_the_filter_panel(
    app, auth_client, active_project, monkeypatch,
):
    """An Apply lands on the mode's /card route with surface=explorer and
    reproduces the same panel URLs a deep link would."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&queue=main&min_nodes=4'
    ).get_data(as_text=True)

    assert 'id="jobs-explore-card"' in body
    assert 'aria-label="Time window"' not in body      # still no pills
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    waits = [u for u in panel_urls if '/wait-times?' in u]
    assert waits and all('queue=main' in u and 'min_nodes=4' in u
                         for u in waits)


def test_explore_card_route_without_the_surface_flag_is_still_a_pill(
    app, auth_client, active_project, monkeypatch,
):
    """The period pills share these routes; surface= is what tells them
    apart, so a pill click must keep its pills and its lookback."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&days=30'
    ).get_data(as_text=True)

    assert 'aria-label="Time window"' in body
    assert f'start={_days_ago(30).isoformat()}' in body


def test_explore_page_user_mode_still_ignores_a_crafted_user(
    app, auth_client, monkeypatch,
):
    """The username is pinned server-side on every fragment; the panel
    omits the picker so the card can't be re-aimed at someone else."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    body = auth_client.get(
        '/dashboards/user/jobs/user/derecho/explore?user=someone_else'
    ).get_data(as_text=True)
    assert 'name="user_id"' not in body
    # Nor is it baked into the panel URLs, where it would look like a
    # filter that works while the server quietly overrode it.
    assert 'user=someone_else' not in body

    auth_client.get(
        '/dashboards/user/jobs/user/derecho?user=someone_else&machine=derecho')
    assert captured['last_jobs_search_kwargs']['user'] == 'benkirk'


def test_explore_page_renders_facet_chips_with_counts(
    app, auth_client, active_project, monkeypatch,
):
    """The strip rides inside the card: value chips with live counts,
    NULL-FK rows skipped, wired to the filter panel form."""
    import re
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=cpu'
    ).get_data(as_text=True)

    assert 'data-action="set-filter-submit"' in body
    assert 'data-form-id="jobs-filters-panel-jobs-explore-jobs"' in body
    # Active chip (queue=cpu) fills in and clears on click.
    cpu_chip = re.search(
        r'<button[^>]*data-field="queue"[^>]*data-value=""[^>]*>', body)
    assert cpu_chip is not None and 'facet-chip is-active' in cpu_chip.group(0)
    # Inactive chip carries its value.
    assert 'data-value="gpu"' in body
    assert 'data-value="271"' in body
    # NULL-FK queue row renders no chip (nothing to filter by).
    assert 'data-value="None"' not in body
    # One grid row per dimension: every label opens its own line.
    assert body.count('class="facet-grid-label"') == 3
    # Facets saw the same filter set as the panels.
    fkw = captured['last_jobs_facets_kwargs']
    assert fkw['queue'] == 'cpu'
    assert fkw['limit'] == 8


def test_explore_card_route_refreshes_the_chips(
    app, auth_client, active_project, monkeypatch,
):
    """Counts refresh with the panels, not with the table — otherwise a
    viewer who filters while looking at a chart keeps the old counts."""
    _install_mock_plugin(app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&queue=cpu'
    ).get_data(as_text=True)

    assert 'data-action="set-filter-submit"' in body
    assert 'data-value="gpu"' in body


def test_jobs_table_fragment_never_queries_facets(
    app, auth_client, active_project, monkeypatch,
):
    """Sorting or paging cannot change a facet count, so the table no
    longer pays for one — the strip belongs to the shell."""
    captured = _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&chips=1'
    ).get_data(as_text=True)

    assert 'hx-swap-oob' not in body
    assert 'data-action="set-filter-submit"' not in body
    assert captured['last_jobs_facets_kwargs'] is None


def test_explore_chips_degrade_on_facets_error(
    app, auth_client, active_project, monkeypatch,
):
    """A facets failure must not take the page down — the card renders
    normally with no chip strip."""
    _install_mock_plugin(app, monkeypatch, jobs_facets_raises=True)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-action="set-filter-submit"' not in body
    assert 'class="facet-grid"' not in body
    assert 'id="jobs-explore-card"' in body


def test_explore_chips_omit_dimensions_with_nothing_to_offer(
    app, auth_client, active_project, monkeypatch,
):
    """A dimension with no filterable values contributes no grid row,
    and a strip with no rows at all renders no surface — an empty
    bordered band above the tabs would be worse than none."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_facets_return={'queue': [{'value': 'cpu', 'count': 3}],
                            'qos': [],
                            'exit_status': [{'value': None, 'count': 7}]},
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert body.count('class="facet-grid-label"') == 1
    assert 'data-value="cpu"' in body

    _install_mock_plugin(
        app, monkeypatch,
        jobs_facets_return={'queue': [], 'qos': [], 'exit_status': []},
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert 'class="facet-grid"' not in body


def test_explore_chips_project_scope_pins_account(
    app, auth_client, active_project, monkeypatch,
):
    """Facets are scoped exactly like the panels — the project tree's
    projcodes pin the account filter."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    )
    fkw = captured['last_jobs_facets_kwargs']
    assert active_project.projcode in fkw['account']


def test_explore_user_chips_pin_username(app, auth_client, monkeypatch):
    """User-mode chips describe the pinned user's jobs only — the session
    username rides into the facets call, client ?user= notwithstanding."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/explore?user=mallory')
    fkw = captured['last_jobs_facets_kwargs']
    assert fkw['user'] == 'benkirk'


def test_explore_page_scope_rerooting_badge(
    app, auth_client, active_project, monkeypatch,
):
    """A valid child scope shows the scope badge; out-of-tree falls back."""
    import types as _types
    from sam import Project

    child = _types.SimpleNamespace(projcode='CHILD0001')
    real_get = Project.get_by_projcode

    def _fake_get(session, projcode):
        if projcode == 'CHILD0001':
            fake = MagicMock()
            fake.projcode = 'CHILD0001'
            fake.tree_root = active_project.tree_root
            fake.get_descendants = lambda include_self=True: [child]
            return fake
        return real_get(session, projcode)

    monkeypatch.setattr(Project, 'get_by_projcode', staticmethod(_fake_get))
    _install_mock_plugin(app, monkeypatch)

    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&scope=CHILD0001'
    )
    assert resp.status_code == 200
    assert 'scope: CHILD0001' in resp.get_data(as_text=True)


def test_fragment_scope_rerooting_narrows_account(
    app, auth_client, active_project, monkeypatch,
):
    """?scope=<child> narrows the account filter to the child's subtree."""
    import types as _types
    from sam import Project

    child_desc = [_types.SimpleNamespace(projcode='CHILD0001'),
                  _types.SimpleNamespace(projcode='CHILD0001_a')]
    real_get = Project.get_by_projcode

    def _fake_get(session, projcode):
        if projcode == 'CHILD0001':
            fake = MagicMock()
            fake.projcode = 'CHILD0001'
            fake.tree_root = active_project.tree_root
            fake.get_descendants = lambda include_self=True: child_desc
            return fake
        return real_get(session, projcode)

    monkeypatch.setattr(Project, 'get_by_projcode', staticmethod(_fake_get))
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&scope=CHILD0001'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['account'] == ['CHILD0001', 'CHILD0001_a']
