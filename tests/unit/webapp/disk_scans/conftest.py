from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _disable_fs_scans_cache():
    """Disable the scan-result cache for these tests by default.

    The scoping/route tests exercise the query path directly; the cache is
    covered explicitly by the test_cached_scan_* cases (which re-enable it).
    Reset to a clean, enabled state on teardown so other modules aren't
    affected by the process-wide adapter singleton.
    """
    from webapp.disk_scans import cache as _c
    # A stored None per bucket means "initialized but disabled".
    _c._CACHE.reset_for_tests()
    yield
    # disabled=False -> drop the memo so buckets re-init on next use
    _c._CACHE.reset_for_tests(disabled=False)


@pytest.fixture
def _anchored_scan(monkeypatch):
    """Pin the scan date the age bands are measured back from.

    Anchoring is on the scan, not today (service.scan_reference_date), so the
    control's dates match the access-history chart's bands exactly — pinning it
    is what makes the expected dates below stable.
    """
    from webapp.disk_scans import service
    monkeypatch.setattr(service, 'scan_reference_date',
                        lambda scope: datetime(2026, 6, 1))
    return datetime(2026, 6, 1)
