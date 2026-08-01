"""Server-side chart rendering.

Every chart is a `BaseChart` subclass bound to its cache by `chart_view`,
which reads `cache_name` / `cache_maxsize` off the class and composes the two
render axes into the cache key. Callers pass normal Python objects; hashing
and caching are handled internally.

    charts/
      __init__.py     this facade: bindings, re-exports, __all__
      base.py         BaseChart lifecycle + chart_view (the cache binder)
      theme.py        fonts, rcParams, the Unity palettes, Theme
      layout.py       Layout — the geometry axis
      links.py        drill targets                     [no matplotlib]
      series.py       stacked-band normalization        [no matplotlib]
      jobs_metrics.py plugin-envelope accessors         [no matplotlib]
      pie.py stacked.py histogram.py dualpanel.py pace.py    the families

## Adding chart #17

1. Subclass the closest family (or `BaseChart` directly — see `pace.py` for
   when that is the right call).
2. Set `cache_name`, `cache_maxsize`, `empty_message`, `LAYOUTS`.
3. Implement `cache_key` as a **staticmethod over the raw constructor
   arguments**, so a cache hit never constructs the chart or runs `prepare()`.
4. Bind it here with `chart_view(...)` and add its name to `__all__`.
5. Add a case to `tests/unit/chart_samples.py` — a gate asserts every public
   generator has one.

If it drills into a table row, declare the row attribute at the chart
(`links.RowDrill('data-...')`). **No JavaScript change is needed**; the
attribute travels in the href.

## Two things to know before editing

**Cache names are Redis key prefixes** (`caching/redis_chart.py`), and the
order of the `chart_view` calls below is the order rows appear on the admin
Caching card. `tests/unit/test_chart_cache_registry.py` pins both.

**Cache keys hash input data, not rendering code.** After a deploy, warm Redis
entries serve old-code SVGs until the 600 s TTL expires. Run
`sam-admin cache --refresh --category chart` when a change is user-visible.
"""

from webapp.dashboards.charts import (  # noqa: F401
    jobs_metrics, layout, links, series, theme,
)
from webapp.dashboards.charts.base import (  # noqa: F401
    BaseChart,
    chart_view,
    empty_state as _empty_state,
    fig_to_svg as _fig_to_svg,
)
from webapp.dashboards.charts.dualpanel import (  # noqa: F401
    DualPanelTimeSeriesChart,
    NodetypeHistoryChart,
    QueueHistoryChart,
    _to_display_tz,
)
from webapp.dashboards.charts.histogram import (  # noqa: F401
    CategoricalStackChart,
    DistributionHistogram,
    JobsHistogram,
    bucket_segments as _bucket_segments,
)
from webapp.dashboards.charts.jobs_metrics import (  # noqa: F401
    JOBS_METRIC_KEYS as _JOBS_METRIC_KEYS,
    JOBS_METRIC_LABELS as _JOBS_METRIC_LABELS,
    jobs_bucket_segments as _jobs_bucket_segments,
    jobs_metric_value as _jobs_metric_value,
    jobs_timeseries_series as _jobs_timeseries_series,
)
from webapp.dashboards.charts.layout import Layout  # noqa: F401
from webapp.dashboards.charts.pace import (  # noqa: F401
    PaceChart,
    pace_bands as _pace_bands,
    pace_key_fields as _pace_key_fields,
)
from webapp.dashboards.charts.pie import (  # noqa: F401
    AllocationTypePie,
    DiskEntityPie,
    FacilityPie,
    JobsUsagePie,
    PieChart,
    UserUsagePie,
    trim_cumulative as _pie_cumulative_keep,
    trim_fixed_cap as _pie_trim,
)
from webapp.dashboards.charts.stacked import (  # noqa: F401
    DiskUsageAreaChart,
    JobsTimeseriesChart,
    StackedSeriesChart,
    UsageTrendChart,
    UsageTrendStackedChart,
    UserProjAreaChart,
    _USAGE_METRIC_YLABELS,
)
from webapp.dashboards.charts.theme import (  # noqa: F401
    UNITY_NCAR_BLUE,
    UNITY_NCAR_GOLD,
    UNITY_NCAR_GRAY,
    UNITY_NCAR_GRAY_LIGHT,
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
    _FONT_DIR,
    autopct_color_for as _autopct_color_for,
    resolve_theme,
    scale_bytes,
    shade_family as _shade_family,
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
# The 15 cached charts.
#
# ORDER IS LOAD-BEARING: `chart_cached` appends to the cache registry at
# decoration time, so this is the order rows appear on the admin Caching card.
# ---------------------------------------------------------------------------

# 1. Stacked time series — usage trend (flat + by user), disk usage,
#    user/project queue load.  charts/stacked.py
generate_usage_timeseries_matplotlib = chart_view(UsageTrendChart)
generate_usage_timeseries_stacked_by_user = chart_view(UsageTrendStackedChart)
generate_disk_usage_stacked_area = chart_view(DiskUsageAreaChart)
generate_user_proj_stacked_area = chart_view(UserProjAreaChart)

# 2. Categorical stacked-bar histogram.  charts/histogram.py
generate_distribution_histogram = chart_view(DistributionHistogram)

# 3. Dual-panel status time series.  charts/dualpanel.py
generate_nodetype_history_matplotlib = chart_view(NodetypeHistoryChart)
generate_queue_history_matplotlib = chart_view(QueueHistoryChart)

# 4. Pies.  charts/pie.py
generate_facility_pie_chart_matplotlib = chart_view(FacilityPie)
generate_allocation_type_pie_chart_matplotlib = chart_view(AllocationTypePie)
generate_disk_entity_pie_chart = chart_view(DiskEntityPie)
generate_user_usage_pie_chart = chart_view(UserUsagePie)

# 5. Job-history charts.  charts/histogram.py, charts/stacked.py, charts/pie.py
generate_jobs_histogram = chart_view(JobsHistogram)
generate_jobs_timeseries_stacked = chart_view(JobsTimeseriesChart)
generate_jobs_usage_pie_chart = chart_view(JobsUsagePie)

# 6. Allocation pace chart.  charts/pace.py
generate_pace_chart_matplotlib = chart_view(PaceChart)


def generate_jobs_user_pie_chart(entity_data, metric='cpu_hours') -> str:
    """By User pie — delegates to the entity-agnostic renderer with the
    ``data-job-user`` row family.

    Deliberately a facade rather than a 16th bound chart: binding it would
    register a second cache and add a row to the admin Caching card for what
    is really the same chart under a different drill attribute.
    """
    return generate_jobs_usage_pie_chart(entity_data, metric,
                                         row_attr=links.JOB_USER.attr)


#: Cache-key helpers under their historical module-level names. They moved
#: onto the chart classes as `cache_key`; several tests import them from here
#: and they are the same function either way.
_usage_timeseries_cache_key = UsageTrendChart.cache_key
_usage_stacked_cache_key = UsageTrendStackedChart.cache_key
_disk_usage_stacked_area_cache_key = DiskUsageAreaChart.cache_key
_user_proj_stacked_area_cache_key = UserProjAreaChart.cache_key
_distribution_cache_key = DistributionHistogram.cache_key
_disk_entity_pie_cache_key = DiskEntityPie.cache_key
_user_usage_pie_cache_key = UserUsagePie.cache_key
_jobs_histogram_cache_key = JobsHistogram.cache_key
_jobs_timeseries_cache_key = JobsTimeseriesChart.cache_key
_jobs_usage_pie_cache_key = JobsUsagePie.cache_key
_pace_cache_key = PaceChart.cache_key


__all__ = [
    # The 16 public generators.
    'generate_usage_timeseries_matplotlib',
    'generate_usage_timeseries_stacked_by_user',
    'generate_disk_usage_stacked_area',
    'generate_user_proj_stacked_area',
    'generate_distribution_histogram',
    'generate_nodetype_history_matplotlib',
    'generate_queue_history_matplotlib',
    'generate_facility_pie_chart_matplotlib',
    'generate_allocation_type_pie_chart_matplotlib',
    'generate_disk_entity_pie_chart',
    'generate_user_usage_pie_chart',
    'generate_jobs_histogram',
    'generate_jobs_timeseries_stacked',
    'generate_jobs_usage_pie_chart',
    'generate_jobs_user_pie_chart',
    'generate_pace_chart_matplotlib',
    # The hierarchy, for anyone subclassing.
    'BaseChart',
    'chart_view',
    'CategoricalStackChart',
    'DualPanelTimeSeriesChart',
    'PieChart',
    'StackedSeriesChart',
    # The render axes.
    'Layout',
    'Theme',
    'resolve_theme',
    # Palettes and helpers used outside the package.
    'UNITY_PALETTE_10',
    'UNITY_STACK_10',
    'UNITY_STACK_20',
    'scale_bytes',
]
