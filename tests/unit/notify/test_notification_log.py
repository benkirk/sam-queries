"""The ``notification_log`` model — vocabulary, truncation, and the one
permitted transition.

These run against the raw test session, so rows are built directly and rolled
back by the per-test SAVEPOINT.

The behavior under test is mostly *refusal*: the columns are bare VARCHARs by
design (an ENUM change is a DBA ticket where a string is not), so
``create()``/``resolve()` are the only thing between a typo and a row that no
facet chip will ever match.
"""

from datetime import datetime, timedelta

import pytest
from factories.notify import make_notification_log

from sam import NotificationLog
from sam.notify import NOTIFICATION_STATUSES


class TestStatusVocabulary:

    @pytest.mark.parametrize('status', NOTIFICATION_STATUSES)
    def test_every_declared_status_is_accepted(self, session, status):
        row = make_notification_log(session, status=status)
        assert row.status == status

    def test_an_unknown_status_is_refused(self, session):
        with pytest.raises(ValueError, match='unknown notification_log.status'):
            NotificationLog.create(
                session, kind='expiration', channel='email', transport='null',
                status='delivered', recipient='pi@x.edu', requested_by='benkirk')

    def test_the_error_names_the_whole_vocabulary(self, session):
        """So the fix is visible without opening the model."""
        with pytest.raises(ValueError) as exc:
            NotificationLog.create(
                session, kind='expiration', channel='email', transport='null',
                status='nope', recipient='pi@x.edu', requested_by='benkirk')
        for status in NOTIFICATION_STATUSES:
            assert status in str(exc.value)


class TestTimestamps:

    def test_creation_time_comes_from_the_app_clock(self, session):
        """Never a DB default: the server resolves UTC while SAM is
        naive-Mountain, and MySQL rounds fractional seconds."""
        before = datetime.now().replace(microsecond=0)
        row = make_notification_log(session)
        assert row.creation_time >= before

    def test_a_queued_row_has_no_sent_time(self, session):
        assert make_notification_log(session, status='queued').sent_time is None

    @pytest.mark.parametrize('status', ['sent', 'failed', 'suppressed',
                                        'redirected'])
    def test_a_terminal_status_is_stamped_immediately(self, session, status):
        """It was learned at the moment it was written; only `queued` is
        genuinely pending."""
        assert make_notification_log(session, status=status).sent_time is not None

    def test_a_row_can_be_back_dated(self, session):
        """The staleness horizon is untestable without this."""
        row = make_notification_log(session, age=timedelta(hours=1))
        assert row.creation_time < datetime.now() - timedelta(minutes=59)


class TestTheOnePermittedTransition:

    def test_queued_resolves_to_sent(self, session):
        row = make_notification_log(session, status='queued')
        row.resolve(status='sent')
        assert row.status == 'sent'
        assert row.sent_time is not None

    def test_queued_resolves_to_failed_and_keeps_the_error(self, session):
        row = make_notification_log(session, status='queued')
        row.resolve(status='failed', error='550 mailbox unavailable')
        assert row.status == 'failed'
        assert '550' in row.error

    def test_resolving_a_settled_row_is_refused(self, session):
        """That would be a state overwrite — the one thing this table's
        append-only design rules out. A retry is a new row sharing the
        dedup_key."""
        row = make_notification_log(session, status='sent')
        with pytest.raises(ValueError, match='append-only'):
            row.resolve(status='failed')

    def test_resolving_to_an_unknown_status_is_refused(self, session):
        row = make_notification_log(session, status='queued')
        with pytest.raises(ValueError, match='unknown notification_log.status'):
            row.resolve(status='bounced')

    def test_there_is_no_general_update_method(self):
        """`resolve` is named for what it does rather than left as a
        general-purpose setter, so the append-only rule is not one careless
        call away."""
        assert not hasattr(NotificationLog, 'update')


class TestTruncation:
    """Losing the tail of a relay's rejection is strictly better than losing
    the record that we tried to mail somebody (MySQL 1406)."""

    def test_a_long_error_is_clipped_rather_than_raising(self, session):
        row = make_notification_log(session, status='queued')
        row.resolve(status='failed', error='x' * 9000)
        assert len(row.error) == 4000

    def test_a_long_subject_is_clipped(self, session):
        row = make_notification_log(session, subject='s' * 500)
        assert len(row.subject) == 255

    def test_a_long_recipient_name_is_clipped(self, session):
        row = make_notification_log(session, recipient_name='n' * 500)
        assert len(row.recipient_name) == 255

    def test_requested_by_is_clipped_to_the_username_width(self, session):
        row = make_notification_log(session, requested_by='u' * 80)
        assert len(row.requested_by) == 35

    def test_requested_by_defaults_rather_than_going_null(self, session):
        """The column is NOT NULL, and an unattended caller has no username."""
        row = NotificationLog.create(
            session, kind='expiration', channel='email', transport='null',
            status='sent', recipient='pi@x.edu', requested_by=None)
        assert row.requested_by == 'system'


class TestRedirectColumns:

    def test_intended_recipient_is_null_in_the_normal_case(self, session):
        assert make_notification_log(session).intended_recipient is None

    def test_intended_recipient_records_who_it_was_really_for(self, session):
        row = make_notification_log(session, status='redirected',
                                    recipient='me@x.edu',
                                    intended_recipient='pi@x.edu')
        assert row.intended_recipient == 'pi@x.edu'


class TestNoForeignKeys:

    def test_an_entity_id_pointing_nowhere_is_accepted(self, session):
        """Deliberate. A deleted parent must not cascade the evidence away,
        and a column per entity type would be a DBA ticket per kind."""
        row = make_notification_log(session, entity_type='project',
                                    entity_id=999_999_999)
        assert row.entity_id == 999_999_999

    def test_the_table_declares_no_foreign_keys(self):
        assert list(NotificationLog.__table__.foreign_keys) == []

    def test_a_projcode_that_matches_no_project_is_accepted(self, session):
        row = make_notification_log(session, projcode='GONE0001')
        assert row.projcode == 'GONE0001'


class TestFactoryIsXdistSafe:

    def test_two_rows_get_distinct_keys_without_a_module_counter(self, session):
        """Workers share one database; a process-local counter would give
        gw0 and gw1 the same dedup_key."""
        a = make_notification_log(session)
        b = make_notification_log(session)
        assert a.dedup_key != b.dedup_key
        assert a.recipient != b.recipient
