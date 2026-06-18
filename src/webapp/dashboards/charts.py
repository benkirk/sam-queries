"""
Chart generation utilities for server-side rendering.

All chart functions are decorated with `@caching.chart_cached(name=..., maxsize=...)`,
which caches rendered SVGs by content hash through the unified `Caching` facade
(see webapp.caching). The public API is unchanged -- callers pass normal Python
objects; hashing and caching are handled internally.

Cache keys are stable MD5 hex digests of the input data, so key computation is
O(n) time but O(1) memory regardless of input size. This is safe for large
inputs (e.g. a year of 5-minute history) where materialising the full data as
a hashable tuple would allocate several MB per call even on a cache hit.

NOTE: ChartCache is per-process and thread-safe. It is safe with both gunicorn
sync workers (each worker is a forked process) and gthread workers.
"""

from io import StringIO
from pathlib import Path
from typing import List, Dict
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.font_manager
import matplotlib.pyplot as plt
import numpy as np

from flask import url_for

from sam import fmt
from webapp.caching import caching
from webapp.caching.chart import content_hash as _content_hash  # legacy alias used by _pace_cache_key


# ---------------------------------------------------------------------------
# Unity NCAR chart styling — runs once at module import.
#
# Two pieces:
#   1. Register Poppins TTFs with matplotlib's font manager. Skipped silently
#      if the directory is empty / missing, so the import still works in
#      environments where the static assets haven't been deployed yet.
#
#      NOTE: these .ttf files are a deliberate SERVER-SIDE copy for matplotlib
#      and are NOT the same assets the browser uses. The browser loads the
#      vendored .woff2 set under static/vendor/poppins/ (see vendor_assets.py);
#      matplotlib's font_manager cannot read woff2 (only ttf/otf/afm), so it
#      needs its own ttf set here. Do NOT delete static/fonts/poppins/*.ttf as
#      "unreferenced" — they are referenced here, and a regression test
#      (test_chart_fonts.py) asserts findfont('Poppins') still resolves.
#   2. Apply rcParams that mirror the editorial flat look on the HTML side:
#      Poppins text, space-blue chrome, hairline gray grid, no top/right
#      spines, transparent figure/axes (we already savefig with
#      transparent=True so legend/grid colors carry against any backdrop).
# ---------------------------------------------------------------------------

_FONT_DIR = Path(__file__).resolve().parent.parent / 'static' / 'fonts' / 'poppins'
if _FONT_DIR.exists():
    for _ttf in _FONT_DIR.glob('*.ttf'):
        matplotlib.font_manager.fontManager.addfont(str(_ttf))

plt.rcParams.update({
    'font.family':        ['Poppins', 'DejaVu Sans'],   # fallback if Poppins missing
    'font.size':          11,
    'axes.titleweight':   600,
    'axes.titlecolor':    '#011837',   # ncar-space-blue
    'axes.labelcolor':    '#011837',
    'axes.labelweight':   600,
    'axes.edgecolor':     '#011837',
    'axes.spines.top':    False,
    'axes.spines.right':  False,
    'xtick.color':        '#011837',
    'ytick.color':        '#011837',
    'grid.color':         '#bbbcbc',   # ncar-gray-light
    'grid.alpha':         0.4,
    'grid.linewidth':     0.5,
    'legend.fontsize':    11,
    'legend.frameon':     False,
    'figure.facecolor':   'none',
    'axes.facecolor':     'none',
})


# Unity NCAR palette ordered for chart use. Indices 0-2 are the brand spine
# (blue → navy → vermilion); 3-4 the warm accents (gold, orange); 5-7 the
# teal family (teal, sky, light-blue); 8-9 are tertiary fillers. Sequential
# visual distinction at small sizes (pie wedges).
UNITY_PALETTE_10 = (
    '#0057c2',  # ncar-blue
    '#00357a',  # ncar-navy
    '#ff1f1f',  # ncar-vermilion
    '#fdd509',  # ncar-gold
    '#faa119',  # ncar-orange
    '#00818F',  # ncar-teal
    '#42C0FF',  # ncar-sky
    '#00A2B4',  # ncar-light-blue
    '#011837',  # ncar-space-blue
    '#97999b',  # ncar-gray
)

UNITY_NCAR_BLUE       = '#0057c2'
UNITY_NCAR_NAVY       = '#00357a'
UNITY_NCAR_VERMILION  = '#ff1f1f'
UNITY_NCAR_ORANGE     = '#faa119'
UNITY_NCAR_GOLD       = '#fdd509'
UNITY_NCAR_TEAL       = '#00818F'
UNITY_NCAR_SKY        = '#42C0FF'
UNITY_NCAR_LIGHT_BLUE = '#00A2B4'
UNITY_NCAR_SPACE_BLUE = '#011837'
UNITY_NCAR_GRAY_LIGHT = '#bbbcbc'
UNITY_NCAR_GRAY       = '#97999b'


# Stacked-area categorical palette. Family-grouped: each color family's
# shades sit adjacent (gold→yellow-33→yellow-66, orange→orange-33→…),
# then we move to the next family. Within a family, ordered saturated →
# pale. Ordered warm → cool so the highest-rank bands (which stackplot
# puts at the bottom, visually most prominent) get the loudest warm
# anchors (gold, orange, vermilion), then transition through teal /
# sky / blue / navy as rank decreases.
UNITY_STACK_20 = (
    # Gold family — bright warm anchor, highest visual prominence
    '#fdd509',   # 1.  gold
    '#fbe174',   # 2.  yellow-33
    '#f8ebb7',   # 3.  yellow-66

    # Orange family
    '#faa119',   # 4.  orange
    '#fabe72',   # 5.  orange-33
    '#f8dbb5',   # 6.  orange-66

    # Vermilion (single; no lighter variant in Unity's secondary ladder)
    '#ff1f1f',   # 7.  vermilion

    # Teal family — warm-cool transition
    '#00818F',   # 8.  teal
    '#00a2b4',   # 9.  ucar-base-33
    '#71c0cb',   # 10. ucar-base-66

    # Sky / cyan family
    '#42c0ff',   # 11. sky
    '#86d3fc',   # 12. ncar-light-33
    '#34e1f4',   # 13. ucar-light (cyan)
    '#86e8f5',   # 14. ucar-light-33

    # Blue family (deep cool)
    '#0057c2',   # 15. ncar-blue
    '#5a77a6',   # 16. blue-33
    '#a8b7ce',   # 17. blue-66
    '#adc2e6',   # 18. ncar-base-66

    # Navy / slate family
    '#00357a',   # 19. navy
    '#556379',   # 20. space-blue-33
)

# 10-color variant: distinct tuple (NOT UNITY_STACK_20[:10], which would be
# 3 golds + 3 oranges + vermilion + 3 teals — too warm-loaded). Picks 2
# shades from each main hue family plus vermilion, in the same warm-to-cool
# order as _20 so the same chart looks like a subsetted version, not a
# different palette.
UNITY_STACK_10 = (
    '#fdd509',   # 1.  gold
    '#fbe174',   # 2.  yellow-33
    '#faa119',   # 3.  orange
    '#fabe72',   # 4.  orange-33
    '#ff1f1f',   # 5.  vermilion
    '#00818F',   # 6.  teal
    '#00a2b4',   # 7.  ucar-base-33
    '#42c0ff',   # 8.  sky
    '#0057c2',   # 9.  ncar-blue
    '#5a77a6',   # 10. blue-33
)


def _autopct_color_for(bg_hex: str) -> str:
    """Pick a readable text color for percent labels on a colored pie wedge.

    Returns space-blue on light wedges (gold, sky) and white on dark wedges
    (blue, navy, vermilion). Luminance threshold ~0.6 — empirically tuned
    against UNITY_PALETTE_10."""
    r, g, b = (int(bg_hex[i:i+2], 16) / 255 for i in (1, 3, 5))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return UNITY_NCAR_SPACE_BLUE if lum > 0.6 else '#fff'


def _project_modal_url(projcode: str) -> str:
    """Resolve the project-details modal route, with blueprint prefix.
    Used to mark legend entries with set_url() — svg-chart-links.js
    intercepts clicks on these anchors and dispatches the modal."""
    return url_for('user_dashboard.project_details_modal', projcode=projcode)


def _user_modal_url(username: str) -> str:
    """Resolve the user-card modal route, with blueprint prefix."""
    return url_for('admin_dashboard.user_card', username=username)


def _to_display_tz(naive_utc_ts: datetime) -> datetime:
    """Naive-UTC → naive-local for matplotlib axis rendering.  Strips tzinfo
    after conversion so the existing naive-datetime plotting path is
    unchanged (matplotlib renders the local-clock values directly)."""
    return fmt.to_local_dt(naive_utc_ts).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 1. Usage timeseries (user dashboard)
# ---------------------------------------------------------------------------

_USAGE_METRIC_YLABELS = {
    'charges':    'Charges',
    'jobs':       'Job Count',
    'core_hours': 'Core-Hours',
}


def _usage_timeseries_cache_key(daily_data, link_to_day_rows=False, metric='charges'):
    return _content_hash([_content_hash(daily_data), bool(link_to_day_rows), metric])


# One entry per (resource, time-range, metric) combination active in the current snapshot window.
@caching.chart_cached(name='usage_timeseries', maxsize=128,
                      key_fn=_usage_timeseries_cache_key)
def generate_usage_timeseries_matplotlib(daily_data, link_to_day_rows=False,
                                         metric='charges') -> str:
    """
    Generate time-series bar chart using Matplotlib.

    Args:
        daily_data: Dict with 'dates' and 'values' keys. Values can be
            per-day charges, job counts, or core-hours depending on
            ``metric``; the renderer is metric-agnostic.
        link_to_day_rows: When True, each bar is wrapped in an
            ``<a xlink:href="#day-bar-YYYY-MM-DD">`` anchor via
            ``Rectangle.set_url()``. ``svg-chart-links.js`` intercepts
            those clicks and expands the matching day row in the
            Historical Usage card below. Zero-value days are skipped.
        metric: One of ``'charges'`` / ``'jobs'`` / ``'core_hours'``.
            Controls the y-axis label and the cache key so the three
            variants are stored independently.

    Returns:
        SVG string ready for template rendering
    """
    if not daily_data:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    dates = list(daily_data.get('dates') or [])
    vals  = list(daily_data.get('values') or [])

    combined = sorted(zip(dates, vals))
    if not combined:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    dates, vals = zip(*combined)
    dates = list(dates)
    vals = list(vals)

    fig, ax = plt.subplots(figsize=(18, 5))
    bars = ax.bar(dates, vals, width=1, lw=2,
                  color=UNITY_NCAR_BLUE, edgecolor=UNITY_NCAR_NAVY)
    if link_to_day_rows:
        for d, value, rect in zip(dates, vals, bars.patches):
            if not value:
                continue
            iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)
            rect.set_url(f'#day-bar-{iso}')
    ax.set_ylabel(_USAGE_METRIC_YLABELS.get(metric, 'Charges'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 1b. Disk usage stacked-area chart (Resource Usage Details — DISK)
# ---------------------------------------------------------------------------

_BYTES_PER_GIB = 1024 ** 3
_BYTES_PER_TIB = 1024 ** 4
_BYTES_PER_PIB = 1024 ** 5


def _disk_usage_stacked_area_cache_key(timeseries, link_kind=None):
    return _content_hash([_content_hash(timeseries), link_kind or ''])


# `link_kind` ('user' | None) controls whether legend usernames are
# wrapped in <a xlink:href> SVG anchors targeting the user-details
# modal. None = no links (default, backward compatible). Mirrors the
# user_proj_stacked_area chart's pattern.
@caching.chart_cached(name='disk_usage_stacked_area', maxsize=128,
                      key_fn=_disk_usage_stacked_area_cache_key)
def generate_disk_usage_stacked_area(timeseries, link_kind=None) -> str:
    """Render a stacked-area chart of disk bytes vs time.

    Args:
        timeseries: dict shaped as ``sam.queries.disk_usage.get_disk_usage_timeseries_by_user``
                    returns: ``{'dates': [...], 'series': [{'username','values'}, ...]}``.
                    The last series is conventionally ``'Others'`` (rendered last
                    so it sits on top of the named-user stack).
        link_kind: ``'user'`` to make legend usernames clickable to
            ``/admin/user/<username>`` (user-details modal), or ``None``
            for no links. The 'Others' bucket is never linked.
            ``svg-chart-links.js`` intercepts the click and shows the
            modal — ``set_url()`` only emits the ``<a>`` wrapper.

    Y-axis is auto-scaled to TiB or PiB based on the peak stacked total
    (>= 1 PiB → PiB, else TiB). X-axis is date-formatted. Legend on the
    right.
    """
    if not timeseries or not timeseries.get('dates') or not timeseries.get('series'):
        return '<div class="text-center text-muted">No disk-usage history for this period</div>'

    dates = list(timeseries['dates'])
    series = list(timeseries['series'])
    if not dates or not series:
        return '<div class="text-center text-muted">No disk-usage history for this period</div>'

    stacked_totals = [
        sum(s['values'][i] for s in series)
        for i in range(len(dates))
    ]
    peak = max(stacked_totals) if stacked_totals else 0
    if peak >= _BYTES_PER_PIB:
        scale = _BYTES_PER_PIB
        unit_label = 'PiB'
    else:
        scale = _BYTES_PER_TIB
        unit_label = 'TiB'

    fig, ax = plt.subplots(figsize=(18, 5))
    scaled_series = [
        [v / scale for v in s['values']]
        for s in series
    ]
    # Others (always first per get_disk_usage_timeseries_by_user) gets a
    # neutral grey so it doesn't compete with the named-user palette.
    # Named users use the Unity 10-color stacked palette.
    colors = []
    cycle_idx = 0
    for s in series:
        if s['username'] == 'Others':
            colors.append(UNITY_NCAR_GRAY_LIGHT)  # NCAR ncar-gray-light
        else:
            colors.append(UNITY_STACK_10[cycle_idx % 10])
            cycle_idx += 1
    ax.stackplot(dates, *scaled_series, colors=colors, alpha=0.85)
    ax.set_ylabel(f'Disk usage ({unit_label})')
    ax.grid(True, alpha=0.3)

    # Build the legend explicitly with reversed-order Patch handles so
    # each handle/text artist is addressable by index for set_url() —
    # mirrors the user_proj_stacked_area / pace chart pattern. Reverses
    # the visual stack so legend reads top-to-bottom in the same order.
    import matplotlib.patches as mpatches
    rev_series = list(reversed(series))
    rev_colors = list(reversed(colors))
    handles = [mpatches.Patch(color=c, label=s['username'])
               for s, c in zip(rev_series, rev_colors)]
    leg = ax.legend(
        handles=handles,
        loc='center left',
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        fontsize=11,
        title_fontsize=12,
    )

    if link_kind == 'user':
        for s, patch, text in zip(rev_series, leg.get_patches(), leg.get_texts()):
            if s['username'] == 'Others':
                continue
            url = _user_modal_url(s['username'])
            patch.set_url(url)
            text.set_url(url)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 1c. User / project queue load stacked-area chart (status drill-down)
# ---------------------------------------------------------------------------

def _user_proj_stacked_area_cache_key(timeseries, link_kind=None, rank_by='current'):
    return _content_hash([_content_hash(timeseries), link_kind or '', rank_by])


# `link_kind` ('user' | 'project' | None) controls whether legend
# entries are wrapped in <a xlink:href> SVG anchors targeting the
# user- or project-details modal. None = no links (default, backward
# compatible).
@caching.chart_cached(name='user_proj_stacked_area', maxsize=128,
                      key_fn=_user_proj_stacked_area_cache_key)
def generate_user_proj_stacked_area(timeseries, link_kind=None,
                                    rank_by: str = 'current') -> str:
    """Render a stacked-area chart of per-user or per-project queue load.

    Args:
        timeseries: dict shaped as
            ``system_status.queries.user_proj_queues.get_user_proj_queue_timeseries``
            returns: ``{'dates','series','metric_label','group_by_label'}``.
            ``series[0]`` is conventionally ``'Others'`` (rendered first
            so it sits at the bottom of the stack with a neutral colour).
        link_kind: 'user' to make legend usernames clickable to
            ``/admin/user/<username>`` (user-details modal), 'project'
            to make legend projcodes clickable to
            ``/project-details-modal/<projcode>`` (project-details
            modal), or None for no links. The 'Others' bucket is never
            linked. svg-chart-links.js intercepts the click and shows
            the modal — set_url() only emits the ``<a>`` wrapper.
        rank_by: which value to quote in parens after each legend
            entry. Mirrors the route's `rank_by` selector so the legend
            number tracks the active sort:
              - ``'current'`` → ``values[-1]`` (latest tick).
              - ``'peak'``    → ``max(values)`` over the window.
            Unknown values fall back to ``'current'``.

    Y-axis is integer counts (jobs). X-axis is datetime-formatted at
    5-minute snapshot grain. Legend on the right, reversed so it reads
    top-to-bottom matching the visual stack order.
    """
    if not timeseries or not timeseries.get('dates') or not timeseries.get('series'):
        return ('<div class="text-center text-muted py-4">'
                'No per-user / per-project history for this period</div>')

    dates = [_to_display_tz(d) if isinstance(d, datetime) else d
             for d in timeseries['dates']]
    series = list(timeseries['series'])
    metric_label = timeseries.get('metric_label', 'Jobs')
    group_by_label = timeseries.get('group_by_label', '')

    fig, ax = plt.subplots(figsize=(18, 5))
    values_matrix = [s['values'] for s in series]
    # UNITY_STACK_20 (20 distinct colours) so Top-15+Others has no colour
    # reuse; disk_usage uses UNITY_STACK_10 because its default top_n is 10.
    #
    # Series ordering convention here is [Others, lowest-rank, …, highest-rank]
    # (Others first so it sits at the bottom of the visual stack). Walking the
    # palette forward would give the LOWEST-rank entry the warmest color and
    # the highest-rank entry a cool one — backwards from how pace_chart
    # behaves. Reverse the palette index for named entries so the largest
    # visual band (highest-rank, top of the stack) gets UNITY_STACK_20[0]
    # (gold), matching the pace_chart convention.
    n_named = sum(1 for s in series if s['label'] != 'Others')
    colors = []
    named_idx = 0
    for s in series:
        if s['label'] == 'Others':
            colors.append(UNITY_NCAR_GRAY_LIGHT)  # NCAR ncar-gray-light
        else:
            palette_idx = (n_named - 1 - named_idx) % 20
            colors.append(UNITY_STACK_20[palette_idx])
            named_idx += 1
    ax.stackplot(dates, *values_matrix, colors=colors, alpha=0.85)
    ax.set_ylabel(metric_label, fontsize=13)
    ax.tick_params(axis='both', labelsize=12)
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)

    # Build the legend explicitly with reversed-order Patch handles so
    # each handle/text artist is addressable by index for set_url() —
    # mirrors the pace chart pattern.
    import matplotlib.patches as mpatches
    rev_series = list(reversed(series))
    rev_colors = list(reversed(colors))

    # Per-series legend value mirrors the active rank_by selector so
    # the number in parens matches whichever sort the user chose:
    # 'current' = right-edge value, 'peak' = max over window.
    # 'Others' uses the same formula on its aggregate values array.
    if rank_by == 'peak':
        def _legend_value(s):
            vs = s.get('values') or []
            return max(vs) if vs else 0
    else:
        def _legend_value(s):
            vs = s.get('values') or []
            return vs[-1] if vs else 0

    handles = [mpatches.Patch(color=c,
                              label=f"{s['label']} ({fmt.number(_legend_value(s))})")
               for s, c in zip(rev_series, rev_colors)]
    n_named = sum(1 for s in series if s['label'] != 'Others')
    legend_title = (
        f'Top {n_named} {group_by_label}s' if group_by_label else f'Top {n_named}'
    )
    leg = ax.legend(
        handles=handles,
        #title=legend_title,
        loc='center left',
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        fontsize=13,
        title_fontsize=12,
        labelspacing=0.7,
    )

    if link_kind in ('user', 'project'):
        url_fn = _user_modal_url if link_kind == 'user' else _project_modal_url
        for s, patch, text in zip(rev_series, leg.get_patches(), leg.get_texts()):
            if s['label'] == 'Others':
                continue
            url = url_fn(s['label'])
            patch.set_url(url)
            text.set_url(url)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 1c. Access-time distribution histogram (Filesystem Scans — DISK)
# ---------------------------------------------------------------------------

def _access_history_cache_key(hist):
    """Stable key from the per-bucket data/file totals + reference date.

    Hashes only what the rendered bars depend on (bucket order, data and
    file counts, and the snapshot date in the title) — not the full
    per-owner breakdown, which doesn't affect the chart.
    """
    labels = list((hist or {}).get('bucket_labels', []))
    buckets = (hist or {}).get('buckets', {})
    payload = [
        (lbl,
         buckets.get(lbl, {}).get('data', 0),
         buckets.get(lbl, {}).get('files', 0))
        for lbl in labels
    ]
    return _content_hash([payload, str((hist or {}).get('reference_scan_date', ''))])


@caching.chart_cached(name='access_history_histogram', maxsize=128,
                      key_fn=_access_history_cache_key)
def generate_access_history_histogram(hist) -> str:
    """Render a bar chart of data volume across access-time buckets.

    Args:
        hist: the dict returned by
            ``webapp.disk_scans.service.scan_access_history`` —
            ``{'bucket_labels': [...10 bands...],
               'buckets': {label: {'data', 'files', 'owners'}}, ...}``.
            Bars plot ``data`` (bytes) per bucket; ``files``/``owners`` are
            surfaced in the surrounding table, not the chart.

    Y-axis auto-scales to GiB / TiB / PiB based on the peak bucket. Bars
    use the Unity 10-color stacked palette in band order (recent → stale).
    Returns a "no data" placeholder div when the histogram is empty.
    """
    if not hist or not hist.get('bucket_labels'):
        return '<div class="text-center text-muted">No access-history data for this scope</div>'

    labels = list(hist['bucket_labels'])
    buckets = hist.get('buckets', {})
    data_vals = [buckets.get(lbl, {}).get('data', 0) or 0 for lbl in labels]

    peak = max(data_vals) if data_vals else 0
    if peak >= _BYTES_PER_PIB:
        scale, unit_label = _BYTES_PER_PIB, 'PiB'
    elif peak >= _BYTES_PER_TIB:
        scale, unit_label = _BYTES_PER_TIB, 'TiB'
    else:
        scale, unit_label = _BYTES_PER_GIB, 'GiB'
    scaled = [v / scale for v in data_vals]

    fig, ax = plt.subplots(figsize=(14, 5))
    colors = [UNITY_STACK_10[i % len(UNITY_STACK_10)] for i in range(len(labels))]
    ax.bar(range(len(labels)), scaled, color=colors,
           edgecolor=UNITY_NCAR_NAVY, linewidth=0.5)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha='right')
    ax.set_ylabel(f'Data ({unit_label})')
    ax.grid(True, axis='y', alpha=0.3)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 2. Node type history (status dashboard)
# ---------------------------------------------------------------------------

# One entry per node type; can be O(10s) across all machines.
@caching.chart_cached(name='nodetype_history', maxsize=64)
def generate_nodetype_history_matplotlib(history_data: List[Dict]) -> str:
    """
    Generate node type history chart showing availability and utilization.
    Title is rendered in the surrounding HTML (see status dashboard template).

    Args:
        history_data: List of dicts with timestamp, nodes_*, utilization_percent

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this node type</div>'

    timestamps = [_to_display_tz(d['timestamp']) for d in history_data]
    nodes_available = [d.get('nodes_available', 0) for d in history_data]
    nodes_down = [d.get('nodes_down', 0) for d in history_data]
    nodes_allocated = [d.get('nodes_allocated', 0) for d in history_data]
    utilization = [d.get('utilization_percent') for d in history_data]
    memory_utilization = [d.get('memory_utilization_percent') for d in history_data]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 10), sharex=True)

    ax1.stackplot(timestamps, nodes_down, nodes_allocated, nodes_available,
                  labels=['Down', 'Fully Allocated', 'Resources Available'],
                  colors=[UNITY_NCAR_VERMILION, UNITY_NCAR_BLUE, UNITY_NCAR_SKY])
    ax1.set_ylabel('Number of Nodes', fontsize=11)
    ax1.set_ylim([0, None])
    ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax1.legend(loc=2, fontsize=10,
               frameon=True, facecolor='white', edgecolor='none', framealpha=0.9)
    ax1.grid(True, alpha=0.3, color='grey')

    if any(u is not None for u in utilization):
        util_times = [timestamps[i] for i, u in enumerate(utilization) if u is not None]
        util_vals = [u for u in utilization if u is not None]
        ax2.plot(util_times, util_vals, color=UNITY_NCAR_BLUE, linewidth=3, label='CPU/GPU Utilization')

    if any(m is not None for m in memory_utilization):
        mem_times = [timestamps[i] for i, m in enumerate(memory_utilization) if m is not None]
        mem_vals = [m for m in memory_utilization if m is not None]
        ax2.plot(mem_times, mem_vals, color=UNITY_NCAR_TEAL, linewidth=3, label='Memory Utilization')

    ax2.set_ylabel('Utilization', fontsize=11)
    ax2.set_xlabel(f'Time ({fmt.local_tz_label()})', fontsize=11)
    ax2.set_ylim(0, 100)
    ax2.yaxis.set_major_formatter(fmt.mpl_pct_formatter())
    ax2.legend(loc='best', fontsize=10,
               frameon=True, facecolor='white', edgecolor='none', framealpha=0.9)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 3. Queue history (status dashboard)
# ---------------------------------------------------------------------------

# One entry per queue; queue counts can be O(10s) across all resources.
@caching.chart_cached(name='queue_history', maxsize=64)
def generate_queue_history_matplotlib(history_data: List[Dict]) -> str:
    """
    Generate queue history chart showing job flow and resource demand.
    Title is rendered in the surrounding HTML (see status dashboard template).

    Args:
        history_data: List of dicts with timestamp, job counts, resources

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this queue</div>'

    timestamps = [_to_display_tz(d['timestamp']) for d in history_data]
    running_jobs = [d.get('running_jobs', 0) for d in history_data]
    pending_jobs = [d.get('pending_jobs', 0) for d in history_data]
    held_jobs = [d.get('held_jobs', 0) for d in history_data]
    active_users = [d.get('active_users', 0) for d in history_data]
    cores_allocated = [d.get('cores_allocated', 0) for d in history_data]
    cores_pending = [d.get('cores_pending', 0) for d in history_data]
    gpus_allocated = [d.get('gpus_allocated', 0) for d in history_data]
    gpus_pending = [d.get('gpus_pending', 0) for d in history_data]

    has_gpus = any(gpus_allocated) or any(gpus_pending)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    ax1.plot(timestamps, running_jobs, color=UNITY_NCAR_TEAL, linewidth=3, label='Running')
    ax1.plot(timestamps, pending_jobs, color=UNITY_NCAR_ORANGE, linewidth=3, label='Pending')
    ax1.plot(timestamps, held_jobs, color=UNITY_NCAR_VERMILION, linewidth=3, label='Held')
    ax1.plot(timestamps, active_users, color=UNITY_NCAR_BLUE, linestyle='--', linewidth=2, label='Active Users')
    ax1.set_ylim([0, None])
    ax1.set_ylabel('Count', fontsize=11)
    ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax1.legend(loc=2, fontsize=10,
               frameon=True, facecolor='white', edgecolor='none', framealpha=0.9)
    ax1.grid(True, alpha=0.3)

    if has_gpus:
        ax2.plot(timestamps, gpus_allocated, color=UNITY_NCAR_BLUE, linewidth=3, label='GPUs Running')
        ax2.plot(timestamps, gpus_pending, color=UNITY_NCAR_TEAL, linewidth=3, label='GPUs Pending')
    else:
        ax2.plot(timestamps, cores_allocated, color=UNITY_NCAR_BLUE, linewidth=3, label='Cores Running')
        ax2.plot(timestamps, cores_pending, color=UNITY_NCAR_TEAL, linewidth=3, label='Cores Pending')

    ax2.set_ylim([0, None])
    ax2.set_ylabel('Resources', fontsize=11)
    ax2.set_xlabel(f'Time ({fmt.local_tz_label()})', fontsize=11)
    ax2.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax2.legend(loc=2, fontsize=10,
               frameon=True, facecolor='white', edgecolor='none', framealpha=0.9)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 4. Facility pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

_PIE_START_ANGLE = 60
_PIE_MAX_ENTITIES = 10


def _pie_trim(names: list, values: list) -> tuple[list, list]:
    """Sort by value descending, cap at _PIE_MAX_ENTITIES, group remainder as 'Others (N)'."""
    paired = sorted(zip(names, values), key=lambda x: x[1], reverse=True)
    names_s = [p[0] for p in paired]
    values_s = [p[1] for p in paired]
    if len(names_s) > _PIE_MAX_ENTITIES:
        n_others = len(names_s) - _PIE_MAX_ENTITIES
        others_sum = sum(values_s[_PIE_MAX_ENTITIES:])
        names_s = names_s[:_PIE_MAX_ENTITIES] + [f'Others ({n_others})']
        values_s = values_s[:_PIE_MAX_ENTITIES] + [others_sum]
    return names_s, values_s


# One entry per resource filter combination; small number of distinct views.
@caching.chart_cached(name='facility_pie_chart', maxsize=32)
def generate_facility_pie_chart_matplotlib(facility_data: List[Dict]) -> str:
    """
    Generate pie chart showing distribution by facility. Title is rendered
    in the surrounding HTML (see allocations dashboard template).

    Args:
        facility_data: List of dicts with facility, annualized_rate, count, percent

    Returns:
        SVG string ready for template rendering
    """
    if not facility_data:
        return '<div class="text-center text-muted">No facility data available</div>'

    raw_names = [d['facility'] for d in facility_data]
    raw_values = [d['annualized_rate'] for d in facility_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = UNITY_PALETTE_10[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda p: fmt.pct(p, decimals=1) if p >= 5 else '',
        startangle=_PIE_START_ANGLE,
        counterclock=False,
        colors=colors,
        pctdistance=0.85,
    )
    for at, wedge_color in zip(autotexts, colors):
        at.set_color(_autopct_color_for(wedge_color))
        at.set_fontweight('bold')
        at.set_fontsize(8)

    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 5. Allocation type pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

# One entry per (resource, facility) filter combination.
@caching.chart_cached(name='allocation_type_pie_chart', maxsize=64)
def generate_allocation_type_pie_chart_matplotlib(type_data: List[Dict]) -> str:
    """
    Generate pie chart showing allocation distribution by type within a facility.
    Title is rendered in the surrounding HTML (see allocations dashboard template).

    Args:
        type_data: List of dicts with allocation_type, total_amount, count, avg_amount

    Returns:
        SVG string ready for template rendering
    """
    if not type_data:
        return '<div class="text-center text-muted">No allocation type data available</div>'

    raw_names = [d['allocation_type'] for d in type_data]
    raw_values = [d['total_amount'] for d in type_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = UNITY_PALETTE_10[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda p: fmt.pct(p, decimals=1) if p >= 5 else '',
        startangle=_PIE_START_ANGLE,
        counterclock=False,
        colors=colors,
        pctdistance=0.85,
    )
    for at, wedge_color in zip(autotexts, colors):
        at.set_color(_autopct_color_for(wedge_color))
        at.set_fontweight('bold')
        at.set_fontsize(8)

    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 6. Allocation pace chart (allocations dashboard)
#
# Stacked-area chart where each allocation is one band with a step at
# `active_at`. Left of the step: constant past-burn-rate (used/elapsed_days).
# Right of the step: constant required-future-rate (remaining/remaining_days).
# Past and future of the same allocation share a color (one band = one color).
# Top-N projcodes (by total allocated in-scope) get distinct colors; the rest
# share a muted "Other" color.
# ---------------------------------------------------------------------------

_PACE_OTHER_COLOR = matplotlib.colors.to_rgba(UNITY_NCAR_GRAY_LIGHT, 0.85)
_PACE_TODAY_LINE_COLOR = matplotlib.colors.to_rgba(UNITY_NCAR_NAVY, 0.7)
_PACE_RATE_SCALE = 365  # internal per-day rates → per-year axis


def _pace_bands(allocations: List[Dict], active_at: datetime,
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


def _pace_key_fields(allocations: List[Dict]) -> list:
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


def _pace_cache_key(allocations, active_at, window_days=180, top_n=15,
                    resource_name='', sort_by='size'):
    return _content_hash([_pace_key_fields(allocations), active_at.isoformat(),
                          int(window_days), int(top_n), resource_name, sort_by])


# One entry per (resource, window_days, top_n, sort_by) combination across
# concurrent viewers. maxsize sized for ~30 resources × 3 sort_by × small
# facility-scope fanout — well under 10 MB of cached SVG per process.
@caching.chart_cached(name='pace_chart', maxsize=192, key_fn=_pace_cache_key)
def generate_pace_chart_matplotlib(
    allocations: List[Dict],
    active_at: datetime,
    window_days: int = 180,
    top_n: int = 20,
    resource_name: str = '',
    sort_by: str = 'size',
) -> str:
    """Stacked-area pace chart: one band per allocation, past-rate | future-rate
    step at ``active_at``. Top-N projcodes get distinct colors; the rest share
    an "Other" color.

    Args:
        allocations: per-allocation rows (from ``cached_allocation_usage``)
            with at least ``projcode``, ``start_date``, ``end_date``,
            ``total_amount``, ``total_used``.
        active_at: chart centerline ("today").
        window_days: half-window on each side of ``active_at`` (default 180).
        top_n: projects with their own color + legend entry (default 20).
        resource_name: used only for cache key disambiguation.
        sort_by: ranking metric for the top-N selection. One of:
            - ``'size'``  — total allocated amount (default; legacy behaviour).
            - ``'past'``  — past burn rate (used / past_days), per year.
            - ``'future'`` — future required rate ((amount - used) / future_days),
              per year — the "risk" signal: steeper future slope = more burn
              required to complete.
            The legend number on each band reflects this same metric.

    Returns:
        SVG string ready for template rendering.
    """
    if not allocations:
        return '<div class="text-center text-muted">No allocations available</div>'

    window_start = active_at - timedelta(days=window_days)
    window_end = active_at + timedelta(days=window_days)

    days, bands = _pace_bands(allocations, active_at, window_start, window_end)
    if not bands:
        return '<div class="text-center text-muted">No allocations in the ±{}d window</div>'.format(window_days)

    # today_idx on the full daily grid — needed both for ranking by
    # past/future rate (band heights at the step) and for the later
    # RLE step preservation.
    n_days = len(days)
    today_idx = (active_at - days[0]).days

    # Per-project aggregations for the three rank metrics:
    #   - size:   sum of total_amount   (legacy default — biggest pool)
    #   - past:   sum of past-rate band heights at today-1 (visible
    #             past slope, per day)
    #   - future: sum of future-rate band heights at today   (visible
    #             future slope = required burn-to-completion)
    # Past/future rates are piecewise-constant inside each band (set by
    # _pace_bands), so the value at the single sample point IS the band's
    # rate over its active region. Summing across bands handles projects
    # with multiple allocations on the same resource.
    proj_size:   Dict[str, float] = {}
    proj_past:   Dict[str, float] = {}
    proj_future: Dict[str, float] = {}
    past_sample_idx = max(today_idx - 1, 0)
    future_sample_idx = min(today_idx, n_days - 1)
    for pc, amount, rates in bands:
        proj_size[pc]   = proj_size.get(pc, 0.0) + amount
        proj_past[pc]   = proj_past.get(pc, 0.0) + float(rates[past_sample_idx])
        proj_future[pc] = proj_future.get(pc, 0.0) + float(rates[future_sample_idx])

    # Pick ranking + legend-display metric in lockstep so the legend
    # number always reflects the active sort. Unknown sort_by falls
    # back to 'size' (parallels the route's input validation).
    if sort_by == 'past':
        rank_metric = proj_past
    elif sort_by == 'future':
        rank_metric = proj_future
    else:
        sort_by = 'size'
        rank_metric = proj_size
    top_projs = [pc for pc, _ in sorted(
        rank_metric.items(), key=lambda kv: kv[1], reverse=True
    )[:top_n]]
    palette = UNITY_STACK_10 if len(top_projs) <= 10 else UNITY_STACK_20
    color_map = {pc: palette[i] for i, pc in enumerate(top_projs)}

    n_other_projs = len(rank_metric) - len(top_projs)
    other_label = f'Other ({n_other_projs} project{"s" if n_other_projs != 1 else ""})'

    # Collapse per-allocation bands into one band per color group BEFORE
    # handing to matplotlib. Stackplot emits one <path> per band; without
    # this aggregation, a ~1000-project resource produces 1000 paths and a
    # ~20 MB SVG. Stacking is associative, so element-wise summing the rate
    # arrays within each color group is mathematically identical and
    # visually identical (the group shares one color anyway).
    OTHER_KEY = '__other__'
    group_keys = list(top_projs) + [OTHER_KEY]
    group_rates: Dict[str, np.ndarray] = {k: np.zeros(n_days) for k in group_keys}
    # Per-group running total of the active sort metric — used by the
    # "Other" legend entry to summarize the long tail in the same units
    # as the per-project entries.
    group_sort_totals: Dict[str, float] = {k: 0.0 for k in group_keys}

    for pc, amount, rates in bands:
        key = pc if pc in color_map else OTHER_KEY
        group_rates[key] += rates
        if sort_by == 'past':
            group_sort_totals[key] += float(rates[past_sample_idx])
        elif sort_by == 'future':
            group_sort_totals[key] += float(rates[future_sample_idx])
        else:
            group_sort_totals[key] += amount

    # Stack order: top-N (ranked) first, Other capping the top. Drop empty
    # groups so stackplot doesn't emit a zero-area path.
    ordered = [(k, group_rates[k]) for k in top_projs] + [(OTHER_KEY, group_rates[OTHER_KEY])]
    ordered = [(k, r) for k, r in ordered if r.any()]

    # Lossless run-length compression on the time axis. Each band's rate
    # is piecewise constant (set in flat slices by `_pace_bands`), so a
    # 361-element daily array is mostly repeated values. Subset to:
    #   - chart endpoints (so axis bounds stay correct),
    #   - today_idx and today_idx-1 (the past→future step is the most
    #     prominent visual feature; keeping both anchors a vertical edge),
    #   - every transition index i where any band's rate flips between
    #     day i-1 and day i, plus i-1 itself (the predecessor preserves
    #     the step appearance — without it, stackplot draws a 1-day-wide
    #     ramp instead of a vertical edge).
    # On a single resource, allocations typically cluster on common
    # cycle dates (fiscal year boundaries, etc.), so the union of
    # transition days is usually small (~10-30 of 361 days). Per-band
    # vertex count drops by 10-50×, lossless.
    band_rates_full = np.stack([r for _, r in ordered], axis=0)  # (n_bands, n_days)
    diffs = np.any(np.diff(band_rates_full, axis=1) != 0, axis=0)  # (n_days-1,)
    trans = np.flatnonzero(diffs) + 1  # day i where rate[i-1] != rate[i]

    keep = {0, n_days - 1, today_idx}
    if today_idx - 1 >= 0:
        keep.add(today_idx - 1)
    for t in trans:
        ti = int(t)
        keep.add(ti)
        if ti - 1 >= 0:
            keep.add(ti - 1)
    keep_idx = np.fromiter(sorted(keep), dtype=int)

    days = [days[i] for i in keep_idx]
    rates_matrix = [band_rates_full[bi, keep_idx] * _PACE_RATE_SCALE
                    for bi in range(band_rates_full.shape[0])]
    colors = [color_map.get(k, _PACE_OTHER_COLOR) for k, _ in ordered]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.stackplot(days, rates_matrix, colors=colors, edgecolor='none',
                 linewidth=0, antialiased=True)

    # Clamp ymax to the larger of the stacked totals at the window edges,
    # plus 25% headroom. Allocations expiring within a day or two of
    # active_at otherwise produce future-rates of remaining/1d that
    # dominate the axis and squash the rest of the chart into a flat strip.
    totals_by_day = np.sum(rates_matrix, axis=0)
    edge_bound = max(float(totals_by_day[0]), float(totals_by_day[-1]))
    ax.set_ylim(bottom=0, top=(1.25 * edge_bound) if edge_bound > 0 else None)

    # Today marker — placed after set_ylim so the label sits at the
    # clamped ymax rather than the auto-scaled spike.
    ax.axvline(active_at, color=_PACE_TODAY_LINE_COLOR, linestyle='--', linewidth=1)
    _, ymax = ax.get_ylim()
    ax.text(active_at, ymax, ' today', color=_PACE_TODAY_LINE_COLOR,
            fontsize=8, va='top', ha='left')

    # Deduplicated legend: one handle per top-N projcode + one Other.
    # Number shown next to each project tracks the active sort_by — see
    # rank_metric above. For rate sorts, scale per-day → per-year so the
    # number matches the axis units, and tag with "/yr" to keep that
    # explicit.
    import matplotlib.patches as mpatches
    if sort_by == 'size':
        def _fmt_value(v): return fmt.number(v)
    else:
        def _fmt_value(v): return f'{fmt.number(v * _PACE_RATE_SCALE)}/yr'
    handles = [mpatches.Patch(color=color_map[pc],
                              label=f'{pc} ({_fmt_value(rank_metric[pc])})')
               for pc in top_projs]
    if n_other_projs > 0:
        handles.append(mpatches.Patch(
            color=_PACE_OTHER_COLOR,
            label=f'{other_label} ({_fmt_value(group_sort_totals[OTHER_KEY])})'
        ))
    leg = ax.legend(handles=handles, loc='center left', bbox_to_anchor=(1.0, 0.5),
                    fontsize=9, frameon=False)

    # Tag each top-N legend entry with the project-modal URL. matplotlib's
    # SVG backend wraps the patch swatch and label text in <a xlink:href>.
    # svg-chart-links.js intercepts the click and dispatches the existing
    # HTMX modal trigger. The trailing "Other" patch (if present) gets no
    # URL since it's not a single project.
    for pc, patch, text in zip(top_projs, leg.get_patches(), leg.get_texts()):
        url = _project_modal_url(pc)
        patch.set_url(url)
        text.set_url(url)

    # Axes
    ax.set_xlim(window_start, window_end)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.set_ylabel('Rate (per year)')
    ax.grid(True, alpha=0.2)
    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()
