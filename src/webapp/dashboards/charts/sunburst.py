"""Sunbursts: groups in the inner ring, their parts outside.

`TwoRingPie` is a `PieChart` drawn twice; each family member names what the groups
and parts are (fair share, allocations, job-history usage by facility).
`PanelSunburst` is the three-ring expanded view: facility, panel, project.
"""

import math
from typing import Dict, List

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.layout import profile
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


def facility_color(theme, slot):
    """A facility's hue: its ``facility_palette`` slot, else the muted data color."""
    palette = theme.facility_palette
    if slot and slot <= len(palette):
        return theme.data_color(palette[slot - 1])
    return theme.muted_data


def label_wedges(ax, wedges, names, percents, colors, radius, minimum, size,
                 orient='horizontal', band=0, weight='bold', ink=None):
    """Name each wedge of ``minimum`` percent or more at ``radius``; ``ink`` None picks per wedge.

    ``orient``: 'horizontal'; 'tangent' (along the arc); 'radial' (along the radius,
    else the arc); 'arc' (the arc, else the radius). A label fitting neither is dropped.
    """
    if orient != 'horizontal':
        unit = _pixels_per_unit(ax)
        floor = size * ax.figure.dpi / 72     # under one em of arc nothing fits
    for wedge, name, pct, color in zip(wedges, names, percents, colors):
        if not name or pct < minimum or color == 'none':
            continue
        mid = (wedge.theta1 + wedge.theta2) / 2
        if orient != 'horizontal':
            arc = math.radians(wedge.theta2 - wedge.theta1) * radius * unit
            if arc < floor:
                continue
        angle = math.radians(mid)
        text = ax.text(radius * math.cos(angle), radius * math.sin(angle), name,
                       ha='center', va='center', fontsize=size, fontweight=weight,
                       color=ink or autopct_color_for(color))
        if orient == 'horizontal':
            continue
        width, height = _text_size(ax, text)
        radial = width <= band * unit and height <= arc
        if orient == 'radial' and radial:
            rotation = mid % 360
        elif width <= arc:
            rotation = (mid - 90) % 360
        elif orient == 'arc' and radial:
            rotation = mid % 360
        else:
            text.remove()
            continue
        text.set_rotation(rotation - 180 if 90 < rotation < 270 else rotation)


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

    def part_url(self, row, name):
        """Drill target of one outer wedge; ``None`` leaves it inert."""
        key = row.get('id')
        return self.drill.url(key) if key is not None else None

    def gap_color(self, base, theme):
        """Fill for a group's shortfall wedge; ``'none'`` draws it blank."""
        return 'none'

    def label_ink(self, theme):
        """One ink for every wedge label, or None to pick per wedge."""
        return None

    def part_tooltip(self, row, name, value):
        """Hover text for one outer wedge; ``name`` None is the gap."""
        return self.tooltip_text(name, value) if name else None

    def draw(self, ax, layout, theme):
        bases = [facility_color(theme, r.get('slot')) for r in self.rows]
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

        inner = self.ring(ax, self.values, self.inner_radius, self.ring_width, bases, theme, 1.5)
        outer_width = self.outer_width or self.ring_width
        outer = self.ring(ax, outer_vals, self.inner_radius + outer_width + 0.02, outer_width,
                          outer_colors, theme, 1)
        self.wedges, self.colors = inner, bases
        for wedge, url in zip(outer, outer_urls):
            if url is not None:
                wedge.set_url(url)
        for wedge, label, value in zip(inner, self.labels, self.values):
            self.tooltip(wedge, self.tooltip_text(label, value))
        for wedge, row, name, value in zip(outer, outer_rows, outer_names, outer_vals):
            self.tooltip(wedge, self.part_tooltip(row, name, value))

        size = self.autopct_fontsize
        ink = self.label_ink(theme)
        label_wedges(ax, inner, self.labels, [self.percent(v) for v in self.values], bases,
                    self.inner_radius - self.ring_width / 2, self.inner_label_min, size,
                    self.inner_label_orient, self.ring_width, ink=ink)
        if layout.name != 'mobile':   # a phone's outer ring is too narrow; the legend carries it
            label_wedges(ax, outer, outer_names, [self.percent(v) for v in outer_vals], outer_colors,
                        self.inner_radius + outer_width / 2 + 0.02, self.outer_label_min,
                        self.outer_label_fontsize or size - 1, self.outer_label_orient, outer_width,
                        ink=ink)
        if self.center_text:
            ax.text(0, 0, self.center_text, ha='center', va='center', fontsize=size + 1,
                    color=theme.text, alpha=0.7)
        ax.set_aspect('equal')


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

    def label_ink(self, theme):
        """White in light, where two inks read as noise (Ben, 2026-10-04); dark keeps the per-wedge pick.

        Light wedges such as NSC fall under 3:1; `e2e/test_dark_mode.py` exempts this chart there.
        """
        return '#fff' if theme.name == 'light' else None


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

    def label_ink(self, theme):
        return None

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


def panel_rows(values, panels, slots):
    """`PanelSunburst` input: ``{projcode: value}`` grouped by facility and panel.

    ``panels`` is `project_panels` output and ``slots`` `facility_slots`. Facilities
    order by slot (unmapped projects last, as Unknown); panels and projects by value.
    """
    tree = {}
    for code, value in values.items():
        if not value or value <= 0:
            continue
        fid, facility, panel = panels.get(code, (None, None, None))
        tree.setdefault((fid, facility), {}).setdefault(panel, []).append((code, float(value)))

    rows = []
    for (fid, facility), by_panel in tree.items():
        plist = [{'name': panel or 'Unknown', 'value': sum(v for _, v in projects),
                  'projects': [{'name': code, 'value': v}
                               for code, v in sorted(projects, key=lambda p: (-p[1], p[0]))]}
                 for panel, projects in by_panel.items()]
        plist.sort(key=lambda p: (-p['value'], p['name']))
        rows.append({'facility': facility or 'Unknown', 'slot': slots.get(fid),
                     'value': sum(p['value'] for p in plist), 'panels': plist,
                     '_order': (facility is None, slots.get(fid) is None, slots.get(fid) or 0,
                                facility or '')})
    rows.sort(key=lambda r: r.pop('_order'))
    return rows


class PanelSunburst(PieChart):
    """Three rings: facilities, their allocation panels, and every project on the rim.

    Rim labels go where one radial line fits; hover names the rest. A project
    under ``min_wedge_deg`` folds into its panel's pale "+N" wedge, and past
    ``max_wedges`` every panel keeps only its ``top_n``. Facility and panel colors
    follow the facility slot, as on the two-ring charts. ``data`` is `panel_rows`.
    """

    cache_name = 'panel_sunburst'
    cache_maxsize = 32
    empty_message = 'No usage data available'
    drill = None
    #: What the rim holds: where a wedge links, and the plural its fold is named in.
    rim_link = links.PROJECT_MODAL
    rim_noun = 'projects'
    LAYOUTS = profile((10, 10), (7, 7), (8, 8), mobile={'legend_placement': 'right'})

    #: (outer radius, width) of the facility, panel and project rings.
    rings = ((0.40, 0.16), (0.72, 0.30), (1.10, 0.36))
    min_wedge_deg = 0.05
    edge_min_deg = 0.5
    max_wedges = 1500
    top_n = 12
    facility_fontsize = 11
    panel_fontsize = 8.5
    project_fontsize = 7

    def __init__(self, data: List[Dict], center: str = ''):
        self.data = data or []
        self.center_text = center

    @staticmethod
    def cache_key(data, center=''):
        return content_hash([data, center])

    def prepare(self):
        self.rows = [r for r in self.data if r.get('value')]
        self.labels = [r['facility'] for r in self.rows]
        self.values = [r['value'] for r in self.rows]
        self.link_keys = [None] * len(self.rows)
        total = sum(self.values)
        floor = total * self.min_wedge_deg / 360
        self.rim = self._fold(lambda projects: [p for p in projects if p['value'] >= floor])
        if len(self.rim) > self.max_wedges:
            self.rim = self._fold(lambda projects: [p for p in projects if p['value'] >= floor]
                                  [:self.top_n])
        self.folded = any(w['others'] for w in self.rim)

    def _fold(self, keep):
        """Rim wedges ``{facility, panel, name, value, others}`` in drawing order;
        ``others`` counts the projects folded into a panel's trailing wedge."""
        rim = []
        for row in self.rows:
            for panel in row['panels']:
                kept = keep(panel['projects'])
                rim += [{'facility': row, 'panel': panel, 'name': p['name'], 'value': p['value'],
                         'others': 0} for p in kept]
                rest = panel['value'] - sum(p['value'] for p in kept)
                if len(kept) < len(panel['projects']) and rest > 0:
                    rim.append({'facility': row, 'panel': panel, 'name': None, 'value': rest,
                                'others': len(panel['projects']) - len(kept)})
        return rim

    def draw(self, ax, layout, theme):
        bases = [facility_color(theme, r.get('slot')) for r in self.rows]
        panels = [p for r in self.rows for p in r['panels']]
        panel_vals = [p['value'] for p in panels]
        panel_names = [p['name'] for p in panels]
        panel_colors = self.panel_colors(bases, theme)
        panel_color_of = {id(p): c for p, c in zip(panels, panel_colors)}

        rim_colors, alternate = [], {}
        for w in self.rim:
            shade = panel_color_of[id(w['panel'])]
            if w['others']:
                rim_colors.append(shade_family(shade, 2, lightest=0.7, toward=theme.shade_toward)[0])
                continue
            i = alternate[id(w['panel'])] = alternate.get(id(w['panel']), -1) + 1
            rim_colors.append(shade if i % 2 == 0 else
                              shade_family(shade, 2, lightest=0.14, toward=theme.shade_toward)[0])

        (r1, w1), (r2, w2), (r3, w3) = self.rings
        inner = self.ring(ax, self.values, r1, w1, bases, theme, 1.5)
        middle = self.ring(ax, panel_vals, r2, w2, panel_colors, theme, 1)
        rim_vals = [w['value'] for w in self.rim]
        outer = self.ring(ax, rim_vals, r3, w3, rim_colors, theme, 0.6)
        self.wedges, self.colors = inner, bases

        for wedge, w in zip(outer, self.rim):
            if wedge.theta2 - wedge.theta1 < self.edge_min_deg:
                wedge.set_linewidth(0)
            if w['others']:
                self.tooltip(wedge, self.tooltip_text(
                    f"{w['others']} other {w['panel']['name']} {self.rim_noun}", w['value']))
            else:
                wedge.set_url(self.rim_link.url(w['name']))
                self.tooltip(wedge, self.tooltip_text(w['name'], w['value']))
        for wedge, label, value in zip(inner, self.labels, self.values):
            self.tooltip(wedge, self.tooltip_text(label, value))
        for wedge, name, value in zip(middle, panel_names, panel_vals):
            self.tooltip(wedge, self.tooltip_text(name, value))

        pct = self.percent
        label_wedges(ax, inner, self.labels, [pct(v) for v in self.values], bases,
                     r1 - w1 / 2, 0, self.facility_fontsize, 'arc', w1)
        label_wedges(ax, middle, panel_names, [pct(v) for v in panel_vals], panel_colors,
                     r2 - w2 / 2, 0, self.panel_fontsize, 'arc', w2)
        rim_names = [w['name'] or f"+{w['others']}" for w in self.rim]
        label_wedges(ax, outer, rim_names, [pct(v) for v in rim_vals], rim_colors,
                     r3 - w3 / 2, 0, self.project_fontsize, 'radial', w3, weight='normal')

        total = sum(self.values)
        ax.text(0, 0, f'{self.center_text}\n{fmt.number(total)}' if self.center_text
                else fmt.number(total), ha='center', va='center',
                fontsize=self.facility_fontsize + 1, color=theme.text, alpha=0.8)
        ax.set_aspect('equal')

    def panel_colors(self, bases, theme):
        """One shade of its facility's hue per panel, darkest for the largest."""
        return [shade for row, base in zip(self.rows, bases)
                for shade in reversed(shade_family(base, len(row['panels']), lightest=0.5,
                                                   toward=theme.shade_toward))]

    def add_legend(self, ax, layout, theme):
        """Facilities with their panels beneath, as one aligned table."""
        panel_colors = iter(self.panel_colors(self.colors, theme))
        rows, colors, indents = [], [], []
        for row, base in zip(self.rows, self.colors):
            rows.append(self.legend_cells(row['facility'], row['value']))
            colors.append(base)
            indents.append(0)
            for panel in row['panels']:
                rows.append(self.legend_cells(panel['name'], panel['value']))
                colors.append(next(panel_colors))
                indents.append(1.6)
        self.draw_table_legend(ax, rows, colors, [None] * len(rows), layout, theme, indents)
