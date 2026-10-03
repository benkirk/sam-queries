"""Pace chart across a fiscal-year boundary: monthly past, projected future, committed line."""

from datetime import datetime, timedelta

import pytest

from webapp.dashboards.allocations.burn import pace_segments
from webapp.dashboards.charts.pace import PACE_WINDOW_DAYS, PaceChart, pace_bands

NOW = datetime(2026, 10, 1)


def _alloc(projcode, start, end, amount, used, allocation_id):
    return {'projcode': projcode, 'start_date': start, 'end_date': end,
            'total_amount': amount, 'total_used': used, 'allocation_id': allocation_id}


ENDED = _alloc('ENDED01', datetime(2025, 10, 1), datetime(2026, 9, 30, 23, 59, 59),
               1_000_000.0, 600_000.0, 1)
CURRENT = _alloc('CURR01', NOW, datetime(2027, 9, 30, 23, 59, 59), 200_000.0, 0.0, 2)
FUTURE = _alloc('FUT01', datetime(2026, 11, 1), datetime(2027, 10, 31), 300_000.0, 0.0, 3)
OUTSIDE = _alloc('OUT01', datetime(2024, 1, 1), datetime(2025, 1, 1), 50_000.0, 10.0, 4)
#: ENDED01 burned 50k a month, 600k over its year.
BURNS = {1: {y * 100 + m: 50_000.0 for y, m in [(2025, 10), (2025, 11), (2025, 12)]
             + [(2026, m) for m in range(1, 10)]}}


def _bands(allocs, burns=BURNS):
    window = timedelta(days=PACE_WINDOW_DAYS)
    days, bands = pace_bands(pace_segments(allocs, burns, NOW, NOW), NOW, NOW - window, NOW + window)
    return days, {b[0]: b for b in bands}


class TestPaceBands:

    def test_ended_is_past_only_future_is_future_only(self):
        days, bands = _bands([ENDED, CURRENT, FUTURE, OUTSIDE])
        today = days.index(NOW)
        ended_rates = bands['ENDED01'][2]
        assert ended_rates[today - 1] > 0 and not ended_rates[today:].any()
        assert not bands['ENDED01'][4].any()            # nothing left to commit
        fut_rates = bands['FUT01'][2]
        assert not fut_rates[:today + 31].any() and fut_rates[-1] > 0
        assert 'OUT01' not in bands

    def test_covers_flags_the_allocation_spanning_active_at(self):
        _, bands = _bands([ENDED, CURRENT, FUTURE])
        assert [bands[pc][3] for pc in ('ENDED01', 'CURR01', 'FUT01')] == [False, True, False]

    def test_past_is_each_months_charge_rate(self):
        days, bands = _bands([ENDED], {1: {202608: 31_000.0, 202609: 60_000.0}})
        rates = bands['ENDED01'][2]
        assert rates[days.index(datetime(2026, 8, 15))] == pytest.approx(1_000.0)
        assert rates[days.index(datetime(2026, 9, 15))] == pytest.approx(2_000.0)
        assert rates[days.index(datetime(2026, 9, 30))] == pytest.approx(2_000.0)   # 23:59:59 closes the day

    def test_disk_rows_keep_the_lifetime_average(self):
        days, bands = _bands([ENDED], burns=None)
        elapsed = (ENDED['end_date'] - ENDED['start_date']).total_seconds() / 86400
        assert bands['ENDED01'][2][days.index(NOW) - 1] == pytest.approx(600_000.0 / elapsed)


class TestChart:

    @pytest.fixture(autouse=True)
    def _request(self, app):
        with app.test_request_context('/'):
            yield

    def _chart(self, allocs, sort_by='size', burns=BURNS):
        chart = PaceChart(pace_segments(allocs, burns, NOW, NOW), NOW, sort_by=sort_by)
        chart.render()
        return chart

    def test_size_uses_the_covering_allocation_only(self):
        """A big FY26 allocation that just ended does not inflate the FY27 size."""
        renewed = _alloc('ENDED01', NOW, datetime(2027, 9, 30, 23, 59, 59), 10_000.0, 0.0, 5)
        chart = self._chart([ENDED, renewed, CURRENT])
        assert chart.rank_metric['ENDED01'] == 10_000.0
        assert chart.top_projs[0] == 'CURR01'
        assert chart.group_sort_totals['ENDED01'] == 10_000.0

    def test_project_with_no_covering_allocation_falls_back_to_its_bands(self):
        chart = self._chart([ENDED, FUTURE, CURRENT])
        assert chart.rank_metric['ENDED01'] == 1_000_000.0
        assert chart.rank_metric['FUT01'] == 300_000.0
        assert chart.rank_metric['CURR01'] == 200_000.0

    def test_past_ranks_by_the_recent_actual_rate(self):
        chart = self._chart([ENDED, CURRENT], sort_by='past')
        # Look-back Jul 2 23:59:59 to Sep 30 23:59:59: July counts 29 of its 31 days.
        assert chart.rank_metric['ENDED01'] == pytest.approx((50_000.0 * 29 / 31 + 100_000.0) / 90, rel=1e-4)
        assert chart.rank_metric['CURR01'] == 0.0

    def test_future_ranks_by_the_projected_rate_at_today(self):
        # The renewal inherits ENDED01's pace: 600k a year against 1M even, so 0.6x its own.
        renewed = _alloc('ENDED01', NOW, datetime(2027, 9, 30, 23, 59, 59), 365_000.0, 0.0, 5)
        chart = self._chart([ENDED, renewed, CURRENT], sort_by='future')
        assert chart.rank_metric['ENDED01'] == pytest.approx(0.6 * 1_000, rel=0.03)
        assert chart.rank_metric['CURR01'] == pytest.approx(200_000.0 / 365, rel=0.01)

    def test_committed_line_is_every_balance_over_its_days_left(self):
        chart = self._chart([ENDED, CURRENT, FUTURE])
        today = chart.days.index(NOW)
        assert chart.committed[today] == pytest.approx(200_000.0 / 365 * 365, rel=0.01)
        assert not chart.committed[:today].any()
        assert '/yr' in chart.render()
