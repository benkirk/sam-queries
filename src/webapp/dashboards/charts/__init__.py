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
import matplotlib.colors
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from flask import url_for

from sam import fmt
from webapp.caching import caching
# `content_hash` under its historical private name — used by several
# `_*_cache_key` functions below.
from webapp.caching.chart import content_hash as _content_hash

# Imported first for its import-time side effects: registering the
# server-side Poppins TTFs and applying the structural rcParams. The
# re-exported names keep `charts.UNITY_*` working for existing importers.
from webapp.dashboards.charts import links, series as series_mod, theme
from webapp.dashboards.charts.base import (  # noqa: F401
    BaseChart,
    chart_view,
    empty_state as _empty_state,
    fig_to_svg as _fig_to_svg,
)
from webapp.dashboards.charts.dualpanel import (
    NodetypeHistoryChart,
    QueueHistoryChart,
    _to_display_tz,
)
from webapp.dashboards.charts.layout import Layout, profile as layout_profile
from webapp.dashboards.charts.pie import (
    AllocationTypePie,
    DiskEntityPie,
    FacilityPie,
    JobsUsagePie,
    PieChart,
    UserUsagePie,
    trim_cumulative as _pie_cumulative_keep,
    trim_fixed_cap as _pie_trim,
)
from webapp.dashboards.charts.theme import (  # noqa: F401
    UNITY_NCAR_BLUE,
    UNITY_NCAR_GRAY,
    UNITY_NCAR_GRAY_LIGHT,
    UNITY_NCAR_GOLD,
    UNITY_NCAR_LIGHT_BLUE,
    UNITY_NCAR_NAVY,
    UNITY_NCAR_ORANGE,
    UNITY_NCAR_SKY,
    UNITY_NCAR_SPACE_BLUE,
    UNITY_NCAR_TEAL,
    UNITY_NCAR_VERMILION,
    UNITY_PALETTE_10,
    UNITY_STACK_10,
    UNITY_STACK_20,
    Theme,
    autopct_color_for as _autopct_color_for,
    resolve_theme,
    scale_bytes,
    shade_family as _shade_family,
    _FONT_DIR,
)


def _project_modal_url(projcode: str) -> str:
    """Resolve the project-details modal route, with blueprint prefix.
    Used to mark legend entries with set_url() — svg-chart-links.js
    intercepts clicks on these anchors and dispatches the modal."""
    return links.PROJECT_MODAL.url(projcode)


def _user_modal_url(username: str) -> str:
    """Resolve the user-card modal route, with blueprint prefix."""
    return links.USER_MODAL.url(username)


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
            ``<a xlink:href="#sam/day/YYYY-MM-DD">`` anchor via
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
        return _empty_state('No usage data recorded for this period')

    dates = list(daily_data.get('dates') or [])
    vals  = list(daily_data.get('values') or [])

    combined = sorted(zip(dates, vals))
    if not combined:
        return _empty_state('No usage data recorded for this period')

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
            rect.set_url(links.DAY.url(iso))
    ax.set_ylabel(_USAGE_METRIC_YLABELS.get(metric, 'Charges'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    return _fig_to_svg(fig)


def _usage_stacked_cache_key(timeseries, metric='charges'):
    return _content_hash([_content_hash(timeseries), metric])


# One entry per (resource, time-range, metric). Stacked-by-user variant of
# the Usage Trend bar chart — each daily bar is segmented by the top-N users
# over the window + "Others".
@caching.chart_cached(name='usage_timeseries_stacked', maxsize=128,
                      key_fn=_usage_stacked_cache_key)
def generate_usage_timeseries_stacked_by_user(timeseries, metric='charges') -> str:
    """
    Stacked-bar Usage Trend chart: one bar per day, segmented by the top-N
    users over the visible window + "Others", with a clickable right-side
    legend.

    Args:
        timeseries: dict shaped as
            ``sam.queries.charges.get_daily_user_usage_for_project`` returns:
            ``{'dates': [date, ...], 'series': [{'label','values'}, ...]}``.
            ``series[0]`` is conventionally ``'Others'`` (drawn first so it
            sits at the bottom of the stack with a neutral grey).
        metric: one of 'charges' / 'jobs' / 'core_hours' — controls the
            y-axis label and the cache key.

    Interactions (wired via svg-chart-links.js):
        - every bar segment of a non-zero day links to
          ``#sam/day/YYYY-MM-DD`` → expands that day's row in the Historical
          Usage table (same as the flat-bar chart).
        - each named legend entry links to ``#sam/user/<username>`` →
          expands that user's row in the Usage by User card. 'Others' is
          never linked.

    Returns:
        SVG string ready for template rendering.
    """
    if not timeseries or not timeseries.get('dates') or not timeseries.get('series'):
        return _empty_state('No usage data recorded for this period')

    dates = list(timeseries['dates'])
    series = list(timeseries['series'])

    # Others (always first per get_daily_user_usage_for_project) gets a
    # neutral grey; named users use the Unity 10-colour stacked palette.
    bands = series_mod.from_label_series(series)
    colors = series_mod.assign_colors(bands, UNITY_STACK_10, UNITY_NCAR_GRAY_LIGHT)

    fig, ax = plt.subplots(figsize=(18, 5))

    # Stack the daily bars: accumulate `bottom` across series. Every segment
    # of a given day carries the same #sam/day/<iso> url so a click anywhere
    # in that day's stack expands the day (preserves the flat-bar behaviour).
    bottoms = [0.0] * len(dates)
    for s, color in zip(series, colors):
        vals = list(s['values'])
        bars = ax.bar(dates, vals, width=1, bottom=bottoms,
                      color=color, edgecolor=UNITY_NCAR_NAVY, lw=0.3)
        for d, value, rect in zip(dates, vals, bars.patches):
            if not value:
                continue
            iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)
            rect.set_url(links.DAY.url(iso))
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    ax.set_ylabel(_USAGE_METRIC_YLABELS.get(metric, 'Charges'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)

    # Reversed-order legend so it reads top-to-bottom matching the visual
    # stack; each handle/text is addressable by index for set_url().
    rev_series = list(reversed(series))
    rev_colors = list(reversed(colors))
    handles = [mpatches.Patch(color=c, label=s['label'])
               for s, c in zip(rev_series, rev_colors)]
    leg = ax.legend(
        handles=handles,
        loc='center left',
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        fontsize=11,
        title_fontsize=12,
        labelspacing=0.7,
    )

    # Named legend entries → expand that user's Usage-by-User row.
    # `is_linkable` is the one rule: Others (and any aggregate) is inert.
    for band, patch, text in zip(reversed(bands), leg.get_patches(), leg.get_texts()):
        if not band.is_linkable:
            continue
        url = links.USAGE_USER.url(band.link_key)
        patch.set_url(url)
        text.set_url(url)

    fig.autofmt_xdate()

    return _fig_to_svg(fig)


# ---------------------------------------------------------------------------
# 1b. Disk usage stacked-area chart (Resource Usage Details — DISK)
# ---------------------------------------------------------------------------

# Historical private aliases; the definitions live in theme.py.
_BYTES_PER_GIB = theme.BYTES_PER_GIB
_BYTES_PER_TIB = theme.BYTES_PER_TIB
_BYTES_PER_PIB = theme.BYTES_PER_PIB


def _disk_usage_stacked_area_cache_key(timeseries, link_kind=None, metric='bytes'):
    return _content_hash([_content_hash(timeseries), link_kind or '', metric])


# `link_kind` ('user' | None) controls whether legend usernames are
# wrapped in <a xlink:href> SVG anchors targeting the user-details
# modal. None = no links (default, backward compatible). Mirrors the
# user_proj_stacked_area chart's pattern.
@caching.chart_cached(name='disk_usage_stacked_area', maxsize=128,
                      key_fn=_disk_usage_stacked_area_cache_key)
def generate_disk_usage_stacked_area(timeseries, link_kind=None, metric='bytes') -> str:
    """Render a stacked-area chart of disk usage vs time.

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
        metric: ``'bytes'`` (default) renders a byte-volume y-axis
            auto-scaled to TiB or PiB; ``'files'`` renders a raw file-count
            y-axis with compact, integer ticks.

    For ``metric='bytes'`` the y-axis is auto-scaled to TiB or PiB based on
    the peak stacked total (>= 1 PiB → PiB, else TiB). X-axis is
    date-formatted. Legend on the right.
    """
    if not timeseries or not timeseries.get('dates') or not timeseries.get('series'):
        return _empty_state('No disk-usage history for this period')

    dates = list(timeseries['dates'])
    series = list(timeseries['series'])
    if not dates or not series:
        return _empty_state('No disk-usage history for this period')

    if metric == 'files':
        # Raw file counts: no scaling; compact integer y-axis ticks.
        scaled_series = [list(s['values']) for s in series]
        ylabel = 'Number of files'
    else:
        stacked_totals = [
            sum(s['values'][i] for s in series)
            for i in range(len(dates))
        ]
        peak = max(stacked_totals) if stacked_totals else 0
        # floor='TiB': this chart never drops to a GiB axis, so a sub-TiB
        # series shows as a fractional TiB. Deliberate — its readers think
        # in TiB — and NOT the same ladder the distribution histogram uses.
        scale, unit_label = scale_bytes(peak, floor='TiB')
        scaled_series = [
            [v / scale for v in s['values']]
            for s in series
        ]
        ylabel = f'Disk usage ({unit_label})'

    fig, ax = plt.subplots(figsize=(18, 5))
    # Others (always first per get_disk_usage_timeseries_by_user) gets a
    # neutral grey so it doesn't compete with the named-user palette.
    # Named users use the Unity 10-color stacked palette.
    bands = series_mod.from_username_series(series)
    colors = series_mod.assign_colors(bands, UNITY_STACK_10, UNITY_NCAR_GRAY_LIGHT)
    ax.stackplot(dates, *scaled_series, colors=colors, alpha=0.85)
    ax.set_ylabel(ylabel)
    if metric == 'files':
        from matplotlib.ticker import MaxNLocator
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, alpha=0.3)

    # Build the legend explicitly with reversed-order Patch handles so
    # each handle/text artist is addressable by index for set_url() —
    # mirrors the user_proj_stacked_area / pace chart pattern. Reverses
    # the visual stack so legend reads top-to-bottom in the same order.
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
        for band, patch, text in zip(reversed(bands), leg.get_patches(), leg.get_texts()):
            if not band.is_linkable:
                continue
            url = _user_modal_url(band.link_key)
            patch.set_url(url)
            text.set_url(url)

    fig.autofmt_xdate()

    return _fig_to_svg(fig)


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
        return _empty_state('No per-user / per-project history for this period',
                            extra_classes='py-4')

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
    bands = series_mod.from_label_series(series)
    colors = series_mod.assign_colors(bands, UNITY_STACK_20, UNITY_NCAR_GRAY_LIGHT,
                                      reverse=True)
    ax.stackplot(dates, *values_matrix, colors=colors, alpha=0.85)
    ax.set_ylabel(metric_label, fontsize=13)
    ax.tick_params(axis='both', labelsize=12)
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)

    # Build the legend explicitly with reversed-order Patch handles so
    # each handle/text artist is addressable by index for set_url() —
    # mirrors the pace chart pattern.
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
        for band, patch, text in zip(reversed(bands), leg.get_patches(), leg.get_texts()):
            if not band.is_linkable:
                continue
            url = url_fn(band.link_key)
            patch.set_url(url)
            text.set_url(url)

    fig.autofmt_xdate()

    return _fig_to_svg(fig)


# ---------------------------------------------------------------------------
# 1c. Distribution histograms — access-time & file-size (Filesystem Scans — DISK)
# ---------------------------------------------------------------------------

# Top-N owners drawn as their own stack segment per bar; the rest collapse
# into one aggregated "other" segment at the base. Matches the table's top-10.
_AH_TOP_SEGMENTS = 10


def _bucket_segments(owners, metric='data'):
    """Per-bucket stacked-bar segments, bottom → top.

    Returns a list of segment values (in *metric* units — ``'data'`` bytes or
    ``'files'`` counts) ordered as the long-tail "other" aggregate (if any)
    followed by the top-``_AH_TOP_SEGMENTS`` owners ascending — so the largest
    owner sits at the top of the bar. Empty list when the bucket has no owners
    (→ drawn as a single flat bar).
    """
    if not owners:
        return []
    ranked = sorted((d.get(metric, 0) or 0) for d in owners.values())
    if len(ranked) > _AH_TOP_SEGMENTS:
        return [sum(ranked[:-_AH_TOP_SEGMENTS])] + ranked[-_AH_TOP_SEGMENTS:]
    return ranked


def _distribution_cache_key(hist, *, log_y=False, metric='data'):
    """Stable key from the per-bucket totals + segment shape + date + options.

    Hashes the bucket order, the exact stacked-bar segment values for the
    chosen *metric* (top-N owners + "other"), the snapshot date in the title,
    and the y-scale / metric flags — everything the rendered SVG depends on.
    """
    labels = list((hist or {}).get('bucket_labels', []))
    buckets = (hist or {}).get('buckets', {})
    payload = [
        (lbl,
         tuple(_bucket_segments(buckets.get(lbl, {}).get('owners') or {}, metric)))
        for lbl in labels
    ]
    return _content_hash(
        [payload, str((hist or {}).get('reference_scan_date', '')),
         bool(log_y), str(metric)]
    )


@caching.chart_cached(name='distribution_histogram', maxsize=128,
                      key_fn=_distribution_cache_key)
def generate_distribution_histogram(hist, *, log_y=False, metric='data') -> str:
    """Render a stacked bar chart of a metric across distribution buckets.

    Shared by the Access-history and File-size tabs — both consume the same
    ``{'bucket_labels', 'buckets': {label: {'data','files','owners'}},
       'reference_scan_date', ...}`` shape (see
    ``webapp.disk_scans.service.scan_access_history`` /
    ``scan_file_sizes``). The ``files``/``owners`` detail is surfaced in the
    surrounding table.

    Each bar is a single-hue stack: the top owners (largest at top) over an
    aggregated "other" base, shaded light → dark in that band's Unity color,
    so the spread between users is legible before clicking.

    Args:
        metric: ``'data'`` plots bytes per bucket (y-axis auto-scaled to
            GiB / TiB / PiB); ``'files'`` plots file counts (compact-number
            y-axis). Per-owner stack segments use the same metric.
        log_y: use a logarithmic y-axis. A log scale can't represent a stack
            meaningfully, so this falls back to one solid bar per bucket (the
            band base color). Useful when bucket totals span many orders of
            magnitude (file sizes by data), where a linear stack buries small
            bands.

    Returns a "no data" placeholder div when the histogram is empty.
    """
    if not hist or not hist.get('bucket_labels'):
        return _empty_state('No distribution data for this scope')

    is_bytes = (metric != 'files')
    labels = list(hist['bucket_labels'])
    buckets = hist.get('buckets', {})
    vals = [buckets.get(lbl, {}).get(metric, 0) or 0 for lbl in labels]

    if is_bytes:
        peak = max(vals) if vals else 0
        # floor='GiB': three rungs here, unlike the disk-usage timeseries.
        scale, unit_label = scale_bytes(peak, floor='GiB')
        ylabel = f'Data ({unit_label})'
    else:
        scale, ylabel = 1, 'Files'
    scaled = [v / scale for v in vals]

    fig, ax = plt.subplots(figsize=(14, 5))
    colors = [UNITY_STACK_10[i % len(UNITY_STACK_10)] for i in range(len(labels))]

    # Buckets with owners get a drill-down anchor (#sam/row/data-ah-bucket/<i>) on every
    # segment so a click anywhere on the bar expands the matching row —
    # svg-chart-links.js intercepts the sentinel (mirrors the Usage Trend
    # day-drill pattern) and scopes the lookup to the originating tab pane.
    if log_y:
        # Log scale: one solid bar per bucket (stacking is meaningless on a
        # log axis). Still anchored for drill-down.
        bars = ax.bar(range(len(labels)), scaled, color=colors,
                      edgecolor=UNITY_NCAR_NAVY, linewidth=0.5)
        for i, (lbl, rect) in enumerate(zip(labels, bars.patches)):
            if buckets.get(lbl, {}).get('owners'):
                rect.set_url(links.AH_BUCKET.url(i))
        ax.set_yscale('log')
    else:
        # Linear: stack per-owner segments (bottom "other" + top owners
        # ascending), shaded within the band's color family.
        for i, lbl in enumerate(labels):
            owners = buckets.get(lbl, {}).get('owners') or {}
            segs = [s / scale for s in _bucket_segments(owners, metric)]
            if not segs:
                ax.bar(i, scaled[i], color=colors[i],
                       edgecolor=UNITY_NCAR_NAVY, linewidth=0.5)
                continue
            shades = _shade_family(colors[i], len(segs))
            bottom = 0.0
            for seg_val, shade in zip(segs, shades):
                cont = ax.bar(i, seg_val, bottom=bottom, color=shade,
                              edgecolor='white', linewidth=0.3)
                cont.patches[0].set_url(links.AH_BUCKET.url(i))
                bottom += seg_val
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha='right')
    ax.set_ylabel(ylabel)
    if not is_bytes:
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, axis='y', alpha=0.3)

    return _fig_to_svg(fig)


# ---------------------------------------------------------------------------
# 2/3. Dual-panel status time series (node type + queue history)
#
# Migrated to the class hierarchy — see charts/dualpanel.py. `chart_view`
# reads cache_name/cache_maxsize off the class and composes the layout/theme
# render axes into the cache key.
# ---------------------------------------------------------------------------

generate_nodetype_history_matplotlib = chart_view(NodetypeHistoryChart)
generate_queue_history_matplotlib = chart_view(QueueHistoryChart)



# ---------------------------------------------------------------------------
# 4/5. Pie charts (allocations dashboard, disk scans, per-user usage)
#
# Migrated to the class hierarchy — see charts/pie.py, where the five pies'
# byte-identical ax.pie block lives once and each subclass is only what
# differed: trim strategy, "Other" derivation, legend formatter, drill target.
# ---------------------------------------------------------------------------

generate_facility_pie_chart_matplotlib = chart_view(FacilityPie)
generate_allocation_type_pie_chart_matplotlib = chart_view(AllocationTypePie)
generate_disk_entity_pie_chart = chart_view(DiskEntityPie)
generate_user_usage_pie_chart = chart_view(UserUsagePie)


# ---------------------------------------------------------------------------
# Job-history charts (jobs card: Wait Times / Job Sizes / Durations + By User)
#
# Inputs are the hpc-usage-queries plugin envelopes verbatim (see
# webapp.jobs.service.jobs_histogram / jobs_usage_by_user) — the histogram
# envelope is self-describing (dimension, unit, full zero-filled bucket
# vector, null_count), so these renderers never hardcode bucket tables.
# ---------------------------------------------------------------------------

# UI metric name → the plugin key(s) SUMMED to produce it. 'jobs' is the
# count metric; the hours metrics come from the LEFT OUTER JOIN against
# job_charges upstream. 'charges' is a pair because the plugin reports
# cpu_charges and gpu_charges separately (they are separately meaningful and
# separately rankable) while the pill means "total charged".
#
# Charges are NOT proportional to hours: qos_factor is a genuine 0.0 for the
# 'uncharged' QoS, so a charges view can legitimately render an empty bar
# where an hours view shows work.
_JOBS_METRIC_KEYS = {
    'jobs':      ('job_count',),
    'cpu_hours': ('cpu_hours',),
    'gpu_hours': ('gpu_hours',),
    'charges':   ('cpu_charges', 'gpu_charges'),
}
_JOBS_METRIC_LABELS = {
    'jobs':      'Jobs',
    'cpu_hours': 'CPU-hours',
    'gpu_hours': 'GPU-hours',
    'charges':   'Charges',
}


def _jobs_metric_value(d, metric, default='jobs'):
    """Value of *metric* from a plugin band / row / owner dict.

    One accessor so a multi-key metric can never be read as a single key
    somewhere and silently render as zero.
    """
    keys = _JOBS_METRIC_KEYS.get(metric) or _JOBS_METRIC_KEYS[default]
    return sum(float((d or {}).get(k) or 0) for k in keys)


def _jobs_bucket_segments(bucket, metric, default='jobs'):
    """Per-bucket stacked-bar segments (active-metric units), bottom → top.

    The plugin envelope carries pre-truncated top-N ``owners`` per bucket
    with authoritative bucket totals, so — unlike the fs_scans
    ``_bucket_segments``, which derives the long tail locally — the "other"
    base segment here is ``bucket total − Σ owners`` (it also absorbs
    NULL-username jobs). Owner segments follow ascending so the largest
    owner sits at the top of the bar. Empty list when the bucket has no
    owners (→ drawn as a single flat bar).
    """
    owners = bucket.get('owners') or {}
    if not owners:
        return []
    vals = sorted(_jobs_metric_value(d, metric, default)
                  for d in owners.values())
    remainder = _jobs_metric_value(bucket, metric, default) - sum(vals)
    if remainder > 1e-9:
        return [remainder] + vals
    return vals


def _jobs_histogram_cache_key(hist, *, metric='jobs', log_y=False):
    """Hash exactly what the SVG depends on: the bucket labels, the chosen
    metric's values and owner-segment split, the dimension, null_count and
    the y-scale (not the full envelope — e.g. min_param/max_param don't
    affect the rendering). The job_count positivity vector joins the key
    because it decides which bars carry #sam/row/data-jh-bucket/<i> drill URLs — an
    hours-metric SVG with matching hours but a different populated-band set
    must not be reused. Owner names stay out of the key: the SVG carries no
    owner labels, so only the segment values shape it."""
    buckets = (hist or {}).get('buckets') or []
    payload = [(b.get('label'), _jobs_metric_value(b, metric),
                tuple(_jobs_bucket_segments(b, metric))) for b in buckets]
    clickable = [int(bool(b.get('job_count'))) for b in buckets]
    return _content_hash([
        payload, clickable, str((hist or {}).get('dimension', '')),
        int((hist or {}).get('null_count') or 0), str(metric), bool(log_y),
    ])


@caching.chart_cached(name='jobs_histogram', maxsize=128,
                      key_fn=_jobs_histogram_cache_key)
def generate_jobs_histogram(hist, *, metric='jobs', log_y=False) -> str:
    """Bar chart over a jobs_histogram envelope; owner-stacked when possible.

    Shared by the Wait Times, Job Sizes, and Durations tabs — the envelope's
    bucket vector is already complete and ordered (zeros included), so the
    x-axis is stable across filter changes. When buckets carry ``owners``
    (plugin ``owners_limit``), each bar becomes a single-hue per-user stack
    over an aggregated remainder base; otherwise the historical flat
    single-series chart renders unchanged.

    Args:
        hist: plugin envelope — ``{'dimension', 'buckets':
            [{'label','lo','hi','job_count','cpu_hours','gpu_hours'}, …],
            'null_count', 'total_count', …}``.
        metric: ``'jobs'`` (bucket job counts), ``'cpu_hours'`` or
            ``'gpu_hours'`` (charged hours per bucket).
        log_y: use a logarithmic y-axis — the treatment the filesystem-scan
            distribution histogram already offers. Job distributions are
            heavily skewed (most jobs wait seconds, a few wait days), so a
            linear axis buries the tail bands entirely. A log scale can't
            represent a stack meaningfully, so this falls back to one solid
            bar per band (the band's base color), keeping the drill anchors.

    Returns a "no jobs" placeholder div when every bucket is zero.
    """
    buckets = (hist or {}).get('buckets') or []
    if not buckets:
        return _empty_state('No jobs in this range')

    labels = [b.get('label', '') for b in buckets]
    vals = [_jobs_metric_value(b, metric) for b in buckets]
    if not any(vals):
        return _empty_state('No jobs in this range')

    fig, ax = plt.subplots(figsize=(14, 5))
    # Populated bands are clickable: #sam/row/data-jh-bucket/<index> sentinels route
    # through svg-chart-links.js to the matching data-jh-bucket row in
    # the bucket table, which drills into that band. Index-keyed (not
    # label) so the JS never parses band labels. Clickability follows
    # job_count, not the plotted metric.
    has_owners = any(b.get('owners') for b in buckets)
    band_colors = [UNITY_STACK_10[i % len(UNITY_STACK_10)]
                   for i in range(len(labels))]
    if not has_owners or log_y:
        # Owner-less envelope (owners_limit unset, or an older plugin) — the
        # historical flat single-series chart, byte-identical — and the log
        # y-axis, on which a stack carries no meaning: one solid bar per
        # band, in the band color its stack would have used.
        bars = ax.bar(range(len(labels)), vals,
                      color=(band_colors if has_owners else UNITY_PALETTE_10[0]),
                      edgecolor=UNITY_NCAR_NAVY, linewidth=0.5)
        for i, (rect, b) in enumerate(zip(bars, buckets)):
            if b.get('job_count'):
                rect.set_url(links.JH_BUCKET.url(i))
    else:
        # Stack per-owner segments (bottom "other" remainder + owners
        # ascending), shaded within the band's color family — the fs_scans
        # distribution-histogram treatment: the spread between users is
        # legible before clicking.
        colors = band_colors
        for i, b in enumerate(buckets):
            segs = _jobs_bucket_segments(b, metric)
            url = links.JH_BUCKET.url(i) if b.get('job_count') else None
            if not segs:
                bar = ax.bar(i, vals[i], color=colors[i],
                             edgecolor=UNITY_NCAR_NAVY, linewidth=0.5)
                if url:
                    bar.patches[0].set_url(url)
                continue
            shades = _shade_family(colors[i], len(segs))
            bottom = 0.0
            for seg_val, shade in zip(segs, shades):
                cont = ax.bar(i, seg_val, bottom=bottom, color=shade,
                              edgecolor='white', linewidth=0.3)
                if url:
                    cont.patches[0].set_url(url)
                bottom += seg_val
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha='right')
    ax.set_ylabel(_JOBS_METRIC_LABELS.get(metric, 'Jobs'))
    if log_y:
        ax.set_yscale('log')
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, axis='y', alpha=0.3)

    return _fig_to_svg(fig)


def _jobs_timeseries_series(ts, metric):
    """``(labels, series)`` for the stacked timeline, bottom → top.

    ``series`` is ``[(label, [value per band]), …]`` with ``'Others'``
    first — the ``get_daily_user_usage_for_project`` convention the
    resource-details Usage Trend already renders, so the two stacked charts
    read the same way.

    The plugin hands owners back in **global rank order, identical in every
    band**, so a name keeps its colour and its position across the whole
    axis. That is the property a stacked time series needs and the reason
    ``jobs_timeseries`` ranks once over the window rather than per band.
    Owners are reversed here so the largest lands on top of the stack, and
    "Others" is ``band total − Σ owners`` — derivable, never synthesized.
    """
    bands = (ts or {}).get('bands') or []
    labels = [b.get('label', '') for b in bands]
    if not bands:
        return labels, []

    # Every band carries the same keys; take the order from the first.
    owner_names = list((bands[0].get('owners') or {}).keys())

    others = []
    for band in bands:
        owners = band.get('owners') or {}
        total = _jobs_metric_value(band, metric)
        named = sum(_jobs_metric_value(owners.get(n), metric)
                    for n in owner_names)
        others.append(max(0.0, total - named))

    series = []
    if any(v > 1e-9 for v in others) or not owner_names:
        series.append(('Others', others))
    for name in reversed(owner_names):
        series.append((name, [
            _jobs_metric_value((b.get('owners') or {}).get(name), metric)
            for b in bands
        ]))
    return labels, series


def _jobs_timeseries_cache_key(ts, *, metric='jobs', period='day',
                               entity_kind='user',
                               link_entities=True):
    """Hash what the SVG depends on: band labels, the chosen metric's
    per-series values, and the legend's link treatment. The job_count
    positivity vector joins the key because it decides which bars carry
    #sam/row/data-jt-period/<i> drill URLs — a charges SVG with matching charges but a
    different populated-band set must not be reused."""
    labels, series = _jobs_timeseries_series(ts, metric)
    clickable = [int(bool(b.get('job_count')))
                 for b in (ts or {}).get('bands') or []]
    return _content_hash([
        labels, [(n, v) for n, v in series], clickable,
        str(metric), str(period), str(entity_kind), bool(link_entities),
    ])


@caching.chart_cached(name='jobs_timeseries', maxsize=128,
                      key_fn=_jobs_timeseries_cache_key)
def generate_jobs_timeseries_stacked(ts, *, metric='jobs', period='day',
                                     entity_kind='user',
                                     link_entities=True) -> str:
    """Stacked activity timeline over a ``jobs_timeseries`` envelope.

    The time axis the job-history card otherwise lacks: one bar per calendar
    band, segmented by the window's top-N owners over an aggregated
    "Others" base, with a clickable right-side legend.

    Args:
        ts: plugin envelope — ``{'period', 'bands': [{'label','start','end',
            'job_count','cpu_hours','gpu_hours','cpu_charges','gpu_charges',
            'owners'}, …], 'totals', 'total_count', …}``.
        metric: a ``_JOBS_METRIC_KEYS`` member. ``'charges'`` sums the
            plugin's separate cpu/gpu charge keys.
        period: ``'day'``/``'week'``/``'month'`` — labels the x-axis and
            joins the cache key (same values under a different granularity
            are a different chart).
        entity_kind: ``'user'`` or ``'project'`` — which modal a legend
            click opens, matching the owner axis.
        link_entities: when False the legend renders unlinked. Gate this on
            the viewer's permission, exactly as the By User / By Project
            tables gate their own quick-view links.

    Interactions (via svg-chart-links.js):
        - every segment of a populated band links to ``#sam/row/data-jt-period/<index>`` →
          expands that band's row in the period table below. Index-keyed,
          so the JS never parses band labels.
        - each named legend entry links to the entity's **modal route**
          (``MODAL_ROUTES``), NOT a ``#sam/row/data-job-user/`` row sentinel. Those
          sentinels are scoped by ``openEntityRow`` to the *clicked* chart's
          tab-pane, and this chart lives in the Jobs pane while the By User
          / By Project rows live in their own — which are lazily loaded and
          usually absent besides. A row sentinel here is a silent no-op
          (verified in the browser); the modal works from any pane and
          needs nothing pre-rendered. The stacked-area chart on the status
          dashboard resolves the same problem the same way.
          "Others" is never linked.

    Returns a placeholder div when every band is empty.
    """
    labels, series = _jobs_timeseries_series(ts, metric)
    if not labels or not series:
        return _empty_state('No jobs in this range')
    if not any(any(v > 0 for v in vals) for _n, vals in series):
        return _empty_state('No jobs in this range')

    env_bands = (ts or {}).get('bands') or []
    # Others keeps the neutral grey and does NOT advance the palette cursor,
    # so the named colours are stable whether or not a remainder exists.
    bands = series_mod.from_pairs(series)
    colors = series_mod.assign_colors(bands, UNITY_STACK_10, UNITY_NCAR_GRAY_LIGHT)

    fig, ax = plt.subplots(figsize=(18, 5))
    x = range(len(labels))
    bottoms = [0.0] * len(labels)
    for (name, vals), color in zip(series, colors):
        bars = ax.bar(x, vals, width=1.0, bottom=bottoms, color=color,
                      edgecolor=UNITY_NCAR_NAVY, linewidth=0.3)
        for i, (value, rect) in enumerate(zip(vals, bars.patches)):
            # A zero-height rect is an invisible click target, so it gets no
            # link — and job_count gates it besides, so a band with no jobs
            # is never clickable whatever the plotted metric says. Note the
            # consequence on the charges view: an all-uncharged band draws
            # at zero and loses its BAR link, but its row in the period
            # table below still carries data-jt-period and still drills.
            if value and env_bands[i].get('job_count'):
                rect.set_url(links.JT_PERIOD.url(i))
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    # Thin the tick labels rather than rotating 120 of them into a smear.
    step = max(1, len(labels) // 12)
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[i] for i in ticks], rotation=30, ha='right')
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.set_ylabel(_JOBS_METRIC_LABELS.get(metric, 'Jobs'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, axis='y', alpha=0.3)

    # Reversed so the legend reads top-to-bottom matching the visual stack.
    # Built from proxy Patches (not the BarContainers) because that is what
    # makes get_patches()/get_texts() positionally addressable for set_url.
    rev = list(reversed(series))
    rev_colors = list(reversed(colors))
    handles = [mpatches.Patch(color=c, label=n)
               for (n, _v), c in zip(rev, rev_colors)]
    leg = ax.legend(handles=handles, loc='center left',
                    bbox_to_anchor=(1.01, 0.5), frameon=False,
                    fontsize=11, labelspacing=0.7)
    if link_entities:
        modal_url = (_project_modal_url if entity_kind == 'project'
                     else _user_modal_url)
        for band, patch, text in zip(
                reversed(bands), leg.get_patches(), leg.get_texts()):
            if not band.is_linkable:
                continue
            url = modal_url(band.link_key)
            patch.set_url(url)
            text.set_url(url)

    return _fig_to_svg(fig)


generate_jobs_usage_pie_chart = chart_view(JobsUsagePie)

#: Cache-key helpers under their historical module-level names. They moved
#: onto the chart classes as `cache_key`, but several tests import them from
#: here and they are the same function either way.
_disk_entity_pie_cache_key = DiskEntityPie.cache_key
_user_usage_pie_cache_key = UserUsagePie.cache_key
_jobs_usage_pie_cache_key = JobsUsagePie.cache_key


def generate_jobs_user_pie_chart(entity_data, metric='cpu_hours') -> str:
    """By User pie — delegates to the entity-agnostic renderer with the
    ``data-job-user`` row family."""
    return generate_jobs_usage_pie_chart(entity_data, metric,
                                         row_attr=links.JOB_USER.attr)


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


def _pace_cache_key(allocations, active_at, window_days=180, top_n=20,
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
        return _empty_state('No allocations available')

    window_start = active_at - timedelta(days=window_days)
    window_end = active_at + timedelta(days=window_days)

    days, bands = _pace_bands(allocations, active_at, window_start, window_end)
    if not bands:
        return _empty_state('No allocations in the ±{}d window'.format(window_days))

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

    return _fig_to_svg(fig)
