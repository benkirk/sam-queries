"""The `layout` axis as behaviour, not as a snapshot.

`test_chart_fingerprints` pins what mobile renders *today*; regenerating it is
a one-line command, so on its own it records the tuning rather than defending
it. These are the claims that must survive any future retuning:

- every family declares its own mobile figure (no derived default);
- desktop is the identity — its profile reproduces pre-axis rendering;
- a capped legend still points its drill links at the right swatches;
- and the axis reaches the leaves, i.e. no chart quietly ignores it.

The last one is the reason this file exists. PR 1 shipped six `Layout` fields
of which two were read by nothing and three by one or two charts of fifteen,
and every test still passed — because "renders without raising" is all a
smoke test can see.
"""

import inspect

import pytest

from webapp.dashboards import charts
from webapp.dashboards.charts import dualpanel, histogram, pace, pie, stacked
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.layout import (
    MOBILE_DEFAULTS, Layout, profile, resolve_layout,
)

#: Every class in the package that declares its own profile.
LAYOUT_OWNERS = [
    pie.PieChart,
    stacked.StackedSeriesChart,
    histogram.CategoricalStackChart,
    dualpanel.NodetypeHistoryChart,
    dualpanel.QueueHistoryChart,
    pace.PaceChart,
]


def _chart_classes():
    return {n: fn.chart_class for n, fn in vars(charts).items()
            if n.startswith('generate_') and hasattr(fn, 'chart_class')}


# --------------------------------------------------------------------------
# profile()
# --------------------------------------------------------------------------

class TestProfile:

    def test_mobile_figsize_is_required(self):
        """It used to default to the desktop aspect ratio at 4.5in wide, which
        turns 18:5 into a strip with no room for a plot once the legend moves
        underneath. Making it positional means a new family cannot inherit
        that mistake by omission."""
        with pytest.raises(TypeError):
            profile((18, 5))

    def test_keywords_configure_desktop_not_mobile(self):
        """The bug this signature was changed to prevent.

        `label_rotation=30` reads like a mobile override and is not one. When
        mobile overrides were collected with `**kwargs`, any name colliding
        with a desktop parameter was silently applied to desktop instead —
        the override looked accepted and did nothing.
        """
        p = profile((18, 5), (4.6, 3.2), label_rotation=30)
        assert p['desktop'].label_rotation == 30
        assert p['mobile'].label_rotation == MOBILE_DEFAULTS['label_rotation']

    def test_mobile_dict_overrides_the_defaults(self):
        p = profile((7, 4), (4.0, 3.2), mobile={'legend_placement': 'right'})
        assert p['mobile'].legend_placement == 'right'
        assert p['desktop'].legend_placement == 'right'
        # Untouched keys still come from MOBILE_DEFAULTS.
        assert p['mobile'].max_legend_entries == MOBILE_DEFAULTS['max_legend_entries']

    def test_unknown_mobile_override_is_rejected(self):
        """A typo'd field name would otherwise be dropped in silence, which is
        exactly how `legend_placement` came to look wired when it wasn't."""
        with pytest.raises(TypeError, match='unknown Layout field'):
            profile((7, 4), (4.0, 3.2), mobile={'legend_size': 9})

    def test_desktop_legend_fontsize_is_none(self):
        """None means "defer to the chart", which is what keeps desktop
        byte-identical across four different per-family legend sizes."""
        for cls in LAYOUT_OWNERS:
            assert cls.LAYOUTS['desktop'].legend_fontsize is None, cls.__name__


class TestResolveLayout:

    def test_unknown_name_falls_back_silently(self):
        layouts = pie.PieChart.LAYOUTS
        assert resolve_layout(layouts, 'sideways') is layouts['desktop']
        assert resolve_layout(layouts, None) is layouts['desktop']

    def test_a_layout_object_passes_through(self):
        lay = pie.PieChart.LAYOUTS['mobile']
        assert resolve_layout(pie.PieChart.LAYOUTS, lay) is lay


# --------------------------------------------------------------------------
# The profiles themselves
# --------------------------------------------------------------------------

class TestProfiles:

    @pytest.mark.parametrize('cls', LAYOUT_OWNERS, ids=lambda c: c.__name__)
    def test_mobile_figure_is_narrower_and_phone_sized(self, cls):
        desktop = cls.LAYOUTS['desktop'].figsize
        mobile = cls.LAYOUTS['mobile'].figsize
        assert mobile[0] < desktop[0], f'{cls.__name__} mobile is not narrower'
        # ~4.6in of figure lands near 350pt after the tight bbox, which is
        # about a phone viewport once card padding is off. Wider and the
        # browser scales it back down, which is the whole defect.
        assert 3.5 <= mobile[0] <= 5.0, f'{cls.__name__} mobile width {mobile[0]}'

    @pytest.mark.parametrize('cls', LAYOUT_OWNERS, ids=lambda c: c.__name__)
    def test_mobile_is_not_the_desktop_aspect_ratio(self, cls):
        """Preserving the ratio is what produced a 4.5 x 1.25in strip."""
        d, m = cls.LAYOUTS['desktop'].figsize, cls.LAYOUTS['mobile'].figsize
        assert abs(m[1] / m[0] - d[1] / d[0]) > 0.01, cls.__name__

    def test_every_bound_chart_reaches_a_profile(self):
        for name, cls in _chart_classes().items():
            assert cls.LAYOUTS, f'{name} ({cls.__name__}) has no LAYOUTS'
            assert set(cls.LAYOUTS) == {'desktop', 'mobile'}, name


# --------------------------------------------------------------------------
# The fields actually reach the leaves
# --------------------------------------------------------------------------

class TestFieldsAreConsumed:
    """Guards against the PR-1 state: fields declared, nothing reading them.

    Asserted through rendered output rather than by grepping for attribute
    access, because a field can be read and then discarded.
    """

    def _svgs(self, app, case):
        _id, fn, args, kwargs = case
        with app.test_request_context('/'):
            return (fn(*args, **kwargs),
                    fn(*args, **kwargs, layout='mobile'))

    def _case(self, name):
        from chart_samples import CASES
        return next(c for c in CASES if c[0] == name)

    def test_legend_placement_moves_the_legend(self, app):
        """A below-legend widens the figure less and lengthens it more. The
        aspect ratio is the observable: a side legend adds width."""
        import re
        _d, mobile = self._svgs(app, self._case('user_proj_area.project_current'))
        w, h = (float(x) for x in
                re.search(r'width="([\d.]+)pt" height="([\d.]+)pt"', mobile).groups())
        assert h > 0.6 * w, (
            'the stacked family still looks like a strip on mobile — its '
            'legend is probably still beside the plot rather than below it')

    def test_max_legend_entries_caps_pie_slices(self, app):
        """A pie caps its *data*, so no wedge is left unlabelled — and every
        one of these pies is a drill target, so an unlabelled wedge would also
        be an unlabelled click."""
        from chart_samples import CASES
        case = next(c for c in CASES if c[0] == 'disk_entity_pie.owner')
        desktop, mobile = self._svgs(app, case)
        cap = pie.PieChart.LAYOUTS['mobile'].max_legend_entries
        # One <a> per linked wedge, tripled (wedge + swatch + label).
        assert mobile.count('<a ') < desktop.count('<a ')
        assert mobile.count('<a ') <= 3 * cap

    def test_max_legend_entries_clamps_pace_top_n(self, app):
        desktop, mobile = self._svgs(app, self._case('pace.size'))
        assert 'Other (19 projects)' in mobile, (
            'pace did not clamp top_n on mobile — 20 legend rows under a '
            '3.4in figure is taller than the chart')
        assert 'Other (19 projects)' not in desktop

    def test_base_fontsize_reaches_tick_labels(self, app):
        """Applied centrally in `render()`, because a family that forgot would
        be invisible until someone looked at a phone.

        Counted, not merely detected. This chart's axis label *also* takes
        `base_fontsize`, so "the mobile size appears somewhere" stays true
        with tick sizing entirely removed — verified by mutation, and it is
        why the assertion is a count. Ticks outnumber labels roughly 8:1.
        """
        import re
        desktop, mobile = self._svgs(app, self._case('usage_timeseries.charges'))
        d_pt = stacked.StackedSeriesChart.LAYOUTS['desktop'].base_fontsize
        m_pt = stacked.StackedSeriesChart.LAYOUTS['mobile'].base_fontsize
        assert m_pt != d_pt, 'the sizes are equal, so this proves nothing'

        def at(svg, pt):
            return len(re.findall(rf'font-size: ?{pt}(?:\.0)?px', svg))

        assert at(desktop, d_pt) >= 5 and at(desktop, m_pt) == 0
        assert at(mobile, m_pt) >= 5, (
            'only a text or two came out at the mobile size — the axis label '
            'is sized but the tick labels are not')
        assert at(mobile, d_pt) == 0

    def test_max_ticks_thins_the_date_axis(self, app):
        """A dozen date labels do not fit across 4in.

        Counted from the tick groups matplotlib emits, not from label text:
        the smart date axis strips the repeated year, so counting `2026-`
        occurrences (as an earlier version did) now measures the vocabulary
        rather than the tick density.
        """
        import re
        desktop, mobile = self._svgs(app, self._case('usage_stacked.core_hours'))

        def xticks(svg):
            return len(re.findall(r'<g id="xtick_\d+">', svg))

        assert xticks(desktop) > 0, 'no x ticks found — the regex is stale'
        assert xticks(mobile) < xticks(desktop), (
            f'mobile drew {xticks(mobile)} x ticks, desktop {xticks(desktop)}')


# --------------------------------------------------------------------------
# Capping must not misalign drill links
# --------------------------------------------------------------------------

def test_capped_legend_keeps_links_on_the_right_swatches():
    """`link_legend` zips bands against legend patches by position.

    A capped legend is no longer `reversed(bands)`, so the caller must pass
    `ordered=True`. Getting this wrong produces valid-looking hrefs on the
    wrong entries — which the fingerprint cannot catch, because it proves the
    href *strings*, not the artists carrying them.
    """
    src = inspect.getsource(stacked.StackedSeriesChart.add_legend)
    assert 'ordered=True' in src, (
        'add_legend passes a capped, already-reversed list to link_legend; '
        'without ordered=True it gets reversed a second time')


def test_link_legend_ordered_flag_skips_the_reverse():
    class _Band:
        def __init__(self, key):
            self.link_key = key
            self.is_linkable = key is not None

    class _Art:
        def __init__(self):
            self.url = None

        def set_url(self, u):
            self.url = u

    class _Legend:
        def __init__(self, n):
            self._p = [_Art() for _ in range(n)]
            self._t = [_Art() for _ in range(n)]

        def get_patches(self):
            return self._p

        def get_texts(self):
            return self._t

    bands = [_Band('a'), _Band('b'), _Band('c')]
    chart = BaseChart()

    leg = _Legend(3)
    chart.link_legend(leg, bands, lambda k: f'#{k}')
    assert [p.url for p in leg.get_patches()] == ['#c', '#b', '#a']

    leg = _Legend(3)
    chart.link_legend(leg, bands, lambda k: f'#{k}', ordered=True)
    assert [p.url for p in leg.get_patches()] == ['#a', '#b', '#c']


def test_others_band_survives_the_legend_cap():
    """Dropping "Others" would leave a visible grey band with nothing in the
    legend explaining it — worse than dropping a sliver already hard to see."""
    from webapp.dashboards.charts.series import Series

    class _Chart(stacked.StackedSeriesChart):
        pass

    chart = _Chart()
    chart.bands = [Series('Others', [1], None)] + [
        Series(f'p{i}', [1], f'p{i}') for i in range(9)]
    chart.colors = ['#000'] * 10

    entries = chart.legend_entries(_Chart.LAYOUTS['mobile'])
    cap = _Chart.LAYOUTS['mobile'].max_legend_entries
    assert len(entries) == cap
    assert entries[-1][0].label == 'Others'
    assert entries[0][0].label == 'p8'   # largest named band still first


def test_uncapped_layout_returns_every_band():
    from webapp.dashboards.charts.series import Series

    chart = stacked.StackedSeriesChart()
    chart.bands = [Series(f'p{i}', [1], f'p{i}') for i in range(9)]
    chart.colors = ['#000'] * 9
    entries = chart.legend_entries(stacked.StackedSeriesChart.LAYOUTS['desktop'])
    assert len(entries) == 9


# --------------------------------------------------------------------------
# The axis stays keyed
# --------------------------------------------------------------------------

def test_layout_objects_and_names_share_a_cache_slot():
    """`chart_view._key` keys on `layout.name`, so passing the object and
    passing the string must not split the cache."""
    lay = pie.PieChart.LAYOUTS['mobile']
    assert getattr(lay, 'name', lay) == 'mobile'
    assert isinstance(lay, Layout)
