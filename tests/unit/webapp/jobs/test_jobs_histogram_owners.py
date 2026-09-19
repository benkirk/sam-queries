from __future__ import annotations

import pytest

from _jobs_helpers import (
    _get_hist_body,
    _install_mock_plugin,
    _make_row,
    _sample_hist,
    _sample_hist_owners,
    _sample_hist_project_owners,
    _sample_usage,
    _two_user_rows,
)


def test_histogram_bucket_renders_owner_table(
    app, auth_client, active_project, monkeypatch,
):
    """Multi-owner band expands to a per-user tier ranked by the active
    metric (jobs default -> alice first; cpu_hours -> bob first)."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'alice' in body and 'bob' in body
    assert '-b0-u1-row' in body and '-b0-u2-row' in body
    assert body.index('alice') < body.index('bob')

    body_cpu = _get_hist_body(app, auth_client, active_project, monkeypatch,
                              query='&metric=cpu_hours')
    assert body_cpu.index('bob') < body_cpu.index('alice')


def test_histogram_owner_drill_appends_user(
    app, auth_client, active_project, monkeypatch,
):
    """Each owner row lazy-loads the band's jobs fragment with the band
    bounds AND the username appended."""
    import re
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    m = re.search(r'id="[^"]*-b0-u1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'owner drill div missing'
    url = m.group(1).replace('&amp;', '&')
    assert 'min_eligible_secs=0' in url
    assert 'max_eligible_secs=59' in url
    assert 'user=alice' in url
    assert 'machine=derecho' in url
    assert 'target_id=' in url
    # Owner drills bind to the Bootstrap collapse event, never click.
    assert 'shown.bs.collapse' in body


def test_histogram_owner_remainder_row(
    app, auth_client, active_project, monkeypatch,
):
    """Owners summing below the band totals -> a muted, non-drillable
    "Other users" row."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'Other users' in body
    assert 'beyond top 2' in body


def test_histogram_single_owner_shortcut(
    app, auth_client, active_project, monkeypatch,
):
    """A band wholly owned by one user skips the per-user tier and drills
    straight to their jobs."""
    import re
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert '-b1-u1-row' not in body
    m = re.search(r'id="[^"]*-b1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'single-owner drill div missing'
    assert 'user=carol' in m.group(1).replace('&amp;', '&')


def test_histogram_no_details_wrapper(
    app, auth_client, active_project, monkeypatch,
):
    """The bucket table is always visible now — no <details> wrapper."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert '<details' not in body
    assert 'Bucket breakdown' in body


def test_histogram_collapse_rows_no_persist(
    app, auth_client, active_project, monkeypatch,
):
    """Histogram collapse rows opt out of nav-view-persistence — a restored
    auto-expand would re-fire the lazy drill fetches on every reload."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'data-no-persist' in body


def test_histogram_ownerless_fallback_keeps_single_level(
    app, auth_client, active_project, monkeypatch,
):
    """Owner-less envelope (older plugin / cached) renders the original
    band -> jobs drill with no per-user tier."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert re.search(r'id="[^"]*-b0-content"\s+hx-get="', body)
    assert '-b0-u1-row' not in body


def test_histogram_owners_limit_and_sort_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    """owners_limit rides every histogram call, and owners_sort_by follows
    the metric pill — which top-N survives must match what's displayed."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_limit'] == 10
    assert kwargs['owners_sort_by'] == 'job_count'   # _DEFAULT_METRIC_HIST

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_sort_by'] == 'gpu_hours'


def test_histogram_owner_pill_offered_in_machine_mode(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times'
    ).get_data(as_text=True)
    assert 'aria-label="Owner dimension"' in body
    assert 'group_by=project' in body


@pytest.mark.parametrize('crafted', ['', '?group_by=user', '?group_by=project'])
def test_histogram_in_user_mode_stacks_by_project_whatever_the_client_asks(
    app, auth_client, monkeypatch, crafted,
):
    """User mode pins the username, so a per-USER stack is a stack of one.

    The axis that can still vary is the project, so that's what the
    segments and the per-band tier use — server-decided, with the pill
    hidden and any crafted ?group_by ignored in both directions.
    """
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/user/derecho/wait-times{crafted}'
    ).get_data(as_text=True)
    assert 'aria-label="Owner dimension"' not in body
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs.get('owners_by') == 'account'


def test_histogram_drops_owner_grouping_when_both_axes_are_pinned(
    app, auth_client, monkeypatch,
):
    """My own jobs on ONE project: every band has exactly one owner, so
    skip the grouping entirely — flat bars, band drills straight to jobs."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/wait-times?account=SCSG0001'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_limit' not in kwargs


def test_histogram_owner_pill_follows_project_tree_size(
    app, auth_client, active_project, monkeypatch,
):
    """Project mode offers the pill iff the account tree spans >1
    projcode — same gate as the By Project tab."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho'
    ).get_data(as_text=True)
    multi = len(active_project.get_descendants(include_self=True)) > 1
    assert ('aria-label="Owner dimension"' in body) == multi


def test_histogram_owners_by_forwarded_only_when_account(
    app, auth_client, monkeypatch,
):
    """Soft degradation contract: the default never sends owners_by (an
    older plugin keeps working); the Project pill sends the plugin's
    'account' for the URL's canonical group_by=project."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    auth_client.get('/dashboards/user/jobs/machine/derecho/wait-times')
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_by' not in kwargs

    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?group_by=project')
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_by'] == 'account'

    # The plugin's own spelling still works — bookmarks and any URL built
    # before the rename keep resolving to the same view.
    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?owners_by=account')
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_by'] == 'account'


def test_histogram_account_owner_tier_and_drill(
    app, auth_client, monkeypatch,
):
    """The Project pill drives the whole drill: Project tier header,
    project-modal triggers on owner cells, account= (not user=) on the
    per-owner jobs drill, 'Other projects' remainder, and the group_by
    round-trip hidden input for the metric pills."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?group_by=project'
    ).get_data(as_text=True)
    assert '<th>Project</th>' in body
    assert 'Other projects' in body
    assert 'project-details-modal/SCSG0001' in body
    assert 'name="group_by" value="project"' in body
    m = re.search(r'id="[^"]*-b0-u1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'owner drill div missing'
    url = m.group(1).replace('&amp;', '&')
    assert 'account=SCSG0001' in url
    assert 'user=' not in url


def test_histogram_user_owner_cells_offer_user_modal(
    app, auth_client, monkeypatch,
):
    """Under the default User pill the owner tier's usernames carry the
    same quick-view modal affordance as the By User table (VIEW_USERS
    gate)."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times'
    ).get_data(as_text=True)
    assert 'data-bs-target="#userDetailsModal"' in body
    assert '/admin/user/alice' in body


def test_user_column_structure():
    """`user` is a default (sortable) column and no longer a drawer extra."""
    from webapp.jobs.routes import _DEFAULT_COLS, _VERBOSE_EXTRAS, \
        _SORT_WHITELIST
    assert 'user' in _DEFAULT_COLS
    assert 'user' in _SORT_WHITELIST
    assert 'user' not in _VERBOSE_EXTRAS


def test_user_column_visible_with_multiple_users(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_search_return=_two_user_rows(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' in body           # sortable header rendered
    assert 'alice' in body and 'zed' in body


def test_user_column_hidden_with_user_filter(
    app, auth_client, active_project, monkeypatch,
):
    rows = [_make_row(job_id='600.desched1', user='alice')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&user=alice'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body
    # The pin is already surfaced by the user: filter badge — no user_badge.
    assert 'user: alice' in body


def test_user_column_hidden_in_user_mode(app, auth_client, monkeypatch):
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[_make_row(job_id='600.desched1', user='benkirk')],
    )
    resp = auth_client.get('/dashboards/user/jobs/user/derecho?machine=derecho')
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body


def test_user_column_hidden_uniform_single_page_shows_badge(
    app, auth_client, monkeypatch,
):
    """No pin, no filter, but the whole (single-page) result is one user ->
    column suppressed and the username surfaces as a header badge.

    Machine mode: its count path always uses the mocked plugin
    jobs_count. Project mode's unfiltered count takes the SAM-summary
    fast path (a real comp_charge_summary query), so the single-page
    premise couldn't be pinned there.
    """
    rows = [_make_row(job_id='600.desched1', user='solo'),
            _make_row(job_id='601.desched1', user='solo')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body
    assert 'user: solo' in body


def test_user_column_kept_uniform_but_multipage(
    app, auth_client, monkeypatch,
):
    """A uniform PAGE of a multi-page result proves nothing — the column
    stays rather than paying a distinct-count query."""
    rows = [_make_row(job_id='600.desched1', user='solo')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows,
                         jobs_count_return=500)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' in body
    assert 'user: solo' not in body


def test_user_sort_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_search_return=_two_user_rows(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&sort_by=user&sort_dir=asc'
    )
    kw = captured['last_jobs_search_kwargs']
    assert kw['sort_by'] == 'user'
    assert kw['sort_dir'] == 'asc'


def test_name_column_truncates(
    app, auth_client, active_project, monkeypatch,
):
    """Long job names must not widen the column: ellipsis truncation with
    the full name on the title tooltip."""
    long_name = 'a_very_long_job_name_' + 'x' * 100
    rows = [_make_row(job_id='600.desched1', user='alice', name=long_name),
            _make_row(job_id='601.desched1', user='zed')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'max-width: 35ch' in body
    assert 'text-truncate' in body
    assert f'title="{long_name}"' in body


def test_by_user_fragment_threads_metric_sort(
    app, auth_client, active_project, monkeypatch,
):
    """The metric pill decides the plugin ranking (and thus which top-N
    survives) — the Derecho GPU-Hours one-user bug."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'cpu_hours'     # _DEFAULT_METRIC_PIE

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'gpu_hours'

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=jobs'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'job_count'


def test_by_project_fragment_threads_metric_sort(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project'
        '?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert _dim == 'account'
    assert kwargs['sort_by'] == 'gpu_hours'


def test_by_user_sort_indicator_follows_metric(
    app, auth_client, active_project, monkeypatch,
):
    """The initial client-sort indicator sits on the active metric column."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=gpu_hours'
    ).get_data(as_text=True)
    import re
    gpu_th = re.search(r'<th[^>]*sort-desc[^>]*>GPU-hours</th>', body)
    assert gpu_th, 'sort-desc must sit on the GPU-hours header'
    assert not re.search(r'<th[^>]*sort-desc[^>]*>CPU-hours</th>', body)
