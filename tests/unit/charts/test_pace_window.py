"""Pace chart across a fiscal-year boundary: ended and later-starting allocations."""

from datetime import datetime, timedelta

import pytest

from webapp.dashboards.charts.pace import PACE_WINDOW_DAYS, PaceChart, pace_bands

NOW = datetime(2026, 10, 1)


def _alloc(projcode, start, end, amount, used):
    return {'projcode': projcode, 'start_date': start, 'end_date': end,
            'total_amount': amount, 'total_used': used}


ENDED = _alloc('ENDED01', datetime(2025, 10, 1), datetime(2026, 9, 30, 23, 59, 59),
               1_000_000.0, 600_000.0)
CURRENT = _alloc('CURR01', NOW, datetime(2027, 9, 30, 23, 59, 59), 200_000.0, 0.0)
FUTURE = _alloc('FUT01', datetime(2026, 11, 1), datetime(2027, 10, 31), 300_000.0, 0.0)
OUTSIDE = _alloc('OUT01', datetime(2024, 1, 1), datetime(2025, 1, 1), 50_000.0, 10.0)


def _bands(allocs):
    window = timedelta(days=PACE_WINDOW_DAYS)
    days, bands = pace_bands(allocs, NOW, NOW - window, NOW + window)
    return days, {b[0]: b for b in bands}


class TestPaceBands:

    def test_ended_is_past_only_future_is_future_only(self):
        days, bands = _bands([ENDED, CURRENT, FUTURE, OUTSIDE])
        today = days.index(NOW)
        _, _, ended_rates, _ = bands['ENDED01']
        assert ended_rates[today - 1] > 0 and not ended_rates[today:].any()
        _, _, fut_rates, _ = bands['FUT01']
        assert not fut_rates[:today + 1].any() and fut_rates[-1] > 0
        assert 'OUT01' not in bands

    def test_covers_flags_the_allocation_spanning_active_at(self):
        _, bands = _bands([ENDED, CURRENT, FUTURE])
        assert [bands[pc][3] for pc in ('ENDED01', 'CURR01', 'FUT01')] == [False, True, False]


class TestSizeRank:

    @pytest.fixture(autouse=True)
    def _request(self, app):
        with app.test_request_context('/'):
            yield

    def _chart(self, allocs):
        chart = PaceChart(allocs, NOW, sort_by='size')
        chart.render()
        return chart

    def test_size_uses_the_covering_allocation_only(self):
        """A big FY26 allocation that just ended does not inflate the FY27 size."""
        renewed = _alloc('ENDED01', NOW, datetime(2027, 9, 30, 23, 59, 59), 10_000.0, 0.0)
        chart = self._chart([ENDED, renewed, CURRENT])
        assert chart.rank_metric['ENDED01'] == 10_000.0
        assert chart.top_projs[0] == 'CURR01'
        assert chart.group_sort_totals['ENDED01'] == 10_000.0

    def test_project_with_no_covering_allocation_falls_back_to_its_bands(self):
        chart = self._chart([ENDED, FUTURE, CURRENT])
        assert chart.rank_metric['ENDED01'] == 1_000_000.0
        assert chart.rank_metric['FUT01'] == 300_000.0
        assert chart.rank_metric['CURR01'] == 200_000.0
