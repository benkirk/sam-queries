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
from webapp.dashboards.charts.histogram import (
    CategoricalStackChart,
    DistributionHistogram,
    JobsHistogram,
    bucket_segments as _bucket_segments,
)
from webapp.dashboards.charts.stacked import (
    DiskUsageAreaChart,
    JobsTimeseriesChart,
    StackedSeriesChart,
    UsageTrendChart,
    UsageTrendStackedChart,
    UserProjAreaChart,
    _USAGE_METRIC_YLABELS,
)
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
# 1. Stacked time series (usage trend, disk usage, user/proj queue load)
#
# Migrated to the class hierarchy — see charts/stacked.py, where bar vs area
# is a `stack_mode` attribute rather than a fourth level of hierarchy.
# ---------------------------------------------------------------------------

generate_usage_timeseries_matplotlib = chart_view(UsageTrendChart)
generate_usage_timeseries_stacked_by_user = chart_view(UsageTrendStackedChart)
generate_disk_usage_stacked_area = chart_view(DiskUsageAreaChart)
generate_user_proj_stacked_area = chart_view(UserProjAreaChart)

_usage_timeseries_cache_key = UsageTrendChart.cache_key
_usage_stacked_cache_key = UsageTrendStackedChart.cache_key
_disk_usage_stacked_area_cache_key = DiskUsageAreaChart.cache_key
_user_proj_stacked_area_cache_key = UserProjAreaChart.cache_key


# ---------------------------------------------------------------------------
# 1c. Distribution histogram (Filesystem Scans — DISK)
#
# Migrated to the class hierarchy — see charts/histogram.py, shared with the
# job-history histogram below.
# ---------------------------------------------------------------------------

generate_distribution_histogram = chart_view(DistributionHistogram)
_distribution_cache_key = DistributionHistogram.cache_key


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


generate_jobs_histogram = chart_view(JobsHistogram)
_jobs_histogram_cache_key = JobsHistogram.cache_key


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


generate_jobs_timeseries_stacked = chart_view(JobsTimeseriesChart)
_jobs_timeseries_cache_key = JobsTimeseriesChart.cache_key


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
