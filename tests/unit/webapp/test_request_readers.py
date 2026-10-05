"""Direct tests of the query-string readers every filtered list goes through."""

from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from webapp.utils.faceted_log import build_facet_strip, parse_window
from webapp.utils.htmx import read_flag, read_page, read_sort


class TestReadFlag:

    @pytest.mark.parametrize('raw', ['1', 'true', 'TRUE', ' on ', 'yes'])
    def test_affirmative_spellings(self, raw):
        assert read_flag(MultiDict([('x', raw)]), 'x') is True

    @pytest.mark.parametrize('raw', ['0', 'false', '', 'off', 'nope'])
    def test_anything_else_present_is_off(self, raw):
        assert read_flag(MultiDict([('x', raw)]), 'x', default=True) is False

    def test_absent_takes_the_default(self):
        assert read_flag(MultiDict(), 'x') is False
        assert read_flag(MultiDict(), 'x', default=True) is True


class TestReadPage:

    def test_defaults(self):
        assert read_page(MultiDict()) == {'n': 1, 'per_page': 50}

    @pytest.mark.parametrize('page, per_page, expected', [
        ('3', '25', {'n': 3, 'per_page': 25}),
        ('0', '5', {'n': 1, 'per_page': 10}),
        ('-2', '99999', {'n': 1, 'per_page': 200}),
        ('junk', 'junk', {'n': 1, 'per_page': 50}),
    ])
    def test_bad_values_degrade_and_per_page_is_clamped(self, page, per_page, expected):
        args = MultiDict([('page', page), ('per_page', per_page)])
        assert read_page(args) == expected


class TestReadSort:

    WHITELIST = {'name', 'when'}

    def test_a_whitelisted_column_passes(self):
        args = MultiDict([('sort_by', 'name'), ('sort_dir', 'asc')])
        assert read_sort(args, self.WHITELIST) == {'sort_by': 'name', 'sort_dir': 'asc'}

    def test_an_unknown_column_is_dropped_not_passed_on(self):
        args = MultiDict([('sort_by', 'password; --'), ('sort_dir', 'asc')])
        assert read_sort(args, self.WHITELIST)['sort_by'] is None

    def test_a_bad_direction_takes_the_default(self):
        args = MultiDict([('sort_by', 'when'), ('sort_dir', 'sideways')])
        assert read_sort(args, self.WHITELIST)['sort_dir'] == 'desc'
        assert read_sort(MultiDict(), self.WHITELIST, default_dir='asc') == {
            'sort_by': None, 'sort_dir': 'asc'}


class TestBuildFacetStrip:

    def test_declared_values_zero_fill_in_order_and_strays_append(self):
        strip = build_facet_strip({'sent': 3, 'odd': 1}, ('queued', 'sent'))
        assert strip == [{'value': 'queued', 'count': 0},
                         {'value': 'sent', 'count': 3},
                         {'value': 'odd', 'count': 1}]

    def test_observed_only_sorts_by_count_and_drops_falsy(self):
        strip = build_facet_strip({'b': 2, 'a': 2, 'c': 5, None: 9, '': 1})
        assert [row['value'] for row in strip] == ['c', 'a', 'b']


class TestParseWindow:

    NOW = datetime(2026, 6, 1, 12, 0)

    def test_default_lookback_and_first_page(self):
        since, page = parse_window(MultiDict(), default_days=30, per_page=50, now=self.NOW)
        assert since == self.NOW - timedelta(days=30)
        assert page == {'n': 1, 'per_page': 50, 'days': 30}

    def test_days_are_clamped_and_page_floors_at_one(self):
        args = MultiDict([('days', '99999'), ('page', '-3')])
        since, page = parse_window(args, default_days=30, per_page=50, now=self.NOW)
        assert page['days'] == 365 and page['n'] == 1
        assert since == self.NOW - timedelta(days=365)
