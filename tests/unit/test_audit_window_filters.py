"""The window half of the audit-style filter panels.

``_parse_audit_filters`` — the Transactions / Adjustments parser — had **zero**
tests, while its XRAS sibling has three (``test_xras_dashboard.py`` ::
``TestDefaultWindowUpperBound``). Its absent-vs-empty rule is subtle, load-bearing
and entirely undocumented outside its own docstring, so it is pinned here.
"""

from datetime import timedelta

from werkzeug.datastructures import MultiDict

from webapp.dashboards.allocations.blueprint import _parse_audit_filters

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
        """The undocumented escape hatch, now documented by a test — reachable
        today only by emptying both boxes by hand."""
        filters, _, _ = _parse_audit_filters(
            MultiDict([('start_date', ''), ('end_date', '')]), SORTS)
        assert filters['start_date'] is None
        assert filters['end_date'] is None

    def test_an_empty_start_with_a_real_end_is_unbounded_below(self):
        """A half-open pair: the older edge clears, the newer edge stays.
        Pinned because it is the shape a lookback control would have to submit
        to mean "everything up to now"."""
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
