"""``sam.dates``: the month arithmetic the request forms and the Renew defaults share."""

from datetime import date, datetime

import pytest

from sam.dates import add_months, end_of_month, first_of_next_month, format_ymd, parse_wire_date


class TestMonthArithmetic:

    @pytest.mark.parametrize('day, months, expected', [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),      # clamped to the shorter month
        (date(2028, 1, 31), 1, date(2028, 2, 29)),      # leap year
        (date(2026, 10, 1), 12, date(2027, 10, 1)),
        (date(2026, 11, 15), 2, date(2027, 1, 15)),     # across the year end
        (date(2026, 3, 31), -1, date(2026, 2, 28)),     # back, still clamped
        (datetime(2026, 4, 13, 9, 30), 12, datetime(2027, 4, 13, 9, 30)),
    ])
    def test_add_months_clamps_to_the_target_months_end(self, day, months, expected):
        assert add_months(day, months) == expected

    @pytest.mark.parametrize('day, expected', [
        (date(2026, 2, 1), date(2026, 2, 28)), (date(2028, 2, 10), date(2028, 2, 29)),
        (date(2026, 12, 31), date(2026, 12, 31))])
    def test_end_of_month(self, day, expected):
        assert end_of_month(day) == expected

    @pytest.mark.parametrize('day, expected', [
        (date(2026, 1, 31), date(2026, 2, 1)), (date(2026, 12, 5), date(2027, 1, 1)),
        (date(2026, 2, 1), date(2026, 3, 1))])
    def test_first_of_next_month(self, day, expected):
        assert first_of_next_month(day) == expected


class TestWireDates:

    def test_a_payload_date_round_trips(self):
        """The submission payload stores ``yyyy-MM-dd`` and reads it back as a ``date``."""
        assert parse_wire_date(format_ymd(date(2027, 9, 30))) == date(2027, 9, 30)
        assert format_ymd(None) is None and parse_wire_date(None) is None
        assert parse_wire_date('2027-09-30T00:00:00') == date(2027, 9, 30)
