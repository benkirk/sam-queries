"""The chart hover layer: `BaseChart.tooltip` -> a native SVG ``<title>`` per mark.

Design record: docs/plans/CHART_HOVER_LAYER.md.
"""

import re

import matplotlib
import matplotlib.pyplot as plt
import pytest

from webapp.dashboards.charts import (
    generate_allocation_sunburst,
    generate_fair_share_sunburst,
    generate_jobs_facility_sunburst,
)
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


def test_no_chart_leaks_a_tooltip_id(app):
    """`tt-` is reserved: every stamped id must be rewritten away, on every chart."""
    with app.test_request_context('/'):
        leaked = [name for name, fn, args, kwargs in CASES
                  if 'id="tt-' in fn(*args, **kwargs)]
    assert not leaked
