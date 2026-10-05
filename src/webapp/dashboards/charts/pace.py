"""Allocation pace chart.

Stacked-area chart where each allocation is one band, split at ``active_at``.
Left: its actual monthly charge rate. Right: its projected rate, the project's
last-90-day pace, until its balance runs out or it ends. A dashed line over the
stack is the **committed** rate: every balance over its days left, what the
allocations promise to deliver, and more than will be used. The rates arrive
precomputed per row (``allocations/burn.py`` ``pace_segments``).
Top-N projcodes get distinct colors; the rest share a muted "Other" color.

**A direct `BaseChart` subclass with no family, deliberately.** Roughly 60% of
this file is bespoke — the daily-grid band builder, the run-length compression,
the committed line, the today marker, and the only `MonthLocator` in the app — and
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
import matplotlib.patches as mpatches
import numpy as np

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart, cells_label
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import UNITY_STACK_10, UNITY_STACK_20

_PACE_RATE_SCALE = 365  # internal per-day rates -> per-year axis

OTHER_KEY = '__other__'

#: Half-window either side of ``active_at``; the route's data spans the calendar's wider window.
PACE_WINDOW_DAYS = 180


def _pace_other_color(theme):
    """The inert "N other" band.

    Translucent so the ranked bands above it stay dominant, and derived from
    the theme rather than fixed: `--ncar-gray-light` recedes on a white card
    and is the *brightest* thing on a dark one, which is exactly backwards for
    the band that means the least. See `Theme.muted_data`.
    """
    return matplotlib.colors.to_rgba(theme.muted_data, 0.85)


def _day(d, window_start, n):
    # Nearest day, not floor: an end date at 23:59:59 closes its day.
    return min(max(round((d - window_start).total_seconds() / 86400), 0), n)


def _fill(arr, lo, hi, rate, window_start):
    arr[_day(lo, window_start, len(arr)):_day(hi, window_start, len(arr))] += rate


def pace_bands(allocations: List[Dict], active_at: datetime,
               window_start: datetime, window_end: datetime):
    """Per-allocation rate arrays on a daily grid, from each row's ``pace`` segments.

    Returns ``(days, bands)``, bands a list of ``(projcode, total_amount, rates,
    covers, committed, recent)``: ``rates`` is past then projected, ``committed`` the
    dashed line's share (future only), ``covers`` whether it spans ``active_at``.
    Rows with no area and no commitment in the window are dropped.
    """
    n_days = (window_end - window_start).days + 1
    bands = []
    for a in allocations:
        p = a.get('pace')
        if not p:
            continue
        rates, committed = np.zeros(n_days), np.zeros(n_days)
        for lo, hi, rate in p['past']:
            _fill(rates, lo, min(hi, active_at), rate, window_start)
        for lo, hi, rate in p['projected']:
            _fill(rates, max(lo, active_at), hi, rate, window_start)
        for lo, hi, rate in p['committed']:
            _fill(committed, max(lo, active_at), hi, rate, window_start)
        if not rates.any() and not committed.any():
            continue
        covers = a['start_date'] <= active_at <= a['end_date']
        bands.append((a.get('projcode', ''), float(a.get('total_amount') or 0.0), rates, covers,
                      committed, p['recent']))
    days = [window_start + timedelta(days=i) for i in range(n_days)]
    return days, bands


def pace_key_fields(allocations: List[Dict]) -> list:
    """Extract only the fields the pace chart consumes, for a compact hash input."""
    def _d(x):
        return x.isoformat() if x is not None else None

    def _segs(segs):
        return [(_d(lo), _d(hi), round(rate, 6)) for lo, hi, rate in segs]
    return [
        (
            a.get('projcode', ''),
            _d(a.get('start_date')),
            _d(a.get('end_date')),
            float(a.get('total_amount') or 0.0),
            _segs(a['pace']['past']), _segs(a['pace']['projected']),
            _segs(a['pace']['committed']), round(a['pace']['recent'], 6),
        )
        for a in allocations if a.get('pace')
    ]


class PaceChart(BaseChart):
    """One band per allocation, past-rate | future-rate step at ``active_at``.

    Args (via the public view):
        allocations: per-allocation rows through ``burn.pace_segments``: at least
            ``projcode``, ``start_date``, ``end_date``, ``total_amount``, ``pace``.
        active_at: chart centerline ("today"); the window is ``PACE_WINDOW_DAYS`` either side.
        top_n: projects with their own color + legend entry.
        resource_name: used only for cache key disambiguation.
        sort_by: ranking metric for the top-N selection — ``'size'`` (total
            allocated), ``'past'`` (actual rate over the last 90 days) or
            ``'future'`` (projected rate at ``active_at``). The legend number on
            each band reflects this same metric.
    """

    cache_name = 'pace_chart'
    #: One entry per (resource, top_n, sort_by) combination across
    #: concurrent viewers. Sized for ~30 resources x 3 sort_by x small
    #: facility-scope fanout — well under 10 MB of cached SVG per process.
    cache_maxsize = 192
    empty_message = 'No allocations available'
    #: Tablet: the narrowest desktop figure of the wide families, and still
    #: the worst reader — 6.3px at a 624px card, because its smallest text is
    #: 6pt where the others' is 8.25pt. 6.5in lands the tight bbox at ~590pt.
    #: The `TABLET_DEFAULTS` legend cap does real work here: 20 project rows
    #: set the figure height on their own, whatever `figsize` says.
    LAYOUTS = profile((10, 4), (4.0, 3.0), (6.5, 3.2))
    table_legend = True
    #: Normalized to the 0.3 every other chart uses (was 0.2, undocumented).
    grid = {'alpha': 0.3}

    #: 9pt: this is a (10,4) figure, so the legend is proportionally larger
    #: than the same point size on an 18-inch chart. Same tier as the pies.
    legend_fontsize = 9

    def __init__(self, allocations: List[Dict], active_at: datetime, top_n: int = 20,
                 resource_name: str = '', sort_by: str = 'size'):
        self.allocations = allocations or []
        self.active_at = active_at
        self.top_n = top_n
        self.resource_name = resource_name
        self.sort_by = sort_by

    @staticmethod
    def cache_key(allocations, active_at, top_n=20, resource_name='', sort_by='size'):
        return content_hash([pace_key_fields(allocations), active_at.isoformat(),
                             int(top_n), resource_name, sort_by])

    # --- lifecycle --------------------------------------------------------

    def prepare(self):
        self.window_start = self.active_at - timedelta(days=PACE_WINDOW_DAYS)
        self.window_end = self.active_at + timedelta(days=PACE_WINDOW_DAYS)
        self.days, self._bands = pace_bands(
            self.allocations, self.active_at, self.window_start, self.window_end)
        if not self._bands:
            # Two distinct empty states, and only this one knows the window
            # width. Setting the message during prepare() lets the base
            # driver's short-circuit stay the single exit path.
            if self.allocations:
                self.empty_message = (
                    f'No allocations in the ±{PACE_WINDOW_DAYS}d window')
            return

        n_days = len(self.days)
        today_idx = (self.active_at - self.days[0]).days

        # Rank metrics per project, summed over its allocations: size = amount of
        # those covering today (else all of its bands); past = actual last-90-day
        # rate; future = projected rate at today.
        proj_size: Dict[str, float] = {}
        proj_past: Dict[str, float] = {}
        proj_future: Dict[str, float] = {}
        future_i = min(today_idx, n_days - 1)
        size_all: Dict[str, float] = {}
        for pc, amount, rates, covers, _committed, recent in self._bands:
            size_all[pc] = size_all.get(pc, 0.0) + amount
            if covers:
                proj_size[pc] = proj_size.get(pc, 0.0) + amount
            proj_past[pc] = proj_past.get(pc, 0.0) + recent
            proj_future[pc] = proj_future.get(pc, 0.0) + float(rates[future_i])
        for pc, total in size_all.items():
            proj_size.setdefault(pc, total)

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
        self.color_map = {pc: self.theme.data_color(palette[i])
                          for i, pc in enumerate(self.top_projs)}

        self.n_other_projs = len(self.rank_metric) - len(self.top_projs)
        # A count, not "Other (N projects)": the legend's widest row sets its width.
        self.other_label = f'{fmt.number(self.n_other_projs)} other'

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

        committed = np.zeros(n_days)
        for pc, _amount, rates, _covers, band_committed, _recent in self._bands:
            group_rates[pc if pc in self.color_map else OTHER_KEY] += rates
            committed += band_committed
        for pc, value in self.rank_metric.items():
            self.group_sort_totals[pc if pc in self.color_map else OTHER_KEY] += value

        # Stack order: top-N (ranked) first, Other capping the top. Drop empty
        # groups so stackplot doesn't emit a zero-area path.
        ordered = [(k, group_rates[k]) for k in self.top_projs]
        ordered += [(OTHER_KEY, group_rates[OTHER_KEY])]
        ordered = [(k, r) for k, r in ordered if r.any()]

        self._compress(ordered, committed, n_days, today_idx)

    def _compress(self, ordered, committed, n_days, today_idx):
        """Lossless run-length compression on the time axis.

        Each band's rate is piecewise constant (set in flat slices by
        `pace_bands`), so a 361-element daily array is mostly repeated values.
        The committed line rides along as one more row.
        Subset to:
          - chart endpoints (so axis bounds stay correct),
          - today_idx and today_idx-1 (the past->future step is the most
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
        band_rates_full = np.stack([r for _, r in ordered] + [committed], axis=0)
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
                             for bi in range(len(ordered))]
        self.committed = band_rates_full[-1, keep_idx] * _PACE_RATE_SCALE
        other = _pace_other_color(self.theme)
        self.colors = [self.color_map.get(k, other) for k, _ in ordered]

    def is_empty(self) -> bool:
        # Explicit, not inherited: `self._bands` holds ndarrays, so any
        # truthiness test over its contents raises "truth value of an array is
        # ambiguous". Length is the only safe question to ask.
        return not self.allocations or len(self._bands) == 0 or not self.rates_matrix

    # --- drawing ----------------------------------------------------------

    def draw(self, ax, layout, theme):
        ax.stackplot(self.days, self.rates_matrix, colors=self.colors,
                     edgecolor='none', linewidth=0, antialiased=True)

        # The axis fits the stack; an off-scale committed line is clipped and
        # labeled at today, since the gap to the area is the point.
        top = float(np.max(np.sum(self.rates_matrix, axis=0))) * 1.15 or None
        ax.set_ylim(bottom=0, top=top)
        ymax = ax.get_ylim()[1]
        future = np.array([d >= self.active_at for d in self.days])
        if self.committed[future].any():
            ax.plot(self.days, np.where(future, self.committed, np.nan), color=theme.text,
                    linestyle=(0, (4, 2)), linewidth=1.5)
            now = float(self.committed[future][0])
            label = f'committed {fmt.number(now)}/yr'
            ax.annotate(label + (' \u2191' if now > ymax else ''), (self.active_at, min(now, ymax)),
                        xytext=(4, -2 if now > ymax * 0.9 else 3), textcoords='offset points',
                        color=theme.text, fontsize=8, ha='left',
                        va='top' if now > ymax * 0.9 else 'bottom')

        ax.axvline(self.active_at, color=theme.accent, linestyle='--',
                   linewidth=1)
        ax.annotate('today', (self.active_at, ymax), xytext=(-4, -2), textcoords='offset points',
                    color=theme.accent, fontsize=8, va='top', ha='right')

    def add_legend(self, ax, layout, theme):
        # Deduplicated: one handle per top-N projcode + one Other. The number
        # next to each project tracks the active sort_by. For rate sorts,
        # scale per-day -> per-year so the number matches the axis units, and
        # tag with "/yr" to keep that explicit.
        if self.sort_by == 'size':
            def _fmt(v):
                return fmt.number(v)
        else:
            def _fmt(v):
                return f'{fmt.number(v * _PACE_RATE_SCALE)}/yr'

        rows = [(pc, _fmt(self.rank_metric[pc])) for pc in self.top_projs]
        colors = [self.color_map[pc] for pc in self.top_projs]
        urls = [links.PROJECT_MODAL.url(pc) for pc in self.top_projs]
        if self.n_other_projs > 0:
            rows.append((self.other_label, _fmt(self.group_sort_totals[OTHER_KEY])))
            colors.append(_pace_other_color(theme))
            urls.append(None)
        if self.table_legend and self.draw_table_legend(ax, rows, colors, urls, layout, theme):
            return
        handles = [mpatches.Patch(color=c, label=cells_label(r)) for r, c in zip(rows, colors)]
        legend = ax.legend(handles=handles, frameon=False,
                           **self.legend_kwargs(layout))

        # Tag each top-N legend entry with the project-modal URL. The trailing
        # "Other" patch (if present) gets none — it is not a single project.
        # NOTE this legend is built FORWARD over top_projs, unlike the
        # StackedSeriesChart family's reversed legends, so it must not use
        # `link_legend`.
        for url, patch, text in zip(urls, legend.get_patches(), legend.get_texts()):
            if url is not None:
                patch.set_url(url)
                text.set_url(url)

    def decorate(self, ax, layout, theme):
        ax.set_xlim(self.window_start, self.window_end)
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        ax.set_ylabel('Rate (per year)', **self.label_kw(layout))
        self.apply_grid(ax, theme)

    def finish(self, fig, axes, layout, theme):
        # Was a `MonthLocator` with `%b %Y` on every tick — twelve labels
        # repeating the same year across a default 360-day window. The shared
        # date axis still lands on month boundaries and still says the year,
        # once, where it changes.
        self.apply_date_axis(axes, layout)
