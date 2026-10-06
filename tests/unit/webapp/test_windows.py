"""webapp.utils.windows: the three date-window reads."""

from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from webapp.utils.windows import read_chart_window, read_days, read_log_window


class TestReadDays:

    @pytest.mark.parametrize('raw, expected', [
        (None, 30), ('', 30), ('junk', 30), ('7', 7), ('0', 30), ('-4', 1),
        ('9999', 365),
    ])
    def test_clamped_with_a_default(self, raw, expected):
        args = MultiDict() if raw is None else MultiDict([('days', raw)])
        assert read_days(args, default=30, maximum=365) == expected


class TestReadLogWindow:

    def test_first_load_looks_back_from_midnight_and_is_open_above(self):
        start, end = read_log_window(MultiDict(), 30)
        assert end is None
        assert start.hour == start.minute == 0
        assert timedelta(days=30) <= datetime.now() - start < timedelta(days=31)

    def test_a_blank_param_means_all_time_not_the_default(self):
        assert read_log_window(MultiDict([('start_date', '')]), 30) == (None, None)

    def test_typed_dates_parse_and_the_end_is_end_of_day(self):
        start, end = read_log_window(
            MultiDict([('start_date', '2026-01-02'), ('end_date', '2026-01-03')]), 30)
        assert start == datetime(2026, 1, 2)
        assert end == datetime(2026, 1, 3, 23, 59, 59)

    def test_a_malformed_date_reads_as_unbounded(self):
        start, end = read_log_window(
            MultiDict([('start_date', 'nope'), ('end_date', '2026-01-03')]), 30)
        assert start is None and end is not None


class TestReadChartWindow:

    def test_both_bounds_default(self):
        start, end = read_chart_window(MultiDict(), 90)
        assert timedelta(days=89) < end - start <= timedelta(days=90)

    def test_one_typed_bound_keeps_the_other_default(self):
        start, end = read_chart_window(MultiDict([('start_date', '2026-01-02')]), 90)
        assert start == datetime(2026, 1, 2)
        assert datetime.now() - end < timedelta(seconds=5)

    def test_a_malformed_date_raises(self):
        with pytest.raises(ValueError):
            read_chart_window(MultiDict([('end_date', '01/02/2026')]), 90)
