"""Server-side chart rendering.

Every chart is a `BaseChart` subclass bound to its cache by `chart_view`, which
reads `cache_name` / `cache_maxsize` off the class and composes the layout and
theme axes into the cache key. Callers pass normal Python objects.

Adding a chart: subclass the closest family, set `cache_name`,
`cache_maxsize`, `empty_message` and `LAYOUTS`; implement `cache_key` as a
**staticmethod over the raw constructor arguments** so a cache hit never
constructs the chart; bind it below with `chart_view(...)`, add it to
`__all__`, and add a case to `tests/unit/charts/chart_samples.py` (a gate requires
one). A row drill needs only the row attribute declared at the chart -- no
JavaScript change; the attribute travels in the href.

WARNING: cache names are Redis key prefixes, and the ORDER of the `chart_view`
calls below is the row order on the admin Caching card.
`tests/unit/charts/test_chart_cache_registry.py` pins both.

WARNING: cache keys hash input data, not rendering code, so after a deploy warm
Redis entries serve old-code SVGs until the 600 s TTL expires. Run
`sam-admin cache --refresh --category chart` when a change is user-visible.

Module layout and the family hierarchy: CLAUDE.md, *Charts*.
"""

from webapp.dashboards.charts import (  # noqa: F401
    jobs_metrics, layout, links, series, theme,
)
from webapp.dashboards.charts.base import BaseChart, chart_view  # noqa: F401
from webapp.dashboards.charts.dualpanel import (  # noqa: F401
    DualPanelTimeSeriesChart,
    NodetypeHistoryChart,
    QueueHistoryChart,
)
from webapp.dashboards.charts.histogram import (  # noqa: F401
    CategoricalStackChart,
    DistributionHistogram,
    JobsHistogram,
)
from webapp.dashboards.charts.layout import Layout  # noqa: F401
from webapp.dashboards.charts.pace import PaceChart  # noqa: F401
from webapp.dashboards.charts.pie import (  # noqa: F401
    DiskEntityPie,
    JobsUsagePie,
    PieChart,
    UserUsagePie,
)
from webapp.dashboards.charts.stacked import (  # noqa: F401
    DiskUsageAreaChart,
    JobsTimeseriesChart,
    StackedSeriesChart,
    UsageTrendChart,
    UsageTrendStackedChart,
    UserProjAreaChart,
)
from webapp.dashboards.charts.sunburst import (  # noqa: F401
    AllocationSunburst,
    FairShareSunburst,
    JobsFacilitySunburst,
    PanelSunburst,
    panel_rows,
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
    resolve_theme,
    scale_bytes,
)


# ---------------------------------------------------------------------------
# The 16 cached charts.
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
generate_disk_entity_pie_chart = chart_view(DiskEntityPie)
generate_user_usage_pie_chart = chart_view(UserUsagePie)

# 5. Job-history charts.  charts/histogram.py, charts/stacked.py, charts/pie.py
generate_jobs_histogram = chart_view(JobsHistogram)
generate_jobs_timeseries_stacked = chart_view(JobsTimeseriesChart)
generate_jobs_usage_pie_chart = chart_view(JobsUsagePie)

# 6. Allocation pace chart.  charts/pace.py
generate_pace_chart_matplotlib = chart_view(PaceChart)

# 7. Fair-share sunburst (admin Facilities card).  charts/sunburst.py
generate_fair_share_sunburst = chart_view(FairShareSunburst)

# 8. Allocated / Used sunbursts (allocations dashboard).  charts/sunburst.py
generate_allocation_sunburst = chart_view(AllocationSunburst)

# 9. Job-history By Project, grouped by facility.  charts/sunburst.py
generate_jobs_facility_sunburst = chart_view(JobsFacilitySunburst)

# 10. Facility / panel / project expanded view (allocations + job history).  charts/sunburst.py
generate_panel_sunburst = chart_view(PanelSunburst)


__all__ = [
    # The public generators.
    'generate_usage_timeseries_matplotlib',
    'generate_usage_timeseries_stacked_by_user',
    'generate_disk_usage_stacked_area',
    'generate_user_proj_stacked_area',
    'generate_distribution_histogram',
    'generate_nodetype_history_matplotlib',
    'generate_queue_history_matplotlib',
    'generate_disk_entity_pie_chart',
    'generate_user_usage_pie_chart',
    'generate_jobs_histogram',
    'generate_jobs_timeseries_stacked',
    'generate_jobs_usage_pie_chart',
    'generate_pace_chart_matplotlib',
    'generate_fair_share_sunburst',
    'generate_allocation_sunburst',
    'generate_jobs_facility_sunburst',
    'generate_panel_sunburst',
    'panel_rows',
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
