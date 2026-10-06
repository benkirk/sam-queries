"""BaseChart.draw_table_legend: aligned legend columns, right placement only."""

import matplotlib.pyplot as plt

from webapp.dashboards.charts.base import BaseChart, cells_label, fig_to_svg
from webapp.dashboards.charts.layout import profile, resolve_layout
from webapp.dashboards.charts.theme import resolve_theme

_LAYOUTS = profile((7, 4), (3.6, 2.9), (7, 4))
_ROWS = [('ALPHA0001', '60.0%', '600'), ('Other', '40.0%', '400')]
_URLS = ['#sam/row/data-x/ALPHA0001', None]


def _draw(layout_name):
    fig, ax = plt.subplots()
    drawn = BaseChart().draw_table_legend(ax, _ROWS, ['#0057c2', '#999999'], _URLS,
                                          resolve_layout(_LAYOUTS, layout_name),
                                          resolve_theme('light'))
    return drawn, fig_to_svg(fig)


def test_right_placement_draws_and_links_swatch_and_every_cell():
    drawn, svg = _draw('desktop')
    assert drawn
    assert svg.count('xlink:href="#sam/row/data-x/ALPHA0001"') == 4   # swatch + 3 cells
    for text in ('ALPHA0001', '60.0%', '600', 'Other', '40.0%', '400'):
        assert text in svg


def test_below_placement_draws_nothing():
    drawn, svg = _draw('mobile')
    assert not drawn
    assert 'ALPHA0001' not in svg


def test_cells_label_is_the_fallback_string():
    assert cells_label(('ALPHA0001', '1.2M/yr')) == 'ALPHA0001 (1.2M/yr)'
    assert cells_label(('ALPHA0001',)) == 'ALPHA0001'


def test_every_pie_layout_places_its_legend_at_the_right():
    """`PieChart.add_legend` has no fallback: `draw_table_legend` draws nothing
    for a 'below' placement, so a pie profile must never ask for one."""
    from webapp.dashboards import charts
    from webapp.dashboards.charts.pie import PieChart
    pies = [fn.chart_class for fn in vars(charts).values()
            if issubclass(getattr(fn, 'chart_class', type), PieChart)]
    assert len(pies) >= 7
    for cls in pies:
        assert {lay.legend_placement for lay in cls.LAYOUTS.values()} == {'right'}, cls.__name__


def test_legend_cells_has_one_signature_across_the_families():
    """`(label, value)` everywhere: a name alone when the row has no number."""
    import inspect
    from webapp.dashboards import charts
    for fn in vars(charts).values():
        cls = getattr(fn, 'chart_class', None)
        if cls is not None:
            assert list(inspect.signature(cls.legend_cells).parameters) == [
                'self', 'label', 'value'], cls.__name__
    assert BaseChart().legend_cells('alice', None) == ('alice',)
    assert BaseChart().legend_cells('alice', 1234) == ('alice', '1,234')


def test_the_stacked_legends_carry_the_figure_their_bands_are_ranked_by():
    from webapp.dashboards.charts import stacked
    from webapp.dashboards.charts.series import Series
    band = Series('alice', [2 * 1024 ** 4, 3 * 1024 ** 4], 'alice')
    trend = stacked.UsageTrendStackedChart({})
    assert trend.table_legend and trend.legend_cells('alice', trend.band_value(Series('a', [5, 20, 30]))) == (
        'alice', '55')                                        # the window total
    timeline = stacked.JobsTimeseriesChart({})
    assert timeline.table_legend and timeline.band_value(Series('a', [3, 4])) == 7
    disk = stacked.DiskUsageAreaChart({}, metric='bytes')
    assert disk.table_legend and disk.legend_cells('alice', disk.band_value(band)) == (
        'alice', '3.00 TiB')                                  # the latest scan, as a size
    files = stacked.DiskUsageAreaChart({}, metric='files')
    assert files.legend_cells('alice', 1234) == ('alice', '1,234')
