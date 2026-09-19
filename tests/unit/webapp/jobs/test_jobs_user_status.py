from __future__ import annotations

import pytest

from webapp.jobs.scope import UserJobScope
from _jobs_helpers import (
    _PROJECT_USAGE,
    _USER_FRAGMENTS,
    _hx_trigger_after,
    _install_mock_plugin,
    _make_row,
    _sample_hist,
    _sample_usage,
)


def test_status_job_history_403_without_permission(non_admin_client):
    resp = non_admin_client.get('/status/job-history')
    assert resp.status_code == 403


def test_status_job_history_empty_state_when_disabled(auth_client):
    """Plugin off -> no machines -> the info alert (never a broken card)."""
    resp = auth_client.get('/status/job-history')
    assert resp.status_code == 200
    assert 'No job-history data is currently available' in resp.get_data(as_text=True)


def test_timeline_is_open_by_default_on_the_cards(
    app, auth_client, monkeypatch,
):
    """The cards used to collapse the timeline because it cost a `jobs`
    scan. It now serves a card's scope off the plugin's daily_summary
    rollup (~65 ms for 180 bands), so it is open everywhere."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    body = auth_client.get('/status/job-history').get_data(as_text=True)
    assert 'id="jobs-m1-timeline-wrap"' in body
    # `show` is what Bootstrap reads; aria-expanded is what a screen reader
    # reads. They must agree or the panel lies to one of them.
    assert 'class="collapse show mt-2"' in body
    assert 'aria-expanded="true"' in body


@pytest.mark.parametrize('machine_slot,should_fire_now', [('m1', True),
                                                          ('m2', False)])
def test_open_timeline_shares_the_jobs_pane_trigger(
    app, auth_client, monkeypatch, machine_slot, should_fire_now,
):
    """Open must not mean eager.

    The timeline is in the Jobs pane, so it takes that pane's trigger —
    whatever the host chose. Hardcoding `load` would fetch it inside a
    hidden pane (a second machine card, or a restored non-Jobs tab), paying
    for a chart nobody asked for: exactly the cost the collapse used to
    avoid, and the reason opening it by default is safe at all.

    Pinned per machine slot because the two differ: the first card's pane is
    visible at render, the second sits behind the machine tab and must wait.
    """
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    body = auth_client.get('/status/job-history').get_data(as_text=True)

    # The table's fetch is wired on its TAB BUTTON, the timeline's on the
    # chart div; both resolve through the same `_trig('jobs')`.
    timeline = _hx_trigger_after(body, f'jobs-{machine_slot}-timeline')
    table = _hx_trigger_after(body, f'jobs-{machine_slot}-jobs-tab')
    assert timeline == table, (
        f'{machine_slot}: timeline {timeline!r} != table {table!r}')
    assert ('load' in timeline) is should_fire_now, timeline


def test_status_job_history_machine_pills_when_enabled(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    resp = auth_client.get('/status/job-history')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'job-hist-subtab-1' in body
    assert 'Derecho' in body
    assert 'Casper' in body
    # Machine-mode card fragments wired per pill.
    assert '/dashboards/user/jobs/machine/casper' in body


def test_status_tab_hidden_without_machines(auth_client):
    """Plugin off -> the Job History tab is absent from the status pages."""
    resp = auth_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' not in resp.get_data(as_text=True)


def test_status_tab_visible_for_operator_with_machines(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' in resp.get_data(as_text=True)


def test_status_tab_hidden_for_plain_user_with_machines(
    app, non_admin_client, monkeypatch,
):
    """Even with machines up, no VIEW_ALL_JOB_DATA -> no tab, anywhere."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = non_admin_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' not in resp.get_data(as_text=True)


@pytest.mark.parametrize('suffix', _USER_FRAGMENTS + ['/explore'])
def test_user_routes_disabled_banner(auth_client, suffix):
    """Plugin off -> 200 with the unavailable banner (login only, no perm)."""
    resp = auth_client.get(f'/dashboards/user/jobs/user/derecho{suffix}')
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


@pytest.mark.parametrize('suffix', _USER_FRAGMENTS + ['/explore'])
def test_user_routes_404_unknown_machine(app, auth_client, monkeypatch, suffix):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get(f'/dashboards/user/jobs/user/gust{suffix}')
    assert resp.status_code == 404


def test_user_jobs_fragment_pins_session_user(app, auth_client, monkeypatch):
    """The table is pinned to the logged-in user (benkirk for auth_client)."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho')
    assert resp.status_code == 200
    kw = captured['last_jobs_search_kwargs']
    assert kw['user'] == 'benkirk'
    ckw = captured['last_jobs_count_kwargs']
    assert ckw['user'] == 'benkirk'


def test_user_jobs_fragment_ignores_client_user_param(
    app, auth_client, monkeypatch,
):
    """?user=<other> must change nothing — the pin always wins."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho?user=mallory')
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['user'] == 'benkirk'
    assert captured['last_jobs_count_kwargs']['user'] == 'benkirk'


@pytest.mark.parametrize('suffix', ['/wait-times', '/job-sizes', '/durations'])
def test_user_histogram_fragments_ignore_client_user_param(
    app, auth_client, monkeypatch, suffix,
):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())
    resp = auth_client.get(
        f'/dashboards/user/jobs/user/derecho{suffix}?user=mallory'
    )
    assert resp.status_code == 200
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['user'] == 'benkirk'


def test_by_project_fragment_renders_rows_and_pinned_pie(
    app, auth_client, monkeypatch,
):
    """The My Jobs By Project tab: plugin grouped by 'account' with the
    session user pinned (client ?user= ignored); rows carry
    data-job-project and the pie #job-proj sentinels."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE,
    )
    resp = auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project?user=mallory'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs['user'] == 'benkirk'
    assert kwargs['limit'] == 25
    assert 'data-job-project="SCSG0001"' in body
    assert '#sam/row/data-job-project/SCSG0001' in body
    # Row drill narrows the user-mode jobs fragment by account.
    assert '/dashboards/user/jobs/user/derecho?machine=derecho&account=SCSG0001' in body


def test_by_project_fragment_404_unknown_machine(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/user/fugaku/by-project')
    assert resp.status_code == 404


def test_by_project_fragment_disabled_banner(auth_client):
    resp = auth_client.get('/dashboards/user/jobs/user/derecho/by-project')
    assert resp.status_code == 200
    assert 'plugin is not loaded' in resp.get_data(as_text=True)


def test_user_fragment_account_narrows_own_jobs(app, auth_client, monkeypatch):
    """?account=<projcode> narrows the pinned user's OWN jobs — both the
    rows and the count see it, and the user pin survives."""
    captured = _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        '/dashboards/user/jobs/user/derecho?machine=derecho&account=SCSG0001'
    )
    assert resp.status_code == 200
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == 'SCSG0001'
    assert skw['user'] == 'benkirk'
    ckw = captured['last_jobs_count_kwargs']
    assert ckw['account'] == 'SCSG0001'
    assert ckw['user'] == 'benkirk'
    # The narrowing surfaces as a header badge.
    assert 'project: SCSG0001' in resp.get_data(as_text=True)


def test_project_fragment_ignores_client_account_param(
    app, auth_client, active_project, monkeypatch,
):
    """Project mode keeps its account list server-derived — a client
    ?account= must not replace the tree pin."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&account=EVIL0001'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == [active_project.projcode] or \
        active_project.projcode in skw['account']
    assert 'EVIL0001' not in skw['account']


def test_project_by_project_fragment_scoped_to_tree(
    app, auth_client, active_project, monkeypatch,
):
    """Project-mode By Project: dimension 'account' scoped by the
    server-derived tree list, no user pin; rows drill into the
    project-mode jobs fragment narrowed by account=."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-project'
        '?machine=derecho'
    )
    assert resp.status_code == 200
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs.get('user') is None      # tree scoping, no user pin
    assert active_project.projcode in kwargs['account']
    assert (f'/dashboards/user/jobs/{active_project.projcode}'
            '?machine=derecho&account=SCSG0001') in resp.get_data(as_text=True)


def test_project_fragment_intree_account_narrows(
    app, auth_client, active_project, monkeypatch,
):
    """An in-tree ?account= narrows the project table to that projcode
    (the parent-project By Project drill) and surfaces as the badge —
    the complement of test_project_fragment_ignores_client_account_param,
    which pins that out-of-tree values stay ignored."""
    captured = _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&account={active_project.projcode}'
    )
    assert resp.status_code == 200
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == [active_project.projcode]
    assert f'project: {active_project.projcode}' in resp.get_data(as_text=True)


def test_status_job_history_card_offers_both_tabs(app, auth_client, monkeypatch):
    """Machine mode renders BOTH By User and By Project tabs."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    body = auth_client.get('/status/job-history').get_data(as_text=True)
    assert 'By User' in body
    assert 'By Project' in body
    assert '/dashboards/user/jobs/machine/derecho/by-project' in body


def test_resource_details_modal_shells_and_by_project_gate(
    app, auth_client, active_project, monkeypatch,
):
    """The project-mode host page carries both entity-modal shells (it
    extends dashboards/base.html, which includes neither), and the By
    Project tab renders only when the account tree spans >1 projcode."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/user/resource-details/{active_project.projcode}?resource=Derecho')
    if resp.status_code != 200:
        return  # snapshot doesn't have this resource — nothing to check
    body = resp.get_data(as_text=True)
    assert 'id="userDetailsModal"' in body
    assert 'id="projectDetailsModal"' in body
    multi = len(active_project.get_descendants(include_self=True)) > 1
    assert (f'/dashboards/user/jobs/{active_project.projcode}/by-project'
            in body) == multi


def test_by_user_username_links_open_user_modal(app, auth_client, monkeypatch):
    """With VIEW_USERS, usernames become quick-view modal triggers."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/by-user').get_data(as_text=True)
    assert 'data-bs-target="#userDetailsModal"' in body
    assert '/admin/user/alice' in body


def test_by_user_no_modal_affordance_without_view_users(
    app, client, session, multi_project_user, monkeypatch,
):
    """A project member without VIEW_USERS gets plain <code> usernames —
    no affordance that would 403 at the user_card route."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    with client.session_transaction() as sess_data:
        sess_data['_user_id'] = str(multi_project_user.user_id)
        sess_data['_fresh'] = True
    resp = None
    for proj in multi_project_user.projects:
        resp = client.get(
            f'/dashboards/user/jobs/{proj.projcode}/by-user?machine=derecho')
        if resp.status_code == 200:
            break
    else:
        pytest.skip('snapshot member user has no accessible project')
    body = resp.get_data(as_text=True)
    assert 'data-job-user="alice"' in body      # rows render fine
    assert 'userDetailsModal' not in body       # affordance suppressed


def test_by_project_projcode_links_open_project_modal(
    app, auth_client, monkeypatch,
):
    """User mode always renders the project quick-view affordance — the
    rows are the pinned user's own projects."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project').get_data(as_text=True)
    assert 'data-bs-target="#projectDetailsModal"' in body
    assert 'project-details-modal/SCSG0001' in body


def test_my_jobs_card_offers_by_project_tab(app, auth_client, monkeypatch):
    """The user-mode card swaps By User for By Project."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'By Project' in body
    assert 'by-project' in body
    assert 'By User' not in body


def test_service_jobs_usage_by_project_rejects_user_filter(app, monkeypatch):
    """The pin owns the user dimension — a client value beside it raises
    rather than being silently overwritten. (The empty-username case is
    rejected at scope construction; see test_user_scope_requires_a_username.)"""
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)
    with app.app_context():
        with pytest.raises(ValueError, match='pin the user server-side'):
            service.jobs_usage_by_project(
                'derecho', UserJobScope('benkirk'), user='mallory',
            )


def test_user_explore_page_omits_user_picker(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho/explore')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My Jobs' in body
    # No user picker in user mode — the pin is not negotiable.
    assert 'name="user_id"' not in body
    # Other filter fields still present.
    assert 'name="queue"' in body


def test_my_jobs_page_404_without_machines(auth_client):
    """Plugin off -> no machines -> the page 404s (tab is hidden too)."""
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 404


def test_my_jobs_page_renders_machine_pills(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'my-jobs-subtab-1' in body
    assert 'Derecho' in body and 'Casper' in body
    # User-mode card fragments wired per pill.
    assert '/dashboards/user/jobs/user/casper' in body
    # By User tab suppressed in user mode.
    assert 'By User' not in body


def test_my_jobs_tab_visibility_follows_machines(app, auth_client, monkeypatch):
    # Hidden when plugin off…
    resp = auth_client.get('/user/accounts')
    assert '/user/jobs' not in resp.get_data(as_text=True)
    # …visible when machines are up.
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/user/accounts')
    assert '/user/jobs' in resp.get_data(as_text=True)
