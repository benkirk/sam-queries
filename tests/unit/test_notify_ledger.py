"""NotificationLedger — recording, and the suppression rule.

These run against the raw test session. The ledger normally opens its own
short-lived session and commits (mail cannot be un-sent by a rollback), but
under xdist a committed row escapes the per-test SAVEPOINT and leaks into a
shared database. So the factory here hands back the *test* session with
`commit` neutered — the ledger's calls become flushes, the SAVEPOINT still
rolls everything back, and the SQL under test is unchanged.

`test_notify_ledger_sessions.py` covers the commit discipline itself.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from factories.notify import make_notification_log

from sam import NotificationLog
from sam.notify import (
    Message, NotifyConfig, Recipient, RenderedMessage, SUPPRESSING_STATUSES,
)
from sam.notify.ledger import LedgerError, NotificationLedger


@pytest.fixture
def ledger(session):
    """A ledger writing through the test session, so rollback still works."""
    @contextmanager
    def factory():
        real_commit = session.commit
        session.commit = session.flush          # keep it inside the SAVEPOINT
        try:
            yield session
        finally:
            session.commit = real_commit

    return NotificationLedger(factory, config=NotifyConfig())


def _message(address='pi@x.edu', **kwargs):
    kwargs.setdefault('kind', 'expiration')
    kwargs.setdefault('subject', 'Expiration Notice')
    kwargs.setdefault('requested_by', 'benkirk')
    return Message(recipient=Recipient(address, name='A PI', role='lead'),
                   **kwargs)


class TestRecord:

    def test_a_row_is_written_and_its_id_returned(self, ledger, session):
        log_id = ledger.record(_message(), status='sent', transport='null')
        row = session.get(NotificationLog, log_id)
        assert row.recipient == 'pi@x.edu'
        assert (row.kind, row.status, row.transport) == \
            ('expiration', 'sent', 'null')

    def test_the_message_fields_land_in_the_right_columns(self, ledger, session):
        message = _message(
            entity=('project', 4711), projcode='SCSG0001',
            dedup_key='expiration:SCSG0001:2026-09-30:pi@x.edu')
        rendered = RenderedMessage(subject='s', text='t',
                                   template_text='expiration-WNA.txt')
        row = session.get(NotificationLog, ledger.record(
            message, status='sent', transport='smtp', rendered=rendered))
        assert (row.entity_type, row.entity_id) == ('project', 4711)
        assert row.projcode == 'SCSG0001'
        assert row.dedup_key.endswith('pi@x.edu')
        assert row.recipient_role == 'lead'
        assert row.requested_by == 'benkirk'

    def test_the_template_actually_chosen_is_recorded(self, ledger, session):
        """Bodies are not stored, so this is the only thing that can answer
        'which letter did this PI actually get' after the fact."""
        rendered = RenderedMessage(subject='s', text='t',
                                   template_text='expiration-WNA.txt')
        row = session.get(NotificationLog, ledger.record(
            _message(), status='sent', transport='smtp', rendered=rendered))
        assert row.template == 'expiration-WNA.txt'

    def test_a_redirect_records_both_addresses(self, ledger, session):
        message = _message('me@x.edu', intended_recipient='pi@x.edu')
        row = session.get(NotificationLog, ledger.record(
            message, status='redirected', transport='null'))
        assert (row.recipient, row.intended_recipient) == \
            ('me@x.edu', 'pi@x.edu')

    def test_detail_lands_in_the_error_column(self, ledger, session):
        row = session.get(NotificationLog, ledger.record(
            _message(), status='failed', transport='smtp',
            detail='550 mailbox unavailable'))
        assert '550' in row.error

    def test_a_database_failure_becomes_a_ledger_error(self, session):
        def broken_factory():
            raise RuntimeError('connection pool exhausted')

        ledger = NotificationLedger(broken_factory, config=NotifyConfig())
        with pytest.raises(LedgerError, match='connection pool exhausted'):
            ledger.record(_message(), status='queued', transport='smtp')


class TestResolve:

    def test_queued_resolves_to_sent(self, ledger, session):
        log_id = ledger.record(_message(), status='queued', transport='smtp')
        ledger.resolve(log_id, status='sent')
        assert session.get(NotificationLog, log_id).status == 'sent'

    def test_queued_resolves_to_failed_with_the_relay_message(self, ledger, session):
        log_id = ledger.record(_message(), status='queued', transport='smtp')
        ledger.resolve(log_id, status='failed', detail='relay refused')
        row = session.get(NotificationLog, log_id)
        assert (row.status, 'relay refused' in row.error) == ('failed', True)

    def test_resolving_a_vanished_row_does_not_raise(self, ledger):
        ledger.resolve(999_999_999, status='sent')

    def test_a_failure_to_resolve_is_swallowed(self, session, caplog):
        """The message is already with the relay by then. Raising would turn
        a bookkeeping problem into a send failure and invite a retry of mail
        that did go out. The row stays `queued` — which is exactly what the
        staleness horizon is for."""
        calls = {'n': 0}

        @contextmanager
        def flaky():
            calls['n'] += 1
            if calls['n'] > 1:
                raise RuntimeError('database went away')
            real_commit = session.commit
            session.commit = session.flush
            try:
                yield session
            finally:
                session.commit = real_commit

        ledger = NotificationLedger(flaky, config=NotifyConfig())
        log_id = ledger.record(_message(), status='queued', transport='smtp')
        ledger.resolve(log_id, status='sent')          # must not raise
        assert session.get(NotificationLog, log_id).status == 'queued'


class TestSuppression:

    def test_a_sent_row_suppresses_its_key(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='K1')
        assert ledger.already_sent('K1') is True

    def test_an_unused_key_does_not_suppress(self, ledger):
        assert ledger.already_sent('never-seen') is False

    def test_an_empty_key_never_suppresses(self, ledger):
        """`dedup_key=None` means 'always send this one'."""
        assert ledger.already_sent(None) is False
        assert ledger.already_sent('') is False

    def test_a_redirected_row_suppresses(self, ledger, session):
        """It really was delivered — just not to its subject. Re-sending
        would mail the staging inbox twice."""
        make_notification_log(session, status='redirected', dedup_key='K2')
        assert ledger.already_sent('K2') is True

    def test_a_failed_row_does_not_suppress(self, ledger, session):
        """A failure is exactly the case a retry is for."""
        make_notification_log(session, status='failed', dedup_key='K3')
        assert ledger.already_sent('K3') is False

    def test_a_suppressed_row_does_not_suppress(self, ledger, session):
        """Load-bearing. If a suppression suppressed, the first skip would
        make every later attempt skip for ever — on a key that had never
        actually been delivered."""
        make_notification_log(session, status='suppressed', dedup_key='K4')
        assert ledger.already_sent('K4') is False

    def test_the_suppressing_set_is_exactly_sent_and_redirected(self):
        assert set(SUPPRESSING_STATUSES) == {'sent', 'redirected'}

    def test_a_different_key_is_unaffected(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='K5')
        assert ledger.already_sent('K5-other') is False


class TestTheStaleQueuedDeadlock:
    """§ 5's ⚠️. Invisible without a test that manipulates the clock."""

    def test_a_fresh_queued_row_suppresses(self, ledger, session):
        """A process that died AFTER handing the message to the relay must
        not re-send."""
        make_notification_log(session, status='queued', dedup_key='Q1')
        assert ledger.already_sent('Q1') is True

    def test_a_stale_queued_row_does_not_suppress(self, ledger, session):
        """...but a process that died BEFORE the relay leaves an
        indistinguishable row. Without the horizon, that one crash
        suppresses this recipient permanently, with --force the only
        recovery."""
        make_notification_log(session, status='queued', dedup_key='Q2',
                              age=timedelta(hours=1))
        assert ledger.already_sent('Q2') is False

    def test_the_horizon_is_configurable(self, session):
        @contextmanager
        def factory():
            real_commit = session.commit
            session.commit = session.flush
            try:
                yield session
            finally:
                session.commit = real_commit

        make_notification_log(session, status='queued', dedup_key='Q3',
                              age=timedelta(seconds=90))
        patient = NotificationLedger(
            factory, config=NotifyConfig(queued_stale_seconds=3600))
        impatient = NotificationLedger(
            factory, config=NotifyConfig(queued_stale_seconds=30))
        assert patient.already_sent('Q3') is True
        assert impatient.already_sent('Q3') is False

    def test_stuck_queued_counts_exactly_what_stops_suppressing(self, ledger,
                                                                session):
        """The operator's counter and the retry rule are one mechanism, so
        they cannot disagree about what 'stuck' means."""
        make_notification_log(session, status='queued', dedup_key='Q4',
                              age=timedelta(hours=1))
        make_notification_log(session, status='queued', dedup_key='Q5')
        assert ledger.already_sent('Q4') is False        # stale → retryable
        assert ledger.already_sent('Q5') is True         # fresh → in flight
        assert ledger.stuck_queued() >= 1


class TestSinceWindow:

    def test_all_time_is_the_default(self, ledger, session):
        """A 30-day window looks conservative and is wrong: both key formats
        already carry their own window, so a window on top would silently
        re-enable the re-email bug for anything older than it."""
        make_notification_log(session, status='sent', dedup_key='OLD',
                              age=timedelta(days=400))
        assert ledger.already_sent('OLD') is True

    def test_an_explicit_since_bounds_the_search(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='OLD2',
                              age=timedelta(days=400))
        recent = datetime.now() - timedelta(days=30)
        assert ledger.already_sent('OLD2', since=recent) is False


class TestReadFailsOpen:

    def test_a_broken_suppression_query_does_not_block_sending(self, caplog):
        """Asymmetric with `record`, on purpose: a ledger that cannot be
        *queried* must not become a mailer that cannot send. The write path
        still fails closed, so an unrecordable send is still refused."""
        def broken_factory():
            raise RuntimeError('read replica down')

        ledger = NotificationLedger(broken_factory, config=NotifyConfig())
        assert ledger.already_sent('K') is False


# ── already_sent_many ────────────────────────────────────────────────────────
#
# The batch form exists for the scheduled expiration send, where a typical
# week's selection is ~85% already-notified and asking one key at a time is
# several hundred round trips to learn that almost nothing needs sending.

#: (status, age, does it suppress). Every case `already_sent`'s docstring
#: reasons about, in one place, so the matrix below cannot go stale silently.
SUPPRESSION_CASES = [
    ('sent',       None,                    True),
    ('redirected', None,                    True),
    ('failed',     None,                    False),
    ('suppressed', None,                    False),
    ('queued',     None,                    True),    # fresh: in flight
    ('queued',     timedelta(hours=1),      False),   # stale: never learned
]


class TestTheSingleAndBatchFormsAgree:
    """THE anti-drift gate on commit 1.

    `already_sent` keeps its own `.limit(1)` fast path rather than delegating
    to `already_sent_many`, so the two really are separate statements. What
    stops them diverging is the shared `_suppression_conditions` — and this
    matrix, which would fail the moment one path learned a rule the other did
    not. A divergence here mails a PI a second copy and leaves no trace of why.
    """

    @pytest.mark.parametrize('status,age,suppresses', SUPPRESSION_CASES)
    def test_every_status_and_age_case_matches(self, ledger, session,
                                               status, age, suppresses):
        key = f'AGREE:{status}:{age}'
        make_notification_log(session, status=status, dedup_key=key, age=age)
        assert ledger.already_sent(key) is suppresses
        assert (key in ledger.already_sent_many([key])) is suppresses

    def test_a_mixed_batch_partitions_exactly_as_the_single_form_does(
            self, ledger, session):
        """The same six cases in ONE call, which is how the task uses it."""
        keys = []
        for status, age, _ in SUPPRESSION_CASES:
            key = f'MIXED:{status}:{age}'
            make_notification_log(session, status=status, dedup_key=key,
                                  age=age)
            keys.append(key)

        found = ledger.already_sent_many(keys)
        assert found == {k for k in keys if ledger.already_sent(k)}

    def test_an_unknown_key_suppresses_in_neither(self, ledger):
        assert ledger.already_sent('NEVER-SEEN') is False
        assert ledger.already_sent_many(['NEVER-SEEN']) == set()

    def test_the_since_window_bounds_both_the_same_way(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='OLD3',
                              age=timedelta(days=400))
        recent = datetime.now() - timedelta(days=30)
        assert ledger.already_sent('OLD3', since=recent) is False
        assert ledger.already_sent_many(['OLD3'], since=recent) == set()
        assert ledger.already_sent_many(['OLD3']) == {'OLD3'}

    def test_the_horizon_is_computed_once_for_the_whole_batch(self, ledger,
                                                              session):
        """Per-chunk horizons would let an identical `queued` row suppress in
        one chunk and not the next, purely on where it landed in the list."""
        for i in range(6):
            make_notification_log(session, status='queued',
                                  dedup_key=f'HZ{i}')
        keys = [f'HZ{i}' for i in range(6)]
        assert ledger.already_sent_many(keys, chunk_size=2) == set(keys)


class TestAlreadySentManyInputHandling:

    def test_an_empty_batch_never_opens_a_session(self):
        """A caller may hold no database at all; building one to answer
        'nothing' would make an empty batch cost more than a small one."""
        def exploding_factory():
            raise AssertionError('session_factory must not be called')

        ledger = NotificationLedger(exploding_factory, config=NotifyConfig())
        assert ledger.already_sent_many([]) == set()
        assert ledger.already_sent_many([None, '', None]) == set()

    def test_falsy_keys_are_dropped_not_queried(self, ledger, session):
        """Mirrors `already_sent`: an absent key never suppresses."""
        make_notification_log(session, status='sent', dedup_key='REAL')
        assert ledger.already_sent_many(['REAL', '', None]) == {'REAL'}

    def test_duplicates_collapse(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='DUP')
        assert ledger.already_sent_many(['DUP', 'DUP', 'DUP']) == {'DUP'}

    def test_a_generator_is_accepted(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='GEN')
        assert ledger.already_sent_many(k for k in ['GEN']) == {'GEN'}

    def test_only_the_suppressed_subset_comes_back(self, ledger, session):
        make_notification_log(session, status='sent', dedup_key='S1')
        make_notification_log(session, status='failed', dedup_key='S2')
        assert ledger.already_sent_many(['S1', 'S2', 'S3']) == {'S1'}


class TestAlreadySentManyChunking:
    """Chunking is a statement-length workaround, so it must be invisible."""

    @pytest.mark.parametrize('chunk_size', [1, 2, 3, 500, None, 0, -1])
    def test_the_answer_is_the_same_at_every_chunk_size(self, ledger, session,
                                                        chunk_size):
        for i in range(5):
            make_notification_log(session, status='sent', dedup_key=f'C{i}')
        keys = [f'C{i}' for i in range(5)] + ['C-absent']
        assert ledger.already_sent_many(keys, chunk_size=chunk_size) == \
            {f'C{i}' for i in range(5)}

    def test_one_session_serves_every_chunk(self, session):
        """A session per chunk would make a driver artifact into a second
        operation — and hold N connections where the class docstring promises
        one short-lived one."""
        opened = []

        @contextmanager
        def counting_factory():
            opened.append(1)
            real_commit = session.commit
            session.commit = session.flush
            try:
                yield session
            finally:
                session.commit = real_commit

        ledger = NotificationLedger(counting_factory, config=NotifyConfig())
        for i in range(6):
            make_notification_log(session, status='sent', dedup_key=f'O{i}')
        ledger.already_sent_many([f'O{i}' for i in range(6)], chunk_size=2)
        assert len(opened) == 1


class TestAlreadySentManyFailsOpenPerChunk:

    def test_a_broken_batch_query_does_not_block_sending(self, caplog):
        """Same asymmetry as `already_sent`."""
        def broken_factory():
            raise RuntimeError('read replica down')

        ledger = NotificationLedger(broken_factory, config=NotifyConfig())
        assert ledger.already_sent_many(['K1', 'K2']) == set()

    def test_a_failure_mid_batch_keeps_what_already_completed(self, session):
        """Discarding the partial result would re-send to recipients we had
        just proved were already done — the expensive half of failing open."""
        class FailsOnTheSecondStatement:
            def __init__(self, inner):
                self._inner = inner
                self.calls = 0

            def execute(self, *args, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError('connection lost mid-batch')
                return self._inner.execute(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        @contextmanager
        def flaky_factory():
            yield FailsOnTheSecondStatement(session)

        ledger = NotificationLedger(flaky_factory, config=NotifyConfig())
        for i in range(4):
            make_notification_log(session, status='sent', dedup_key=f'F{i}')

        # chunk_size=2 → chunk one succeeds, chunk two raises.
        found = ledger.already_sent_many([f'F{i}' for i in range(4)],
                                         chunk_size=2)
        assert found == {'F0', 'F1'}
