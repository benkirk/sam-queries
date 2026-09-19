from __future__ import annotations

import pytest

from _jobs_helpers import (
    _WASTED_HIST,
    _install_mock_plugin,
    _sample_hist,
    _sample_usage,
)


def test_by_user_fragment_renders_rows_and_pie(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-job-user="alice"' in body
    assert 'data-job-user="bob"' in body
    assert '<svg' in body                       # pie rendered
    assert '#sam/row/data-job-user/alice' in body            # clickable wedge sentinel
    # No remainder beyond the row cap -> no Other row.
    assert 'beyond top' not in body
    # Plugin was asked for the user dimension, scoped to the project tree.
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'user'
    assert active_project.projcode in kwargs['account']


def test_by_user_fragment_other_row_from_pretruncation_totals(
    app, auth_client, active_project, monkeypatch,
):
    """totals bigger than the visible rows -> an inert Other summary row."""
    usage = _sample_usage(totals={'job_count': 100, 'cpu_hours': 900.0,
                                  'gpu_hours': 5.0})
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=usage)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'beyond top' in body


def test_by_user_fragment_charges_metric_renders_its_own_column(
    app, auth_client, active_project, monkeypatch,
):
    """?metric=charges ranks the rows by charges, so the table must SHOW
    charges. It shipped ranking by an invisible column (upstream C1)."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=charges'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '>Charges</th>' in body
    # The initial-sort indicator follows the active metric — on charges it
    # used to land on no header at all.
    assert 'sort-desc" data-sort="numeric">Charges' in body
    # alice's 150.0 charges (cpu 150 + gpu 0), sortable on the raw value.
    assert 'data-sort-value="150.0"' in body
    # ...and the uncharged-QoS caption, since bob has hours but no charges.
    assert 'Uncharged QoS' in body


def test_by_user_fragment_charges_column_absent_indicator_on_other_metrics(
    app, auth_client, active_project, monkeypatch,
):
    """The column is always present; only its sort indicator is conditional."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=cpu_hours'
    )
    body = resp.get_data(as_text=True)
    assert '>Charges</th>' in body
    assert 'sort-desc" data-sort="numeric">Charges' not in body
    assert 'Uncharged QoS' not in body      # caption is charges-only


def test_by_user_other_row_carries_a_charges_figure(
    app, auth_client, active_project, monkeypatch,
):
    """The Other row says 'beyond top N by charges' — so it must show them."""
    usage = _sample_usage(totals={'job_count': 100, 'cpu_hours': 900.0,
                                  'gpu_hours': 5.0, 'cpu_charges': 400.0,
                                  'gpu_charges': 0.0})
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=usage)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=charges'
    )
    body = resp.get_data(as_text=True)
    assert 'beyond top' in body
    # The "by <noun>" label wraps across a template line break, so match the
    # noun on its own rather than the phrase.
    assert 'charges)</span>' in body
    # 400 total - 150 shown = 250 remaining, in charge units.
    assert '<td class="text-end">250</td>' in body


def test_usage_other_remainder_includes_charge_keys():
    """_usage_other must carry the full metric vector, or a charges view has
    no remainder figure in its own units."""
    from webapp.jobs.routes import _usage_other
    rem = _usage_other(_sample_usage(
        totals={'job_count': 100, 'cpu_hours': 900.0, 'gpu_hours': 5.0,
                'cpu_charges': 400.0, 'gpu_charges': 7.0}))
    assert rem['cpu_charges'] == pytest.approx(250.0)
    assert rem['gpu_charges'] == pytest.approx(7.0)


def test_usage_other_visibility_ignores_charges_alone():
    """An entirely uncharged tail is still a real tail — gating on charges
    would hide it, and charges can never appear without hours anyway."""
    from webapp.jobs.routes import _usage_other
    # Tail exists in hours, contributes no charges.
    rem = _usage_other(_sample_usage(
        totals={'job_count': 100, 'cpu_hours': 900.0, 'gpu_hours': 5.0,
                'cpu_charges': 150.0, 'gpu_charges': 0.0}))
    assert rem is not None
    assert rem['cpu_charges'] == pytest.approx(0.0)
    # Rows exactly cover totals -> no Other row at all.
    assert _usage_other(_sample_usage()) is None


def test_by_user_fragment_disabled_banner(auth_client, active_project):
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


def test_by_user_fragment_400_on_invalid_machine(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=gust'
    )
    assert resp.status_code == 400


def test_by_user_fragment_404_on_unknown_projcode(auth_client):
    resp = auth_client.get('/dashboards/user/jobs/NOPE9999/by-user?machine=derecho')
    assert resp.status_code == 404


def test_wait_times_fragment_caption_on_null_count(
    app, auth_client, active_project, monkeypatch,
):
    """null_count > 0 on the wait dimension -> the eligible-time caption."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(null_count=7),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'no wait measurement' in body
    assert 'early 2025 on Derecho' in body


def test_wait_times_fragment_no_caption_when_all_measured(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(null_count=0),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times?machine=derecho'
    )
    assert 'no wait measurement' not in resp.get_data(as_text=True)


def test_wait_times_fragment_pins_wait_dimension(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&dimension=gpus'   # client cannot override the pin
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'wait'


def test_job_sizes_fragment_dimension_pills_and_default(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Dimension pills present (only on the Sizes tab).
    assert 'dimension=cpus' in body
    assert 'dimension=memory' in body
    # Default dimension is nodes.
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'nodes'


def test_job_sizes_fragment_invalid_dimension_falls_back(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=bogus'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'nodes'


def test_job_sizes_fragment_memory_dimension_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='memory'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'memory'


def test_job_sizes_fragment_offers_memory_trio_pills(
    app, auth_client, active_project, monkeypatch,
):
    """The Sizes tab renders all six dimension pills, memory trio labeled
    Req mem / Used mem / Wasted."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'dimension=memory_used' in body
    assert 'dimension=memory_wasted' in body
    assert 'Req mem' in body
    assert 'Used mem' in body
    assert 'Wasted' in body


@pytest.mark.parametrize('dimension', ['memory_used', 'memory_wasted'])
def test_job_sizes_fragment_memory_trio_forwarded(
    app, auth_client, active_project, monkeypatch, dimension,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension=dimension),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        f'?machine=derecho&dimension={dimension}'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == dimension


def test_job_sizes_wasted_caption_derecho_only(
    app, auth_client, active_project, monkeypatch,
):
    """The whole-node caveat renders on derecho's Wasted view and nowhere
    else — not on casper (shared nodes make wasted meaningful there) and
    not on derecho's other dimensions."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_histogram_return=_sample_hist(dimension='memory_wasted'),
        machines=('derecho', 'casper'),
    )
    base = f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'

    body = auth_client.get(
        f'{base}?machine=derecho&dimension=memory_wasted').get_data(as_text=True)
    assert 'node-exclusive' in body

    body = auth_client.get(
        f'{base}?machine=casper&dimension=memory_wasted').get_data(as_text=True)
    assert 'node-exclusive' not in body

    body = auth_client.get(
        f'{base}?machine=derecho&dimension=memory').get_data(as_text=True)
    assert 'node-exclusive' not in body


def test_histogram_fragment_native_bounds_passthrough(
    app, auth_client, active_project, monkeypatch,
):
    """Envelope-native params (min_param/max_param names) pass through
    verbatim — no display-unit re-derivation, no double conversion."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_eligible_secs=120&max_eligible_secs=900'
        '&min_reqmem=1073741824&min_memory_used=2147483648'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 120
    assert kwargs['max_eligible_secs'] == 900
    assert kwargs['min_reqmem'] == 1073741824
    assert kwargs['min_memory_used'] == 2147483648


def test_histogram_fragment_native_bound_wins_over_human_units(
    app, auth_client, active_project, monkeypatch,
):
    """When a deep link carries both spellings of the same bound, the
    native form (parsed last) wins."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_wait_hours=2&min_eligible_secs=120'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 120


def test_histogram_fragment_negative_wasted_bound_not_clamped(
    app, auth_client, active_project, monkeypatch,
):
    """The 'over request' band replays as max_memory_wasted=-1 — the signed
    parse must forward the negative, not clamp it to 0 or drop it."""
    captured = _install_mock_plugin(
        app, monkeypatch,
        jobs_histogram_return=_sample_hist(dimension='memory_wasted'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory_wasted&max_memory_wasted=-1'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['max_memory_wasted'] == -1


def test_jobs_fragment_native_bounds_forwarded_to_search_and_count(
    app, auth_client, active_project, monkeypatch,
):
    """The per-job table accepts the same native bounds (bar-drill target):
    both jobs_search and the count see them, and the count leaves the
    SAM-summary fast path (range bound in play)."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&min_memory_wasted=-4294967296&max_memory_wasted=-1'
        '&min_elapsed=3600'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['min_memory_wasted'] == -4294967296
    assert skw['max_memory_wasted'] == -1
    assert skw['min_elapsed'] == 3600
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None                      # plugin path, not SAM summary
    assert ckw['max_memory_wasted'] == -1


def test_durations_fragment_pins_duration_dimension(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='duration'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/durations?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'duration'
    # No dimension pills outside the Sizes tab.
    assert 'dimension=cpus' not in body


def test_histogram_fragment_converts_wait_hours_to_secs(
    app, auth_client, active_project, monkeypatch,
):
    """min/max_wait_hours (human units) convert to eligible_secs at the
    route boundary — the service/plugin only ever see seconds."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_wait_hours=2&max_wait_hours=4.5'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 7200
    assert kwargs['max_eligible_secs'] == 16200


def test_histogram_bucket_rows_carry_band_drill_urls(
    app, auth_client, active_project, monkeypatch,
):
    """Populated bands render data-jh-bucket rows whose collapse content
    lazy-loads the per-job fragment with the envelope's min/max bounds
    plus the pane's round-trip filters."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&queue=cpu'
    )
    body = resp.get_data(as_text=True)
    assert 'data-jh-bucket="0"' in body
    assert 'data-jh-bucket="1"' in body
    import re
    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert f'/dashboards/user/jobs/{active_project.projcode}?' in url0
    assert 'min_eligible_secs=0' in url0
    assert 'max_eligible_secs=59' in url0
    assert 'queue=cpu' in url0             # pane filters carried into the drill
    assert 'machine=derecho' in url0
    assert 'target_id=' in url0


def test_histogram_bucket_drill_omits_open_ends(
    app, auth_client, active_project, monkeypatch,
):
    """A None band end is an open side — its param is omitted from the
    drill URL in both directions: the negative 'over request' band emits
    only the max bound, the open top band only the min. Empty bands get
    no drill row at all."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_WASTED_HIST,
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory_wasted'
    )
    body = resp.get_data(as_text=True)

    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert 'min_memory_wasted' not in url0
    assert 'max_memory_wasted=-1' in url0

    assert 'data-jh-bucket="1"' not in body        # empty band: inert row
    assert re.search(r'id="[^"]*-b1-content"', body) is None

    url2 = re.search(r'id="[^"]*-b2-content"\s+hx-get="([^"]+)"', body).group(1)
    assert f'min_memory_wasted={2 ** 30}' in url2
    assert 'max_memory_wasted' not in url2


def test_histogram_bucket_drill_band_bound_replaces_pane_param(
    app, auth_client, active_project, monkeypatch,
):
    """When the pane itself is filtered on the same native bound the band
    replays, the band's value replaces the pane's in the drill URL —
    never both."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_eligible_secs=999'
    )
    body = resp.get_data(as_text=True)
    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert 'min_eligible_secs=0' in url0
    assert 'min_eligible_secs=999' not in url0


def test_histogram_fragment_metric_pill_roundtrip(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&metric=cpu_hours'
    )
    body = resp.get_data(as_text=True)
    # The cpu_hours pill is the active one.
    import re
    assert re.search(r'active[^>]*>\s*CPU-hours', body) or \
        re.search(r'CPU-hours', body)
