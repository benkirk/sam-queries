import pytest


@pytest.fixture(autouse=True)
def _disable_jobs_cache():
    """Disable the aggregation TTL cache for these tests by default.

    The route tests exercise the service path directly with per-test mock
    returns; a live cache would leak one test's envelope into the next
    (same key: filters all None). Explicit cache behavior is covered by
    test_webapp_jobs_cache.py. Reset on teardown so other modules aren't
    affected by the process-wide adapter singleton.
    """
    from webapp.jobs import cache as _c
    _c._CACHE.reset_for_tests()
    yield
    # disabled=False -> drop the memo so buckets re-init on next use
    _c._CACHE.reset_for_tests(disabled=False)
