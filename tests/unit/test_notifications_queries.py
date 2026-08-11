"""`sam.queries.notifications` — the counts and the facet rollups.

The self-exclusion property in `facet_notifications` is the one worth a test:
the failure it prevents is a chip strip that reads all-zeros the moment one
filter is picked, which turns switchers into dead ends.
"""

from datetime import datetime, timedelta

import pytest
from factories.notify import make_notification_log

from sam.queries.notifications import (
    CARD_STATUSES,
    count_recent_notifications,
    count_stuck_queued,
    facet_notifications,
    get_recent_notifications,
    summarize_notifications,
)


@pytest.fixture
def rows(session):
    """A small spread across status, kind and channel."""
    made = [
        make_notification_log(session, status='sent', kind='expiration',
                              projcode='AAAA0001', recipient='a@x.edu'),
        make_notification_log(session, status='sent', kind='expiration',
                              projcode='AAAA0001', recipient='b@x.edu'),
        make_notification_log(session, status='failed', kind='expiration',
                              projcode='BBBB0001', recipient='c@x.edu'),
        make_notification_log(session, status='suppressed',
                              kind='xras_activation',
                              projcode='CCCC0001', recipient='d@x.edu'),
        make_notification_log(session, status='redirected',
                              kind='xras_activation',
                              projcode='CCCC0001', recipient='e@x.edu'),
    ]
    return made


def _since():
    """A window tight enough to exclude anything the snapshot might carry."""
    return datetime.now() - timedelta(minutes=5)


class TestSummarize:

    def test_it_counts_by_status(self, session, rows):
        summary = summarize_notifications(session, since=_since())
        assert summary['sent'] == 2
        assert summary['failed'] == 1
        assert summary['suppressed'] == 1
        assert summary['redirected'] == 1

    def test_every_card_status_is_present_even_at_zero(self, session):
        """So the template needs no `default(0)` on any of them."""
        summary = summarize_notifications(session, since=_since())
        for status in CARD_STATUSES:
            assert summary[status] == 0

    def test_the_window_excludes_older_rows(self, session):
        make_notification_log(session, status='sent', age=timedelta(days=3))
        summary = summarize_notifications(session, window_hours=24)
        assert summary['sent'] == 0

    def test_window_hours_is_reported_back_for_the_card_heading(self, session):
        assert summarize_notifications(
            session, window_hours=48)['window_hours'] == 48

    def test_total_is_the_sum_across_statuses(self, session, rows):
        summary = summarize_notifications(session, since=_since())
        assert summary['total'] == 5


class TestStuckQueued:

    def test_a_fresh_queued_row_is_not_stuck(self, session):
        make_notification_log(session, status='queued')
        assert count_stuck_queued(session, queued_stale_seconds=300) == 0

    def test_a_row_past_the_horizon_is_stuck(self, session):
        make_notification_log(session, status='queued', age=timedelta(hours=1))
        assert count_stuck_queued(session, queued_stale_seconds=300) >= 1

    def test_the_horizon_matches_the_mailers(self, session):
        """The counter an operator reads and the rule that lets a retry
        through are one mechanism; a different horizon here would make the
        card disagree with the mailer about what 'stuck' means."""
        make_notification_log(session, status='queued',
                              age=timedelta(seconds=90))
        assert count_stuck_queued(session, queued_stale_seconds=3600) == 0
        assert count_stuck_queued(session, queued_stale_seconds=30) >= 1

    def test_the_stuck_count_is_not_windowed(self, session):
        """A row stuck three days ago is more interesting than one stuck an
        hour ago; windowing would let the oldest breakage age off the card."""
        make_notification_log(session, status='queued', age=timedelta(days=5))
        summary = summarize_notifications(session, window_hours=24)
        assert summary['total'] == 0          # outside the window
        assert summary['queued_stuck'] >= 1   # still surfaced


class TestFacetsExcludeTheirOwnDimension:

    def test_the_status_facet_ignores_the_status_filter(self, session, rows):
        """Scope a dimension by itself and every unselected value drops to
        zero the moment one is picked — the chips become dead ends."""
        facet = facet_notifications(session, 'status', since=_since(),
                                    statuses=['sent'])
        assert facet['sent'] == 2
        assert facet['failed'] == 1, \
            'picking "sent" must not zero the other status chips'

    def test_the_status_facet_still_honours_other_filters(self, session, rows):
        facet = facet_notifications(session, 'status', since=_since(),
                                    statuses=['sent'], kinds=['xras_activation'])
        assert facet.get('sent') is None
        assert facet['suppressed'] == 1
        assert facet['redirected'] == 1

    def test_the_kind_facet_ignores_the_kind_filter(self, session, rows):
        facet = facet_notifications(session, 'kind', since=_since(),
                                    kinds=['expiration'])
        assert facet['expiration'] == 3
        assert facet['xras_activation'] == 2

    def test_the_kind_facet_honours_the_status_filter(self, session, rows):
        facet = facet_notifications(session, 'kind', since=_since(),
                                    statuses=['sent'])
        assert facet == {'expiration': 2}

    def test_the_channel_facet_ignores_the_channel_filter(self, session, rows):
        facet = facet_notifications(session, 'channel', since=_since(),
                                    channels=['email'])
        assert facet['email'] == 5

    def test_an_unknown_dimension_raises_with_the_vocabulary(self, session):
        with pytest.raises(ValueError, match='status, kind, channel'):
            facet_notifications(session, 'recipient')


class TestListing:

    def test_rows_come_back_newest_first(self, session):
        old = make_notification_log(session, age=timedelta(hours=2))
        new = make_notification_log(session)
        rows = get_recent_notifications(session, since=_since() - timedelta(hours=3))
        ids = [r.notification_log_id for r in rows]
        assert ids.index(new.notification_log_id) < ids.index(old.notification_log_id)

    def test_status_filter(self, session, rows):
        found = get_recent_notifications(session, since=_since(),
                                         statuses=['failed'])
        assert [r.status for r in found] == ['failed']

    def test_search_matches_the_recipient(self, session, rows):
        found = get_recent_notifications(session, since=_since(), search='c@x')
        assert [r.recipient for r in found] == ['c@x.edu']

    def test_search_also_matches_the_projcode(self, session, rows):
        """The two things an operator arrives knowing."""
        found = get_recent_notifications(session, since=_since(),
                                         search='CCCC0001')
        assert len(found) == 2

    def test_search_is_case_insensitive(self, session, rows):
        assert len(get_recent_notifications(session, since=_since(),
                                            search='cccc0001')) == 2

    def test_count_matches_the_listing(self, session, rows):
        """One filter builder, so a filter added to the table cannot be
        forgotten in the count or the facets."""
        filters = dict(since=_since(), kinds=['expiration'])
        assert count_recent_notifications(session, **filters) == \
            len(get_recent_notifications(session, **filters))

    def test_limit_and_offset_paginate(self, session, rows):
        first = get_recent_notifications(session, since=_since(), limit=2)
        second = get_recent_notifications(session, since=_since(), limit=2,
                                          offset=2)
        assert len(first) == len(second) == 2
        assert not ({r.notification_log_id for r in first}
                    & {r.notification_log_id for r in second})
