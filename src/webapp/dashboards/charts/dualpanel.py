"""Two-axes time-series charts for the status dashboard.

`nodetype_history` and `queue_history` are the only charts in the app using
`subplots(2, 1, sharex=True)`. They share a skeleton — empty guard, UTC->local
timestamp conversion, an upper panel, a conditional lower panel, a framed
legend on each, `autofmt_xdate` — and differ only in what they plot.

Chosen as the pilot for the class hierarchy: no drill links, no custom cache
key, two charts, and the smallest blast radius of any family.
"""

from typing import Dict, List

import matplotlib.pyplot as plt

from sam import fmt
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_BLUE, UNITY_NCAR_ORANGE, UNITY_NCAR_SKY, UNITY_NCAR_TEAL,
    UNITY_NCAR_VERMILION,
)


def _to_display_tz(naive_utc_ts):
    """Naive-UTC -> naive-local for matplotlib axis rendering.  Strips tzinfo
    after conversion so the existing naive-datetime plotting path is
    unchanged (matplotlib renders the local-clock values directly)."""
    return fmt.to_local_dt(naive_utc_ts).replace(tzinfo=None)


class DualPanelTimeSeriesChart(BaseChart):
    """Shared skeleton: stacked upper panel, conditional lower panel."""

    #: Legend keyword arguments. Framed with a solid face — the one place in
    #: the app where a chart legend sits *over* the data rather than beside
    #: it, so it needs to occlude. `facecolor` comes from the theme.
    #: 11pt, matching the rcParams default and every other large figure.
    #: Was 10 for no recorded reason.
    legend_fontsize = 11
    #: Two- and three-word labels ("Resources Available", "GPUs Pending"), so
    #: an outside legend gets two columns.
    legend_ncol_below = 2

    def __init__(self, history_data: List[Dict]):
        self.history_data = history_data or []
        self.timestamps = []

    def prepare(self):
        self.timestamps = [_to_display_tz(d['timestamp'])
                           for d in self.history_data]

    def is_empty(self) -> bool:
        return not self.history_data

    def make_figure(self, layout):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=layout.figsize, sharex=True)
        if layout.legend_placement == 'below':
            # Open up the inter-panel gap for the lower panel's legend — see
            # `panel_legend` on why it goes there rather than underneath.
            fig.subplots_adjust(hspace=0.45)
        return fig, (ax1, ax2)

    def panel_legend(self, ax, layout, theme, loc=2):
        """A framed legend inside the panel — or, on a phone, above it.

        Occluding the data is an acceptable trade at 18x10in, where the legend
        covers a small corner. At 4.6in wide it covers the plot: measured, the
        nodetype legend spanned over half the upper panel's width and most of
        its height. So mobile moves it out, and with it the frame goes away —
        there is nothing left to occlude.

        **Above, not below** — and this family is the reason the direction is
        not simply "below" everywhere. Two stacked Axes share one x axis, so
        the lower panel's underside is where the rotated date labels and the
        "Time (MDT)" label live; a legend placed there lands on top of them
        (measured, at three different anchors — moving the anchor down moves
        the label down with it, because the tight bbox grows to fit both).
        Above puts the upper panel's legend in the figure's top margin and the
        lower panel's in the inter-panel gap, which `sharex` leaves empty
        precisely because the upper panel's tick labels are hidden. `hspace`
        in `make_figure` is what widens that gap to hold it.
        """
        if layout.legend_placement != 'below':
            ax.legend(loc=loc, fontsize=self.legend_fontsize, frameon=True,
                      facecolor=theme.legend_face, edgecolor='none',
                      framealpha=0.9)
            return
        ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.03),
                  ncol=self.legend_ncol_below,
                  fontsize=layout.legend_fontsize or self.legend_fontsize,
                  frameon=False)

    def column(self, key, default=0):
        return [d.get(key, default) for d in self.history_data]

    def finish(self, fig, axes, layout, theme):
        # sharex=True, so the lower panel owns the visible tick labels.
        self.apply_date_axis(axes[1], layout)


class NodetypeHistoryChart(DualPanelTimeSeriesChart):
    """Node availability (stacked) over CPU/GPU + memory utilization."""

    cache_name = 'nodetype_history'
    #: One entry per node type; can be O(10s) across all machines. Raised
    #: 64 -> 96 for the second layout profile, 96 -> 144 for the third.
    cache_maxsize = 144
    empty_message = 'No history data available for this node type'
    #: Two stacked panels need real vertical room on a phone — this is the
    #: tallest mobile figure in the package, and still barely enough.
    #: Tablet: this is the chart the tablet band exists for. The status pages
    #: nest cards, so their chart gets the viewport less 144px — 624px at a
    #: 768 viewport, where the 18in figure reads 6.0px. 12in lands the tight
    #: bbox at ~736pt, i.e. 9.3px in that card.
    LAYOUTS = profile((18, 10), (4.0, 4.7), (12, 7.2))

    @staticmethod
    def cache_key(history_data):
        # Single argument, so the decorator's default key_fn would also be
        # correct — but `chart_view` composes layout/theme in, and doing that
        # requires an explicit key. See `chart_view`'s docstring.
        from webapp.caching.chart import content_hash
        return content_hash(history_data)

    def draw(self, axes, layout, theme):
        ax1, ax2 = axes
        # Series colors through the theme: only ncar-blue actually moves (it
        # is 2.27:1 on the dark card), but resolving all of them one way keeps
        # the next color added here from being the exception.
        vermilion, blue, sky, teal = theme.data_colors(
            [UNITY_NCAR_VERMILION, UNITY_NCAR_BLUE, UNITY_NCAR_SKY,
             UNITY_NCAR_TEAL])
        ax1.stackplot(
            self.timestamps,
            self.column('nodes_down'),
            self.column('nodes_allocated'),
            self.column('nodes_available'),
            labels=['Down', 'Fully Allocated', 'Resources Available'],
            colors=[vermilion, blue, sky])

        utilization = [d.get('utilization_percent') for d in self.history_data]
        memory = [d.get('memory_utilization_percent') for d in self.history_data]

        if any(u is not None for u in utilization):
            times = [self.timestamps[i] for i, u in enumerate(utilization) if u is not None]
            ax2.plot(times, [u for u in utilization if u is not None],
                     color=blue, linewidth=3, label='CPU/GPU Utilization')

        if any(m is not None for m in memory):
            times = [self.timestamps[i] for i, m in enumerate(memory) if m is not None]
            ax2.plot(times, [m for m in memory if m is not None],
                     color=teal, linewidth=3, label='Memory Utilization')

    def decorate(self, axes, layout, theme):
        ax1, ax2 = axes
        ax1.set_ylabel('Number of Nodes', fontsize=layout.base_fontsize)
        ax1.set_ylim([0, None])
        ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        # Normalized: this panel used the literal 'gray' rather than the
        # themed gray-light every other chart uses. Undocumented, and the one
        # grid color a dark theme could not have swapped.
        self.apply_grid(ax1, theme)

        ax2.set_ylabel('Utilization', fontsize=layout.base_fontsize)
        ax2.set_xlabel(f'Time ({fmt.local_tz_label()})', fontsize=layout.base_fontsize)
        ax2.set_ylim(0, 100)
        ax2.yaxis.set_major_formatter(fmt.mpl_pct_formatter())
        self.apply_grid(ax2, theme)

    def add_legend(self, axes, layout, theme):
        ax1, ax2 = axes
        self.panel_legend(ax1, layout, theme, loc=2)
        self.panel_legend(ax2, layout, theme, loc='best')


class QueueHistoryChart(DualPanelTimeSeriesChart):
    """Job flow over resource demand (GPUs when present, else cores)."""

    cache_name = 'queue_history'
    #: One entry per queue; queue counts can be O(10s) across all resources.
    #: Raised 64 -> 96 for the second layout profile, 96 -> 144 for the third.
    cache_maxsize = 144
    empty_message = 'No history data available for this queue'
    #: Tablet: same 624px card as its sibling, and the same ~736pt target —
    #: which this family reaches at the same 12in despite starting 4in
    #: narrower, because the legend is what the tight bbox is made of.
    LAYOUTS = profile((14, 8), (4.0, 4.2), (12, 7.2))

    @staticmethod
    def cache_key(history_data):
        from webapp.caching.chart import content_hash
        return content_hash(history_data)

    def draw(self, axes, layout, theme):
        ax1, ax2 = axes
        ts = self.timestamps
        teal, orange, vermilion, blue = theme.data_colors(
            [UNITY_NCAR_TEAL, UNITY_NCAR_ORANGE, UNITY_NCAR_VERMILION,
             UNITY_NCAR_BLUE])
        ax1.plot(ts, self.column('running_jobs'), color=teal,
                 linewidth=3, label='Running')
        ax1.plot(ts, self.column('pending_jobs'), color=orange,
                 linewidth=3, label='Pending')
        ax1.plot(ts, self.column('held_jobs'), color=vermilion,
                 linewidth=3, label='Held')
        ax1.plot(ts, self.column('active_users'), color=blue,
                 linestyle='--', linewidth=2, label='Active Users')

        gpus_alloc = self.column('gpus_allocated')
        gpus_pend = self.column('gpus_pending')
        if any(gpus_alloc) or any(gpus_pend):
            ax2.plot(ts, gpus_alloc, color=blue, linewidth=3,
                     label='GPUs Running')
            ax2.plot(ts, gpus_pend, color=teal, linewidth=3,
                     label='GPUs Pending')
        else:
            ax2.plot(ts, self.column('cores_allocated'), color=blue,
                     linewidth=3, label='Cores Running')
            ax2.plot(ts, self.column('cores_pending'), color=teal,
                     linewidth=3, label='Cores Pending')

    def decorate(self, axes, layout, theme):
        ax1, ax2 = axes
        ax1.set_ylim([0, None])
        ax1.set_ylabel('Count', fontsize=layout.base_fontsize)
        ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        self.apply_grid(ax1, theme)

        ax2.set_ylim([0, None])
        ax2.set_ylabel('Resources', fontsize=layout.base_fontsize)
        ax2.set_xlabel(f'Time ({fmt.local_tz_label()})', fontsize=layout.base_fontsize)
        ax2.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        self.apply_grid(ax2, theme)

    def add_legend(self, axes, layout, theme):
        for ax in axes:
            self.panel_legend(ax, layout, theme, loc=2)
