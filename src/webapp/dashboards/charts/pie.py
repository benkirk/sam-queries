"""Pie charts.

`PieChart` owns the `ax.pie` call, the autopct recoloring and the wedge/legend
drill wiring. A subclass supplies the trim (`trim_cumulative`, ~90%), the "Other" derivation, the legend formatter and an
optional drill target. `TwoRingPie` is the two-ring (sunburst) variant.
"""

import math
from typing import Dict, List

from matplotlib.offsetbox import AnchoredOffsetbox, DrawingArea, HPacker, TextArea, VPacker
from matplotlib.patches import Rectangle

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.jobs_metrics import jobs_metric_value
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import (
    UNITY_PALETTE_10, autopct_color_for, shade_family,
)

_PIE_START_ANGLE = 60

#: Cumulative-share trim: show entities up to ~90% of the total, but never
#: more than 9 named slices (the palette has 10; keep "Other" distinct).
_PIE_CUM_SHARE = 0.90
_PIE_HARD_CAP = 9


def trim_cumulative(values_desc: list, cap: int = _PIE_HARD_CAP) -> int:
    """How many leading (descending) entries to show individually.

    The fewest whose cumulative share reaches ``_PIE_CUM_SHARE``, capped at
    ``cap``. The remainder (if any) is meant to collapse into one 'Other'
    slice. Returns ``len(values_desc)`` when everything fits.
    """
    total = sum(values_desc)
    if total <= 0:
        return min(len(values_desc), cap)
    cum = 0.0
    for i, v in enumerate(values_desc):
        cum += v
        if i + 1 >= cap:
            return i + 1
        if cum / total >= _PIE_CUM_SHARE:
            return i + 1
    return len(values_desc)


class PieChart(BaseChart):
    """Shared pie rendering. Subclasses supply slices and, optionally, a drill."""

    #: Side legend on mobile (a square pie leaves width a legend can use; the cap
    #: keeps it no taller than the pie). Tablet is desktop: a pie's ~360pt bbox
    #: already gets all the width it can use, and there the cap would cut slices.
    LAYOUTS = profile((7, 4), (3.6, 2.9), (7, 4),
                      mobile={'legend_placement': 'right'},
                      tablet={'max_legend_entries': None})
    grid = None                       # pies have no grid

    start_angle = _PIE_START_ANGLE
    pctdistance = 0.85
    #: Wedges under this percentage get no inline label — they'd overlap.
    autopct_min_pct = 5
    autopct_fontsize = 8
    #: 9pt on a (7,4) figure — see the note on PaceChart.legend_fontsize.
    legend_fontsize = 9
    legend_anchor = (1.01, 0.5)

    #: A drill target (`RowDrill`/`UserDrill`), or None for an inert pie.
    drill = None

    def build(self):
        """Return ``(labels, values, colors, link_keys)``, all same length.

        A ``link_key`` of None marks an inert slice — the "Other" aggregate,
        or an entity with nothing to link to. One rule, as in `series.py`.
        """
        raise NotImplementedError

    def legend_label(self, label, value) -> str:
        return f'{label} ({fmt.number(value)})'

    def slice_cap(self, default: int) -> int:
        """Named slices this layout affords before 'Other'. Caps the data, not the
        legend: an unlegended wedge is an unlabelled click. Reads `self.layout`
        because `build()` runs in `prepare()`, before the drawing hooks."""
        return min(default, self.layout.max_legend_entries or default)

    # --- lifecycle --------------------------------------------------------

    def prepare(self):
        self.labels, self.values, self.colors, self.link_keys = self.build()

    def is_empty(self) -> bool:
        return not self.values

    def draw(self, ax, layout, theme):
        wedges, _texts, autotexts = ax.pie(
            self.values,
            labels=None,
            autopct=(lambda p: fmt.pct(p, decimals=1)
                     if p >= self.autopct_min_pct else ''),
            startangle=self.start_angle,
            counterclock=False,
            colors=self.colors,
            pctdistance=self.pctdistance,
        )
        # Percent labels take their color from the WEDGE's luminance, not the
        # page — already correct in both themes, so no theme argument.
        for at, wedge_color in zip(autotexts, self.colors):
            at.set_color(autopct_color_for(wedge_color))
            at.set_fontweight('bold')
            at.set_fontsize(self.autopct_fontsize)
        self.wedges = wedges

    def add_legend(self, ax, layout, theme):
        legend_labels = [self.legend_label(l, v)
                         for l, v in zip(self.labels, self.values)]
        legend = ax.legend(self.wedges, legend_labels,
                           **self.legend_kwargs(layout))
        if self.drill is None:
            return

        # A drill target spans three artists — the wedge, its legend swatch
        # and its legend text — which is why these stay <a> anchors rather
        # than set_gid()s: an id has to be unique.
        leg_patches = legend.get_patches()
        leg_texts = legend.get_texts()
        for i, key in enumerate(self.link_keys):
            if key is None:
                continue
            url = self.drill.url(key)
            self.wedges[i].set_url(url)
            if i < len(leg_patches):
                leg_patches[i].set_url(url)
            if i < len(leg_texts):
                leg_texts[i].set_url(url)


class _CumulativePie(PieChart):
    """~90%-cumulative-share slices with one inert 'Other'. Clickable."""

    def split(self, values_desc):
        """``(keep, n_others)`` for a descending value vector."""
        keep = trim_cumulative(values_desc, cap=self.slice_cap(_PIE_HARD_CAP))
        return keep, len(values_desc) - keep


class DiskEntityPie(_CumulativePie):
    """Scanned bytes by owner (kind='owner') or group (kind='group').

    Sentinels are keyed by uid/gid — part of the hashed input — so the cached
    SVG is independent of any per-render container id.
    """

    cache_name = 'disk_entity_pie_chart'
    cache_maxsize = 64
    empty_message = 'No usage data available'

    def __init__(self, entity_data: List[Dict], kind: str):
        self.entity_data = entity_data or []
        self.kind = kind

    @staticmethod
    def cache_key(entity_data, kind):
        # `kind` is NOT in the default content_hash(args[0]) key, but it drives
        # the drill attribute — include it so owner/group never alias.
        return content_hash([entity_data, kind])

    @property
    def drill(self):
        return links.DISK_OWNER if self.kind == 'owner' else links.DISK_GROUP

    def legend_label(self, label, value):
        return f'{label} ({fmt.size(value)})'

    def build(self):
        numeric_label = 'uid ' if self.kind == 'owner' else 'gid '

        # Coerce to float at the single entry point: scan rollups arrive as
        # decimal.Decimal from Postgres, and Decimal/float don't mix in
        # arithmetic (cum += v) or matplotlib. Everything downstream is then
        # plain float.
        data = sorted(self.entity_data, key=lambda d: float(d['value']),
                      reverse=True)
        values_desc = [float(d['value']) for d in data]
        keep, n_others = self.split(values_desc)

        keys = [d['id'] for d in data[:keep]]
        labels = [d['name'] or f'{numeric_label}{d["id"]}' for d in data[:keep]]
        values = list(values_desc[:keep])
        colors = self.theme.data_colors(list(UNITY_PALETTE_10[:keep]))

        if n_others > 0:
            keys.append(None)                  # inert slice
            labels.append(f'Other ({n_others})')
            values.append(sum(values_desc[keep:]))
            colors.append(self.theme.muted_data)

        return labels, values, colors, keys


class UserUsagePie(_CumulativePie):
    """SAM's own comp_charge_summary rollup by username, for the compute
    resource-details By User tab.

    Drills to ``#sam/user/<username>`` — the same target the stacked Usage
    Trend legend uses, so both charts expand the same Usage-by-User row.
    """

    cache_name = 'user_usage_pie_chart'
    cache_maxsize = 64
    empty_message = 'No user activity recorded for this period'
    drill = links.USAGE_USER

    def __init__(self, user_data: List[Dict], metric: str = 'charges'):
        self.user_data = user_data or []
        self.metric = metric

    @staticmethod
    def cache_key(user_data, metric='charges'):
        # metric selects which column is plotted AND what the legend numbers
        # say, but it isn't part of the default content_hash(args[0]) key —
        # include it so charges/jobs/core_hours never alias.
        #
        # The default MUST mirror the constructor's: a key function is called
        # with the caller's arguments, so an omitted-but-defaulted parameter
        # arrives missing, not defaulted.
        return content_hash([user_data, metric])

    def build(self):
        rows = [d for d in self.user_data if float(d.get(self.metric) or 0) > 0]
        if not rows:
            return [], [], [], []

        data = sorted(rows, key=lambda d: float(d[self.metric]), reverse=True)
        values_desc = [float(d[self.metric]) for d in data]
        keep, n_others = self.split(values_desc)

        keys = [d['username'] for d in data[:keep]]
        labels = list(keys)
        values = list(values_desc[:keep])
        colors = self.theme.data_colors(list(UNITY_PALETTE_10[:keep]))

        if n_others > 0:
            keys.append(None)                  # inert slice
            labels.append(f'Other ({n_others})')
            values.append(sum(values_desc[keep:]))
            colors.append(self.theme.muted_data)

        return labels, values, colors, keys


class JobsUsagePie(_CumulativePie):
    """Per-entity usage from a jobs_usage_by(dimension) plugin envelope.

    Entity-kind-agnostic: the By User tab renders it with
    ``row_attr='data-job-user'``, the By Project tab with
    ``'data-job-project'``. Naming the row attribute here rather than in the
    JavaScript is what makes adding a drill-down chart a zero-JS change.

    "Other" is sized ``totals - sum(kept)``, absorbing both beyond-cap rows
    AND the upstream limit's remainder, so the pie always sums to the true
    total. ``totals`` is computed upstream BEFORE any limit truncation.
    """

    cache_name = 'jobs_usage_pie_chart'
    cache_maxsize = 64
    empty_message = 'No usage data available'

    def __init__(self, entity_data, metric='cpu_hours', *,
                 row_attr='data-job-user', unknown_label='(unknown)'):
        self.entity_data = entity_data or {}
        self.metric = metric
        self.row_attr = row_attr
        self.unknown_label = unknown_label

    @staticmethod
    def cache_key(entity_data, metric='cpu_hours', *,
                  row_attr='data-job-user', unknown_label='(unknown)'):
        """row_attr joins the key: identical usage vectors rendered for
        different entity kinds carry different drill anchors."""
        rows = (entity_data or {}).get('rows') or []
        totals = (entity_data or {}).get('totals') or {}
        payload = [(r.get('value'), jobs_metric_value(r, metric, 'cpu_hours'))
                   for r in rows]
        return content_hash([payload,
                             jobs_metric_value(totals, metric, 'cpu_hours'),
                             str(metric), str(row_attr), str(unknown_label)])

    @property
    def drill(self):
        return links.RowDrill(self.row_attr)

    def build(self):
        rows = self.entity_data.get('rows') or []
        totals = self.entity_data.get('totals') or {}
        total = jobs_metric_value(totals, self.metric, 'cpu_hours')
        if not rows or total <= 0:
            return [], [], [], []

        # Upstream sorts by combined hours; re-sort by the *chosen* metric so
        # e.g. the Jobs view leads with the most job-count-heavy users.
        def value_of(r):
            return jobs_metric_value(r, self.metric, 'cpu_hours')

        data = sorted(rows, key=value_of, reverse=True)
        values_desc = [value_of(r) for r in data]
        keep, _n_others = self.split(values_desc)

        keys = [r.get('value') for r in data[:keep]]
        labels = [k if k is not None else self.unknown_label for k in keys]
        values = list(values_desc[:keep])
        colors = self.theme.data_colors(list(UNITY_PALETTE_10[:keep]))

        remainder = total - sum(values)
        if remainder > 1e-9:
            keys.append(None)                  # inert slice
            labels.append('Other')
            values.append(remainder)
            colors.append(self.theme.muted_data)

        return labels, values, colors, keys


class TwoRingPie(PieChart):
    """Groups in the inner ring, their parts in the outer ring, each part a shade of
    its group's hue (``facility_palette`` slot). Every wedge and legend entry drills.

    ``data`` = ``[{'id', 'facility', 'slot', 'value', 'types': [{'name', 'value'}]}]``;
    parts summing above their group are scaled to fit, and a shortfall is a blank wedge.
    """

    drill = links.FACILITY_ROW

    inner_radius = 0.66
    ring_width = 0.3
    #: Smallest wedge (percent of the whole) that carries a direct label.
    inner_label_min = 5
    outer_label_min = 6
    center_text = ''
    #: True draws the legend as aligned columns from ``legend_cells()``.
    table_legend = False

    def __init__(self, data: List[Dict]):
        self.data = data or []

    @staticmethod
    def cache_key(data):
        return content_hash(data)

    def groups(self):
        """``[(row, value)]`` for the inner ring."""
        return [(r, r.get('value')) for r in self.data]

    def parts(self, row, value):
        """``([(name, value)], gap)``: one group's outer wedges in name order."""
        types = sorted(((t['name'], t['value']) for t in row.get('types', []) if t.get('value')),
                       key=lambda t: t[0])
        total = sum(v for _, v in types)
        scale = value / total if total > value else 1
        return [(n, v * scale) for n, v in types], value - total * scale

    def percent(self, value):
        """``value`` as a percent of the whole, for the direct-label thresholds."""
        return value * 100 / self.total if self.total else 0

    def prepare(self):
        pairs = [(r, v) for r, v in self.groups() if v]
        self.rows = [r for r, _ in pairs]
        self.labels = [r['facility'] for r in self.rows]
        self.values = [v for _, v in pairs]
        self.link_keys = [r.get('id') for r in self.rows]
        self.total = sum(self.values)

    def is_empty(self) -> bool:
        return not self.values

    def _base(self, slot):
        palette = self.theme.facility_palette
        if slot and slot <= len(palette):
            return self.theme.data_color(palette[slot - 1])
        return self.theme.muted_data

    def draw(self, ax, layout, theme):
        bases = [self._base(r.get('slot')) for r in self.rows]
        outer_vals, outer_colors, outer_names, outer_keys = [], [], [], []
        for row, value, base in zip(self.rows, self.values, bases):
            parts, gap = self.parts(row, value)
            shades = shade_family(base, len(parts), lightest=0.55, toward=theme.shade_toward)
            for (name, part), shade in zip(parts, reversed(shades)):
                outer_vals.append(part)
                outer_colors.append(shade)
                outer_names.append(name)
                outer_keys.append(row.get('id'))
            if gap > 1e-9 * max(value, 1):
                outer_vals.append(gap)
                outer_colors.append('none')
                outer_names.append(None)
                outer_keys.append(None)

        common = dict(startangle=self.start_angle, counterclock=False)
        edge = {'edgecolor': theme.surface}
        inner, _ = ax.pie(self.values, radius=self.inner_radius, colors=bases,
                          wedgeprops={**edge, 'width': self.ring_width, 'linewidth': 1.5}, **common)
        outer, _ = ax.pie(outer_vals, radius=self.inner_radius + self.ring_width + 0.02,
                          colors=outer_colors,
                          wedgeprops={**edge, 'width': self.ring_width, 'linewidth': 1}, **common)
        self.wedges, self.bases = inner, bases
        for wedge, key in zip(outer, outer_keys):
            if key is not None:
                wedge.set_url(self.drill.url(key))

        size = self.autopct_fontsize
        self._label(ax, inner, self.labels, [self.percent(v) for v in self.values], bases,
                    self.inner_radius - self.ring_width / 2, self.inner_label_min, size)
        if layout.name != 'mobile':   # a phone's outer ring is too narrow; the legend carries it
            self._label(ax, outer, outer_names, [self.percent(v) for v in outer_vals], outer_colors,
                        self.inner_radius + self.ring_width / 2 + 0.02, self.outer_label_min, size - 1)
        if self.center_text:
            ax.text(0, 0, self.center_text, ha='center', va='center', fontsize=size + 1,
                    color=theme.text, alpha=0.7)
        ax.set_aspect('equal')

    def legend_cells(self, label, value):
        """Strings for one table-legend row: the name, then its numbers."""
        raise NotImplementedError

    def add_legend(self, ax, layout, theme):
        if not self.table_legend:
            return super().add_legend(ax, layout, theme)
        # Columns, not one string per entry: names left, numbers right-aligned, so
        # they compare down the column in a proportional font. Columns after the
        # second are secondary and drawn muted.
        size = layout.legend_fontsize or self.legend_fontsize
        rows = [self.legend_cells(l, v) for l, v in zip(self.labels, self.values)]
        urls = [self.drill.url(k) if k is not None else None for k in self.link_keys]
        for wedge, url in zip(self.wedges, urls):
            wedge.set_url(url)

        def cell(text, url, alpha=1.0):
            area = TextArea(text, textprops=dict(fontsize=size, color=theme.text, alpha=alpha))
            area._text.set_url(url)
            return area

        def name(text, color, url):
            swatch = DrawingArea(size * 1.4, size, 0, 0)
            rect = Rectangle((0, size * 0.2), size * 1.4, size * 0.6, facecolor=color, edgecolor='none')
            rect.set_url(url)
            swatch.add_artist(rect)
            return HPacker(children=[swatch, cell(text, url)], sep=size * 0.6, align='center')

        sep = size * 0.55
        columns = [VPacker(children=[name(r[0], c, u) for r, c, u in zip(rows, self.bases, urls)],
                           sep=sep, align='left')]
        for j in range(1, len(rows[0])):
            columns.append(VPacker(children=[cell(r[j], u, alpha=1.0 if j == 1 else 0.7)
                                             for r, u in zip(rows, urls)],
                                   sep=sep, align='right'))
        table = AnchoredOffsetbox(loc='center left', child=HPacker(children=columns, sep=size * 1.1,
                                                                   align='top'),
                                  bbox_to_anchor=self.legend_anchor, bbox_transform=ax.transAxes,
                                  frameon=False, borderpad=0, pad=0)
        ax.add_artist(table)

    @staticmethod
    def _label(ax, wedges, names, percents, colors, radius, minimum, size):
        for wedge, name, pct, color in zip(wedges, names, percents, colors):
            if not name or pct < minimum or color == 'none':
                continue
            angle = math.radians((wedge.theta1 + wedge.theta2) / 2)
            ax.text(radius * math.cos(angle), radius * math.sin(angle), name,
                    ha='center', va='center', fontsize=size, fontweight='bold',
                    color=autopct_color_for(color))


class FairShareSunburst(TwoRingPie):
    """Fair share: facilities (share of the machine) inside, their allocation types
    outside. ``data`` rows carry ``share`` for ``value``, and a type's share is of
    its facility, so its wedge is facility x type / 100.
    """

    cache_name = 'fair_share_sunburst'
    cache_maxsize = 24
    empty_message = 'No active facility has a fair share'
    center_text = 'Fair\nshare'
    table_legend = True

    def legend_cells(self, label, value):
        return label, fmt.pct(value, decimals=2)

    def groups(self):
        return [(r, r.get('share')) for r in self.data]

    def parts(self, row, value):
        types = sorted((t for t in row.get('types', []) if t.get('share')), key=lambda t: t['name'])
        total = sum(t['share'] for t in types)
        scale = 100 / total if total > 100 else 1
        parts = [(t['name'], value * t['share'] * scale / 100) for t in types]
        return parts, value * (100 - total * scale) / 100

    def percent(self, value):
        return value


class AllocationSunburst(TwoRingPie):
    """Allocations dashboard: a resource's facilities inside, their allocation types
    outside, in absolute units. ``center`` names the measure ('Allocated', 'Used').
    """

    cache_name = 'allocation_sunburst'
    #: Two per resource tab (Allocated, Used), split by layout and theme.
    cache_maxsize = 144
    empty_message = 'No allocations to chart'
    table_legend = True

    def __init__(self, data: List[Dict], center: str = ''):
        super().__init__(data)
        self.center_text = center

    @staticmethod
    def cache_key(data, center=''):
        return content_hash([data, center])

    def legend_cells(self, label, value):
        # Under 1% keeps two decimals, so a sliver never reads as 0.0%.
        share = self.percent(value)
        return label, fmt.pct(share, decimals=1 if share >= 1 else 2), fmt.number(value)
