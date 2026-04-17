"""Tests for webapp admin projects_routes pure helpers.

The helpers under test (``_snap_to_end_of_month``, ``_propose_renew_dates``,
``_propose_extend_end``) compute default form values from source-allocation
date ranges. They take plain attribute-only objects, so tests use a trivial
stub and avoid the DB.
"""
from datetime import datetime

import pytest

from webapp.dashboards.admin.projects_routes import (
    _propose_extend_end,
    _propose_renew_dates,
    _snap_to_end_of_month,
)


pytestmark = pytest.mark.unit


class _Alloc:
    """Minimal stand-in for Allocation — start_date / end_date only."""
    def __init__(self, start, end):
        self.start_date = start
        self.end_date = end


# ---------------------------------------------------------------------------
# _snap_to_end_of_month
# ---------------------------------------------------------------------------


class TestSnapToEndOfMonth:

    def test_mid_month_snaps_forward(self):
        assert _snap_to_end_of_month(datetime(2026, 4, 15)) == datetime(2026, 4, 30)

    def test_already_end_of_month_no_change(self):
        assert _snap_to_end_of_month(datetime(2026, 4, 30)) == datetime(2026, 4, 30)
        assert _snap_to_end_of_month(datetime(2026, 12, 31)) == datetime(2026, 12, 31)

    def test_day_one_snaps_backward(self):
        # May 1 → Apr 30 (previous month's last day)
        assert _snap_to_end_of_month(datetime(2026, 5, 1)) == datetime(2026, 4, 30)

    def test_jan_one_crosses_year_boundary(self):
        # Jan 1 2028 → Dec 31 2027
        assert _snap_to_end_of_month(datetime(2028, 1, 1)) == datetime(2027, 12, 31)

    def test_late_month_snaps_forward(self):
        # Oct 29 → Oct 31 (the observed CESM0002-style drift)
        assert _snap_to_end_of_month(datetime(2027, 10, 29)) == datetime(2027, 10, 31)

    def test_leap_february(self):
        assert _snap_to_end_of_month(datetime(2024, 2, 3)) == datetime(2024, 2, 29)

    def test_non_leap_february(self):
        assert _snap_to_end_of_month(datetime(2025, 2, 3)) == datetime(2025, 2, 28)

    def test_preserves_time_of_day(self):
        result = _snap_to_end_of_month(datetime(2026, 4, 15, 10, 30, 45))
        assert result == datetime(2026, 4, 30, 10, 30, 45)


# ---------------------------------------------------------------------------
# _propose_renew_dates
# ---------------------------------------------------------------------------


class TestProposeRenewDates:

    def test_fiscal_year_source_is_contiguous_and_aligned(self):
        # Nov 1 2024 → Oct 31 2025 (1-year fiscal). Renew should propose
        # Nov 1 2025 → Oct 31 2026.
        src = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        assert _propose_renew_dates([src]) == ('2025-11-01', '2026-10-31')

    def test_eighteen_month_source_snaps_forward(self):
        # CESM0002-style source: Nov 1 2024 → Apr 30 2026 (~18 months).
        # new_start = May 1 2026; new_start + period lands on Oct 29-30 2027
        # and snaps to Oct 31. Regression guard for the user-reported drift.
        src = _Alloc(datetime(2024, 11, 1), datetime(2026, 4, 30))
        _, end = _propose_renew_dates([src])
        assert end == '2027-10-31'

    def test_two_year_jan_dec_source_snaps_backward(self):
        # 2-year Jan 1 → Dec 31 source. new_start + period lands on Jan 1,
        # which must snap BACKWARD to Dec 31 of the prior year (not forward
        # to Jan 31 — that would miss by a month).
        src = _Alloc(datetime(2024, 1, 1), datetime(2025, 12, 31))
        assert _propose_renew_dates([src]) == ('2026-01-01', '2027-12-31')

    def test_multiple_sources_anchors_on_latest_end(self):
        # Two sources with different periods; anchor must be the latest-ending.
        earlier = _Alloc(datetime(2024, 1, 1), datetime(2025, 3, 31))
        latest  = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        start, end = _propose_renew_dates([earlier, latest])
        # Anchored on latest — Nov 1 next year start, snapped Oct 31 end
        assert start == '2025-11-01'
        assert end == '2026-10-31'

    def test_open_ended_sources_ignored_falls_back_to_today_plus_year(self):
        # Open-ended source (end_date=None) is skipped; fallback is used.
        src = _Alloc(datetime(2020, 1, 1), None)
        start, end = _propose_renew_dates([src])
        # End should be end-of-month one year from today — just assert shape.
        assert len(start) == 10 and start[4] == '-' and start[7] == '-'
        assert len(end) == 10
        # end must be a month-end (day 28-31)
        assert 28 <= int(end[8:10]) <= 31

    def test_empty_source_list_falls_back(self):
        start, end = _propose_renew_dates([])
        assert len(start) == 10
        assert 28 <= int(end[8:10]) <= 31


# ---------------------------------------------------------------------------
# _propose_extend_end
# ---------------------------------------------------------------------------


class TestProposeExtendEnd:

    def test_fiscal_year_source_extends_one_year(self):
        # Source period = 1 year; extend adds another year.
        src = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        assert _propose_extend_end([src]) == '2026-10-31'

    def test_two_year_jan_dec_source_snaps_backward(self):
        # 2-year source: extending 2 years lands on Dec 31 (not Jan 31).
        src = _Alloc(datetime(2024, 1, 1), datetime(2025, 12, 31))
        assert _propose_extend_end([src]) == '2027-12-31'

    def test_six_month_source_extends_six_months(self):
        # Half-year source; end + 6mo ≈ Sep 30. Must snap to Sep 30.
        src = _Alloc(datetime(2025, 4, 1), datetime(2025, 9, 30))
        assert _propose_extend_end([src]) == '2026-03-31'

    def test_open_ended_source_ignored(self):
        # Only open-ended source → no proposal.
        src = _Alloc(datetime(2020, 1, 1), None)
        assert _propose_extend_end([src]) == ''

    def test_no_sources(self):
        assert _propose_extend_end([]) == ''

    def test_multiple_sources_anchors_on_latest_end(self):
        earlier = _Alloc(datetime(2024, 1, 1), datetime(2025, 3, 31))
        latest  = _Alloc(datetime(2024, 11, 1), datetime(2025, 10, 31))
        # Anchor = latest; period = 1 year; end_date + period = Oct 31 2026.
        assert _propose_extend_end([earlier, latest]) == '2026-10-31'
