from __future__ import annotations

import pytest

from _jobs_helpers import (
    _HIST_TABS,
    _LENS,
    _banded_hist,
    _card_url,
    _explore_body,
    _install_mock_plugin,
    _job_sizes_body,
    _labels,
    _sample_hist,
    _sizes_hist,
    _tag_for,
)


def test_trim_drops_a_leading_empty_band():
    """Every job uses ≥1 node, so that 0 band can never fill."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _sizes_hist()
    trimmed = _trim_empty_edge_bands(hist)

    assert _labels(trimmed) == ['1', '2-4']
    # The envelope is a shared cache entry — trimming must copy, not mutate.
    assert _labels(hist) == ['0', '1', '2-4']


def test_trim_keeps_a_populated_leading_band():
    """The GPU 0 band holds the CPU-only jobs and stays."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(_sizes_hist(first_jobs=9))) == \
        ['0', '1', '2-4']


def test_trim_drops_trailing_empty_bands():
    """The bucket tables are sized for the biggest machine the plugin
    serves, so a smaller one's top bands can never fill."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([3, 7, 2, 0, 0, 0])
    trimmed = _trim_empty_edge_bands(hist)

    assert _labels(trimmed) == ['0', '1', '2']
    assert _labels(hist) == ['0', '1', '2', '3', '4', '5']   # not mutated


def test_trim_drops_both_edges_at_once():
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 0, 4, 9, 0]))) == ['2', '3']


def test_trim_keeps_interior_empty_bands():
    """A gap inside the distribution is a finding, not noise."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 5, 0, 0, 8, 0]))) == ['1', '2', '3', '4']


def test_trim_keeps_a_single_populated_band():
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 0, 6, 0, 0]))) == ['2']


def test_trim_empties_an_entirely_empty_range():
    """All-zero -> no bands at all, so the caller renders an empty state
    instead of a bar-less axis over a table of zeros."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([0, 0, 0])
    trimmed = _trim_empty_edge_bands(hist)

    assert trimmed['buckets'] == []
    assert len(hist['buckets']) == 3                         # not mutated


def test_trim_tolerates_an_envelope_with_no_bands():
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([])
    assert _trim_empty_edge_bands(hist) is hist
    assert _trim_empty_edge_bands(None) is None


def test_trim_ignores_the_displayed_metric():
    """A band of real jobs charging no GPU-hours must not shift the axis."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _sizes_hist(first_jobs=4)
    hist['buckets'][0]['cpu_hours'] = 0.0
    hist['buckets'][0]['gpu_hours'] = 0.0
    assert _trim_empty_edge_bands(hist)['buckets'][0]['label'] == '0'


def test_job_sizes_fragment_hides_the_empty_zero_band(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=_sizes_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)

    import re
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert '0' not in labels
    assert '1' in labels and '2-4' in labels


def test_job_sizes_bar_and_row_indices_stay_aligned_after_trim(
    app, auth_client, active_project, monkeypatch,
):
    """#sam/row/data-jh-bucket/<i> and data-jh-bucket=<i> both index the trimmed vector."""
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=_sizes_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)

    import re
    # Band 0 of the rendered chart is now the '1' band, and the row that
    # answers a click on it must be the one carrying its counts.
    row = re.search(r'data-jh-bucket="0".*?</tr>', body, re.S)
    assert row, 'no bucket-0 row rendered'
    assert '<code>1</code>' in row.group(0)


def test_job_sizes_fragment_hides_trailing_empty_bands(
    app, auth_client, active_project, monkeypatch,
):
    """Casper can't fill the top of an axis sized for the biggest machine."""
    import re
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 4, 9, 0, 0, 0]))
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert labels == ['1', '2']


def test_job_sizes_fragment_keeps_interior_empty_bands(
    app, auth_client, active_project, monkeypatch,
):
    import re
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 5, 0, 8, 0]))
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert labels == ['1', '2', '3']


def test_histogram_with_no_matching_jobs_renders_one_empty_state(
    app, auth_client, active_project, monkeypatch,
):
    """All-zero: one sentence, not a bar-less axis over a table of zeros."""
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 0, 0]))

    assert 'No jobs match these filters.' in body
    assert 'Bucket breakdown' not in body
    assert 'data-jh-bucket' not in body


def test_histogram_all_unmeasured_says_so_instead_of_no_jobs(
    app, auth_client, active_project, monkeypatch,
):
    """Matching jobs that carry no value on this dimension are a different
    story from no matching jobs — Derecho waits before 2025 are the case."""
    hist = _banded_hist([0, 0, 0])
    hist.update(dimension='wait', null_count=42, total_count=42)
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=hist)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert 'no wait measurement' in body
    assert 'No jobs match these filters.' not in body


@pytest.mark.parametrize('tab', _HIST_TABS)
def test_histogram_tabs_offer_the_log_switch_off_by_default(
    app, auth_client, active_project, monkeypatch, tab,
):
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/{tab}?machine=derecho'
    ).get_data(as_text=True)

    assert 'Log scale' in body
    assert 'id="jobs-hist-log-' in body
    # Off by default, and the switch offers the ON direction.
    assert 'log=1' in body
    assert 'checked' not in body


def test_log_on_renders_and_offers_the_way_back(
    app, auth_client, active_project, monkeypatch,
):
    """?log=1 -> a chart still renders (solid bars), the switch reflects the
    state, the band drill anchors survive, and clicking it turns log off."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&log=1'
    ).get_data(as_text=True)

    assert '<svg' in body
    assert 'checked' in body
    assert '#sam/row/data-jh-bucket/0' in body
    assert 'log=0' in body


def test_log_rides_the_roundtrip_form_so_the_other_pills_keep_it(
    app, auth_client, active_project, monkeypatch,
):
    """The metric / dimension / owner pills carry no ?log= of their own —
    they inherit it from the hidden params form they hx-include. Absent when
    off, so a stale `1` can never outlive the switch."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    url = (f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
           '?machine=derecho')

    on = auth_client.get(url + '&log=1').get_data(as_text=True)
    assert '<input type="hidden" name="log" value="1">' in on

    off = auth_client.get(url).get_data(as_text=True)
    assert 'name="log"' not in off


def test_log_does_not_leak_into_the_band_drill_urls(
    app, auth_client, active_project, monkeypatch,
):
    """Drill URLs are built before the y-scale joins the params — the jobs
    table has no log axis and should not be asked about one."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&log=1'
    ).get_data(as_text=True)

    import re
    drills = re.findall(r'hx-get="([^"]*min_eligible_secs[^"]*)"', body)
    assert drills, 'no band drill URLs rendered'
    assert not [d for d in drills if 'log=' in d]


@pytest.mark.parametrize('query,expected', [
    ('log=1', True),
    ('log=true', True),
    ('log=on', True),
    ('log=0', False),
    ('log=', False),
    ('log=nonsense', False),
    ('', False),
])
def test_parse_log(app, query, expected):
    from webapp.jobs.routes import _parse_log
    with app.test_request_context(f'/?{query}'):
        assert _parse_log() is expected


def test_lens_declared_on_both_ends_of_every_pill_panel(
    app, auth_client, active_project, monkeypatch,
):
    """Tab button (injects on fetch) and container (saves on settle).

    Both ends are required: the button is what requests the panel, and the
    container is the element htmx reports as settled, which is what lets an
    in-panel pill click persist without a click handler.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)

    for panel in ('byuser', 'wait', 'sizes', 'durations'):
        assert _LENS in _tag_for(body, f'id="jobs-hist-{panel}-tab"'), panel
        assert _LENS in _tag_for(body, f'id="jobs-hist-{panel}"'), panel


def test_lens_not_declared_on_the_jobs_tab(
    app, auth_client, active_project, monkeypatch,
):
    """The per-job table has none of the three pills — no reason to carry
    the lens into its URL."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)

    assert _LENS not in _tag_for(body, 'id="jobs-hist-jobs-tab"')
    assert _LENS not in _tag_for(body, 'id="jobs-hist-jobs"')


def test_lens_is_independent_of_window_persistence(
    app, auth_client, active_project, monkeypatch,
):
    """Resource-details opts out of a *stored window* (it would shadow the
    page's own date range) — that says nothing about the lens, which has no
    such conflict and persists everywhere."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)

    assert 'data-chart-persist-id' not in body      # no window persistence
    assert _LENS in body                            # lens regardless


@pytest.mark.parametrize('query,expected', [
    ('group_by=project', 'project'),
    ('group_by=user', 'user'),
    ('owners_by=account', 'project'),   # the plugin's spelling, still honored
    ('owners_by=user', 'user'),
    ('group_by=nonsense', 'user'),
    ('', 'user'),
])
def test_parse_group_by(app, query, expected):
    from webapp.jobs.routes import _parse_group_by
    with app.test_request_context(f'/?{query}'):
        assert _parse_group_by() == expected


def test_parse_group_by_prefers_the_canonical_spelling(app):
    """Both present (a stale round-trip form beside a fresh pill click):
    group_by wins, so the click a user just made is what renders."""
    from webapp.jobs.routes import _parse_group_by
    with app.test_request_context('/?group_by=user&owners_by=account'):
        assert _parse_group_by() == 'user'


@pytest.mark.parametrize('span_days,expected', [
    (1, 'day'), (30, 'day'), (120, 'day'),
    (121, 'week'),                  # 121 daily bars is over budget
    (365, 'week'), (840, 'week'),   # 840/7 = 120, still exactly in budget
    (841, 'month'),
    (10_000, 'month'),              # full history
])
def test_auto_period_picks_the_finest_granularity_in_budget(span_days, expected):
    """Server-chosen because the explorer permits an unbounded window: a
    fixed 'day' default would blow the bar budget or trip the plugin's
    400-band ValueError, both of which read as a broken panel."""
    from webapp.jobs.routes import _auto_period
    assert _auto_period(span_days) == expected


def test_parse_period_honours_an_explicit_choice_that_fits(app):
    from webapp.jobs.routes import _parse_period
    with app.test_request_context('/?period=month'):
        assert _parse_period(30) == 'month'


def test_parse_period_refuses_a_stale_choice_that_does_not_fit(app):
    """A 'day' saved against last week's 30-day window must not be replayed
    against a five-year one — it would be 1,800 bars."""
    from webapp.jobs.routes import _parse_period
    with app.test_request_context('/?period=day'):
        assert _parse_period(1826) == 'month'


@pytest.mark.parametrize('raw', ['quarter', 'year', 'fortnight', '', 'DAY'])
def test_parse_period_ignores_unknown_values(app, raw):
    """Lenient like _parse_metric: unknown means 'no override', never 400.
    'quarter'/'year' are PeriodGrouper's vocabulary, deliberately not ours."""
    from webapp.jobs.routes import _parse_period
    with app.test_request_context(f'/?period={raw}'):
        assert _parse_period(30) == 'day'


def test_period_choices_disable_rather_than_hide_over_budget_options():
    """A pill that vanishes reads as a bug; a disabled one with the bar
    count is the explanation."""
    from webapp.jobs.routes import _period_choices
    choices = {c['key']: c for c in _period_choices(365)}
    assert choices['day']['enabled'] is False
    assert choices['day']['bars'] == 365
    assert choices['week']['enabled'] is True
    assert set(choices) == {'day', 'week', 'month'}


def test_filter_span_days_reads_the_window(app):
    from webapp.jobs.routes import _filter_span_days, _parse_job_filters
    with app.test_request_context('/?start=2026-01-01&end=2026-01-30'):
        assert _filter_span_days(_parse_job_filters()) == 30


def test_filter_span_days_is_none_for_an_open_ended_window(app):
    """Both date fields cleared on the explorer — the documented opt-in to
    full history. The caller treats None as the widest possible window."""
    from webapp.jobs.routes import _filter_span_days
    with app.test_request_context('/'):
        assert _filter_span_days({'start': '', 'end': ''}) is None


def test_band_drill_url_overrides_the_panes_window():
    """Unlike a histogram band (min_param/max_param), a time band replays
    through start/end — the window filters ARE this dimension — so the
    band's own dates must REPLACE the pane's, not narrow alongside them."""
    from webapp.jobs.routes import _band_drill_url
    url = _band_drill_url(
        '/jobs', {'label': '2026-05-02', 'start': '2026-05-02',
                  'end': '2026-05-02', 'job_count': 7},
        {'machine': 'casper', 'start': '2026-05-01', 'end': '2026-05-31'})
    assert 'start=2026-05-02' in url and 'end=2026-05-02' in url
    assert 'start=2026-05-01' not in url


def test_band_drill_url_is_none_for_an_empty_band():
    from webapp.jobs.routes import _band_drill_url
    assert _band_drill_url('/jobs', {'job_count': 0}, {}) is None


def test_charges_is_a_first_class_metric_everywhere():
    """Ben's call: one vocabulary across all six panels, so the shared
    `metric:jobs` persist family stays valid."""
    from webapp.dashboards.charts import _JOBS_METRIC_KEYS
    from webapp.jobs.routes import (_METRICS, _USAGE_METRIC_KEYS,
                                    _USAGE_SORT_BY)
    assert 'charges' in _METRICS
    # The top-N cut must be rankable by the displayed metric, or a charges
    # view shows owners chosen by hours.
    assert set(_METRICS) <= set(_USAGE_SORT_BY)
    assert _USAGE_SORT_BY['charges'] == 'charges'
    # Every metric must be renderable, not just requestable — this is the
    # membership check that C1 slipped through: 'charges' was in _METRICS
    # and _USAGE_SORT_BY, so it could be asked for and ranked by, while the
    # panel had no way to display it.
    assert set(_METRICS) <= set(_JOBS_METRIC_KEYS)
    # ...and the remainder row has to be computable in every one of them.
    for metric in _METRICS:
        assert set(_JOBS_METRIC_KEYS[metric]) <= set(_USAGE_METRIC_KEYS), metric


def test_job_age_bands_render_on_the_explorer(
        app, auth_client, active_project, monkeypatch):
    """The after/before split was inherited from disk scans; the question is
    the same one, so it gets the same control."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    assert 'ladder-range-bands' in body
    assert 'Job age' in body
    # Scoped by the control's own id namespace: the panel now carries seven
    # ladders (age plus the six numeric ones), so a page-wide count of the
    # shared action would pass on the wrong control's markup.
    assert len(re.findall(r'id="[^"]*-age-lo"', body)) == 1
    assert len(re.findall(r'id="[^"]*-age-hi"', body)) == 1


def test_job_age_ladder_is_shorter_than_the_disk_one(app):
    """Job history is asked about in days and weeks; beyond ~2 years the
    question is 'everything older', not which year."""
    from webapp.jobs import service

    labels = [label for label, _ in service.JOBS_AGE_BANDS]
    assert labels[0] == '< 1 Week'
    assert labels[-1] == '2+ Years'
    assert service.JOBS_AGE_BANDS[-1][1] is None      # open-ended
    assert len(labels) < 10                           # ATIME_BUCKETS is 10


def test_the_age_ladder_is_not_the_days_pill_whitelist(app):
    """The control writes start/end directly and never sets ?days=, so it needs
    no entry in JOBS_WINDOW_CHOICES and _parse_days can't reject it. Pinning
    that keeps someone from 'aligning' the two and silently making 7d a
    rejected pill value."""
    from webapp.jobs import service

    band_days = {upper for _label, upper in service.JOBS_AGE_BANDS if upper}
    assert 7 in band_days
    assert 7 not in service.JOBS_WINDOW_CHOICES


def test_explorer_age_control_writes_start_and_end_only(app, auth_client, active_project, monkeypatch):
    """`days` outranks an explicit range in _parse_job_filters, so the control
    stays clear of it entirely — a panel submit carries no days at all."""
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    assert body.count('name="start"') == 1
    assert body.count('name="end"') == 1
    assert 'name="days"' not in body


def test_explorer_default_window_lands_on_a_band_edge(app, auth_client, active_project, monkeypatch):
    """The 90-day default is also a band edge ('1-3 Months'), so the control
    opens showing a real span rather than its custom state."""
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    assert 'Custom range' not in body


def test_numeric_dimensions_get_machine_shaped_ladders(
        app, auth_client, active_project, monkeypatch):
    """All six numeric filters become sliders, over the plugin's own histogram
    ladders — so a band picked here is a band the matching chart draws."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    for dim in ('nodes', 'cpus', 'gpus', 'wait', 'duration', 'memory'):
        assert len(re.findall(rf'id="[^"]*-{dim}-lo"', body)) == 1, dim
        assert len(re.findall(rf'id="[^"]*-{dim}-hi"', body)) == 1, dim
    # The bounds are still the panel's own display-unit fields; the sliders
    # write into them rather than introducing a second spelling.
    for field in ('min_nodes', 'max_nodes', 'min_wait_hours',
                  'max_elapsed_hours', 'min_reqmem_gb'):
        assert body.count(f'name="{field}"') == 1, field


def test_the_size_section_is_collapsed_until_a_bound_is_set(
        app, auth_client, active_project, monkeypatch):
    """The disclosure is what makes six sliders affordable: the resting panel
    is shorter than the six min/max pairs they replace."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    assert 'Size &amp; runtime' in body
    section = re.search(r'id="[^"]*-size-runtime"\s+class="([^"]*)"', body)
    assert section and 'd-none' in section.group(1)
    toggle = re.search(r'<button[^>]*class="filter-section-toggle[^"]*"[^>]*>', body)
    assert toggle and 'aria-expanded="false"' in toggle.group(0)


def test_a_deep_linked_bound_opens_the_size_section(
        app, auth_client, active_project, monkeypatch):
    """A deep link or a bar drill must never hide the filter it just applied."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode,
                         query='&min_nodes=4')
    section = re.search(r'id="[^"]*-size-runtime"\s+class="([^"]*)"', body)
    assert section and 'd-none' not in section.group(1)


def test_the_nodes_ladder_is_right_sized_per_machine(app):
    """Casper tops out around 128 nodes and derecho around 2488, so one static
    ladder would be wrong for both. The plugin supplies each."""
    from webapp.utils import ladders
    casper = ladders.machine_ladder('casper', 'nodes')
    derecho = ladders.machine_ladder('derecho', 'nodes')
    if casper is None or derecho is None:
        import pytest
        pytest.skip('job_history histogram_buckets not available')
    assert len(casper) < len(derecho)


def test_a_wait_band_edge_survives_the_hours_round_trip(app):
    """The sliders write DISPLAY units into fields the route converts back with
    round(). The '5-15m' band's floor is 300 s, shown as 0.0833 h — and
    int(0.0833 * 3600) is 299, which is why the conversion rounds. A bound one
    second off would fail to match a band edge and render the custom state."""
    from webapp.utils import ladders
    from webapp.jobs.routes import _SECS_PER_HOUR
    ladder = ladders.machine_ladder('derecho', 'wait')
    if ladder is None:
        import pytest
        pytest.skip('job_history histogram_buckets not available')
    for _label, lo, hi in ladder:
        for native in (lo, hi):
            if native is None:
                continue
            shown = ladders.to_display(native, _SECS_PER_HOUR)
            assert round(shown * _SECS_PER_HOUR) == native, (native, shown)


def test_a_bar_drill_now_shows_in_the_panel(
        app, auth_client, active_project, monkeypatch):
    """Regression: a wait/elapsed/memory histogram bar drill writes the
    PLUGIN-native param (min_eligible_secs), which the query has always
    honored — but the panel's own box rendered empty, so the viewer saw a
    filtered table with no visible filter and no way to clear it without going
    back to the chart."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode,
                         query='&min_eligible_secs=3600')
    box = re.search(r'<input[^>]*name="min_wait_hours"[^>]*>', body)
    assert box and 'value="1.0"' in box.group(0)


def test_apply_and_rows_stay_outside_the_size_disclosure(
        app, auth_client, active_project, monkeypatch):
    """Regression: Rows and Apply shared a flex row with the numeric pairs, so
    wrapping those in the disclosure swallowed the SUBMIT BUTTON — invisible
    whenever the section was collapsed, which is its default. Every unit test
    passed; only the rendered page showed it."""
    import re
    body = _explore_body(app, auth_client, monkeypatch, active_project.projcode)
    section = re.search(
        r'id="[^"]*-size-runtime"\s+class="[^"]*d-none[^"]*"(.*?)\n  </div>',
        body, re.S)
    assert section, 'size section not found (or no longer collapsed by default)'
    assert 'type="submit"' not in section.group(1)
    assert 'name="per_page"' not in section.group(1)
    # ...and they are still on the page.
    assert 'type="submit"' in body and 'name="per_page"' in body
