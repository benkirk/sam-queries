"""Two-ring (sunburst) pies: groups in the inner ring, their parts in the outer ring.

`TwoRingPie` is a `PieChart` drawn twice; each family member names what the groups
and parts are (fair share, allocations, job-history usage by facility).
"""

import math
from typing import Dict, List

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.pie import PieChart
from webapp.dashboards.charts.theme import autopct_color_for, shade_family


def _text_size(ax, text):
    """Unrotated ``(width, height)`` of ``text`` in display pixels."""
    canvas = ax.figure.canvas
    renderer = canvas.get_renderer() if hasattr(canvas, 'get_renderer') else ax.figure._get_renderer()
    extent = text.get_window_extent(renderer)
    return extent.width, extent.height


def _pixels_per_unit(ax):
    ax.apply_aspect()
    (x0, _), (x1, _) = ax.transData.transform([(0, 0), (1, 0)])
    return abs(x1 - x0)


class TwoRingPie(PieChart):
    """Groups in the inner ring, their parts in the outer ring, each part a shade of
    its group's hue (``facility_palette`` slot). Every wedge and legend entry drills.

    ``data`` = ``[{'id', 'facility', 'slot', 'value', 'types': [{'name', 'value'}]}]``;
    parts summing above their group are scaled to fit, and a shortfall is a blank wedge.
    """

    drill = links.FACILITY_ROW

    inner_radius = 0.66
    ring_width = 0.3
    #: Outer ring's width when it differs from the inner one's.
    outer_width = None
    #: Smallest wedge (percent of the whole) that carries a direct label.
    inner_label_min = 5
    outer_label_min = 6
    center_text = ''
    #: 'horizontal', or 'tangent' / 'radial': set along the arc / the radius
    #: (radial falls back to the arc), dropping any label that fits neither.
    inner_label_orient = 'horizontal'
    outer_label_orient = 'horizontal'
    outer_label_fontsize = None

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

    def prepare(self):
        pairs = [(r, v) for r, v in self.groups() if v]
        self.rows = [r for r, _ in pairs]
        self.labels = [r['facility'] for r in self.rows]
        self.values = [v for _, v in pairs]
        self.link_keys = [r.get('id') for r in self.rows]

    def is_empty(self) -> bool:
        return not self.values

    def part_url(self, row, name):
        """Drill target of one outer wedge; ``None`` leaves it inert."""
        key = row.get('id')
        return self.drill.url(key) if key is not None else None

    def gap_color(self, base, theme):
        """Fill for a group's shortfall wedge; ``'none'`` draws it blank."""
        return 'none'

    def part_tooltip(self, row, name, value):
        """Hover text for one wedge: its legend cells; ``name`` None is the gap."""
        return ' · '.join(self.legend_cells(name, value)) if name else None

    def _base(self, slot):
        palette = self.theme.facility_palette
        if slot and slot <= len(palette):
            return self.theme.data_color(palette[slot - 1])
        return self.theme.muted_data

    def draw(self, ax, layout, theme):
        bases = [self._base(r.get('slot')) for r in self.rows]
        outer_vals, outer_colors, outer_names, outer_urls, outer_rows = [], [], [], [], []
        for row, value, base in zip(self.rows, self.values, bases):
            parts, gap = self.parts(row, value)
            shades = shade_family(base, len(parts), lightest=0.55, toward=theme.shade_toward)
            for (name, part), shade in zip(parts, reversed(shades)):
                outer_vals.append(part)
                outer_colors.append(shade)
                outer_names.append(name)
                outer_urls.append(self.part_url(row, name))
                outer_rows.append(row)
            if gap > 1e-9 * max(value, 1):
                outer_vals.append(gap)
                outer_colors.append(self.gap_color(base, theme))
                outer_names.append(None)
                outer_urls.append(None)
                outer_rows.append(row)

        common = dict(startangle=self.start_angle, counterclock=False)
        edge = {'edgecolor': theme.surface}
        inner, _ = ax.pie(self.values, radius=self.inner_radius, colors=bases,
                          wedgeprops={**edge, 'width': self.ring_width, 'linewidth': 1.5}, **common)
        outer_width = self.outer_width or self.ring_width
        outer, _ = ax.pie(outer_vals, radius=self.inner_radius + outer_width + 0.02,
                          colors=outer_colors,
                          wedgeprops={**edge, 'width': outer_width, 'linewidth': 1}, **common)
        self.wedges, self.colors = inner, bases
        for wedge, url in zip(outer, outer_urls):
            if url is not None:
                wedge.set_url(url)
        for wedge, label, value in zip(inner, self.labels, self.values):
            self.tooltip(wedge, ' · '.join(self.legend_cells(label, value)))
        for wedge, row, name, value in zip(outer, outer_rows, outer_names, outer_vals):
            self.tooltip(wedge, self.part_tooltip(row, name, value))

        size = self.autopct_fontsize
        self._label(ax, inner, self.labels, [self.percent(v) for v in self.values], bases,
                    self.inner_radius - self.ring_width / 2, self.inner_label_min, size,
                    self.inner_label_orient, self.ring_width)
        if layout.name != 'mobile':   # a phone's outer ring is too narrow; the legend carries it
            self._label(ax, outer, outer_names, [self.percent(v) for v in outer_vals], outer_colors,
                        self.inner_radius + outer_width / 2 + 0.02, self.outer_label_min,
                        self.outer_label_fontsize or size - 1, self.outer_label_orient, outer_width)
        if self.center_text:
            ax.text(0, 0, self.center_text, ha='center', va='center', fontsize=size + 1,
                    color=theme.text, alpha=0.7)
        ax.set_aspect('equal')

    @staticmethod
    def _label(ax, wedges, names, percents, colors, radius, minimum, size,
               orient='horizontal', band=0):
        for wedge, name, pct, color in zip(wedges, names, percents, colors):
            if not name or pct < minimum or color == 'none':
                continue
            mid = (wedge.theta1 + wedge.theta2) / 2
            angle = math.radians(mid)
            text = ax.text(radius * math.cos(angle), radius * math.sin(angle), name,
                           ha='center', va='center', fontsize=size, fontweight='bold',
                           color=autopct_color_for(color))
            if orient == 'horizontal':
                continue
            width, height = _text_size(ax, text)
            unit = _pixels_per_unit(ax)
            arc = math.radians(wedge.theta2 - wedge.theta1) * radius * unit
            if orient == 'radial' and width <= band * unit and height <= arc:
                rotation = mid % 360
            elif width <= arc:          # 'tangent', and radial's fallback
                rotation = (mid - 90) % 360
            else:
                text.remove()
                continue
            text.set_rotation(rotation - 180 if 90 < rotation < 270 else rotation)


class FairShareSunburst(TwoRingPie):
    """Fair share: facilities (share of the machine) inside, their allocation types
    outside. ``data`` rows carry ``share`` for ``value``, and a type's share is of
    its facility, so its wedge is facility x type / 100.
    """

    cache_name = 'fair_share_sunburst'
    cache_maxsize = 24
    empty_message = 'No active facility has a fair share'
    center_text = 'Fair\nshare'

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

    def __init__(self, data: List[Dict], center: str = ''):
        super().__init__(data)
        self.center_text = center

    @staticmethod
    def cache_key(data, center=''):
        return content_hash([data, center])


class JobsFacilitySunburst(AllocationSunburst):
    """Job history By Project, grouped: facilities inside, each one's top projects
    outside in usage order. The remainder is other projects' real usage, so it is a
    pale tint rather than a blank. A project wedge drills to its table row only when
    ``linked`` (the table holds the top 25 alone); facilities have no row to open.
    """

    cache_name = 'jobs_facility_sunburst'
    cache_maxsize = 64
    empty_message = 'No usage data available'
    drill = links.JOB_PROJECT
    #: Thin facility core, thick project rim: a projcode laid along the radius
    #: needs the rim's width, and then only one line's height of arc.
    inner_radius = 0.46
    ring_width = 0.18
    outer_width = 0.5
    inner_label_orient = 'tangent'
    outer_label_orient = 'radial'
    outer_label_fontsize = 6.5
    outer_label_min = 1

    def prepare(self):
        super().prepare()
        self.link_keys = [None] * len(self.rows)

    def parts(self, row, value):
        types = [(t['name'], t['value']) for t in row.get('types', []) if t.get('value')]
        return types, value - sum(v for _, v in types)

    def part_url(self, row, name):
        linked = {t['name'] for t in row.get('types', []) if t.get('linked')}
        return self.drill.url(name) if name in linked else None

    def gap_color(self, base, theme):
        return shade_family(base, 2, lightest=0.8, toward=theme.shade_toward)[0]

    def part_tooltip(self, row, name, value):
        return super().part_tooltip(row, name or f'Other {row["facility"]} projects', value)
