"""The chart hover layer: `BaseChart.tooltip` -> a native SVG ``<title>`` per mark.

Design record: docs/plans/CHART_HOVER_LAYER.md.
"""

import html
import re

import matplotlib
import matplotlib.pyplot as plt
import pytest

from webapp.dashboards.charts import (
    generate_allocation_sunburst,
    generate_fair_share_sunburst,
    generate_jobs_facility_sunburst,
)
from webapp.dashboards.charts.pie import PieChart
from webapp.dashboards.charts.base import BaseChart, fig_to_svg

from chart_samples import CASES

matplotlib.use('Agg')


def _pie_svg(tooltips_for):
    """Two wedges, the first a link; `tooltips_for(chart, wedges)` stamps them."""
    chart = BaseChart()
    chart._tooltips = {}
    fig, ax = plt.subplots()
    wedges, _ = ax.pie([1, 2])
    wedges[0].set_url('#sam/row/data-x/1')
    tooltips_for(chart, wedges)
    return fig_to_svg(fig, chart._tooltips)


def test_a_linked_mark_gets_its_title_inside_the_link():
    svg = _pie_svg(lambda c, w: c.tooltip(w[0], 'first'))
    assert re.search(r'<a [^>]*>\s*<title>first</title>', svg)


def test_an_unlinked_mark_gets_its_title_inside_its_group():
    svg = _pie_svg(lambda c, w: c.tooltip(w[1], 'second'))
    assert re.search(r'<g>\s*<title>second</title>\s*<path', svg)


def test_no_throwaway_id_survives():
    svg = _pie_svg(lambda c, w: [c.tooltip(x, f'w{i}') for i, x in enumerate(w)])
    assert svg.count('<title>') == 2
    assert 'id="tt-' not in svg


def test_markup_in_the_text_is_escaped():
    svg = _pie_svg(lambda c, w: c.tooltip(w[1], '<b>Smith & Co</b>'))
    assert '<title>&lt;b&gt;Smith &amp; Co&lt;/b&gt;</title>' in svg


def test_empty_text_stamps_nothing():
    svg = _pie_svg(lambda c, w: c.tooltip(w[1], ''))
    assert '<title>' not in svg


_SUNBURSTS = {generate_allocation_sunburst, generate_fair_share_sunburst,
              generate_jobs_facility_sunburst}
_SUNBURST_CASES = [c for c in CASES if c[1] in _SUNBURSTS and not c[0].endswith('.empty')]


@pytest.mark.parametrize('name,fn,args,kwargs', _SUNBURST_CASES,
                         ids=[c[0] for c in _SUNBURST_CASES])
def test_every_sunburst_wedge_names_itself(app, name, fn, args, kwargs):
    """One title per drawn wedge: a blank (unfilled) gap wedge has nothing to name."""
    with app.test_request_context('/'):
        chart = fn.chart_class(*args, **kwargs)
        svg = chart.render()
    titles = re.findall(r'<title>([^<]+)</title>', svg)
    assert titles and len(titles) == len(chart._tooltips)
    assert all(' · ' in t for t in titles)


_PIE_CASES = [c for c in CASES if issubclass(c[1].chart_class, PieChart)
              and c[1] not in _SUNBURSTS and not c[0].endswith('.empty')
              and 'panel_sunburst' not in c[0]]


@pytest.mark.parametrize('name,fn,args,kwargs', _PIE_CASES, ids=[c[0] for c in _PIE_CASES])
def test_every_pie_wedge_names_itself(app, name, fn, args, kwargs):
    """A wedge under 5% carries no label, so its hover is the only thing naming it."""
    with app.test_request_context('/'):
        chart = fn.chart_class(*args, **kwargs)
        svg = chart.render()
    titles = re.findall(r'<title>([^<]+)</title>', svg)
    assert len(titles) == len(chart.values) >= 2
    assert [t.split(' · ')[0] for t in titles] == list(chart.labels)
    assert all(t.count(' · ') == 2 for t in titles)     # name, share, amount


def _titles(app, case_id):
    fn, args, kwargs = next((f, a, k) for i, f, a, k in CASES if i == case_id)
    with app.test_request_context('/'):
        return [html.unescape(t) for t in re.findall(r'<title>([^<]+)</title>', fn(*args, **kwargs))]


def test_a_histogram_segment_names_its_bucket_its_owner_and_its_real_size(app):
    titles = _titles(app, 'distribution.data')
    # Bucket '30-90d' has 12 owners: ten named, two folded; values are bytes, not axis units.
    assert '30-90d · 2 other · 11.0 GiB' in titles
    assert '30-90d · uid 1011 · 16.0 GiB' in titles and '30-90d · u10 · 15.0 GiB' in titles
    assert '90-180d · 12.0 GiB' in titles          # no owners: one flat bar


def test_an_fs_scan_owner_keyed_by_uid_hovers_as_its_username(app):
    from webapp.dashboards.charts import generate_distribution_histogram
    hist = {'bucket_labels': ['> 1 year'], 'username_map': {7: 'benkirk'},
            'buckets': {'> 1 year': {'data': 3 * 1024 ** 3, 'files': 30,
                                     'owners': {'7': {'data': 2 * 1024 ** 3, 'files': 20},
                                                8: {'data': 1024 ** 3, 'files': 10}}}}}
    with app.test_request_context('/'):
        svg = generate_distribution_histogram(hist)
    assert '> 1 year · benkirk · 2.00 GiB' in html.unescape(svg)
    assert '> 1 year · uid 8 · 1.00 GiB' in html.unescape(svg)


def test_a_flat_histogram_bar_names_its_bucket_and_value(app):
    titles = _titles(app, 'distribution.log_y')    # log scale abandons the stack
    assert titles == ['< 30d · 55.0 GiB', '30-90d · 180 GiB', '90-180d · 12.0 GiB', '> 180d · 400 GiB']


def test_a_jobs_histogram_segment_names_its_owner_and_the_unknown_remainder(app):
    titles = _titles(app, 'jobs_hist.jobs_owners')
    # The plugin truncates owners upstream, so the remainder's count is unknown.
    assert '0-1m · Others · 110' in titles and '0-1m · u3 · 4' in titles
    assert all(not t.endswith(' · 0') for t in titles)   # a zero segment has no mark to hover


def test_no_chart_leaks_a_tooltip_id(app):
    """`tt-` is reserved: every stamped id must be rewritten away, on every chart."""
    with app.test_request_context('/'):
        leaked = [name for name, fn, args, kwargs in CASES
                  if 'id="tt-' in fn(*args, **kwargs)]
    assert not leaked
