"""Chart styling: fonts, structural rcParams, the Unity palettes, and `Theme`.

Imported first by ``charts/__init__.py`` because two of its effects are
import-time and global: registering the server-side Poppins TTFs with
matplotlib's font manager, and applying the rcParams that give every chart the
editorial flat look. Nothing else in the package may rely on import order.

**Data colours vs chrome colours.** The ``UNITY_*`` palettes below encode data
— a wedge, a stack band, a bar — and are theme-invariant in *hue*: a project
keeps its colour whether the page is light or dark. Everything a `Theme`
carries is *chrome*: text, spines, grid, edges, the shading blend target. That
split is what lets a dark theme be a mechanical swap rather than a redesign.

The one place the split leaks is `Theme.data_color`, and it is a leak the
chart-architecture plan predicted (Appendix B, "needs a design decision, not a
mechanical swap"): three of the ten pie colours are brand *darks*, and a dark
fill on a dark card is not a colour choice, it is an invisible wedge. See
`lift_for_contrast` for what is done about it and why hue survives.
"""

from dataclasses import dataclass
from pathlib import Path

import matplotlib
import matplotlib.colors
import matplotlib.font_manager
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Unity NCAR chart styling — runs once at module import.
#
# Two pieces:
#   1. Register Poppins TTFs with matplotlib's font manager. Skipped silently
#      if the directory is empty / missing, so the import still works in
#      environments where the static assets haven't been deployed yet.
#
#      NOTE: these .ttf files are a deliberate SERVER-SIDE copy for matplotlib
#      and are NOT the same assets the browser uses. The browser loads the
#      vendored .woff2 set under static/vendor/poppins/ (see vendor_assets.py);
#      matplotlib's font_manager cannot read woff2 (only ttf/otf/afm), so it
#      needs its own ttf set here. Do NOT delete static/fonts/poppins/*.ttf as
#      "unreferenced" — they are referenced here, and a regression test
#      (test_chart_fonts.py) asserts findfont('Poppins') still resolves.
#   2. Apply rcParams that mirror the editorial flat look on the HTML side:
#      Poppins text, space-blue chrome, hairline gray grid, no top/right
#      spines, transparent figure/axes (we already savefig with
#      transparent=True so legend/grid colors carry against any backdrop).
# ---------------------------------------------------------------------------

# parents[2] is src/webapp/ — this module is webapp/dashboards/charts/theme.py.
# Counting up from __file__ is fragile under exactly the kind of move that
# created this package, so test_chart_fonts asserts the registered font
# actually resolves inside this directory rather than to a system font.
_FONT_DIR = Path(__file__).resolve().parents[2] / 'static' / 'fonts' / 'poppins'
if _FONT_DIR.exists():
    for _ttf in _FONT_DIR.glob('*.ttf'):
        matplotlib.font_manager.fontManager.addfont(str(_ttf))

# Chrome colours here are the LIGHT theme's values, duplicated as literals
# because the named constants are defined below. `Theme.LIGHT` restates them,
# and `test_theme.py` asserts the two agree — so a dark theme never has to
# fight a stale global. See `Theme` for why per-request theming must NOT go
# through rcParams.
plt.rcParams.update({
    'font.family':        ['Poppins', 'DejaVu Sans'],   # fallback if Poppins missing
    'font.size':          11,
    'axes.titleweight':   600,
    'axes.titlecolor':    '#011837',   # ncar-space-blue
    'axes.labelcolor':    '#011837',
    'axes.labelweight':   600,
    'axes.edgecolor':     '#011837',
    'axes.spines.top':    False,
    'axes.spines.right':  False,
    'xtick.color':        '#011837',
    'ytick.color':        '#011837',
    # Legend labels and any bare ax.text(). NEVER SET until C12, so every
    # legend in every chart rendered in matplotlib's default pure black while
    # the rest of the chrome was space-blue — subtle enough that nobody
    # noticed for a year, and invisible in a diff until the palette had one
    # home.
    'text.color':         '#011837',
    'grid.color':         '#bbbcbc',   # ncar-gray-light
    'grid.alpha':         0.4,
    'grid.linewidth':     0.5,
    'legend.fontsize':    11,
    'legend.frameon':     False,
    'figure.facecolor':   'none',
    'axes.facecolor':     'none',
    # Emit real <text> elements instead of converting every glyph to a path
    # outline. matplotlib's default is 'path', which is what you want for a
    # standalone file that must render without the font installed — but our
    # SVGs are always inlined into a page that already loads Poppins as woff2
    # (static/vendor/poppins/, see vendor_assets.py).
    #
    # Buys, in order of how much we care:
    #   - 40-77% smaller SVGs, which is Redis memory (chart entries are the
    #     bulk of it) and page weight on every dashboard.
    #   - chart text becomes selectable, searchable and screen-readable.
    #   - label colour becomes a real CSS-addressable property, which is what
    #     dark mode will need.
    #   - the font-weight request reaches the browser, so `axes.labelweight:
    #     600` picks vendored Poppins SemiBold rather than matplotlib's
    #     "Failed to find font weight 600, now using 700" fallback.
    #
    # The tradeoff: matplotlib still computes the bbox_inches='tight' box from
    # its own TTF metrics while the browser lays the glyphs out from the
    # woff2, so long labels can differ by a pixel or two. Verified in the
    # browser at 1280px across every chart surface. Reverting is this one
    # line.
    'svg.fonttype':       'none',
})


# Unity NCAR palette ordered for chart use. Indices 0-2 are the brand spine
# (blue → navy → vermilion); 3-4 the warm accents (gold, orange); 5-7 the
# teal family (teal, sky, light-blue); 8-9 are tertiary fillers. Sequential
# visual distinction at small sizes (pie wedges).
UNITY_PALETTE_10 = (
    '#0057c2',  # ncar-blue
    '#00357a',  # ncar-navy
    '#ff1f1f',  # ncar-vermilion
    '#fdd509',  # ncar-gold
    '#faa119',  # ncar-orange
    '#00818F',  # ncar-teal
    '#42C0FF',  # ncar-sky
    '#00A2B4',  # ncar-light-blue
    '#011837',  # ncar-space-blue
    '#97999b',  # ncar-gray
)

UNITY_NCAR_BLUE       = '#0057c2'
UNITY_NCAR_NAVY       = '#00357a'
UNITY_NCAR_VERMILION  = '#ff1f1f'
UNITY_NCAR_ORANGE     = '#faa119'
UNITY_NCAR_GOLD       = '#fdd509'
UNITY_NCAR_TEAL       = '#00818F'
UNITY_NCAR_SKY        = '#42C0FF'
UNITY_NCAR_LIGHT_BLUE = '#00A2B4'
UNITY_NCAR_SPACE_BLUE = '#011837'
UNITY_NCAR_GRAY_LIGHT = '#bbbcbc'
UNITY_NCAR_GRAY       = '#97999b'


# Stacked-area categorical palette. Family-grouped: each color family's
# shades sit adjacent (gold→yellow-33→yellow-66, orange→orange-33→…),
# then we move to the next family. Within a family, ordered saturated →
# pale. Ordered warm → cool so the highest-rank bands (which stackplot
# puts at the bottom, visually most prominent) get the loudest warm
# anchors (gold, orange, vermilion), then transition through teal /
# sky / blue / navy as rank decreases.
UNITY_STACK_20 = (
    # Gold family — bright warm anchor, highest visual prominence
    '#fdd509',   # 1.  gold
    '#fbe174',   # 2.  yellow-33
    '#f8ebb7',   # 3.  yellow-66

    # Orange family
    '#faa119',   # 4.  orange
    '#fabe72',   # 5.  orange-33
    '#f8dbb5',   # 6.  orange-66

    # Vermilion (single; no lighter variant in Unity's secondary ladder)
    '#ff1f1f',   # 7.  vermilion

    # Teal family — warm-cool transition
    '#00818F',   # 8.  teal
    '#00a2b4',   # 9.  ucar-base-33
    '#71c0cb',   # 10. ucar-base-66

    # Sky / cyan family
    '#42c0ff',   # 11. sky
    '#86d3fc',   # 12. ncar-light-33
    '#34e1f4',   # 13. ucar-light (cyan)
    '#86e8f5',   # 14. ucar-light-33

    # Blue family (deep cool)
    '#0057c2',   # 15. ncar-blue
    '#5a77a6',   # 16. blue-33
    '#a8b7ce',   # 17. blue-66
    '#adc2e6',   # 18. ncar-base-66

    # Navy / slate family
    '#00357a',   # 19. navy
    '#556379',   # 20. space-blue-33
)

# 10-color variant: distinct tuple (NOT UNITY_STACK_20[:10], which would be
# 3 golds + 3 oranges + vermilion + 3 teals — too warm-loaded). Picks 2
# shades from each main hue family plus vermilion, in the same warm-to-cool
# order as _20 so the same chart looks like a subsetted version, not a
# different palette.
UNITY_STACK_10 = (
    '#fdd509',   # 1.  gold
    '#fbe174',   # 2.  yellow-33
    '#faa119',   # 3.  orange
    '#fabe72',   # 4.  orange-33
    '#ff1f1f',   # 5.  vermilion
    '#00818F',   # 6.  teal
    '#00a2b4',   # 7.  ucar-base-33
    '#42c0ff',   # 8.  sky
    '#0057c2',   # 9.  ncar-blue
    '#5a77a6',   # 10. blue-33
)


# ---------------------------------------------------------------------------
# Theme — the chrome axis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Theme:
    """Chrome colours for one rendering of a chart.

    Threaded explicitly through `draw`/`decorate`/`add_legend` and applied
    per-artist. It deliberately does NOT go through rcParams:

    - **`plt.rc_context` is thread-unsafe here.** It mutates the global
      `rcParams` dict and restores on exit with no thread isolation. Gunicorn
      runs `gthread` with 4 threads (containers/webapp/gunicorn_config.py),
      so two concurrent requests rendering light and dark would interleave and
      cross-contaminate.
    - **Serialising renders behind a lock** would kill concurrency on a pool
      explicitly tuned for I/O overlap.
    - **Rendering both themes and letting CSS choose** doubles render cost and
      cache size for a feature most users will use one of.

    Per-artist application is mechanical, thread-safe and testable, which is
    worth more here than brevity.

    `LIGHT` reproduces today's rcParams values exactly, so this is inert until
    a caller asks for something else. Nothing requests `DARK` yet — chart dark
    mode is blocked on app dark mode (charts sit inside cards whose background
    is hardcoded white), and lands in a later PR.
    """

    name: str
    text: str            # tick labels, axis labels, titles, legend text
    spine: str
    grid: str
    bar_edge: str        # bar/segment outlines
    segment_edge: str    # inner edges of a stacked bar's segments
    legend_face: str | None   # dual-panel framed legends only
    shade_toward: str    # `shade_family` blend target
    accent: str          # the pace chart's "today" marker

    #: The colour actually behind the figure. Every chart is saved with
    #: ``transparent=True``, so the "background" of a chart is whichever card
    #: the SVG is inlined into — `--surface-card` in `static/css/variables.css`.
    #: Distinct from `shade_toward` despite sharing a value in both themes:
    #: that one is a *blend target* a family may point elsewhere, this one is a
    #: statement of fact about the page.
    surface: str

    #: Minimum contrast a data fill must reach against `surface`, or None to
    #: leave data colours exactly as the palette declares them. See
    #: `lift_for_contrast`.
    min_data_contrast: float | None

    #: Fill for the inert aggregate band — the "Others" / "Other (N projects)"
    #: remainder every family collapses its tail into.
    #:
    #: A *role*, not a colour, and the reason it needs one is that its job is
    #: to **recede**. `--ncar-gray-light` recedes against a white card (1.90:1)
    #: and shouts against a dark one (7.97:1), where it is routinely the
    #: largest band on the chart — so on dark it became the loudest thing in
    #: the picture while meaning the least. The dark value is picked to sit at
    #: 1.77:1, i.e. to be as recessive as the light one is, and it must NOT go
    #: through `data_color`: `min_data_contrast` would lift it straight back
    #: up to 3:1 and undo exactly what it is for. Lift the palette, then place
    #: this beside it.
    muted_data: str

    #: Alpha for stacked *areas*. A property of the theme rather than the
    #: chart, because the figure is transparent and the alpha therefore
    #: composites against `surface`: 0.85 over white is the light page's
    #: deliberate softening, and the same 0.85 over `#1b2733` drags every band
    #: back down toward the card and undoes `min_data_contrast`.
    area_alpha: float

    @property
    def is_dark(self) -> bool:
        return self.name == 'dark'

    # --- data colours ------------------------------------------------------

    def data_color(self, color):
        """One palette colour, made legible against this theme's `surface`.

        Identity — the *same object* — whenever `min_data_contrast` is None,
        which is how `Theme.LIGHT` guarantees byte-identical output rather
        than merely equivalent output.
        """
        if self.min_data_contrast is None:
            return color
        return lift_for_contrast(color, self.surface, self.min_data_contrast)

    def data_colors(self, colors):
        """`data_color` over a sequence, preserving order and length."""
        if self.min_data_contrast is None:
            return colors
        return [self.data_color(c) for c in colors]


#: Today's rendering, exactly. Every value here is the literal the charts
#: used before the theme axis existed.
Theme.LIGHT = Theme(
    name='light',
    text=UNITY_NCAR_SPACE_BLUE,
    spine=UNITY_NCAR_SPACE_BLUE,
    grid=UNITY_NCAR_GRAY_LIGHT,
    bar_edge=UNITY_NCAR_NAVY,
    segment_edge='white',
    legend_face='white',
    shade_toward='#ffffff',
    accent=UNITY_NCAR_NAVY,
    surface='#ffffff',
    # **None, not 3.0.** Four of the palette's warm colours (gold at 1.43:1,
    # yellow-33 at 1.30:1, orange and sky at 2.06:1) have never cleared 3:1
    # against a white card and are not defects — a gold wedge outlined by its
    # neighbours reads fine, and "fixing" them would darken the brand palette
    # on every existing page. The floor exists for the dark surface, where
    # the failing colours are *text-dark navies* rather than brand yellows.
    min_data_contrast=None,
    area_alpha=0.85,
    muted_data=UNITY_NCAR_GRAY_LIGHT,
)

# The dark rendering. Chrome values were chosen in PR 1; the two data-colour
# decisions PR 1 deferred are answered by `min_data_contrast` and
# `area_alpha` above.
#:
# `surface` is `--surface-card` from `static/css/variables.css`, and
# `test_dark_card_matches_chart_blend_target` fails if the two drift — a
# chart blending toward a card colour the card no longer has produces a halo
# that no test other than that one would see.
Theme.DARK = Theme(
    name='dark',
    text='#e9ecef',
    spine='#8f9aa8',
    grid='#4a5561',
    bar_edge='#1b2733',
    segment_edge='#1b2733',
    legend_face='#1b2733',
    shade_toward='#1b2733',
    accent='#adc2e6',
    surface='#1b2733',
    # WCAG 2.1 SC 1.4.11 (Non-text Contrast) — 3:1 for a graphical object you
    # need to see to understand the content, which is what a pie wedge is.
    min_data_contrast=3.0,
    # 1.0, not 0.85 — see the field docstring. The softening the light page
    # wants is the thing that makes a dark page muddy.
    area_alpha=1.0,
    # 1.77:1 against `surface`, mirroring the 1.90:1 `--ncar-gray-light` has
    # against a white card. Deliberately not `grid` (2.00:1) despite the
    # family resemblance — a data band the exact colour of the gridlines
    # reads as a rendering fault.
    muted_data='#434d59',
)

THEMES = {'light': Theme.LIGHT, 'dark': Theme.DARK}


def resolve_theme(theme) -> Theme:
    """Accept a `Theme`, a name, or None → `Theme.LIGHT`.

    Lenient like the route-level selector parsers (`jobs/routes.py`
    `_parse_period`): an unknown name means "no override", never an error. A
    chart is a fragment, and a stale localStorage replay must not 500 a card.
    """
    if isinstance(theme, Theme):
        return theme
    return THEMES.get(theme or 'light', Theme.LIGHT)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def relative_luminance(color) -> float:
    """WCAG 2.1 relative luminance of any matplotlib colour.

    The same formula `e2e/test_dark_mode.py` runs in the browser, so a colour
    this module lifts and a colour Playwright measures are judged identically.
    """
    def channel(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = matplotlib.colors.to_rgb(color)
    return (0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b))


def contrast_ratio(a, b) -> float:
    """WCAG 2.1 contrast ratio between two colours, ignoring alpha."""
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def lift_for_contrast(color, surface, min_ratio: float):
    """*color*, tinted toward white just far enough to clear *min_ratio*.

    Returned unchanged (identity) when it already clears, which is the common
    case: on the dark card this touches 3 of the 10 pie colours, 1 of the 10
    stack colours and 3 of the 20 — the brand *darks*, `#011837` at 1.17:1 and
    `#00357a` at 1.29:1, which are text colours pressed into service as fills.

    **Why tint toward white rather than raise HSL lightness.** Both restore
    contrast; only one keeps the palette usable. The three failing pie colours
    are all blues, and lifting each to exactly 3:1 by lightness converges them
    on `#0469f0` / `#0068ef` / `#0069eb` — three names for one blue, in a
    palette whose entire job is telling ten things apart. Tinting lands them
    on `#627083` / `#4c72a2` / `#246fcb`, which desaturates as it lightens and
    therefore keeps them separable. Measured: this leaves every palette's
    minimum pairwise channel distance at or above what it already was, so
    nothing becomes harder to tell apart than it is today.

    Alpha is carried through untouched — the ratio is a property of the fill,
    and the one translucent constant in the package (the pace chart's
    "Other" band) clears the floor anyway.
    """
    if contrast_ratio(color, surface) >= min_ratio:
        return color

    r, g, b, a = matplotlib.colors.to_rgba(color)
    # Walk in 8-bit steps: the SVG is 8-bit per channel regardless, so a finer
    # search would return values that render identically, and integer steps
    # make the result reproducible across platforms and matplotlib versions.
    for step in range(1, 256):
        f = step / 255
        lifted = tuple(round(255 * (c + (1 - c) * f)) / 255 for c in (r, g, b))
        if contrast_ratio(lifted, surface) >= min_ratio:
            return lifted + (a,)
    return (1.0, 1.0, 1.0, a)


def autopct_color_for(color) -> str:
    """Pick a readable text color for percent labels on a colored pie wedge.

    Returns space-blue on light wedges (gold, sky) and white on dark wedges
    (blue, navy, vermilion). Luminance threshold ~0.6 — empirically tuned
    against UNITY_PALETTE_10.

    Theme-invariant on purpose: the choice is driven by the *wedge* luminance,
    not the page, so it is already correct in both themes — and it stays
    correct for free once `Theme.data_color` lifts a wedge, because a lifted
    wedge is a lighter wedge and this flips to space-blue on its own.

    Accepts any matplotlib colour rather than only a hex string, because a
    lifted wedge arrives as an RGBA tuple.
    """
    r, g, b = matplotlib.colors.to_rgb(color)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return UNITY_NCAR_SPACE_BLUE if lum > 0.6 else '#fff'


def shade_family(base_hex, n, lightest=0.66, toward='#ffffff'):
    """``n`` colors blended from a light tint (index 0) to *base_hex* (index n-1).

    Gives each stacked bar a single-hue gradient: the bottom "other" segment
    is the palest, the top (largest) owner the boldest base color.

    ``toward`` is the blend target — `Theme.shade_toward`. On a dark page the
    pale end has to move toward the page colour, not white, or the "palest"
    segment becomes the loudest thing in the chart.
    """
    base = np.array(matplotlib.colors.to_rgb(base_hex))
    target = np.array(matplotlib.colors.to_rgb(toward))
    if n <= 1:
        return [tuple(base)]
    return [
        tuple(base * (1 - f) + target * f)
        for j in range(n)
        for f in (lightest * (1 - j / (n - 1)),)
    ]


# ---------------------------------------------------------------------------
# Byte-scale ladder
# ---------------------------------------------------------------------------

BYTES_PER_GIB = 1024 ** 3
BYTES_PER_TIB = 1024 ** 4
BYTES_PER_PIB = 1024 ** 5

_LADDER = (
    (BYTES_PER_PIB, 'PiB'),
    (BYTES_PER_TIB, 'TiB'),
    (BYTES_PER_GIB, 'GiB'),
)


def scale_bytes(peak, floor='GiB'):
    """``(divisor, unit_label)`` for a byte axis whose maximum is *peak*.

    The two call sites had subtly different ladders and this preserves both
    rather than unifying them, because the difference is real:

    - ``floor='TiB'`` (disk-usage timeseries) — PiB or TiB only. A sub-TiB
      series renders as a fractional TiB axis (0.004 TiB), which is the
      current behaviour and is deliberate: that chart's readers think in TiB.
    - ``floor='GiB'`` (distribution histogram) — PiB / TiB / GiB.
    """
    floor_idx = [unit for _div, unit in _LADDER].index(floor)
    rungs = _LADDER[:floor_idx + 1]
    for div, unit in rungs[:-1]:
        if peak >= div:
            return div, unit
    return rungs[-1]
