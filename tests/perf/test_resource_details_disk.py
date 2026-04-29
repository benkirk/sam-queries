"""Resource Usage Details (disk path) — query count regression test.

Locks in the ``bulk_current_disk_usage`` refactor that eliminated the
per-descendant N+1 in ``build_disk_subtree``. Before the fix, query
count scaled linearly with ``node_count_with_account``: NMMM0003 (20
disk accounts in a 21-node subtree) measured 83 queries on prod;
post-fix it measured 21, regardless of subtree size.

Mirrors ``utils/profiling/profile_resource_details.py`` — that script
breaks the same call chain into per-phase reports for ad-hoc
investigation; this test gates the full route.

Run::

    pytest -m perf -n 0 -v
    make perf
"""

import pytest

from .conftest import get_baseline

pytestmark = pytest.mark.perf


def test_resource_details_disk_route(
    auth_client, route_count_queries, _disk_target,
):
    """Full-stack query count for ``/user/resource-details`` on a disk
    resource for an active tree-root project from the snapshot.

    Catches a re-introduction of the ``Account.current_disk_usage()``
    N+1 inside ``build_disk_subtree`` — query count would jump from
    its post-fix flat constant back to ~3 × ``node_count_with_account``,
    which on the snapshot's biggest disk subtree would blow past the
    baseline.
    """
    projcode, resource_name = _disk_target
    baseline = get_baseline("resource_details_disk_route")

    with route_count_queries() as stats:
        response = auth_client.get(
            f'/user/resource-details?projcode={projcode}&resource={resource_name}'
        )

    assert response.status_code == 200, (
        f"GET /user/resource-details for {projcode}/{resource_name} "
        f"returned {response.status_code}"
    )
    assert stats.count <= baseline, (
        f"Disk resource-details route query regression: "
        f"{stats.count} queries > {baseline} baseline. "
        f"Subtree fanout in build_disk_subtree may have regressed — "
        f"check that Account.current_disk_usage callers still go "
        f"through bulk_current_disk_usage. {stats.summary()}"
    )
