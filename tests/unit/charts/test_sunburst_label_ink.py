"""Wedge-label ink: the Allocations sunburst labels in white on light; everything else picks per wedge."""
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

from webapp.dashboards.charts import AllocationSunburst, FairShareSunburst, JobsFacilitySunburst  # noqa: E402
from webapp.dashboards.charts.sunburst import label_wedges  # noqa: E402
from webapp.dashboards.charts.theme import THEMES, UNITY_NCAR_SPACE_BLUE  # noqa: E402

LIGHT_WEDGE = '#fdd509'   # gold: the per-wedge pick is space-blue


def test_allocation_sunburst_is_white_in_light_and_per_wedge_in_dark():
    chart = AllocationSunburst([])
    assert chart.label_ink(THEMES['light']) == THEMES['light'].surface == '#ffffff'
    assert chart.label_ink(THEMES['dark']) is None


def test_other_sunbursts_keep_the_per_wedge_pick():
    for cls in (JobsFacilitySunburst, FairShareSunburst):
        assert cls([]).label_ink(THEMES['light']) is None, cls.__name__


def _label_colors(ink=None):
    fig, ax = plt.subplots()
    try:
        wedges, _ = ax.pie([1, 1], colors=[LIGHT_WEDGE, LIGHT_WEDGE])
        for text in list(ax.texts):
            text.remove()
        label_wedges(ax, wedges, ['A', 'B'], [50, 50], [LIGHT_WEDGE] * 2, 0.6, 1, 8, ink=ink)
        return {t.get_color() for t in ax.texts}
    finally:
        plt.close(fig)


def test_label_wedges_honors_one_ink():
    assert _label_colors() == {UNITY_NCAR_SPACE_BLUE}
    assert _label_colors(ink='#fff') == {'#fff'}
