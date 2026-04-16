"""Query-count regression tests for SAM's hot-path functions.

Each test measures the number of SQL queries a function executes and
asserts it stays at or below the baseline recorded in ``baselines.json``.

Run these tests with::

    pytest -m perf -n 0 -v

If a test fails, the assertion message shows the actual count vs. the
baseline. Either fix the regression (N+1 pattern, missing joinedload)
or update ``baselines.json`` if the increase is intentional.

Re-baseline workflow:
    1. Run ``pytest -m perf -n 0 -v``
    2. Update ``baselines.json`` with the new count
    3. Commit both the code change and the updated baseline
"""

from datetime import datetime, timedelta

import pytest

from .conftest import get_baseline

pytestmark = pytest.mark.perf


# ---------------------------------------------------------------------------
# 1. get_user_dashboard_data
# ---------------------------------------------------------------------------

def test_get_user_dashboard_data(session, count_queries, perf_multi_project_user):
    """Query count for the user dashboard — primary target of the ORM perf push."""
    from sam.queries.dashboard import get_user_dashboard_data

    user = perf_multi_project_user
    baseline = get_baseline("get_user_dashboard_data")

    with count_queries() as stats:
        data = get_user_dashboard_data(session, user.user_id)

    assert data is not None, "get_user_dashboard_data returned None"
    assert stats.count <= baseline, (
        f"get_user_dashboard_data query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 2. get_project_dashboard_data
# ---------------------------------------------------------------------------

def test_get_project_dashboard_data(session, count_queries, perf_active_project):
    """Query count for the single-project dashboard path."""
    from sam.queries.dashboard import get_project_dashboard_data

    projcode = perf_active_project.projcode
    baseline = get_baseline("get_project_dashboard_data")

    with count_queries() as stats:
        data = get_project_dashboard_data(session, projcode)

    assert data is not None, f"get_project_dashboard_data returned None for {projcode}"
    assert stats.count <= baseline, (
        f"get_project_dashboard_data query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 3. get_resource_detail_data
# ---------------------------------------------------------------------------

def test_get_resource_detail_data(session, count_queries, perf_active_project, perf_hpc_resource):
    """Query count for the per-resource drilldown route."""
    from sam.queries.dashboard import get_resource_detail_data

    projcode = perf_active_project.projcode
    resource_name = perf_hpc_resource.resource_name
    # Use a 90-day window ending today — covers the common dashboard case
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    baseline = get_baseline("get_resource_detail_data")

    with count_queries() as stats:
        data = get_resource_detail_data(
            session, projcode, resource_name, start_date, end_date,
        )

    # data can be None if project has no allocation on this resource — that's OK,
    # we still care about the query count for the lookup path
    assert stats.count <= baseline, (
        f"get_resource_detail_data query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 4. get_fstree_data
# ---------------------------------------------------------------------------

def test_get_fstree_data(session, count_queries, perf_hpc_resource):
    """Query count for the PBS fairshare tree builder — most expensive single function."""
    from sam.queries.fstree_access import get_fstree_data

    resource_name = perf_hpc_resource.resource_name
    baseline = get_baseline("get_fstree_data")

    with count_queries() as stats:
        data = get_fstree_data(session, resource_name=resource_name)

    assert data is not None, f"get_fstree_data returned None for {resource_name}"
    assert stats.count <= baseline, (
        f"get_fstree_data query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 5. get_allocation_summary
# ---------------------------------------------------------------------------

def test_get_allocation_summary(session, count_queries, perf_hpc_resource):
    """Query count for the allocation summary aggregation."""
    from sam.queries.allocations import get_allocation_summary

    resource_name = perf_hpc_resource.resource_name
    baseline = get_baseline("get_allocation_summary")

    with count_queries() as stats:
        data = get_allocation_summary(session, resource_name=resource_name)

    assert isinstance(data, list), "get_allocation_summary should return a list"
    assert stats.count <= baseline, (
        f"get_allocation_summary query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 6. get_allocation_summary_with_usage
# ---------------------------------------------------------------------------

def test_get_allocation_summary_with_usage(session, count_queries, perf_hpc_resource):
    """Query count for allocation summary + charge joins."""
    from sam.queries.allocations import get_allocation_summary_with_usage

    resource_name = perf_hpc_resource.resource_name
    baseline = get_baseline("get_allocation_summary_with_usage")

    with count_queries() as stats:
        data = get_allocation_summary_with_usage(session, resource_name=resource_name)

    assert isinstance(data, list), "get_allocation_summary_with_usage should return a list"
    assert stats.count <= baseline, (
        f"get_allocation_summary_with_usage query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 7. Project.get_detailed_allocation_usage
# ---------------------------------------------------------------------------

def test_get_detailed_allocation_usage(session, count_queries, perf_active_project):
    """Query count for the Project instance method used in templates."""
    baseline = get_baseline("get_detailed_allocation_usage")

    with count_queries() as stats:
        data = perf_active_project.get_detailed_allocation_usage()

    assert isinstance(data, dict), "get_detailed_allocation_usage should return a dict"
    assert stats.count <= baseline, (
        f"Project.get_detailed_allocation_usage query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 8. get_project_rolling_usage
# ---------------------------------------------------------------------------

def test_get_project_rolling_usage(session, count_queries, perf_active_project):
    """Query count for rolling-window charge data."""
    from sam.queries.rolling_usage import get_project_rolling_usage

    projcode = perf_active_project.projcode
    baseline = get_baseline("get_project_rolling_usage")

    with count_queries() as stats:
        data = get_project_rolling_usage(session, projcode)

    assert isinstance(data, dict), "get_project_rolling_usage should return a dict"
    assert stats.count <= baseline, (
        f"get_project_rolling_usage query count regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Breakdown: {stats.summary()}"
    )
