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
    get_expiration_notice_status,
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


class TestExpirationNoticeStatus:
    """The last-notified rollup behind the admin Expirations badge.

    Reuses `get_recent_notifications(projcodes=..., kinds=...)` and buckets in
    Python — the `get_xras_activity` shape — rather than a bespoke GROUP BY.
    `limit=None` is safe there *because* the projcode IN list bounds it, and
    `notification_log_projcode` is (projcode, creation_time) so the equality
    form can use the index.
    """

    def test_an_empty_request_does_no_query(self, session):
        assert get_expiration_notice_status(session, []) == {}

    def test_every_requested_projcode_gets_an_entry(self, session, rows):
        """Including ones with no notices at all. The consumer is a template
        macro shared with the user dashboard and must tell "notified", "not
        notified" and "nobody asked" apart — so absence means only the last."""
        status = get_expiration_notice_status(
            session, ['AAAA0001', 'BBBB0001', 'ZZZZ9999'])
        assert set(status) == {'AAAA0001', 'BBBB0001', 'ZZZZ9999'}

    def test_a_delivered_notice_is_reported(self, session, rows):
        status = get_expiration_notice_status(session, ['AAAA0001'])
        assert status['AAAA0001']['notified'] is True
        assert status['AAAA0001']['delivered_count'] == 2
        assert status['AAAA0001']['failed_count'] == 0

    def test_a_project_with_only_failures_is_not_notified(self, session, rows):
        """A red badge, not a green one: nothing reached anybody."""
        status = get_expiration_notice_status(session, ['BBBB0001'])
        assert status['BBBB0001']['notified'] is False
        assert status['BBBB0001']['failed_count'] == 1
        assert status['BBBB0001']['notified_age'] is None

    def test_a_project_with_no_rows_is_not_notified(self, session):
        status = get_expiration_notice_status(session, ['ZZZZ9999'])
        assert status['ZZZZ9999'] == {
            'notified': False, 'notified_time': None, 'notified_age': None,
            'delivered_count': 0, 'failed_count': 0,
        }

    def test_redirected_counts_as_delivered(self, session):
        """It reached *a* mailbox, which is what a staging run is for — and
        it must agree with xras_activation's answer, or the same row gets a
        green badge on one card and a grey one on another."""
        make_notification_log(session, status='redirected', kind='expiration',
                              projcode='RRRR0001', recipient='r@x.edu')
        assert get_expiration_notice_status(
            session, ['RRRR0001'])['RRRR0001']['notified'] is True

    def test_other_kinds_are_ignored(self, session, rows):
        """An XRAS activation notice is not an expiration notice, and a badge
        that counted it would say a project had been warned when it had not."""
        status = get_expiration_notice_status(session, ['CCCC0001'])
        assert status['CCCC0001']['notified'] is False
        assert status['CCCC0001']['delivered_count'] == 0

    def test_the_newest_delivery_wins(self, session):
        make_notification_log(session, status='sent', kind='expiration',
                              projcode='NNNN0001', recipient='old@x.edu',
                              age=timedelta(days=200))
        recent = make_notification_log(session, status='sent',
                                       kind='expiration',
                                       projcode='NNNN0001',
                                       recipient='new@x.edu',
                                       age=timedelta(days=3))
        status = get_expiration_notice_status(session, ['NNNN0001'])
        assert status['NNNN0001']['notified_time'] == recent.creation_time
        assert status['NNNN0001']['delivered_count'] == 2

    def test_the_age_is_a_timedelta_for_fmt_ago(self, session):
        """`fmt.ago` takes an elapsed delta, and keeping `datetime.now()` out
        of Jinja is what makes this testable at all."""
        from sam import fmt

        make_notification_log(session, status='sent', kind='expiration',
                              projcode='TTTT0001', recipient='t@x.edu',
                              age=timedelta(days=3))
        age = get_expiration_notice_status(
            session, ['TTTT0001'])['TTTT0001']['notified_age']
        assert isinstance(age, timedelta)
        assert fmt.ago(age) == '3 days'

    def test_one_now_serves_the_whole_page(self, session):
        """Two cards from the same request must not report ages a second
        apart."""
        for code in ('PPPP0001', 'PPPP0002'):
            make_notification_log(session, status='sent', kind='expiration',
                                  projcode=code, recipient=f'{code}@x.edu',
                                  when=datetime.now() - timedelta(days=5))
        status = get_expiration_notice_status(session, ['PPPP0001', 'PPPP0002'])
        a = status['PPPP0001']['notified_age']
        b = status['PPPP0002']['notified_age']
        assert abs((a - b).total_seconds()) < 1
