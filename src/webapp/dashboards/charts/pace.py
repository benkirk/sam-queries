"""Allocation pace chart.

Stacked-area chart where each allocation is one band with a step at
``active_at``. Left of the step: constant past-burn-rate (used/elapsed_days).
Right of the step: constant required-future-rate (remaining/remaining_days).
Past and future of the same allocation share a color (one band = one color).
Top-N projcodes get distinct colors; the rest share a muted "Other" color.

**A direct `BaseChart` subclass with no family, deliberately.** Roughly 60% of
this file is bespoke — the daily-grid band builder, the run-length compression,
the ymax clamp, the today marker, and the only `MonthLocator` in the app — and
it is the most numerically fragile code in the chart layer. It takes `to_svg`,
`empty_state` and the render axes from the base and nothing else. Forcing it
into `StackedSeriesChart` would mean growing that family hooks only one chart
uses; if this class starts pulling the base in that direction, let it override
`render()` outright instead.

Note it does NOT use `series.assign_colors`: it builds `color_map` directly
from the ranked top-N, and its "Other" is an RGBA with baked alpha rather than
a palette entry.
"""

from datetime import datetime, timedelta
from typing import Dict, List

import matplotlib.colors
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_GRAY_LIGHT, UNITY_NCAR_NAVY, UNITY_STACK_10, UNITY_STACK_20,
)

_PACE_OTHER_COLOR = matplotlib.colors.to_rgba(UNITY_NCAR_GRAY_LIGHT, 0.85)
_PACE_TODAY_LINE_COLOR = matplotlib.colors.to_rgba(UNITY_NCAR_NAVY, 0.7)
_PACE_RATE_SCALE = 365  # internal per-day rates → per-year axis

OTHER_KEY = '__other__'


def pace_bands(allocations: List[Dict], active_at: datetime,
               window_start: datetime, window_end: datetime):
    """Build per-allocation rate arrays on a daily grid.

    Returns (days, bands) where bands is a list of
    ``(projcode, total_amount, rates_list)`` tuples — one per allocation
    that intersects the window and has nonzero area.
    """
    n_days = (window_end - window_start).days + 1
    today_idx = (active_at - window_start).days

    bands = []
    for a in allocations:
        s, e = a['start_date'], a['end_date']
        if s is None or e is None or e <= s:
            continue
        if e < window_start or s > window_end:
            continue

        amount = float(a.get('total_amount') or 0.0)
        used = float(a.get('total_used') or 0.0)

        # past region: S → min(active_at, E); height = used / elapsed
        past_end = min(active_at, e)
        past_days = max((past_end - s).days, 0)
        past_rate = (used / past_days) if past_days > 0 else 0.0

        # future region: max(active_at, S) → E; height = remaining / remaining
        future_start = max(active_at, s)
        future_days = max((e - future_start).days, 0)
        future_rate = ((amount - used) / future_days) if future_days > 0 else 0.0

        if past_rate <= 0 and future_rate <= 0:
            continue

        rates = np.zeros(n_days)
        s_idx = max(0, (s - window_start).days)
        e_idx = min(n_days, (e - window_start).days + 1)

        if past_rate > 0:
            rates[s_idx:min(today_idx, e_idx)] = past_rate
        if future_rate > 0:
            rates[max(today_idx, s_idx):e_idx] = future_rate

        bands.append((a.get('projcode', ''), amount, rates))

    days = [window_start + timedelta(days=i) for i in range(n_days)]
    return days, bands


def pace_key_fields(allocations: List[Dict]) -> list:
    """Extract only the fields the pace chart consumes, for a compact hash input."""
    def _d(x):
        return x.isoformat() if x is not None else None
    return [
        (
            a.get('projcode', ''),
            _d(a.get('start_date')),
            _d(a.get('end_date')),
            float(a.get('total_amount') or 0.0),
            float(a.get('total_used') or 0.0),
        )
        for a in allocations
    ]


class PaceChart(BaseChart):
    """One band per allocation, past-rate | future-rate step at ``active_at``.

    Args (via the public view):
        allocations: per-allocation rows (from ``cached_allocation_usage``)
            with at least ``projcode``, ``start_date``, ``end_date``,
            ``total_amount``, ``total_used``.
        active_at: chart centerline ("today").
        window_days: half-window on each side of ``active_at``.
        top_n: projects with their own color + legend entry.
        resource_name: used only for cache key disambiguation.
        sort_by: ranking metric for the top-N selection — ``'size'`` (total
            allocated), ``'past'`` (burn rate so far) or ``'future'`` (required
            rate to completion, the "risk" signal). The legend number on each
            band reflects this same metric.
    """

    cache_name = 'pace_chart'
    #: One entry per (resource, window_days, top_n, sort_by) combination across
    #: concurrent viewers. Sized for ~30 resources x 3 sort_by x small
    #: facility-scope fanout — well under 10 MB of cached SVG per process.
    cache_maxsize = 192
    empty_message = 'No allocations available'
    LAYOUTS = profile((10, 4), (4.6, 3.4))
    #: Normalized to the 0.3 every other chart uses (was 0.2, undocumented).
    grid = {'alpha': 0.3}

    #: 9pt: this is a (10,4) figure, so the legend is proportionally larger
    #: than the same point size on an 18-inch chart. Same tier as the pies.
    legend_fontsize = 9
    legend_anchor = (1.01, 0.5)

    def __init__(self, allocations: List[Dict], active_at: datetime,
                 window_days: int = 180, top_n: int = 20,
                 resource_name: str = '', sort_by: str = 'size'):
        self.allocations = allocations or []
        self.active_at = active_at
        self.window_days = window_days
        self.top_n = top_n
        self.resource_name = resource_name
        self.sort_by = sort_by

    @staticmethod
    def cache_key(allocations, active_at, window_days=180, top_n=20,
                  resource_name='', sort_by='size'):
        return content_hash([pace_key_fields(allocations), active_at.isoformat(),
                             int(window_days), int(top_n), resource_name, sort_by])

    # --- lifecycle --------------------------------------------------------

    def prepare(self):
        self.window_start = self.active_at - timedelta(days=self.window_days)
        self.window_end = self.active_at + timedelta(days=self.window_days)
        self.days, self._bands = pace_bands(
            self.allocations, self.active_at, self.window_start, self.window_end)
        if not self._bands:
            # Two distinct empty states, and only this one knows the window
            # width. Setting the message during prepare() lets the base
            # driver's short-circuit stay the single exit path.
            if self.allocations:
                self.empty_message = (
                    f'No allocations in the ±{self.window_days}d window')
            return

        n_days = len(self.days)
        today_idx = (self.active_at - self.days[0]).days

        # Per-project aggregations for the three rank metrics:
        #   - size:   sum of total_amount   (legacy default — biggest pool)
        #   - past:   sum of past-rate band heights at today-1 (visible
        #             past slope, per day)
        #   - future: sum of future-rate band heights at today   (visible
        #             future slope = required burn-to-completion)
        # Past/future rates are piecewise-constant inside each band (set by
        # pace_bands), so the value at the single sample point IS the band's
        # rate over its active region. Summing across bands handles projects
        # with multiple allocations on the same resource.
        proj_size: Dict[str, float] = {}
        proj_past: Dict[str, float] = {}
        proj_future: Dict[str, float] = {}
        past_i = max(today_idx - 1, 0)
        future_i = min(today_idx, n_days - 1)
        for pc, amount, rates in self._bands:
            proj_size[pc] = proj_size.get(pc, 0.0) + amount
            proj_past[pc] = proj_past.get(pc, 0.0) + float(rates[past_i])
            proj_future[pc] = proj_future.get(pc, 0.0) + float(rates[future_i])

        # Ranking + legend-display metric picked in lockstep so the legend
        # number always reflects the active sort. Unknown sort_by falls back
        # to 'size' (parallels the route's input validation).
        if self.sort_by == 'past':
            self.rank_metric = proj_past
        elif self.sort_by == 'future':
            self.rank_metric = proj_future
        else:
            self.sort_by = 'size'
            self.rank_metric = proj_size

        # `top_n` defaults to 20, which is the single worst legend in the app
        # on a phone: twenty rows of "PROJ0001 (1.2M/yr)" underneath a 3.4in
        # figure would be taller than the chart. The layout clamps it, and the
        # surplus projects fold into the existing "Other" band rather than
        # disappearing — the areas still sum to the same total.
        top_n = min(self.top_n, self.layout.max_legend_entries or self.top_n)
        self.top_projs = [pc for pc, _ in sorted(
            self.rank_metric.items(), key=lambda kv: kv[1], reverse=True
        )[:top_n]]
        palette = UNITY_STACK_10 if len(self.top_projs) <= 10 else UNITY_STACK_20
        self.color_map = {pc: palette[i] for i, pc in enumerate(self.top_projs)}

        self.n_other_projs = len(self.rank_metric) - len(self.top_projs)
        plural = 's' if self.n_other_projs != 1 else ''
        self.other_label = f'Other ({self.n_other_projs} project{plural})'

        # Collapse per-allocation bands into one band per color group BEFORE
        # handing to matplotlib. Stackplot emits one <path> per band; without
        # this aggregation, a ~1000-project resource produces 1000 paths and a
        # ~20 MB SVG. Stacking is associative, so element-wise summing the rate
        # arrays within each color group is mathematically identical and
        # visually identical (the group shares one color anyway).
        group_keys = list(self.top_projs) + [OTHER_KEY]
        group_rates = {k: np.zeros(n_days) for k in group_keys}
        # Per-group running total of the active sort metric — used by the
        # "Other" legend entry to summarize the long tail in the same units
        # as the per-project entries.
        self.group_sort_totals = {k: 0.0 for k in group_keys}

        for pc, amount, rates in self._bands:
            key = pc if pc in self.color_map else OTHER_KEY
            group_rates[key] += rates
            if self.sort_by == 'past':
                self.group_sort_totals[key] += float(rates[past_i])
            elif self.sort_by == 'future':
                self.group_sort_totals[key] += float(rates[future_i])
            else:
                self.group_sort_totals[key] += amount

        # Stack order: top-N (ranked) first, Other capping the top. Drop empty
        # groups so stackplot doesn't emit a zero-area path.
        ordered = [(k, group_rates[k]) for k in self.top_projs]
        ordered += [(OTHER_KEY, group_rates[OTHER_KEY])]
        ordered = [(k, r) for k, r in ordered if r.any()]

        self._compress(ordered, n_days, today_idx)

    def _compress(self, ordered, n_days, today_idx):
        """Lossless run-length compression on the time axis.

        Each band's rate is piecewise constant (set in flat slices by
        `pace_bands`), so a 361-element daily array is mostly repeated values.
        Subset to:
          - chart endpoints (so axis bounds stay correct),
          - today_idx and today_idx-1 (the past→future step is the most
            prominent visual feature; keeping both anchors a vertical edge),
          - every transition index i where any band's rate flips between
            day i-1 and day i, plus i-1 itself (the predecessor preserves the
            step appearance — without it, stackplot draws a 1-day-wide ramp
            instead of a vertical edge).

        On a single resource, allocations typically cluster on common cycle
        dates (fiscal year boundaries, etc.), so the union of transition days
        is usually small (~10-30 of 361 days). Per-band vertex count drops by
        10-50x, lossless.
        """
        band_rates_full = np.stack([r for _, r in ordered], axis=0)
        diffs = np.any(np.diff(band_rates_full, axis=1) != 0, axis=0)
        trans = np.flatnonzero(diffs) + 1   # day i where rate[i-1] != rate[i]

        keep = {0, n_days - 1, today_idx}
        if today_idx - 1 >= 0:
            keep.add(today_idx - 1)
        for t in trans:
            ti = int(t)
            keep.add(ti)
            if ti - 1 >= 0:
                keep.add(ti - 1)
        keep_idx = np.fromiter(sorted(keep), dtype=int)

        self.days = [self.days[i] for i in keep_idx]
        self.rates_matrix = [band_rates_full[bi, keep_idx] * _PACE_RATE_SCALE
                             for bi in range(band_rates_full.shape[0])]
        self.colors = [self.color_map.get(k, _PACE_OTHER_COLOR)
                       for k, _ in ordered]

    def is_empty(self) -> bool:
        # Explicit, not inherited: `self._bands` holds ndarrays, so any
        # truthiness test over its contents raises "truth value of an array is
        # ambiguous". Length is the only safe question to ask.
        return not self.allocations or len(self._bands) == 0

    # --- drawing ----------------------------------------------------------

    def draw(self, ax, layout, theme):
        ax.stackplot(self.days, self.rates_matrix, colors=self.colors,
                     edgecolor='none', linewidth=0, antialiased=True)

        # Clamp ymax to the larger of the stacked totals at the window edges,
        # plus 25% headroom. Allocations expiring within a day or two of
        # active_at otherwise produce future-rates of remaining/1d that
        # dominate the axis and squash the rest of the chart into a flat strip.
        totals_by_day = np.sum(self.rates_matrix, axis=0)
        edge_bound = max(float(totals_by_day[0]), float(totals_by_day[-1]))
        ax.set_ylim(bottom=0, top=(1.25 * edge_bound) if edge_bound > 0 else None)

        # Today marker — placed after set_ylim so the label sits at the
        # clamped ymax rather than the auto-scaled spike.
        ax.axvline(self.active_at, color=theme.accent, linestyle='--',
                   linewidth=1)
        _, ymax = ax.get_ylim()
        ax.text(self.active_at, ymax, ' today', color=theme.accent,
                fontsize=8, va='top', ha='left')

    def add_legend(self, ax, layout, theme):
        # Deduplicated: one handle per top-N projcode + one Other. The number
        # next to each project tracks the active sort_by. For rate sorts,
        # scale per-day → per-year so the number matches the axis units, and
        # tag with "/yr" to keep that explicit.
        if self.sort_by == 'size':
            def _fmt(v):
                return fmt.number(v)
        else:
            def _fmt(v):
                return f'{fmt.number(v * _PACE_RATE_SCALE)}/yr'

        handles = [mpatches.Patch(color=self.color_map[pc],
                                  label=f'{pc} ({_fmt(self.rank_metric[pc])})')
                   for pc in self.top_projs]
        if self.n_other_projs > 0:
            handles.append(mpatches.Patch(
                color=_PACE_OTHER_COLOR,
                label=f'{self.other_label} '
                      f'({_fmt(self.group_sort_totals[OTHER_KEY])})'))
        legend = ax.legend(handles=handles, frameon=False,
                           **self.legend_kwargs(layout))

        # Tag each top-N legend entry with the project-modal URL. The trailing
        # "Other" patch (if present) gets none — it is not a single project.
        # NOTE this legend is built FORWARD over top_projs, unlike the
        # StackedSeriesChart family's reversed legends, so it must not use
        # `link_legend`.
        for pc, patch, text in zip(self.top_projs, legend.get_patches(),
                                   legend.get_texts()):
            url = links.PROJECT_MODAL.url(pc)
            patch.set_url(url)
            text.set_url(url)

    def decorate(self, ax, layout, theme):
        ax.set_xlim(self.window_start, self.window_end)
        # A 360-day window is twelve "Mon YYYY" labels. That fits across 10in
        # and does not across 4.6in, so mobile takes every Nth month — still
        # the MonthLocator, still on month boundaries, just fewer of them.
        interval = 1
        if layout.is_mobile:
            months = max(1, round(2 * self.window_days / 30))
            interval = max(1, -(-months // layout.max_ticks))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=interval))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        ax.set_ylabel('Rate (per year)', **self.label_kw(layout))
        self.apply_grid(ax, theme)

    def finish(self, fig, axes, layout, theme):
        if layout.is_mobile:
            fig.autofmt_xdate(rotation=layout.label_rotation)
            return
        fig.autofmt_xdate()
