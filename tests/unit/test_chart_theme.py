"""Tests for `webapp.dashboards.charts.theme`."""

import matplotlib.pyplot as plt
import pytest

from webapp.dashboards.charts import theme as T


class TestThemeLight:
    """`Theme.LIGHT` must reproduce today's global rcParams exactly.

    The rcParams block still carries the light theme's chrome as literals,
    because it runs at import before any request picks a theme. If the two
    ever disagree, a light-themed chart would draw some artists from the
    global and some from the Theme, and the difference would show up as a
    subtle two-tone chart rather than an error.
    """

    def test_text_matches_rcparams(self):
        assert T.Theme.LIGHT.text == plt.rcParams['axes.labelcolor']
        assert T.Theme.LIGHT.text == plt.rcParams['xtick.color']
        assert T.Theme.LIGHT.text == plt.rcParams['ytick.color']
        assert T.Theme.LIGHT.text == plt.rcParams['axes.titlecolor']

    def test_spine_matches_rcparams(self):
        assert T.Theme.LIGHT.spine == plt.rcParams['axes.edgecolor']

    def test_grid_matches_rcparams(self):
        assert T.Theme.LIGHT.grid == plt.rcParams['grid.color']

    def test_light_is_the_default(self):
        assert T.resolve_theme(None) is T.Theme.LIGHT
        assert T.resolve_theme('light') is T.Theme.LIGHT

    def test_is_dark_flag(self):
        assert not T.Theme.LIGHT.is_dark
        assert T.Theme.DARK.is_dark


class TestResolveTheme:
    def test_accepts_a_theme_instance(self):
        assert T.resolve_theme(T.Theme.DARK) is T.Theme.DARK

    def test_accepts_a_name(self):
        assert T.resolve_theme('dark') is T.Theme.DARK

    @pytest.mark.parametrize('bogus', ['sepia', '', 'LIGHT', 'Dark', 'none'])
    def test_unknown_name_falls_back_silently(self, bogus):
        """Lenient like the route-level selector parsers: an unknown value
        means "no override", never a 400. A chart is an htmx fragment, and a
        stale localStorage replay must not break a card."""
        assert T.resolve_theme(bogus) is T.Theme.LIGHT

    def test_themes_are_frozen(self):
        with pytest.raises(Exception):
            T.Theme.LIGHT.text = '#000000'


class TestScaleBytes:
    """The two call sites have deliberately different ladders."""

    @pytest.mark.parametrize('peak,expect', [
        (5 * T.BYTES_PER_PIB, (T.BYTES_PER_PIB, 'PiB')),
        (T.BYTES_PER_PIB, (T.BYTES_PER_PIB, 'PiB')),
        (900 * T.BYTES_PER_TIB, (T.BYTES_PER_TIB, 'TiB')),
        (T.BYTES_PER_TIB, (T.BYTES_PER_TIB, 'TiB')),
        # Below a TiB this ladder does NOT drop to GiB — it reports a
        # fractional TiB. Preserved behaviour, not an oversight.
        (5 * T.BYTES_PER_GIB, (T.BYTES_PER_TIB, 'TiB')),
        (0, (T.BYTES_PER_TIB, 'TiB')),
    ])
    def test_tib_floor_has_two_rungs(self, peak, expect):
        assert T.scale_bytes(peak, floor='TiB') == expect

    @pytest.mark.parametrize('peak,expect', [
        (5 * T.BYTES_PER_PIB, (T.BYTES_PER_PIB, 'PiB')),
        (900 * T.BYTES_PER_TIB, (T.BYTES_PER_TIB, 'TiB')),
        (5 * T.BYTES_PER_GIB, (T.BYTES_PER_GIB, 'GiB')),
        (0, (T.BYTES_PER_GIB, 'GiB')),
    ])
    def test_gib_floor_has_three_rungs(self, peak, expect):
        assert T.scale_bytes(peak, floor='GiB') == expect

    def test_default_floor_is_gib(self):
        assert T.scale_bytes(0) == (T.BYTES_PER_GIB, 'GiB')


class TestShadeFamily:
    def test_last_shade_is_the_base_color(self):
        shades = T.shade_family('#0057c2', 4)
        assert shades[-1] == pytest.approx((0x00 / 255, 0x57 / 255, 0xc2 / 255))

    def test_single_segment_returns_base(self):
        assert len(T.shade_family('#0057c2', 1)) == 1

    def test_blends_toward_white_by_default(self):
        """Index 0 is the palest. With the default target it must be closer
        to white than the base is."""
        base, pale = T.shade_family('#0057c2', 3)[-1], T.shade_family('#0057c2', 3)[0]
        assert sum(pale) > sum(base)

    def test_blend_target_is_configurable(self):
        """The dark theme blends toward the page colour, not white — a
        white-blended 'palest' segment would be the loudest thing on a dark
        chart."""
        dark = T.shade_family('#0057c2', 3, toward='#1b2733')[0]
        light = T.shade_family('#0057c2', 3, toward='#ffffff')[0]
        assert sum(dark) < sum(light)


class TestAutopctColor:
    @pytest.mark.parametrize('wedge,expect', [
        ('#fdd509', T.UNITY_NCAR_SPACE_BLUE),   # gold — light
        ('#42C0FF', T.UNITY_NCAR_SPACE_BLUE),   # sky — light
        ('#00357a', '#fff'),                    # navy — dark
        ('#ff1f1f', '#fff'),                    # vermilion — dark
        ('#011837', '#fff'),                    # space blue — dark
    ])
    def test_picks_by_wedge_luminance(self, wedge, expect):
        assert T.autopct_color_for(wedge) == expect

    def test_is_theme_invariant(self):
        """Driven by the wedge, not the page — so it needs no theme argument
        and is already correct for dark mode.

        Asserts the *property* (one argument, and it is not a theme) rather
        than that argument's spelling: it was renamed `bg_hex` -> `color` when
        wedge colours started arriving as lifted RGBA tuples, and a test that
        fails on a rename while a real theme argument would slip past it is
        pinning the wrong thing.
        """
        import inspect
        params = list(inspect.signature(T.autopct_color_for).parameters)
        assert len(params) == 1 and 'theme' not in params

    def test_accepts_a_lifted_rgba_wedge(self):
        """`Theme.data_color` hands back RGBA tuples for the colours it lifts,
        and every one of them is a pie wedge that still needs a label."""
        lifted = T.Theme.DARK.data_color(T.UNITY_NCAR_SPACE_BLUE)
        assert not isinstance(lifted, str)          # guard the premise
        assert T.autopct_color_for(lifted) in (T.UNITY_NCAR_SPACE_BLUE, '#fff')


class TestPalettes:
    def test_stack_10_is_not_a_prefix_of_stack_20(self):
        """UNITY_STACK_10 is a curated pick, not UNITY_STACK_20[:10], which
        would be 3 golds + 3 oranges + vermilion + 3 teals."""
        assert T.UNITY_STACK_10 != T.UNITY_STACK_20[:10]

    def test_palette_sizes(self):
        assert len(T.UNITY_PALETTE_10) == 10
        assert len(T.UNITY_STACK_10) == 10
        assert len(T.UNITY_STACK_20) == 20

    def test_named_scalars_appear_in_palette_10(self):
        for name in ('BLUE', 'NAVY', 'VERMILION', 'GOLD', 'ORANGE', 'TEAL',
                     'SKY', 'LIGHT_BLUE', 'SPACE_BLUE', 'GRAY'):
            value = getattr(T, f'UNITY_NCAR_{name}')
            assert value in T.UNITY_PALETTE_10, f'{name} ({value}) not in the pie palette'
