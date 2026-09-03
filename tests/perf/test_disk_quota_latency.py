"""Latency smoke for the disk_quota assembler.

`get_disk_quotas` iterates every qualifying DISK account (one record each),
composing paths / quota / dataManager in Python over selectin-loaded collections.
This benchmark catches a fan-out or N+1 regression — an order-of-magnitude
slowdown, not microseconds.

pytest-benchmark self-disables under xdist, so run serial: ``make perf`` /
``pytest -m perf -n 0 -v``.
"""

import pytest

pytestmark = pytest.mark.perf


def test_disk_quota_latency(benchmark, session):
    """Assembler latency — regression guard against a cartesian/N+1 blow-up."""
    from sam.queries.disk_quota import get_disk_quotas

    result = benchmark(get_disk_quotas, session)
    assert isinstance(result, list)
