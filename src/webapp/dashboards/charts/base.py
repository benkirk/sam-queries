"""`BaseChart` -- the figure lifecycle, and `chart_view` -- the cache binder.

Shaped after `HtmxFormHandler`: one concrete base with documented hooks and no
abstract parent. Three levels (`BaseChart` -> family -> concrete), because a
fourth abstract/matplotlib layer would own about thirty lines and shield
nothing -- `figsize`, `ax.pie`, `stackplot` and `Artist.set_url` are
matplotlib-shaped all the way to the leaf. The migration seam is bought with
module boundaries instead: `links.py` and `series.py` import no matplotlib,
enforced by test.

    render(layout, theme)
        prepare()            raw payload -> plot-ready state on self
        is_empty() -> empty_state()          short-circuit
        make_figure(layout)  plt.subplots(figsize=layout.figsize)
        apply_tick_fontsize()
        draw(axes, ...)      REQUIRED -- the family draws the marks
        decorate(axes, ...)  labels, ticks, grid, scale
        add_legend(axes, ...)
        finish(fig, axes, ...)
        apply_chrome(...)    theme colors onto every chrome artist
        to_svg(fig)          the single savefig/close chokepoint

Hooks default to no-ops, so a leaf implements only what differs. State goes on
`self` rather than a threaded model object, matching the handler idiom.

WARNING: `BaseChart` must NOT swallow exceptions. Callers own that and do it
inconsistently -- `disk_scans/routes.py` wraps its call while `jobs/routes.py`
does not -- so catching here would silently turn the disk-scans error card into
a blank one.
"""

import functools
import html
import inspect
import re
from io import StringIO

import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredOffsetbox, DrawingArea, HPacker, TextArea, VPacker
from matplotlib.patches import Rectangle

from sam import fmt
from webapp.caching import caching
from webapp.caching.chart import content_hash
from webapp.dashboards.charts.layout import resolve_layout
from webapp.dashboards.charts.theme import resolve_theme


def cells_label(cells) -> str:
    """One legend string from table-legend cells: ``NAME (a, b)``, or the name alone."""
    return f'{cells[0]} ({", ".join(cells[1:])})' if len(cells) > 1 else cells[0]


#: Prefix reserved for `BaseChart.tooltip` gids; none may survive into output.
TOOLTIP_GID = 'tt-'
_TOOLTIP_GROUP = re.compile(r'<g id="' + TOOLTIP_GID + r'(\d+)">(\s*<a [^>]*>)?')


def apply_tooltips(svg: str, tooltips: dict) -> str:
    """Give each `tooltip`-stamped group a ``<title>`` (inside its ``<a>`` when it
    links, which names the link) and drop the throwaway id: one cached SVG can
    appear twice on a page, and ids must be unique."""
    def title(m):
        text = tooltips.get(int(m.group(1)))
        tag = f'<title>{html.escape(text)}</title>' if text else ''
        return f'<g>{m.group(2) or ""}{tag}'
    return _TOOLTIP_GROUP.sub(title, svg) if tooltips else svg


def fig_to_svg(fig, tooltips=None) -> str:
    """Serialize a figure to SVG and ALWAYS close it.

    savefig can raise on pathological data; without the finally the figure
    would leak in the Agg backend's global registry until process restart.
    """
    try:
        svg_io = StringIO()
        fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
        return apply_tooltips(svg_io.getvalue(), tooltips)
    finally:
        plt.close(fig)


def empty_state(msg: str, extra_classes: str = '') -> str:
    """The no-data placeholder fragment charts return instead of an SVG."""
    classes = f'text-center text-muted {extra_classes}'.rstrip()
    return f'<div class="{classes}">{msg}</div>'


class BaseChart:
    """Base for every chart. Not an ABC — see the module docstring."""

    # --- configuration, read by `chart_view` at import time --------------
    #: Cache name. **Byte-identical to the pre-refactor decorator argument**:
    #: it is a Redis key prefix (`redis_chart.py`) and test_redis_cache.py
    #: names several directly.
    cache_name: str = None

    #: In-process LRU capacity. **Redis ignores this entirely** — under Redis
    #: eviction is instance-global `allkeys-lru` — so it only bounds the
    #: no-Redis fallback, where overflowing costs a re-render rather than
    #: correctness. A live second layout splits every chart's key space, so
    #: the three tightest budgets were raised when `mobile` shipped and again
    #: when `tablet` did; the rest had enough slack. Sizes are per-chart
    #: because the working sets differ
    #: by two orders of magnitude (one facility pie vs the jobs explorer's
    #: per-filter-set fanout).
    cache_maxsize: int = 128

    #: `{'desktop': Layout, 'mobile': Layout, 'tablet': Layout}` — build with
    #: `layout.profile`.
    LAYOUTS: dict = None

    # --- rendering defaults ----------------------------------------------
    empty_message: str = 'No data available'
    empty_classes: str = ''

    #: `ax.grid` kwargs, or None to leave the grid off. Preserved per-chart
    #: even where the differences look accidental; C12 normalizes them
    #: deliberately.
    grid: dict = {'alpha': 0.3}

    #: Legend anchor for `legend_placement='right'`. Per-family, because the
    #: dual-panel charts put theirs *inside* the axes and override wholesale.
    legend_anchor = (1.01, 0.5)

    #: Legend text size when the layout does not dictate one (i.e. desktop).
    legend_fontsize = 11

    #: True draws a right-placed legend as aligned columns (`draw_table_legend`).
    table_legend = False

    #: Columns to spread a `legend_placement='below'` legend across. Two is
    #: right for the short labels most charts carry; charts with long labels
    #: (usernames, project codes plus a formatted value) set 1.
    legend_ncol_below = 2

    #: Axis-label size when the layout does not dictate one. None means "leave
    #: it to rcParams", which is what every chart but two did before the axis
    #: existed.
    axis_label_fontsize = None

    #: Tick-label size when the layout does not dictate one, or None to take
    #: `layout.base_fontsize` (which is the rcParams value on desktop, so None
    #: and 11 are the same picture).
    tick_fontsize = None

    # --- lifecycle hooks (override what differs) -------------------------

    def prepare(self):
        """Raw constructor arguments -> plot-ready state on `self`."""

    def is_empty(self) -> bool:
        """True to short-circuit to the placeholder.

        **Define this explicitly per family.** A tempting base default of
        `return not self.data` raises `ValueError: truth value of an array is
        ambiguous` the moment a hook holds an ndarray — which the pace chart's
        rates and the histogram's band matrix both do. Defaulting to False
        makes that failure impossible; a family that forgets simply renders an
        empty chart rather than 500ing an htmx fragment.
        """
        return False

    def make_figure(self, layout):
        """Return `(fig, axes)`. `axes` is whatever the family wants —
        a single Axes, or a tuple for the dual-panel family."""
        return plt.subplots(figsize=layout.figsize)

    def draw(self, axes, layout, theme):
        raise NotImplementedError(
            f'{type(self).__name__} must implement draw()')

    def decorate(self, axes, layout, theme):
        """Labels, ticks, scale, grid, and theme chrome."""

    def add_legend(self, axes, layout, theme):
        """Placement comes from `layout`, colors from `theme`."""

    def finish(self, fig, axes, layout, theme):
        """Anything needing the figure — autofmt_xdate, xlim, annotations."""

    # --- shared helpers ---------------------------------------------------

    def apply_grid(self, ax, theme, **overrides):
        """Apply this chart's `grid` config, themed."""
        if not self.grid:
            return
        kwargs = {**self.grid, **overrides}
        kwargs.setdefault('color', theme.grid)
        ax.grid(True, **kwargs)

    def link_legend(self, legend, bands, url_fn, *, ordered=False):
        """Wire drill links onto a proxy-`Patch` legend.

        Zips the bands against `get_patches()`/`get_texts()`, which are
        positionally addressable only because the legend was built from proxy
        Patches rather than the BarContainers.

        `bands` is in *plot* order (bottom to top) and reversed here, matching
        how the legend is built — unless `ordered=True`, which says the caller
        already resolved legend order. That matters once a layout caps the
        legend: a capped legend is no longer `reversed(bands)`, and blindly
        reversing would put valid-looking hrefs on the wrong swatches, which
        is the exact failure the fingerprint cannot see (it proves the href
        *strings*, not the artists they land on).

        `Series.is_linkable` is the whole rule — "Others", unnamed entities
        and aggregates are inert by construction.
        """
        entries = list(bands) if ordered else list(reversed(list(bands)))
        self.link_legend_urls(legend, [url_fn(b.link_key) if b.is_linkable else None
                                       for b in entries])

    @staticmethod
    def link_legend_urls(legend, urls):
        """Put each URL on its legend row's swatch and text, in legend order; None is inert."""
        for url, patch, text in zip(urls, legend.get_patches(), legend.get_texts()):
            if url is not None:
                patch.set_url(url)
                text.set_url(url)

    def legend_amount(self, value) -> str:
        return fmt.number(value)

    def legend_cells(self, label, value):
        """Strings for one legend row: the name, then its number when it has one."""
        return (label,) if value is None else (label, self.legend_amount(value))

    def draw_table_legend(self, ax, rows, colors, urls, layout, theme, indents=None) -> bool:
        """Legend as aligned columns: swatch + name left, numbers right-aligned so
        they compare down the column. Columns after the second are drawn muted; a
        row's URL rides its swatch and every cell. Returns False, drawing nothing,
        unless the legend sits at the right: a phone's legend below needs columns."""
        if layout.legend_placement != 'right' or not rows:
            return False
        size = layout.legend_fontsize or self.legend_fontsize

        def cell(text, url, alpha=1.0):
            area = TextArea(text, textprops=dict(fontsize=size, color=theme.text, alpha=alpha))
            area._text.set_url(url)
            return area

        def name(text, color, url, indent):
            swatch = DrawingArea(size * (1.4 + indent), size, 0, 0)
            rect = Rectangle((size * indent, size * 0.2), size * 1.4, size * 0.6,
                             facecolor=color, edgecolor='none')
            rect.set_url(url)
            swatch.add_artist(rect)
            return HPacker(children=[swatch, cell(text, url)], sep=size * 0.6, align='center')

        sep = size * 0.55
        indents = indents or [0] * len(rows)
        columns = [VPacker(children=[name(r[0], c, u, i)
                                     for r, c, u, i in zip(rows, colors, urls, indents)],
                           sep=sep, align='left')]
        for j in range(1, len(rows[0])):
            columns.append(VPacker(children=[cell(r[j], u, alpha=1.0 if j == 1 else theme.muted_alpha)
                                             for r, u in zip(rows, urls)],
                                   sep=sep, align='right'))
        table = AnchoredOffsetbox(loc='center left', child=HPacker(children=columns, sep=size * 1.1,
                                                                   align='top'),
                                  bbox_to_anchor=self.legend_anchor, bbox_transform=ax.transAxes,
                                  frameon=False, borderpad=0, pad=0)
        ax.add_artist(table)
        return True

    # --- the layout axis --------------------------------------------------

    def legend_kwargs(self, layout, **overrides):
        """Placement kwargs for `ax.legend()`, from `layout.legend_placement`.

        'right' reproduces today's call exactly. 'below' puts the legend under
        the axes in `legend_ncol_below` columns — the only placement that
        works once the figure is phone-width, because a side legend on a 4.5in
        figure eats a third of it before the plot gets any.

        Callers still pass their own `frameon`/`labelspacing`; this owns
        position and nothing else.
        """
        if layout.legend_placement == 'below':
            kwargs = dict(loc='upper center', bbox_to_anchor=(0.5, -0.22),
                          ncol=self.legend_ncol_below)
        else:
            kwargs = dict(loc='center left', bbox_to_anchor=self.legend_anchor)
        kwargs['fontsize'] = layout.legend_fontsize or self.legend_fontsize
        kwargs.update(overrides)
        return kwargs

    def legend_entry_cap(self, layout, n):
        """How many of `n` legend entries this layout affords."""
        cap = layout.max_legend_entries
        return n if cap is None else min(n, cap)

    def label_kw(self, layout):
        """`fontsize` kwargs for axis labels, or `{}` to leave it to rcParams.

        Returned as a dict rather than a value because `fontsize=None` is not
        the same as omitting it.

        The layout wins where it states a size, and defers where it does not —
        the rule `legend_fontsize` uses. Express it as None-means-defer rather
        than a boolean like `layout.is_mobile`: the vocabulary has three values,
        and None-means-defer lets a new profile need no new branch here.
        """
        size = layout.axis_label_fontsize or self.axis_label_fontsize
        return {'fontsize': size} if size is not None else {}

    def apply_date_axis(self, ax, layout):
        """Ticks and labels for a datetime x axis, at either layout.

        Replaces `fig.autofmt_xdate()`, and deliberately does not rotate: the
        rotation existed to fit `2026-07-26` repeated across every tick, and
        `fmt.mpl_date_ticks` removes the repetition instead. Horizontal labels
        give the plot back the vertical band the slant was using — which is
        worth more on a phone but is not worth nothing on a dashboard.

        Tick count comes from the layout, so a phone gets five where a
        dashboard gets twelve.

        Charts whose x axis is categorical — the jobs timeline plots band
        indices against period strings the plugin already formatted — cannot
        use this. They call `fmt.compact_date_labels` on the label strings
        instead, which applies the same vocabulary.
        """
        locator, formatter = fmt.mpl_date_ticks(max_ticks=layout.max_ticks)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        # The offset text is a separate Text artist that `tick_params` does
        # not reach. Nothing sets it now that context rides the tick labels,
        # but sizing it keeps a stray one from rendering at the rcParams
        # default beside 9pt ticks.
        ax.xaxis.get_offset_text().set_fontsize(layout.base_fontsize)

    def apply_chrome(self, fig, axes, theme):
        """Recolour every chrome artist from *theme*, after the chart is drawn.

        The rcParams block in `theme.py` bakes the **light** chrome in at
        import — it runs once per process, before any request has a theme —
        so every title, axis label, tick, spine and legend label starts life
        `#011837`. That is the whole of the dark-chart defect: figures already
        render on a transparent background, so there was never a white box to
        remove, only ink that could not be read. Verified in-browser at
        **1.3:1** on `/allocations/projects` before this existed.

        Applied centrally, after `finish()`, rather than left to each family,
        for the reason `apply_tick_fontsize` gives: a chart that forgets is
        invisible until someone looks at it in the other theme, and there are
        sixteen of them.

        **What it deliberately does not touch: `ax.texts`.** Those are the
        artists a chart placed itself, and every one of them already carries a
        color chosen for a reason no theme should overrule — the pie's
        percentage labels take theirs from the *wedge* luminance
        (`autopct_color_for`), and the pace chart's "today" marker takes
        theirs from `theme.accent`. Blanket-setting them would paint white
        text onto a gold wedge.

        A no-op in light mode by construction: every value assigned here is
        the literal the rcParams block already installed, which
        `test_chart_theme.py::TestThemeLight` is what keeps true.
        """
        for ax in (axes if isinstance(axes, (tuple, list)) else (axes,)):
            ax.title.set_color(theme.text)
            ax.xaxis.label.set_color(theme.text)
            ax.yaxis.label.set_color(theme.text)
            # `colors` covers the tick marks and their labels together, which
            # is what rcParams `xtick.color` + `labelcolor: inherit` does.
            ax.tick_params(axis='both', colors=theme.text)
            for spine in ax.spines.values():
                spine.set_color(theme.spine)
            # A separate Text artist that `tick_params` does not reach — the
            # same one `apply_date_axis` has to size by hand.
            for axis in (ax.xaxis, ax.yaxis):
                axis.get_offset_text().set_color(theme.text)

            legend = ax.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(theme.text)
                legend.get_title().set_color(theme.text)

    def apply_tick_fontsize(self, axes, layout):
        """Size tick labels from the layout.

        Applied centrally in `render()` rather than left to each `decorate()`,
        because a chart that forgets is invisible until someone looks at a
        phone. Desktop's `base_fontsize` equals the rcParams `font.size`, so
        this is a no-op there and the desktop fingerprints do not move.

        Three-way fallback, same None-means-defer rule as `label_kw`: the
        layout's size, else the chart's own, else the layout's base size.
        """
        size = (layout.tick_fontsize or self.tick_fontsize
                or layout.base_fontsize)
        for ax in (axes if isinstance(axes, (tuple, list)) else (axes,)):
            ax.tick_params(labelsize=size)

    def tooltip(self, artist, text):
        """Hover text for one mark (never a legend entry: it already says what it is)."""
        if text:
            artist.set_gid(f'{TOOLTIP_GID}{len(self._tooltips)}')
            self._tooltips[len(self._tooltips)] = text

    # --- the driver -------------------------------------------------------

    def render(self, layout='desktop', theme='light') -> str:
        lay = resolve_layout(self.LAYOUTS, layout)
        thm = resolve_theme(theme)

        # Also on `self`, because `prepare()` runs before the drawing hooks
        # and some charts need the layout while shaping data, not just while
        # drawing it: a pie caps its *slices* on a phone rather than leaving
        # unlabelled wedges, and the pace chart clamps its top-N grouping.
        # The drawing hooks still take it as an argument — that is the
        # signature, and `self.layout` is not an invitation to stop passing it.
        self.layout, self.theme = lay, thm
        self._tooltips = {}

        self.prepare()
        if self.is_empty():
            return empty_state(self.empty_message, self.empty_classes)

        fig, axes = self.make_figure(lay)
        self.apply_tick_fontsize(axes, lay)
        self.draw(axes, lay, thm)
        self.decorate(axes, lay, thm)
        self.add_legend(axes, lay, thm)
        self.finish(fig, axes, lay, thm)
        # Last, so it reaches artists the hooks above created — the legend in
        # particular does not exist until `add_legend` has run.
        self.apply_chrome(fig, axes, thm)
        return fig_to_svg(fig, self._tooltips)

    # --- caching ----------------------------------------------------------

    @staticmethod
    def cache_key(*args, **kwargs):
        """Stable key over the RAW constructor arguments.

        A staticmethod over the raw arguments, deliberately not an instance
        method, so a cache hit never constructs the chart or runs `prepare()`.
        """
        raise NotImplementedError


def chart_view(cls):
    """Bind a `BaseChart` subclass to its cache; return the module-level callable.

    Called AT IMPORT, so the order of `chart_view(...)` calls in the facade is
    the order caches register in `webapp.caching._chart_caches` — which drives
    the admin Caching card. `test_chart_cache_registry.py` pins it.

    ## The aliasing trap this closes structurally

    `caching/chart.py` and `redis_chart.py` both default `key_fn` to
    `lambda *args, **kwargs: content_hash(args[0])` — **the default key
    ignores every argument except the first positional one**. A `theme=`
    kwarg would be silently dropped and the first-rendered theme's SVG served
    to everyone; with Redis the cache is shared across workers *and* pods, so
    the aliasing would be global.

    Rather than trust eleven hand-written key functions to each remember, the
    two render axes are composed into the key here, once, where getting it
    wrong is not expressible.
    """
    def _key(*args, layout='desktop', theme='light', **kwargs):
        # Accept either a name or a Layout/Theme object, and key on the name
        # so the two spellings of one rendering share a cache entry.
        return content_hash([cls.cache_key(*args, **kwargs),
                             getattr(layout, 'name', layout),
                             getattr(theme, 'name', theme)])

    @caching.chart_cached(name=cls.cache_name, maxsize=cls.cache_maxsize,
                          key_fn=_key)
    @functools.wraps(cls, assigned=('__doc__',), updated=())
    def view(*args, layout='desktop', theme='light', **kwargs):
        return cls(*args, **kwargs).render(layout=layout, theme=theme)

    # Keep the callable introspectable: `inspect.signature` should report the
    # chart's own arguments, not `(*args, **kwargs)`. The signature-drift test
    # and anyone reading the facade both depend on this.
    init_sig = inspect.signature(cls.__init__)
    params = [p for name, p in init_sig.parameters.items() if name != 'self']
    axes = [
        inspect.Parameter('layout', inspect.Parameter.KEYWORD_ONLY, default='desktop'),
        inspect.Parameter('theme', inspect.Parameter.KEYWORD_ONLY, default='light'),
    ]
    view.__signature__ = inspect.Signature(params + axes)
    view.chart_class = cls
    return view
