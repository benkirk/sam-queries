"""Stacked time series — bars and areas.

Five charts, one shape: bands accumulated bottom-to-top, a reversed
proxy-`Patch` legend on the right so it reads top-to-bottom matching the
visual stack, and drill links on the bars, the legend, or both.

**Bar vs area is `stack_mode`, not a subclass.** Three charts stack bars and
two stack areas, but that is the only thing the two modes differ in — two
~12-line private methods rather than a fourth level of hierarchy for a 3/2
split.

The legend is built from proxy `Patch` handles rather than the `BarContainer`s
because that is what makes `get_patches()`/`get_texts()` positionally
addressable for `set_url` — the whole reason these charts can have clickable
legends at all.
"""

import matplotlib.patches as mpatches
from matplotlib.ticker import MaxNLocator

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links, series as series_mod
from webapp.dashboards.charts.base import BaseChart, cells_label
from webapp.dashboards.charts.jobs_metrics import (
    JOBS_METRIC_LABELS, jobs_timeseries_series,
)
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_BLUE, UNITY_STACK_10, UNITY_STACK_20, scale_bytes,
)

_USAGE_METRIC_YLABELS = {
    'charges':    'Charges',
    'jobs':       'Job Count',
    'core_hours': 'Core-Hours',
}


class StackedSeriesChart(BaseChart):
    """Bands accumulated bottom-to-top, with an optional clickable legend."""

    #: `label_rotation` is unread by the *date* charts in this family — the
    #: smart date axis shortens labels enough to leave them horizontal. It
    #: survives for `JobsTimeseriesChart`, whose categorical axis falls back
    #: to rotation when its period labels are a grain we cannot compact.
    #: Tablet: 10in. This family has the widest legend labels in the package
    #: — the status page's "WYOM0247 (33,408)" is a name *and* a value — so
    #: its tight bbox runs ~130pt past what a sample payload predicts, and 11in
    #: measured 8.4px in the narrowest card any chart sits in (625px, the
    #: status page nests two card bodies). Proportionally taller than desktop:
    #: 18:5 at 10in is a 2.8in strip, and the band count does not shrink with
    #: the figure.
    LAYOUTS = profile((18, 5), (4.0, 2.8), (10, 3.4), label_rotation=30)

    #: 'bar' — discrete bars per x position; 'area' — filled stackplot.
    stack_mode = 'bar'

    bar_width = 1
    bar_linewidth = 0.3

    #: Palette for named bands, and whether to walk it backwards.
    palette = UNITY_STACK_10
    palette_reverse = False

    show_legend = True
    legend_labelspacing = 0.7

    #: Drill target for the legend entries, or None. May be a property when
    #: it depends on constructor arguments.
    legend_drill = None

    # --- subclass contract -------------------------------------------------

    def build_bands(self) -> list:
        """Return `[Series, ...]` bottom -> top."""
        raise NotImplementedError

    def x_values(self):
        """The x coordinates the bands are plotted against."""
        raise NotImplementedError

    def band_values(self, band):
        """Plot-ready values for one band — the hook byte scaling uses."""
        return band.values

    def band_value(self, band):
        """The number a legend row shows beside the band's name, or None for none."""
        return None

    def ylabel(self) -> str:
        raise NotImplementedError

    def bar_url(self, i):
        """Drill URL for the bar at x-index *i*, or None."""
        return None

    # --- lifecycle ---------------------------------------------------------

    def prepare(self):
        self.bands = self.build_bands()
        # The *palette* is lifted for the theme; the inert "Others" band is
        # not — it takes the theme's muted role, whose whole job is to stay
        # recessive. Lifting it would defeat that. See `Theme.muted_data`.
        self.colors = series_mod.assign_colors(
            self.bands, self.theme.data_colors(self.palette),
            self.theme.muted_data, reverse=self.palette_reverse)
        self.x = self.x_values()

    def is_empty(self) -> bool:
        # Defined explicitly per family — see BaseChart.is_empty.
        return not self.bands or not self.x

    def draw(self, ax, layout, theme):
        if self.stack_mode == 'area':
            self._draw_area(ax, theme)
        else:
            self._draw_bars(ax, theme)

    def _draw_bars(self, ax, theme):
        bottoms = [0.0] * len(self.x)
        for band, color in zip(self.bands, self.colors):
            vals = list(self.band_values(band))
            bars = ax.bar(self.x, vals, width=self.bar_width, bottom=bottoms,
                          color=color, edgecolor=theme.bar_edge,
                          **self._bar_kwargs())
            for i, (value, rect) in enumerate(zip(vals, bars.patches)):
                if not value:
                    continue
                url = self.bar_url(i)
                if url:
                    rect.set_url(url)
            bottoms = [b + v for b, v in zip(bottoms, vals)]

    def _bar_kwargs(self):
        return {'lw': self.bar_linewidth}

    def _draw_area(self, ax, theme):
        matrix = [list(self.band_values(b)) for b in self.bands]
        # Alpha comes from the theme, not this class: the figure is
        # transparent, so it composites against the card. See
        # `Theme.area_alpha`.
        ax.stackplot(self.x, *matrix, colors=self.colors,
                     alpha=theme.area_alpha)

    def decorate(self, ax, layout, theme):
        ax.set_ylabel(self.ylabel(), **self.label_kw(layout))
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        self.apply_grid(ax, theme)

    def legend_entries(self, layout):
        """`[(band, color), ...]` in legend order, capped for the layout.

        Reversed so the legend reads top-to-bottom matching the visual stack.

        When the cap bites, the trailing remainder band — the gray aggregate
        that sits at the bottom of every stack — is *kept* and the smallest
        named bands are dropped instead. Dropping it would
        leave a visible gray band with nothing in the legend explaining it,
        which is worse than dropping a sliver that is already hard to see.
        Every band is still drawn either way; only the legend is capped.
        """
        entries = list(zip(reversed(self.bands), reversed(self.colors)))
        cap = self.legend_entry_cap(layout, len(entries))
        if cap >= len(entries):
            return entries
        keep_tail = entries[-1:] if entries[-1][0].is_other else []
        return entries[:cap - len(keep_tail)] + keep_tail

    def add_legend(self, ax, layout, theme):
        if not self.show_legend or layout.legend_placement == 'none':
            return
        entries, drill = self.legend_entries(layout), self.legend_drill
        rows = [self.legend_cells(b.label, self.band_value(b)) for b, _ in entries]
        urls = [drill.url(b.link_key) if drill and b.is_linkable else None for b, _ in entries]
        if self.table_legend and self.draw_table_legend(
                ax, rows, [c for _, c in entries], urls, layout, theme):
            return
        handles = [mpatches.Patch(color=c, label=cells_label(r)) for r, (_, c) in zip(rows, entries)]
        legend = ax.legend(
            handles=handles,
            frameon=False,
            **self.legend_kwargs(layout),
            **({'labelspacing': self.legend_labelspacing}
               if self.legend_labelspacing else {}),
        )
        if drill is not None:
            # `ordered=True`: `entries` is already in legend order and may be
            # capped, so re-reversing it would misalign hrefs onto the wrong
            # swatches.
            self.link_legend(legend, [b for b, _ in entries], drill.url,
                             ordered=True)

    def finish(self, fig, axes, layout, theme):
        self.apply_date_axis(axes, layout)


# ---------------------------------------------------------------------------
# Usage Trend (compute resource-details)
# ---------------------------------------------------------------------------

class _UsageTrend(StackedSeriesChart):
    """Daily usage bars: the metric names the y axis, a bar drills to its day."""

    def ylabel(self):
        return _USAGE_METRIC_YLABELS.get(self.metric, 'Charges')

    def bar_url(self, i):
        d = self.x[i]
        return links.DAY.url(d.isoformat() if hasattr(d, 'isoformat') else str(d))


class UsageTrendChart(_UsageTrend):
    """Flat daily bars — the degenerate one-band case of the stack.

    Kept a subclass of the stacked family rather than its own thing: with a
    single band, `bottom=[0]*n` produces exactly the historical output. It
    differs only in having no legend and a heavier bar outline.
    """

    cache_name = 'usage_timeseries'
    #: One entry per (resource, time-range, metric) combination active in the
    #: current snapshot window.
    cache_maxsize = 128
    empty_message = 'No usage data recorded for this period'

    show_legend = False
    bar_linewidth = 2

    def __init__(self, daily_data, link_to_day_rows=False, metric='charges'):
        self.daily_data = daily_data or {}
        self.link_to_day_rows = link_to_day_rows
        self.metric = metric

    @staticmethod
    def cache_key(daily_data, link_to_day_rows=False, metric='charges'):
        return content_hash([content_hash(daily_data),
                             bool(link_to_day_rows), metric])

    def prepare(self):
        dates = list(self.daily_data.get('dates') or [])
        vals = list(self.daily_data.get('values') or [])
        combined = sorted(zip(dates, vals))
        if combined:
            dates, vals = (list(t) for t in zip(*combined))
        else:
            dates, vals = [], []
        self._dates = dates
        # A single unnamed band: no legend, so the label never renders.
        self.bands = [series_mod.Series('', vals, None)] if dates else []
        self.colors = self.theme.data_colors([UNITY_NCAR_BLUE])
        self.x = dates

    def x_values(self):
        return self._dates

    def build_bands(self):        # unused — prepare() is overridden
        return self.bands

    def bar_url(self, i):
        return super().bar_url(i) if self.link_to_day_rows else None


class UsageTrendStackedChart(_UsageTrend):
    """Daily bars segmented by the top-N users over the window + "Others".

    Every segment of a given day carries the same day drill, so a click
    anywhere in that day's stack expands the day — preserving the flat chart's
    behavior. Named legend entries expand that user's Usage-by-User row.
    """

    cache_name = 'usage_timeseries_stacked'
    cache_maxsize = 128
    empty_message = 'No usage data recorded for this period'
    legend_drill = links.USAGE_USER

    def __init__(self, timeseries, metric='charges'):
        self.timeseries = timeseries or {}
        self.metric = metric

    @staticmethod
    def cache_key(timeseries, metric='charges'):
        return content_hash([content_hash(timeseries), metric])

    def build_bands(self):
        return series_mod.from_label_series(self.timeseries.get('series'))

    def x_values(self):
        return list(self.timeseries.get('dates') or [])


# ---------------------------------------------------------------------------
# Disk usage stacked area (compute resource-details — DISK)
# ---------------------------------------------------------------------------

class DiskUsageAreaChart(StackedSeriesChart):
    """Disk usage vs time, stacked by user.

    For ``metric='bytes'`` the y-axis auto-scales to TiB or PiB from the peak
    stacked total. `link_kind='user'` makes legend usernames open the
    user-details modal; None (the default) renders them unlinked.
    """

    cache_name = 'disk_usage_stacked_area'
    cache_maxsize = 128
    empty_message = 'No disk-usage history for this period'
    stack_mode = 'area'
    legend_labelspacing = None

    def __init__(self, timeseries, link_kind=None, metric='bytes'):
        self.timeseries = timeseries or {}
        self.link_kind = link_kind
        self.metric = metric

    @staticmethod
    def cache_key(timeseries, link_kind=None, metric='bytes'):
        return content_hash([content_hash(timeseries), link_kind or '', metric])

    @property
    def legend_drill(self):
        return links.USER_MODAL if self.link_kind == 'user' else None

    def build_bands(self):
        return series_mod.from_username_series(self.timeseries.get('series'))

    def x_values(self):
        return list(self.timeseries.get('dates') or [])

    def prepare(self):
        super().prepare()
        if self.metric == 'files':
            self.scale, self._ylabel = 1, 'Number of files'
            return
        stacked_totals = [sum(b.values[i] for b in self.bands)
                          for i in range(len(self.x))]
        peak = max(stacked_totals) if stacked_totals else 0
        # floor='TiB': this chart never drops to a GiB axis, so a sub-TiB
        # series shows as a fractional TiB. Deliberate — its readers think in
        # TiB — and NOT the same ladder the distribution histogram uses.
        self.scale, unit = scale_bytes(peak, floor='TiB')
        self._ylabel = f'Disk usage ({unit})'

    def band_values(self, band):
        return [v / self.scale for v in band.values]

    def ylabel(self):
        return self._ylabel

    def decorate(self, ax, layout, theme):
        super().decorate(ax, layout, theme)
        if self.metric == 'files':
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))


# ---------------------------------------------------------------------------
# User / project queue load (status drill-down)
# ---------------------------------------------------------------------------

class UserProjAreaChart(StackedSeriesChart):
    """Per-user or per-project queue load over the 5-minute snapshot grain.

    The one chart that walks its palette **backwards**. Its series arrive
    ``[Others, lowest-rank, …, highest-rank]`` so Others sits at the bottom of
    the visual stack; walking forward would hand the LOWEST-rank entry the
    warmest color, backwards from the pace chart's convention. `reverse=True`
    gives the largest visual band (highest rank, top of the stack)
    `UNITY_STACK_20[0]` — gold.

    20 distinct colors, not 10, so Top-15 + Others has no color reuse.
    """

    cache_name = 'user_proj_stacked_area'
    cache_maxsize = 128
    empty_message = 'No per-user / per-project history for this period'
    empty_classes = 'py-4'
    stack_mode = 'area'
    palette = UNITY_STACK_20
    palette_reverse = True
    #: A tier larger than the rest of the family: the status dashboard's headline
    #: chart. Class attributes, so a phone's layout can override them.
    legend_fontsize = 13
    axis_label_fontsize = 13
    tick_fontsize = 12
    #: Long labels ("PROJ0001 (1,234)"), so a below-legend gets one column.
    legend_ncol_below = 1
    table_legend = True

    def __init__(self, timeseries, link_kind=None, rank_by: str = 'current'):
        self.timeseries = timeseries or {}
        self.link_kind = link_kind
        self.rank_by = rank_by

    @staticmethod
    def cache_key(timeseries, link_kind=None, rank_by='current'):
        return content_hash([content_hash(timeseries), link_kind or '', rank_by])

    @property
    def legend_drill(self):
        if self.link_kind == 'user':
            return links.USER_MODAL
        if self.link_kind == 'project':
            return links.PROJECT_MODAL
        return None

    def build_bands(self):
        return series_mod.from_label_series(self.timeseries.get('series'))

    def x_values(self):
        from datetime import datetime
        return [series_mod.to_display_tz(d) if isinstance(d, datetime) else d
                for d in (self.timeseries.get('dates') or [])]

    def band_value(self, band):
        # The number tracks the active rank_by selector; 'Others' too, over its aggregate.
        vs = list(band.values)
        if not vs:
            return 0
        return max(vs) if self.rank_by == 'peak' else vs[-1]

    def ylabel(self):
        return self.timeseries.get('metric_label', 'Jobs')


# ---------------------------------------------------------------------------
# Job-history activity timeline (Jobs tab)
# ---------------------------------------------------------------------------

class JobsTimeseriesChart(StackedSeriesChart):
    """Stacked activity timeline over a ``jobs_timeseries`` plugin envelope.

    Legend entries open the entity's **modal**, not a row drill: row drills
    resolve within the clicked chart's tab pane, and this chart lives in the
    Jobs pane while the By User / By Project rows live in their own lazily
    loaded panes — a row drill here is a silent no-op. The status dashboard's
    stacked area resolves the same problem the same way.
    """

    cache_name = 'jobs_timeseries'
    cache_maxsize = 128
    empty_message = 'No jobs in this range'
    grid = {'axis': 'y', 'alpha': 0.3}
    bar_width = 1.0

    def __init__(self, ts, *, metric='jobs', period='day',
                 entity_kind='user', link_entities=True):
        self.ts = ts or {}
        self.metric = metric
        self.period = period
        self.entity_kind = entity_kind
        self.link_entities = link_entities

    @staticmethod
    def cache_key(ts, *, metric='jobs', period='day', entity_kind='user',
                  link_entities=True):
        """Hash what the SVG depends on: band labels, the chosen metric's
        per-series values, and the legend's link treatment. The job_count
        positivity vector joins the key because it decides which bars carry
        drill URLs — a charges SVG with matching charges but a different
        populated-band set must not be reused."""
        labels, series = jobs_timeseries_series(ts, metric)
        clickable = [int(bool(b.get('job_count')))
                     for b in (ts or {}).get('bands') or []]
        return content_hash([
            labels, [(n, v) for n, v in series], clickable,
            str(metric), str(period), str(entity_kind), bool(link_entities),
        ])

    @property
    def legend_drill(self):
        if not self.link_entities:
            return None
        return (links.PROJECT_MODAL if self.entity_kind == 'project'
                else links.USER_MODAL)

    def prepare(self):
        self.labels, self._pairs = jobs_timeseries_series(self.ts, self.metric)
        self.env_bands = self.ts.get('bands') or []
        super().prepare()

    def build_bands(self):
        return series_mod.from_pairs(self._pairs)

    def x_values(self):
        return list(range(len(self.labels)))

    def is_empty(self):
        if not self.labels or not self.bands:
            return True
        return not any(any(v > 0 for v in b.values) for b in self.bands)

    def bar_url(self, i):
        # A zero-height rect is an invisible click target, so `_draw_bars`
        # skips it — and job_count gates it besides, so a band with no jobs is
        # never clickable whatever the plotted metric says. Consequence on the
        # charges view: an all-uncharged band draws at zero and loses its BAR
        # link, but its row in the period table below still drills.
        if not self.env_bands[i].get('job_count'):
            return None
        return links.JT_PERIOD.url(i)

    def ylabel(self):
        return JOBS_METRIC_LABELS.get(self.metric, 'Jobs')

    def decorate(self, ax, layout, theme):
        # Thin the tick labels rather than rotating 120 of them into a smear.
        step = max(1, len(self.labels) // layout.max_ticks)
        ticks = list(range(0, len(self.labels), step))
        ax.set_xticks(ticks)
        # This axis is categorical — band indices against period strings the
        # plugin already formatted (`2026-07-26` / `2026-07` / `2026`), so a
        # matplotlib date formatter cannot reach it. `compact_date_labels`
        # applies the same vocabulary to the strings, which matters because
        # this chart sits one tab away from ones that do use the date axis.
        # A grain it cannot parse (week, quarter) comes back unchanged.
        shown = [self.labels[i] for i in ticks]
        compact = fmt.compact_date_labels(shown)
        # Rotation only if the labels are still long, i.e. nothing was
        # compacted — two-line labels read badly on a slant.
        rotation = 0 if compact != shown else layout.label_rotation
        ax.set_xticklabels(compact, rotation=rotation,
                           ha='center' if rotation == 0 else 'right')
        ax.set_xlim(-0.5, len(self.labels) - 0.5)
        super().decorate(ax, layout, theme)

    def finish(self, fig, axes, layout, theme):
        # No autofmt_xdate: the x axis is categorical band indices, not dates.
        pass
