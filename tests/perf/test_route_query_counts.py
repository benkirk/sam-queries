"""Route-level query count regression tests.

These tests hit actual Flask routes via ``auth_client.get(...)`` and count
ALL SQL queries through the full stack — data fetch, template rendering,
and JSON serialization.  This catches lazy-load regressions that only
surface during ``render_template()`` or ``jsonify()`` traversal, which
are invisible to the function-level tests in ``test_query_counts.py``.

Each test mirrors one of the profiling scripts in ``utils/profiling/``:

    test_user_dashboard_route       ← profile_user_dashboard.py  (Issues 5, 6)
    test_allocations_index_route    ← profile_allocations.py     (show_usage=False)
    test_allocations_usage_route    ← profile_allocations.py     (show_usage=True — Issue 1)
    test_admin_orgs_card_route      ← profile_admin_orgs.py      (Issue 7)
    test_fstree_api_route           ← production 5min cache       (expensive API)

Run::

    pytest -m perf -n 0 -v
    make perf
"""

import pytest

from .conftest import get_baseline

pytestmark = pytest.mark.perf


# ---------------------------------------------------------------------------
# 1. User dashboard — GET /user/
# ---------------------------------------------------------------------------

def test_user_dashboard_route(auth_client, route_count_queries):
    """Full-stack query count for the user dashboard.

    Catches cascade-suppression regressions (Issues 5, 6) that would
    cause lazy loads during template rendering — invisible to
    function-level tests.
    """
    baseline = get_baseline("user_dashboard_route")

    with route_count_queries() as stats:
        response = auth_client.get('/user/')

    assert response.status_code == 200, (
        f"GET /user/ returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"User dashboard route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"{stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 2. Allocations index — GET /allocations/
# ---------------------------------------------------------------------------

def test_allocations_index_route(auth_client, route_count_queries):
    """Full-stack query count for allocations dashboard (no usage).

    Tests the summary + facility overview pipeline from
    profile_allocations.py scenario 1.
    """
    baseline = get_baseline("allocations_index_route")

    with route_count_queries() as stats:
        response = auth_client.get('/allocations/')

    assert response.status_code == 200, (
        f"GET /allocations/ returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"Allocations index route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"{stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 3. Allocations with usage — GET /allocations/?show_usage=true
# ---------------------------------------------------------------------------

def test_allocations_usage_route(auth_client, route_count_queries):
    """Full-stack query count for allocations dashboard WITH usage.

    This is the critical regression test for Issue 1 — the scenario that
    produced 52,923 queries before the bulk-fetch fix.  Exercises
    ``get_allocation_summary_with_usage()`` with ALL active resources,
    ``_aggregate_usage_to_total()``, and chart generation through the
    full route.
    """
    baseline = get_baseline("allocations_usage_route")

    with route_count_queries() as stats:
        response = auth_client.get('/allocations/?show_usage=true')

    assert response.status_code == 200, (
        f"GET /allocations/?show_usage=true returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"Allocations usage route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"{stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 4. Admin organizations card ��� GET /admin/htmx/organizations-card
# ---------------------------------------------------------------------------

def test_admin_orgs_card_route(auth_client, route_count_queries):
    """Full-stack query count for the organizations card fragment.

    Catches cascade-suppression regressions (Issue 7) — lazy loads
    during template rendering of the deep Organization → Institution →
    User relationship tree.
    """
    baseline = get_baseline("admin_orgs_card_route")

    with route_count_queries() as stats:
        response = auth_client.get('/admin/htmx/organizations-card')

    assert response.status_code == 200, (
        f"GET /admin/htmx/organizations-card returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"Admin orgs card route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"{stats.summary()}"
    )


# ---------------------------------------------------------------------------
# 5. Fstree API — GET /api/v1/fstree_access/
# ---------------------------------------------------------------------------

def test_fstree_api_route(auth_client, route_count_queries):
    """Full-stack query count for the fstree API (all resources).

    This endpoint is cached for 5 minutes in production because it's
    expensive — builds the complete fairshare tree and serializes the
    nested facility → allocationType → project → resource → user
    structure to JSON.  Lazy loads during jsonify() traversal would
    be invisible to the function-level ``test_get_fstree_data``.
    """
    baseline = get_baseline("fstree_api_route")

    with route_count_queries() as stats:
        response = auth_client.get('/api/v1/fstree_access/')

    assert response.status_code == 200, (
        f"GET /api/v1/fstree_access/ returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"Fstree API route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"{stats.summary()}"
    )
