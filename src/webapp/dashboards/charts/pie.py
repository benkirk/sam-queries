"""Pie charts.

`PieChart` owns the `ax.pie` call, the autopct recoloring and the wedge/legend
drill wiring. A subclass supplies the trim (`trim_cumulative`, ~90%), the "Other" derivation, the legend formatter and an
optional drill target. The two-ring (sunburst) variant is `sunburst.py`.
"""

from typing import Dict, List

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.jobs_metrics import jobs_metric_value
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.series import OTHERS, other_label
from webapp.dashboards.charts.theme import UNITY_PALETTE_10, autopct_color_for

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

    #: A drill target (`RowDrill`/`UserDrill`), or None for an inert pie.
    drill = None

    def build(self):
        """Return ``(labels, values, colors, link_keys)``, all same length.

        A ``link_key`` of None marks an inert slice — the "Other" aggregate,
        or an entity with nothing to link to. One rule, as in `series.py`.
        """
        raise NotImplementedError

    def percent(self, value):
        """``value`` as a percent of the whole pie."""
        total = sum(self.values)
        return value * 100 / total if total else 0

    def legend_cells(self, label, value):
        """The name, its share of the pie, then its amount."""
        # Under 1% keeps two decimals, so a sliver never reads as 0.0%.
        share = self.percent(value)
        return label, fmt.pct(share, decimals=1 if share >= 1 else 2), self.legend_amount(value)

    def tooltip_text(self, label, value) -> str:
        """Hover text for one wedge: what its legend row says."""
        return ' · '.join(self.legend_cells(label, value))

    def ring(self, ax, values, radius, width, colors, theme, linewidth):
        """One ring of wedges at ``radius``, edged in the card's surface."""
        wedges, _ = ax.pie(values, radius=radius, colors=colors, startangle=self.start_angle,
                           counterclock=False,
                           wedgeprops={'edgecolor': theme.surface, 'width': width,
                                       'linewidth': linewidth})
        return wedges

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
        for wedge, label, value in zip(wedges, self.labels, self.values):
            self.tooltip(wedge, self.tooltip_text(label, value))
        self.wedges = wedges

    def add_legend(self, ax, layout, theme):
        """A wedge and its legend row share one drill URL. The table is the only
        legend: every pie layout places it at the right (pinned by test)."""
        rows = [self.legend_cells(l, v) for l, v in zip(self.labels, self.values)]
        urls = [self.drill.url(k) if self.drill is not None and k is not None else None
                for k in self.link_keys]
        for wedge, url in zip(self.wedges, urls):
            wedge.set_url(url)
        self.draw_table_legend(ax, rows, self.colors, urls, layout, theme)


class _CumulativePie(PieChart):
    """~90%-cumulative-share slices with one inert remainder. Clickable.

    A subclass names its rows (`entities`) and how to read one (`value_of`,
    `key_of`, `label_of`); `remainder` sizes and labels what is left over.
    """

    def split(self, values_desc):
        """``(keep, n_others)`` for a descending value vector."""
        keep = trim_cumulative(values_desc, cap=self.slice_cap(_PIE_HARD_CAP))
        return keep, len(values_desc) - keep

    def entities(self) -> list:
        """The rows to rank, in any order; empty for nothing to draw."""
        raise NotImplementedError

    def value_of(self, row) -> float:
        raise NotImplementedError

    def key_of(self, row):
        """The drill key, or None for a slice with nothing to link to."""
        raise NotImplementedError

    def label_of(self, row) -> str:
        return self.key_of(row)

    def remainder(self, values_desc, keep):
        """``(label, value)`` for the slice past ``keep``, or None."""
        n_others = len(values_desc) - keep
        if n_others <= 0:
            return None
        return other_label(n_others), sum(values_desc[keep:])

    def build(self):
        rows = self.entities()
        if not rows:
            return [], [], [], []
        data = sorted(rows, key=self.value_of, reverse=True)
        values_desc = [self.value_of(r) for r in data]
        keep, _n_others = self.split(values_desc)

        keys = [self.key_of(r) for r in data[:keep]]
        labels = [self.label_of(r) for r in data[:keep]]
        values = list(values_desc[:keep])
        colors = self.theme.data_colors(list(UNITY_PALETTE_10[:keep]))

        rest = self.remainder(values_desc, keep)
        if rest is not None:
            keys.append(None)                  # inert slice
            labels.append(rest[0])
            values.append(rest[1])
            colors.append(self.theme.muted_data)
        return labels, values, colors, keys


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

    def legend_amount(self, value):
        return fmt.size(value)

    def entities(self):
        return self.entity_data

    def value_of(self, row):
        # float at the single entry point: scan rollups arrive as Decimal from
        # Postgres, and Decimal and float do not mix in arithmetic or matplotlib.
        return float(row['value'])

    def key_of(self, row):
        return row['id']

    def label_of(self, row):
        return row['name'] or f"{'uid' if self.kind == 'owner' else 'gid'} {row['id']}"


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

    def entities(self):
        return [d for d in self.user_data if float(d.get(self.metric) or 0) > 0]

    def value_of(self, row):
        return float(row[self.metric])

    def key_of(self, row):
        return row['username']


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

    def __init__(self, entity_data, metric='cpu_hours', *, row_attr='data-job-user'):
        self.entity_data = entity_data or {}
        self.metric = metric
        self.row_attr = row_attr

    @staticmethod
    def cache_key(entity_data, metric='cpu_hours', *, row_attr='data-job-user'):
        """row_attr joins the key: identical usage vectors rendered for
        different entity kinds carry different drill anchors."""
        rows = (entity_data or {}).get('rows') or []
        totals = (entity_data or {}).get('totals') or {}
        payload = [(r.get('value'), jobs_metric_value(r, metric, 'cpu_hours'))
                   for r in rows]
        return content_hash([payload,
                             jobs_metric_value(totals, metric, 'cpu_hours'),
                             str(metric), str(row_attr)])

    @property
    def drill(self):
        return links.RowDrill(self.row_attr)

    def entities(self):
        self._total = jobs_metric_value(self.entity_data.get('totals') or {},
                                        self.metric, 'cpu_hours')
        return (self.entity_data.get('rows') or []) if self._total > 0 else []

    def value_of(self, row):
        # Upstream sorts by combined hours; the pie ranks by the *chosen* metric,
        # so the Jobs view leads with the most job-count-heavy users.
        return jobs_metric_value(row, self.metric, 'cpu_hours')

    def key_of(self, row):
        return row.get('value')

    def label_of(self, row):
        key = row.get('value')
        return key if key is not None else '(unknown)'

    def remainder(self, values_desc, keep):
        rest = self._total - sum(values_desc[:keep])
        # Rows past the plugin's limit are in the total too: the count is unknown.
        return (OTHERS, rest) if rest > 1e-9 else None
