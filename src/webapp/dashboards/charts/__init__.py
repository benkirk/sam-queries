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
from webapp.dashboards.charts.pace import (
    PaceChart,
    pace_bands as _pace_bands,
    pace_key_fields as _pace_key_fields,
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
# A direct BaseChart subclass with no family — see charts/pace.py for why.
# ---------------------------------------------------------------------------

generate_pace_chart_matplotlib = chart_view(PaceChart)
_pace_cache_key = PaceChart.cache_key
