"""Pie charts.

Five charts shared a 16-line `subplots` + `ax.pie` + autopct-recolour block
**byte-identical in all five**, plus an 8-line wedge/legend click-wiring loop
repeated three times. What actually differed was small and is now the whole of
each subclass:

- **trim strategy** — a fixed top-10 cap (`trim_fixed_cap`) or a ~90%
  cumulative share (`trim_cumulative`). Note this splits exactly the same way
  the cache keys do: the two fixed-cap pies are also the two with no drill.
- **"Other" derivation** — three variants: count-based remainder, sum of the
  tail, or `totals - kept` from an upstream-truncated envelope.
- **legend formatter** — `fmt.number` for counts and charges, `fmt.size` for
  bytes.
- **drill target** — four, or none.

`FacilityPie` and `AllocationTypePie` end up as attribute-only subclasses,
which is the declarative tier this repo already reaches for elsewhere
(`CrudSpec`, the Flask-Admin view promotion dict).
"""

from typing import Dict, List

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.jobs_metrics import jobs_metric_value
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_GRAY_LIGHT, UNITY_PALETTE_10, autopct_color_for,
)

_PIE_START_ANGLE = 60
_PIE_MAX_ENTITIES = 10

#: Cumulative-share trim: show entities up to ~90% of the total, but never
#: more than 9 named slices (the palette has 10; keep "Other" distinct).
_PIE_CUM_SHARE = 0.90
_PIE_HARD_CAP = 9


def trim_fixed_cap(names: list, values: list) -> tuple[list, list]:
    """Sort by value descending, cap at 10, group remainder as 'Others (N)'."""
    paired = sorted(zip(names, values), key=lambda x: x[1], reverse=True)
    names_s = [p[0] for p in paired]
    values_s = [p[1] for p in paired]
    if len(names_s) > _PIE_MAX_ENTITIES:
        n_others = len(names_s) - _PIE_MAX_ENTITIES
        others_sum = sum(values_s[_PIE_MAX_ENTITIES:])
        names_s = names_s[:_PIE_MAX_ENTITIES] + [f'Others ({n_others})']
        values_s = values_s[:_PIE_MAX_ENTITIES] + [others_sum]
    return names_s, values_s


def trim_cumulative(values_desc: list) -> int:
    """How many leading (descending) entries to show individually.

    The fewest whose cumulative share reaches ``_PIE_CUM_SHARE``, capped at
    ``_PIE_HARD_CAP``. The remainder (if any) is meant to collapse into one
    'Other' slice. Returns ``len(values_desc)`` when everything fits.
    """
    total = sum(values_desc)
    if total <= 0:
        return min(len(values_desc), _PIE_HARD_CAP)
    cum = 0.0
    for i, v in enumerate(values_desc):
        cum += v
        if i + 1 >= _PIE_HARD_CAP:
            return i + 1
        if cum / total >= _PIE_CUM_SHARE:
            return i + 1
    return len(values_desc)


class PieChart(BaseChart):
    """Shared pie rendering. Subclasses supply slices and, optionally, a drill."""

    LAYOUTS = profile((7, 4))
    grid = None                       # pies have no grid

    start_angle = _PIE_START_ANGLE
    pctdistance = 0.85
    #: Wedges under this percentage get no inline label — they'd overlap.
    autopct_min_pct = 5
    autopct_fontsize = 8
    #: 9pt on a (7,4) figure — see the note on PaceChart.legend_fontsize.
    legend_fontsize = 9
    legend_anchor = (1.01, 0.5)

    #: A drill target (`RowDrill`/`UserDrill`) or None. When None the legend
    #: is built but its return value discarded, exactly as before — the two
    #: allocation pies have never been clickable.
    drill = None

    def build(self):
        """Return ``(labels, values, colors, link_keys)``, all same length.

        A ``link_key`` of None marks an inert slice — the "Other" aggregate,
        or an entity with nothing to link to. One rule, as in `series.py`.
        """
        raise NotImplementedError

    def legend_label(self, label, value) -> str:
        return f'{label} ({fmt.number(value)})'

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
        # Percent labels take their colour from the WEDGE's luminance, not the
        # page — already correct in both themes, so no theme argument.
        for at, wedge_color in zip(autotexts, self.colors):
            at.set_color(autopct_color_for(wedge_color))
            at.set_fontweight('bold')
            at.set_fontsize(self.autopct_fontsize)
        self.wedges = wedges

    def add_legend(self, ax, layout, theme):
        legend_labels = [self.legend_label(l, v)
                         for l, v in zip(self.labels, self.values)]
        legend = ax.legend(self.wedges, legend_labels, loc='center left',
                           bbox_to_anchor=self.legend_anchor,
                           fontsize=self.legend_fontsize)
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


class _FixedCapPie(PieChart):
    """Top-10 cap with an 'Others (N)' remainder; never clickable.

    The two allocations-dashboard pies. Both take a single list argument and
    differ only in which two dict keys they read — which is the whole of each
    subclass below.
    """

    #: ``(name_key, value_key)`` in the incoming row dicts.
    fields: tuple = None

    def __init__(self, data: List[Dict]):
        self.data = data or []

    @staticmethod
    def cache_key(data):
        return content_hash(data)

    def build(self):
        name_key, value_key = self.fields
        names, values = trim_fixed_cap([d[name_key] for d in self.data],
                                       [d[value_key] for d in self.data])
        colors = list(UNITY_PALETTE_10[:len(names)])
        return names, values, colors, [None] * len(names)


class FacilityPie(_FixedCapPie):
    """Allocation distribution by facility. Title is rendered in the
    surrounding HTML (see allocations dashboard template)."""

    cache_name = 'facility_pie_chart'
    #: One entry per resource filter combination; few distinct views.
    cache_maxsize = 32
    empty_message = 'No facility data available'
    fields = ('facility', 'annualized_rate')


class AllocationTypePie(_FixedCapPie):
    """Allocation distribution by type within a facility."""

    cache_name = 'allocation_type_pie_chart'
    #: One entry per (resource, facility) filter combination.
    cache_maxsize = 64
    empty_message = 'No allocation type data available'
    fields = ('allocation_type', 'total_amount')


class _CumulativePie(PieChart):
    """~90%-cumulative-share slices with one inert 'Other'. Clickable."""

    def split(self, values_desc):
        """``(keep, n_others)`` for a descending value vector."""
        keep = trim_cumulative(values_desc)
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
        colors = list(UNITY_PALETTE_10[:keep])

        if n_others > 0:
            keys.append(None)                  # inert slice
            labels.append(f'Other ({n_others})')
            values.append(sum(values_desc[keep:]))
            colors.append(UNITY_NCAR_GRAY_LIGHT)

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
        colors = list(UNITY_PALETTE_10[:keep])

        if n_others > 0:
            keys.append(None)                  # inert slice
            labels.append(f'Other ({n_others})')
            values.append(sum(values_desc[keep:]))
            colors.append(UNITY_NCAR_GRAY_LIGHT)

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
        colors = list(UNITY_PALETTE_10[:keep])

        remainder = total - sum(values)
        if remainder > 1e-9:
            keys.append(None)                  # inert slice
            labels.append('Other')
            values.append(remainder)
            colors.append(UNITY_NCAR_GRAY_LIGHT)

        return labels, values, colors, keys
