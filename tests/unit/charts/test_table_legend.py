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
