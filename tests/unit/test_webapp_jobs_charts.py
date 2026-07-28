"""Unit tests for the job-history chart generators (webapp/dashboards/charts.py).

``generate_jobs_histogram`` renders the plugin's self-describing histogram
envelope; ``generate_jobs_user_pie_chart`` renders the jobs_usage_by('user')
envelope with clickable ``#job-user-<username>`` sentinels routed by
svg-chart-links.js. matplotlib's SVG backend rasterizes most text to paths,
so assertions favor behavior (placeholders, sentinels, distinct output per
metric) over label grepping.
"""

from __future__ import annotations

import pytest

from webapp.dashboards.charts import (
    generate_jobs_histogram,
    generate_jobs_user_pie_chart,
)

pytestmark = pytest.mark.unit


def _hist(counts=(10, 5, 0), cpu_hours=(100.0, 50.0, 0.0),
          gpu_hours=(0.0, 2.0, 0.0), dimension='wait', null_count=0):
    labels = ['<1m', '1-5m', '5-15m'][:len(counts)]
    los = [0, 60, 300]
    his = [59, 299, 899]
    return {
        'dimension': dimension, 'column': 'eligible_secs', 'unit': 'seconds',
        'min_param': 'min_eligible_secs', 'max_param': 'max_eligible_secs',
        'buckets': [
            {'label': lbl, 'lo': lo, 'hi': hi,
             'job_count': c, 'cpu_hours': ch, 'gpu_hours': gh}
            for lbl, lo, hi, c, ch, gh
            in zip(labels, los, his, counts, cpu_hours, gpu_hours)
        ],
        'null_count': null_count,
        'total_count': sum(counts) + null_count,
    }


def _usage(rows=None, totals=None):
    if rows is None:
        rows = [
            {'value': 'alice', 'job_count': 50, 'cpu_hours': 500.0, 'gpu_hours': 0.0},
            {'value': 'bob',   'job_count': 10, 'cpu_hours': 100.0, 'gpu_hours': 5.0},
        ]
    if totals is None:
        totals = {'job_count': 60, 'cpu_hours': 600.0, 'gpu_hours': 5.0}
    return {'dimension': 'user', 'rows': rows, 'totals': totals}


# ---------------------------------------------------------------------------
# generate_jobs_histogram
# ---------------------------------------------------------------------------

def test_histogram_renders_svg_for_all_metrics():
    for metric in ('jobs', 'cpu_hours', 'gpu_hours'):
        out = generate_jobs_histogram(_hist(), metric=metric)
        assert '<svg' in out


def test_histogram_metrics_render_differently():
    """Same envelope, different metric → different SVG (and distinct cache
    keys, so variants never collide in the LRU)."""
    svg_jobs = generate_jobs_histogram(_hist(), metric='jobs')
    svg_cpu  = generate_jobs_histogram(_hist(), metric='cpu_hours')
    assert svg_jobs != svg_cpu


def test_histogram_empty_envelope_returns_placeholder():
    assert 'No jobs in this range' in generate_jobs_histogram({'buckets': []})
    assert 'No jobs in this range' in generate_jobs_histogram({})
    assert 'No jobs in this range' in generate_jobs_histogram(None)


def test_histogram_all_zero_returns_placeholder():
    out = generate_jobs_histogram(_hist(counts=(0, 0, 0),
                                        cpu_hours=(0, 0, 0),
                                        gpu_hours=(0, 0, 0)))
    assert '<svg' not in out
    assert 'No jobs in this range' in out


def test_histogram_zero_metric_nonzero_other_metric():
    """A CPU-only window has zero gpu_hours everywhere → GPU view shows the
    placeholder while the jobs view still renders."""
    h = _hist(counts=(5, 3, 1), cpu_hours=(50.0, 30.0, 10.0),
              gpu_hours=(0.0, 0.0, 0.0))
    assert 'No jobs in this range' in generate_jobs_histogram(h, metric='gpu_hours')
    assert '<svg' in generate_jobs_histogram(h, metric='jobs')


def test_histogram_dimension_in_cache_key():
    """Identical bucket values under different dimensions must not share a
    cache entry (labels differ in practice, but the key must not rely on it)."""
    from webapp.dashboards.charts import _jobs_histogram_cache_key
    a = _jobs_histogram_cache_key(_hist(dimension='wait'))
    b = _jobs_histogram_cache_key(_hist(dimension='duration'))
    assert a != b


def test_histogram_bars_carry_bucket_sentinels():
    """Populated bands are wrapped in #jh-bar-<index> anchors (index-keyed,
    matching data-jh-bucket rows); empty bands get no anchor."""
    svg = generate_jobs_histogram(_hist(counts=(10, 5, 0)))
    assert '#jh-bar-0' in svg
    assert '#jh-bar-1' in svg
    assert '#jh-bar-2' not in svg


def test_histogram_sentinels_follow_job_count_not_metric():
    """Clickability is decided by job_count even on an hours metric — a band
    with jobs but zero cpu_hours must still be drillable."""
    h = _hist(counts=(10, 5, 3), cpu_hours=(100.0, 50.0, 0.0))
    svg = generate_jobs_histogram(h, metric='cpu_hours')
    assert '#jh-bar-2' in svg


def test_histogram_count_vector_in_cache_key():
    """Two envelopes with identical hours vectors but different populated
    band sets must not share a cache entry — the drill URLs differ."""
    from webapp.dashboards.charts import _jobs_histogram_cache_key
    a = _jobs_histogram_cache_key(
        _hist(counts=(10, 5, 3), cpu_hours=(100.0, 50.0, 0.0)),
        metric='cpu_hours')
    b = _jobs_histogram_cache_key(
        _hist(counts=(10, 5, 0), cpu_hours=(100.0, 50.0, 0.0)),
        metric='cpu_hours')
    assert a != b


# ---------------------------------------------------------------------------
# generate_jobs_histogram — owner-stacked bars (plugin owners_limit envelope)
# ---------------------------------------------------------------------------

def _with_owners(h, owners_by_index):
    """Attach an owners mapping to selected buckets of a _hist() envelope."""
    for i, owners in owners_by_index.items():
        h['buckets'][i]['owners'] = owners
    return h


def _owner(job_count, cpu, gpu=0.0):
    return {'job_count': job_count, 'cpu_hours': cpu, 'gpu_hours': gpu}


def test_histogram_stacked_when_owners_present():
    plain = _hist()
    rich = _with_owners(_hist(), {
        0: {'alice': _owner(6, 60.0), 'bob': _owner(4, 40.0)},
        1: {'alice': _owner(5, 50.0)},
    })
    svg_plain = generate_jobs_histogram(plain)
    svg_rich = generate_jobs_histogram(rich)
    assert '<svg' in svg_rich
    assert svg_rich != svg_plain


def test_histogram_owner_segments_follow_active_metric():
    """Segments are cut in the ACTIVE metric: two envelopes with identical
    job-count splits but different cpu splits share a jobs key and diverge
    on the cpu_hours key."""
    from webapp.dashboards.charts import _jobs_histogram_cache_key
    a = _with_owners(_hist(), {
        0: {'alice': _owner(5, 90.0), 'bob': _owner(5, 10.0)}})
    b = _with_owners(_hist(), {
        0: {'alice': _owner(5, 50.0), 'bob': _owner(5, 50.0)}})
    assert _jobs_histogram_cache_key(a, metric='jobs') == \
        _jobs_histogram_cache_key(b, metric='jobs')
    assert _jobs_histogram_cache_key(a, metric='cpu_hours') != \
        _jobs_histogram_cache_key(b, metric='cpu_hours')


def test_histogram_owner_remainder_segment():
    """Owners summing below the bucket total grow a pale base segment —
    the beyond-top-N / NULL-user remainder — reflected in the key."""
    from webapp.dashboards.charts import _jobs_histogram_cache_key, \
        _jobs_bucket_segments
    truncated = _with_owners(_hist(), {
        0: {'alice': _owner(6, 60.0)}})     # bucket holds 10 jobs → 4 unattributed
    assert _jobs_bucket_segments(truncated['buckets'][0], 'job_count') == \
        [4.0, 6.0]
    even = _with_owners(_hist(), {
        0: {'alice': _owner(5, 50.0), 'bob': _owner(5, 50.0)}})
    assert _jobs_bucket_segments(even['buckets'][0], 'job_count') == [5.0, 5.0]
    assert _jobs_histogram_cache_key(truncated) != \
        _jobs_histogram_cache_key(even)
    assert '<svg' in generate_jobs_histogram(truncated)


def test_histogram_segments_ascending_with_remainder_first():
    from webapp.dashboards.charts import _jobs_bucket_segments
    b = {'job_count': 20, 'cpu_hours': 200.0, 'gpu_hours': 0.0,
         'owners': {'alice': _owner(9, 90.0), 'bob': _owner(3, 30.0),
                    'carol': _owner(6, 60.0)}}
    # remainder (20-18=2) first, then owners ascending
    assert _jobs_bucket_segments(b, 'job_count') == [2.0, 3.0, 6.0, 9.0]
    assert _jobs_bucket_segments({'job_count': 5}, 'job_count') == []


def test_histogram_every_segment_carries_sentinel():
    """A stacked bucket's segments all carry the SAME #jh-bar-<i> anchor
    (click anywhere on the bar drills the band); a zero-job bucket stays
    inert even in a stacked chart."""
    rich = _with_owners(_hist(counts=(10, 5, 0)), {
        0: {'alice': _owner(6, 60.0), 'bob': _owner(4, 40.0)},
        1: {'alice': _owner(5, 50.0)},
    })
    svg = generate_jobs_histogram(rich)
    assert svg.count('#jh-bar-0') >= 2
    assert '#jh-bar-2' not in svg


def test_histogram_flat_fallback_without_owners():
    """Owner-less envelopes (older plugin / cached) keep the flat path:
    same sentinels, renders fine."""
    svg = generate_jobs_histogram(_hist(counts=(10, 5, 0)))
    assert '<svg' in svg
    assert '#jh-bar-0' in svg and '#jh-bar-2' not in svg


# ---------------------------------------------------------------------------
# generate_jobs_user_pie_chart
# ---------------------------------------------------------------------------

def test_pie_wedges_clickable():
    svg = generate_jobs_user_pie_chart(_usage())
    assert '<svg' in svg
    assert '#job-user-alice' in svg
    assert '#job-user-bob' in svg


def test_pie_other_slice_from_pretruncation_totals():
    """rows sum to 600 but totals say 800 — the upstream limit dropped rows,
    and the difference must surface as an inert Other slice (no sentinel)."""
    usage = _usage(totals={'job_count': 100, 'cpu_hours': 800.0, 'gpu_hours': 5.0})
    svg = generate_jobs_user_pie_chart(usage)
    assert 'Other' in svg
    assert '#job-user-Other' not in svg
    assert '#job-user-None' not in svg


def test_pie_no_other_when_rows_cover_totals():
    svg = generate_jobs_user_pie_chart(_usage())
    assert 'Other' not in svg


def test_pie_long_tail_lumped_at_hard_cap():
    rows = [{'value': f'u{i}', 'job_count': 1,
             'cpu_hours': float(100 - i), 'gpu_hours': 0.0}
            for i in range(15)]
    total = sum(r['cpu_hours'] for r in rows)
    usage = _usage(rows=rows, totals={'job_count': 15, 'cpu_hours': total,
                                      'gpu_hours': 0.0})
    svg = generate_jobs_user_pie_chart(usage)
    assert '#job-user-u0' in svg            # biggest user kept + clickable
    assert '#job-user-u14' not in svg       # tail folded into Other
    assert 'Other' in svg


def test_pie_metric_selects_and_resorts():
    """metric='jobs' re-sorts by job_count — bob leads despite fewer hours."""
    rows = [
        {'value': 'alice', 'job_count': 5,   'cpu_hours': 500.0, 'gpu_hours': 0.0},
        {'value': 'bob',   'job_count': 200, 'cpu_hours': 10.0,  'gpu_hours': 0.0},
    ]
    usage = _usage(rows=rows, totals={'job_count': 205, 'cpu_hours': 510.0,
                                      'gpu_hours': 0.0})
    svg_hours = generate_jobs_user_pie_chart(usage, metric='cpu_hours')
    svg_jobs  = generate_jobs_user_pie_chart(usage, metric='jobs')
    assert svg_hours != svg_jobs
    assert '#job-user-bob' in svg_jobs


def test_pie_empty_and_zero_total_return_placeholder():
    assert 'No usage data' in generate_jobs_user_pie_chart(
        {'rows': [], 'totals': {}})
    assert 'No usage data' in generate_jobs_user_pie_chart(
        _usage(totals={'job_count': 0, 'cpu_hours': 0.0, 'gpu_hours': 0.0}))
    assert 'No usage data' in generate_jobs_user_pie_chart(None)


def test_pie_unknown_user_row_is_inert():
    """A NULL username row renders (as '(unknown)') but gets no sentinel."""
    rows = [
        {'value': 'alice', 'job_count': 5, 'cpu_hours': 50.0, 'gpu_hours': 0.0},
        {'value': None,    'job_count': 2, 'cpu_hours': 20.0, 'gpu_hours': 0.0},
    ]
    usage = _usage(rows=rows, totals={'job_count': 7, 'cpu_hours': 70.0,
                                      'gpu_hours': 0.0})
    svg = generate_jobs_user_pie_chart(usage)
    assert '#job-user-alice' in svg
    assert '#job-user-None' not in svg


def test_pie_sentinel_prefix_parameterized():
    """The generalized renderer emits the caller's sentinel family — the
    By Project pie drills data-job-project rows, not user rows."""
    from webapp.dashboards.charts import generate_jobs_usage_pie_chart
    rows = [
        {'value': 'SCSG0001', 'job_count': 5, 'cpu_hours': 50.0, 'gpu_hours': 0.0},
    ]
    usage = _usage(rows=rows, totals={'job_count': 5, 'cpu_hours': 50.0,
                                      'gpu_hours': 0.0})
    svg = generate_jobs_usage_pie_chart(usage, sentinel_prefix='job-proj')
    assert '#job-proj-SCSG0001' in svg
    assert '#job-user-' not in svg


def test_pie_sentinel_prefix_in_cache_key():
    """Identical usage vectors under different sentinel families must not
    share a cache entry — the embedded drill anchors differ."""
    from webapp.dashboards.charts import _jobs_usage_pie_cache_key
    a = _jobs_usage_pie_cache_key(_usage(), sentinel_prefix='job-user')
    b = _jobs_usage_pie_cache_key(_usage(), sentinel_prefix='job-proj')
    assert a != b
