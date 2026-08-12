"""The window half of the audit-style filter panels.

Two things live here that nothing covered before:

1. ``_parse_audit_filters`` — the Transactions / Adjustments parser. It had
   **zero** tests, while its XRAS sibling has three
   (``test_xras_dashboard.py`` :: ``TestDefaultWindowUpperBound``). Its
   absent-vs-empty rule is what the window control has to submit *around*, so
   it is pinned here first: these tests are the proof that swapping the bare
   date pair for the band control changed no behaviour.

2. ``AUDIT_AGE_BANDS`` and the span arithmetic behind that control — in
   particular the one coupling that would silently degrade the whole thing to
   a permanent "Custom range" if either side moved.
"""

from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from webapp.dashboards.allocations.blueprint import (
    AUDIT_AGE_BANDS, _parse_audit_filters,
)
from webapp.utils import age_bands

#: A fixed anchor, so the band arithmetic is checkable by hand and cannot
#: depend on the day the suite happens to run.
ANCHOR = datetime(2026, 8, 12)

#: The audit parser takes a sort whitelist it only forwards to read_sort; the
#: window tests below do not care what is in it.
SORTS = frozenset({'transaction_date'})


class TestAuditParserDefaultWindow:
    """Absent, empty and explicit bounds are three different intents.

    The distinction is load-bearing and easy to lose: *absent* means "I have
    not chosen" and gets the 30-day default, while *present-and-empty* means
    "all time". Collapsing them would either delete the all-time affordance or
    make every unfiltered request scan the whole table.
    """

    def test_no_dates_at_all_gets_the_thirty_day_default(self):
        filters, _, _ = _parse_audit_filters(MultiDict(), SORTS)
        assert filters['start_date'] is not None
        assert filters['end_date'] is not None
        span = filters['end_date'] - filters['start_date']
        # 30 days plus however far into today the clock has run.
        assert timedelta(days=30) <= span < timedelta(days=31)

    def test_present_but_empty_means_all_time(self):
        """The undocumented escape hatch, now documented by a test. This is the
        rule a `days`-style pill could not have submitted around, and the reason
        the band control writes the dates directly instead."""
        filters, _, _ = _parse_audit_filters(
            MultiDict([('start_date', ''), ('end_date', '')]), SORTS)
        assert filters['start_date'] is None
        assert filters['end_date'] is None

    def test_an_empty_start_with_a_real_end_is_unbounded_below(self):
        """Exactly the pair the ladder's open-ended top band submits: the older
        edge clears, the newer edge stays at the anchor. Pinned because it is
        the one window position whose meaning is not obvious from the control.
        """
        filters, _, _ = _parse_audit_filters(
            MultiDict([('start_date', ''), ('end_date', '2026-08-12')]), SORTS)
        assert filters['start_date'] is None
        assert filters['end_date'].strftime('%Y-%m-%d %H:%M:%S') \
            == '2026-08-12 23:59:59'

    def test_end_date_normalises_to_the_end_of_that_day(self):
        filters, _, _ = _parse_audit_filters(
            MultiDict([('end_date', '2026-01-15')]), SORTS)
        assert filters['end_date'].strftime('%Y-%m-%d %H:%M:%S') \
            == '2026-01-15 23:59:59'

    def test_a_malformed_date_degrades_to_unbounded(self):
        """A filter panel must never be able to 400 the fragment behind it."""
        filters, _, _ = _parse_audit_filters(
            MultiDict([('start_date', 'not-a-date'),
                       ('end_date', '2026-13-45')]), SORTS)
        assert filters['start_date'] is None
        assert filters['end_date'] is None


class TestAuditAgeBands:

    def test_the_ladder_is_open_ended(self):
        """The last band's `None` upper bound is what makes an "all time"
        position reachable at all — band_bounds returns `after=None` there,
        which the browser writes as an empty start_date."""
        assert AUDIT_AGE_BANDS[-1][1] is None
        assert all(upper is not None for _label, upper in AUDIT_AGE_BANDS[:-1])

    @pytest.mark.parametrize('hi', range(len(AUDIT_AGE_BANDS)))
    def test_every_lookback_span_round_trips(self, hi):
        """band_bounds -> bands_for recovers (0, hi) for every band, including
        the open-ended one. This is the property the whole control rests on: if
        a span does not round-trip, the server renders "Custom range" for a
        window the user picked off the ladder."""
        after, before = age_bands.band_bounds(AUDIT_AGE_BANDS, ANCHOR, 0, hi)
        assert age_bands.bands_for(AUDIT_AGE_BANDS, ANCHOR, after, before) \
            == (0, hi)

    def test_the_default_window_is_a_whole_span(self):
        """⚠️ The tripwire for a four-way coupling. Band 1's upper bound (30) is
        the same 30 as `timedelta(days=30)` in `_parse_audit_filters`,
        `_parse_xras_filters`, `_audit_page_context` and `xras()`. Move any one
        of them and every first load renders the custom state instead of a
        named window — which still filters correctly, so nothing else fails."""
        start = (ANCHOR - timedelta(days=30)).strftime('%Y-%m-%d')
        end = ANCHOR.strftime('%Y-%m-%d')
        assert age_bands.bands_for(AUDIT_AGE_BANDS, ANCHOR, start, end) == (0, 1)

    def test_the_open_ended_band_clears_only_the_older_bound(self):
        """The two thumbs are CROSSED: start_date reads the high thumb and
        end_date the low one. So the all-time position clears start_date and
        leaves end_date at the anchor — it is not "both bounds empty", and the
        difference is a real one on the XRAS log, whose *absent*-branch default
        is deliberately unbounded above."""
        rows = age_bands.band_map(AUDIT_AGE_BANDS, ANCHOR,
                                  'start_date', 'end_date')
        assert rows[-1]['start_date'] is None
        assert rows[0]['end_date'] == ANCHOR.strftime('%Y-%m-%d')

    def test_an_empty_string_bound_is_not_a_missing_one(self):
        """`bands_for` tests `after is None`, not falsiness. Handing it a raw
        '' off the query string renders the custom state for what is actually
        the open-ended band — which is why _window_control_context normalises
        with `or None`."""
        end = ANCHOR.strftime('%Y-%m-%d')
        assert age_bands.bands_for(AUDIT_AGE_BANDS, ANCHOR, None, end) \
            == (0, len(AUDIT_AGE_BANDS) - 1)
        assert age_bands.bands_for(AUDIT_AGE_BANDS, ANCHOR, '', end) is None
