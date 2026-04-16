"""Latency smoke tests via pytest-benchmark.

These are loose upper-bound checks — "this ran in under N ms" — not
precise measurements. Absolute timings are machine-dependent, so the
value is catching order-of-magnitude regressions (the 50ms thing that
suddenly takes 2000ms), not tracking microsecond improvements.

pytest-benchmark is automatically disabled when xdist is active, so
these tests MUST run with ``-n 0`` (serial).  The ``make perf`` target
handles this.

Run::

    pytest -m perf -n 0 -v
    make perf
"""

import pytest

pytestmark = pytest.mark.perf


def test_user_dashboard_latency(benchmark, session, perf_multi_project_user):
    """Smoke benchmark for get_user_dashboard_data — the primary dashboard entry point."""
    from sam.queries.dashboard import get_user_dashboard_data

    user_id = perf_multi_project_user.user_id

    result = benchmark(get_user_dashboard_data, session, user_id)
    assert result is not None


def test_project_dashboard_latency(benchmark, session, perf_active_project):
    """Smoke benchmark for get_project_dashboard_data — single-project path."""
    from sam.queries.dashboard import get_project_dashboard_data

    projcode = perf_active_project.projcode

    result = benchmark(get_project_dashboard_data, session, projcode)
    assert result is not None


def test_allocation_summary_latency(benchmark, session, perf_hpc_resource):
    """Smoke benchmark for get_allocation_summary — heavy aggregation path."""
    from sam.queries.allocations import get_allocation_summary

    resource_name = perf_hpc_resource.resource_name

    result = benchmark(get_allocation_summary, session, resource_name=resource_name)
    assert isinstance(result, list)
