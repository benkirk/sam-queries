"""Latency smoke for the directory_access populators.

`user_populator` was a single mega-join that fanned each user ~14x over
accounts x allocations x phone x institution x organization (~135k rows -> a
temporary-table GROUP BY, ~7s in prod). It is now split-and-assemble (see
`sam/queries/directory_access.py`). This benchmark catches a fan-out regression
— an order-of-magnitude slowdown, not microseconds.

pytest-benchmark self-disables under xdist, so run serial: ``make perf`` /
``pytest -m perf -n 0 -v``.
"""

import pytest

pytestmark = pytest.mark.perf


def test_directory_access_user_populator_latency(benchmark, session):
    """Split-and-assemble populator — regression guard against the mega-join fan-out."""
    from sam.queries.directory_access import user_populator

    result = benchmark(user_populator, session)
    assert isinstance(result, dict)


def test_directory_access_group_populator_latency(benchmark, session):
    """Companion group populator (already split-and-assemble) — baseline for the pair."""
    from sam.queries.directory_access import group_populator

    result = benchmark(group_populator, session)
    assert isinstance(result, dict)
