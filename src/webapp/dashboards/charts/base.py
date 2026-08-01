"""`BaseChart` — the figure lifecycle, and `chart_view` — the cache binder.

Shaped after `webapp/utils/form_handler.py:HtmxFormHandler`: one concrete base
with documented hooks and no abstract parent. Three levels total —
`BaseChart` -> family -> concrete — because a fourth (abstract / matplotlib)
would own about thirty lines and shield nothing: `figsize`, `ax.pie`,
`stackplot`, `bbox_to_anchor` and `Artist.set_url` are matplotlib-shaped all
the way to the leaf. The migration seam is bought with module boundaries
instead — `links.py` and `series.py` import no matplotlib, enforced by test.

## The lifecycle

    render(layout, theme)
        self.layout/self.theme    resolved, for hooks that run pre-draw
        prepare()                 raw payload -> plot-ready state on self
        is_empty()  -> empty_state()      short-circuit
        make_figure(layout)       plt.subplots(figsize=layout.figsize)
        apply_tick_fontsize()     layout.base_fontsize on every Axes
        draw(axes, ...)           REQUIRED — the family draws the marks
        decorate(axes, ...)       labels, ticks, grid, scale, theme chrome
        add_legend(axes, ...)     placement from layout, colours from theme
        finish(fig, axes, ...)    autofmt_xdate, xlim, annotations
        to_svg(fig)               the single savefig/close chokepoint

Hooks default to no-ops, so a leaf implements only what differs. State goes on
`self` rather than through a threaded model object, matching the handler
idiom — the alternative is passing a five-field tuple through seven methods.

`BaseChart` must **not** swallow exceptions. Callers own that today and do it
inconsistently: `disk_scans/routes.py` wraps its chart call (incidentally —
the `try` is really around the data fetch) while `jobs/routes.py` closes its
`try` immediately before calling. Catching here would silently turn the
disk-scans error card into a blank one.
"""

import functools
import inspect
from io import StringIO

import matplotlib.pyplot as plt

from webapp.caching import caching
from webapp.caching.chart import content_hash
from webapp.dashboards.charts.layout import resolve_layout
from webapp.dashboards.charts.theme import resolve_theme


def fig_to_svg(fig) -> str:
    """Serialize a figure to SVG and ALWAYS close it.

    savefig can raise on pathological data; without the finally the figure
    would leak in the Agg backend's global registry until process restart.
    """
    try:
        svg_io = StringIO()
        fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
        return svg_io.getvalue()
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
    #: the three tightest budgets were raised when `mobile` shipped; the rest
    #: had enough slack. Sizes are per-chart because the working sets differ
    #: by two orders of magnitude (one facility pie vs the jobs explorer's
    #: per-filter-set fanout).
    cache_maxsize: int = 128

    #: `{'desktop': Layout, 'mobile': Layout}` — build with `layout.profile`.
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

    #: Columns to spread a `legend_placement='below'` legend across. Two is
    #: right for the short labels most charts carry; charts with long labels
    #: (usernames, project codes plus a formatted value) set 1.
    legend_ncol_below = 2

    #: Axis-label size when the layout does not dictate one. None means "leave
    #: it to rcParams", which is what every chart but two did before the axis
    #: existed.
    axis_label_fontsize = None

    #: Tick-label size on desktop, or None to take `layout.base_fontsize`
    #: (which is the rcParams value, so None and 11 are the same picture).
    #: Mobile always takes the layout's.
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
        """Placement comes from `layout`, colours from `theme`."""

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
        patches, texts = legend.get_patches(), legend.get_texts()
        for band, patch, text in zip(entries, patches, texts):
            if not band.is_linkable:
                continue
            url = url_fn(band.link_key)
            patch.set_url(url)
            text.set_url(url)

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
        """
        size = (layout.base_fontsize if layout.is_mobile
                else self.axis_label_fontsize)
        return {'fontsize': size} if size is not None else {}

    def apply_tick_fontsize(self, axes, layout):
        """Size tick labels from the layout.

        Applied centrally in `render()` rather than left to each `decorate()`,
        because a chart that forgets is invisible until someone looks at a
        phone. Desktop's `base_fontsize` equals the rcParams `font.size`, so
        this is a no-op there and the desktop fingerprints do not move.
        """
        size = (layout.base_fontsize if layout.is_mobile
                else (self.tick_fontsize or layout.base_fontsize))
        for ax in (axes if isinstance(axes, (tuple, list)) else (axes,)):
            ax.tick_params(labelsize=size)

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

        self.prepare()
        if self.is_empty():
            return empty_state(self.empty_message, self.empty_classes)

        fig, axes = self.make_figure(lay)
        self.apply_tick_fontsize(axes, lay)
        self.draw(axes, lay, thm)
        self.decorate(axes, lay, thm)
        self.add_legend(axes, lay, thm)
        self.finish(fig, axes, lay, thm)
        return fig_to_svg(fig)

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
