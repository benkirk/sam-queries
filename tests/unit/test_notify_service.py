"""Notifier — the guard matrix and the batch lifecycle.

Most of these run with no ledger at all, so they pin the *decisions* rather
than the recording; `TestWithALedger` at the end wires a real one and checks
the two meet.
"""

from contextlib import contextmanager

import pytest

from sam import NotificationLog
from sam.notify import (
    Message, Notifier, NotifyConfig, NullTransport, Recipient, TransportError,
    TemplateRenderer,
)
from sam.notify.ledger import NotificationLedger


@pytest.fixture
def template_dir(tmp_path):
    (tmp_path / 'expiration-UNIV.txt').write_text('Dear {{ recipient_name }},')
    return tmp_path


@pytest.fixture
def renderer(template_dir):
    return TemplateRenderer(template_dir=template_dir)


def _message(address='pi@x.edu', **kwargs):
    kwargs.setdefault('kind', 'expiration')
    kwargs.setdefault('subject', 'Expiration Notice')
    kwargs.setdefault('facility', 'UNIV')
    return Message(recipient=Recipient(address, name='A PI'), **kwargs)


def _notifier(renderer, transport=None, **config_kwargs):
    config_kwargs.setdefault('enabled', True)
    return Notifier(config=NotifyConfig(**config_kwargs),
                    transport=transport or NullTransport(),
                    renderer=renderer)


class TestDisabledIsTheDefault:

    def test_disabled_suppresses_without_delivering(self, renderer):
        transport = NullTransport()
        notifier = _notifier(renderer, transport, enabled=False)
        result = notifier.send(_message())
        assert result.status == 'suppressed'
        assert 'NOTIFY_ENABLED' in result.detail
        assert transport.delivered == []

    def test_disabled_never_even_opens_the_transport(self, renderer):
        """A fully-suppressed batch must not cost a connection."""
        transport = NullTransport()
        _notifier(renderer, transport, enabled=False).send_many(
            [_message('a@x.edu'), _message('b@x.edu')])
        assert transport.open_count == 0

    def test_suppressed_counts_as_ok(self, renderer):
        """The caller asked for no duplicate; there was none. That is success."""
        result = _notifier(renderer, enabled=False).send(_message())
        assert result.ok is True


class TestRedirect:

    def test_redirect_rewrites_the_address(self, renderer):
        transport = NullTransport()
        notifier = _notifier(renderer, transport, redirect_to='me@x.edu')
        result = notifier.send(_message('pi@x.edu'))
        assert result.status == 'redirected'
        sent_message, _ = transport.delivered[0]
        assert sent_message.recipient.address == 'me@x.edu'
        assert sent_message.intended_recipient == 'pi@x.edu'

    def test_redirected_is_never_reported_as_sent(self, renderer):
        """The ledger must not claim a delivery that did not reach its
        subject — that is the one fact it exists to record."""
        notifier = _notifier(renderer, redirect_to='me@x.edu')
        assert notifier.send(_message()).status != 'sent'

    def test_the_body_carries_a_banner(self, renderer):
        transport = NullTransport()
        notifier = _notifier(renderer, transport, redirect_to='me@x.edu')
        notifier.send(_message('pi@x.edu'))
        _, rendered = transport.delivered[0]
        assert 'REDIRECT' in rendered.text
        assert 'pi@x.edu' in rendered.text

    def test_the_dedup_key_is_not_rebuilt_from_the_redirect_target(self, renderer):
        """Otherwise a whole staging run collapses onto one key and the second
        project suppresses against the first — suppression would behave
        differently in staging than in production."""
        transport = NullTransport()
        notifier = _notifier(renderer, transport, redirect_to='me@x.edu')
        notifier.send(_message('pi@x.edu', dedup_key='expiration:P:d:pi@x.edu'))
        sent_message, _ = transport.delivered[0]
        assert sent_message.dedup_key == 'expiration:P:d:pi@x.edu'

    def test_no_redirect_leaves_intended_recipient_unset(self, renderer):
        transport = NullTransport()
        _notifier(renderer, transport).send(_message())
        assert transport.delivered[0][0].intended_recipient is None


class TestBatchLifecycle:

    def test_one_open_per_batch_not_per_message(self, renderer):
        """The predecessor opened a fresh SMTP connection inside its send
        loop (email.py:141-146) — one TCP connect plus one STARTTLS
        handshake per recipient. This is the assertion that stops the
        rewrite reproducing it."""
        transport = NullTransport()
        notifier = _notifier(renderer, transport)
        notifier.send_many([_message(f'u{i}@x.edu') for i in range(5)])
        assert transport.open_count == 1
        assert transport.close_count == 1
        assert len(transport.delivered) == 5

    def test_results_come_back_in_input_order(self, renderer):
        notifier = _notifier(renderer)
        addresses = [f'u{i}@x.edu' for i in range(4)]
        results = notifier.send_many([_message(a) for a in addresses])
        assert [r.recipient for r in results] == addresses

    def test_on_result_fires_once_per_message(self, renderer):
        """The seam that keeps `rich` out of sam/ while the CLI keeps its
        progress bar."""
        seen = []
        notifier = _notifier(renderer)
        notifier.send_many([_message('a@x.edu'), _message('b@x.edu')],
                           on_result=seen.append)
        assert len(seen) == 2

    def test_on_result_fires_for_suppressed_messages_too(self, renderer):
        seen = []
        notifier = _notifier(renderer, enabled=False)
        notifier.send_many([_message()], on_result=seen.append)
        assert [r.status for r in seen] == ['suppressed']

    def test_the_transport_is_closed_even_when_delivery_raises(self, renderer):
        class Exploding(NullTransport):
            def deliver(self, message, rendered):
                raise TransportError('relay said no')

        transport = Exploding()
        results = _notifier(renderer, transport).send_many([_message()])
        assert results[0].status == 'failed'
        assert transport.close_count == 1


class TestChunkedBatches:
    """`chunk_size` reconnects mid-run, so one dropped connection costs one
    chunk rather than every message after it.

    The default — `chunk_size=None` — must stay byte-for-byte what the method
    did before chunking existed; `test_one_open_per_batch_not_per_message`
    above is the gate on that and is deliberately not parametrized here.
    """

    def test_the_default_is_still_exactly_one_connection(self, renderer):
        transport = NullTransport()
        _notifier(renderer, transport).send_many(
            [_message(f'u{i}@x.edu') for i in range(5)], chunk_size=None)
        assert (transport.open_count, transport.close_count) == (1, 1)

    @pytest.mark.parametrize('chunk_size,opens', [(1, 5), (2, 3), (5, 1),
                                                  (99, 1)])
    def test_one_connection_per_chunk(self, renderer, chunk_size, opens):
        transport = NullTransport()
        _notifier(renderer, transport).send_many(
            [_message(f'u{i}@x.edu') for i in range(5)], chunk_size=chunk_size)
        assert transport.open_count == opens
        assert transport.close_count == opens
        assert len(transport.delivered) == 5

    @pytest.mark.parametrize('chunk_size', [None, 0, -1, 1, 2, 5, 99])
    def test_the_delivered_set_is_the_same_at_every_chunk_size(self, renderer,
                                                               chunk_size):
        """Chunking is a connection-management concern and must not be
        observable in what gets sent, or in what order."""
        transport = NullTransport()
        addresses = [f'u{i}@x.edu' for i in range(5)]
        results = _notifier(renderer, transport).send_many(
            [_message(a) for a in addresses], chunk_size=chunk_size)
        assert [r.recipient for r in results] == addresses
        assert [m.recipient.address for m, _ in transport.delivered] == addresses

    def test_chunks_are_counted_over_pending_not_input(self, renderer):
        """Suppressed messages never reach a chunk, so a batch that is mostly
        already-notified must not spend a connection per suppressed run of
        `chunk_size`. This is the quiet-week shape: 100 selected, 98 skipped."""
        transport = NullTransport()
        notifier = Notifier(config=NotifyConfig(enabled=True),
                            transport=transport,
                            renderer=renderer,
                            ledger=_StubLedger({'SKIP'}))
        messages = ([_message(f'skip{i}@x.edu', dedup_key='SKIP')
                     for i in range(8)]
                    + [_message('real@x.edu')])
        notifier.send_many(messages, chunk_size=2)
        assert transport.open_count == 1
        assert len(transport.delivered) == 1

    def test_on_result_still_fires_once_per_message_across_chunks(self, renderer):
        seen = []
        _notifier(renderer).send_many(
            [_message(f'u{i}@x.edu') for i in range(5)],
            chunk_size=2, on_result=seen.append)
        assert len(seen) == 5


class TestAChunkFailsAlone:

    def test_a_relay_that_recovers_only_loses_its_own_chunk(self, renderer):
        """THE reason chunking exists. Un-chunked, `open()` is called once, so
        a relay that drops the connection takes every remaining message with
        it. Chunked, the next chunk reconnects and gets through."""
        class DownOnTheSecondConnect(NullTransport):
            attempts = 0

            def open(self):
                self.attempts += 1
                if self.attempts == 2:
                    raise TransportError('connection reset')
                return super().open()

        transport = DownOnTheSecondConnect()
        results = _notifier(renderer, transport).send_many(
            [_message(f'u{i}@x.edu') for i in range(6)], chunk_size=2)

        statuses = [r.status for r in results]
        assert statuses[0:2] == ['sent', 'sent']        # chunk 1
        assert statuses[2:4] == ['failed', 'failed']    # chunk 2 — the drop
        assert statuses[4:6] == ['sent', 'sent']        # chunk 3 — recovered
        assert all('connection reset' in r.detail for r in results[2:4])

    def test_every_recipient_in_a_failed_chunk_is_recorded(self, renderer):
        """The ledger must explain every recipient, not the first and a
        silence — the same rule the un-chunked path already followed."""
        class Unreachable(NullTransport):
            def open(self):
                raise TransportError('connection refused')

        results = _notifier(renderer, Unreachable()).send_many(
            [_message(f'u{i}@x.edu') for i in range(5)], chunk_size=2)
        assert [r.status for r in results] == ['failed'] * 5
        assert all('connection refused' in r.detail for r in results)

    def test_a_hard_down_relay_costs_one_connect_attempt_per_chunk(self, renderer):
        """The documented price of chunking. Stated as a test so nobody
        discovers it as a timeout in production instead."""
        class Unreachable(NullTransport):
            attempts = 0

            def open(self):
                self.attempts += 1
                raise TransportError('connection refused')

        transport = Unreachable()
        _notifier(renderer, transport).send_many(
            [_message(f'u{i}@x.edu') for i in range(6)], chunk_size=2)
        assert transport.attempts == 3          # ceil(6 / 2), not 1
        assert transport.close_count == 0       # never opened, never closed


class _StubLedger:
    """Answers the suppression question from a set, records nothing."""

    def __init__(self, suppressed):
        self._suppressed = set(suppressed)
        self.batch_calls = 0
        self.single_calls = 0

    def already_sent(self, dedup_key, *, since=None):
        self.single_calls += 1
        return dedup_key in self._suppressed

    def already_sent_many(self, dedup_keys, *, since=None, chunk_size=None):
        self.batch_calls += 1
        return {k for k in dedup_keys if k in self._suppressed}

    def record(self, message, *, status, transport, detail=None,
               rendered=None):
        return 0

    def resolve(self, log_id, *, status, detail=None):
        pass


class TestFailureIsNeverAnException:

    def test_transport_error_becomes_a_failed_result(self, renderer):
        class Exploding(NullTransport):
            def deliver(self, message, rendered):
                raise TransportError('550 nope')

        result = _notifier(renderer, Exploding()).send(_message())
        assert (result.ok, result.status) == (False, 'failed')
        assert '550 nope' in result.detail

    def test_an_unexpected_transport_bug_is_also_contained(self, renderer):
        """A route that 500s on a relay hiccup is worse than one that says
        'nothing was sent'."""
        class Buggy(NullTransport):
            def deliver(self, message, rendered):
                raise ZeroDivisionError('oops')

        result = _notifier(renderer, Buggy()).send(_message())
        assert result.status == 'failed'
        assert 'ZeroDivisionError' in result.detail

    def test_a_failure_to_open_fails_every_message_not_just_the_first(self, renderer):
        class Unreachable(NullTransport):
            def open(self):
                raise TransportError('connection refused')

        results = _notifier(renderer, Unreachable()).send_many(
            [_message('a@x.edu'), _message('b@x.edu'), _message('c@x.edu')])
        assert [r.status for r in results] == ['failed'] * 3
        assert all('connection refused' in r.detail for r in results)

    def test_a_render_failure_is_contained_per_message(self, renderer):
        transport = NullTransport()
        notifier = _notifier(renderer, transport)
        results = notifier.send_many([
            _message('a@x.edu'),
            _message('b@x.edu', facility='UNIV', kind='xras_activation'),
            _message('c@x.edu'),
        ])
        assert [r.status for r in results] == ['sent', 'failed', 'sent']
        assert 'no text template' in results[1].detail


class TestKindValidationRaises:

    def test_an_unknown_kind_raises_rather_than_recording(self, renderer):
        """A bad kind is a programmer error, not an outcome. Recording it
        would put a row in the ledger that no facet chip can ever match."""
        bad = Message(kind='nope', recipient=Recipient('a@x.edu'), subject='s')
        with pytest.raises(ValueError, match='unknown notification kind'):
            _notifier(renderer).send(bad)

    def test_it_raises_before_any_message_in_the_batch_is_delivered(self, renderer):
        transport = NullTransport()
        bad = Message(kind='nope', recipient=Recipient('b@x.edu'), subject='s')
        with pytest.raises(ValueError):
            _notifier(renderer, transport).send_many([_message('a@x.edu'), bad])
        assert transport.delivered == []


class TestPreview:

    def test_preview_renders_without_delivering(self, renderer):
        transport = NullTransport()
        rendered = _notifier(renderer, transport).preview(_message())
        assert 'Dear A PI' in rendered.text
        assert transport.delivered == []

    def test_preview_works_even_when_notifications_are_disabled(self, renderer):
        """The XRAS preview modal must render on a box that cannot send."""
        rendered = _notifier(renderer, enabled=False).preview(_message())
        assert rendered.text

    def test_preview_validates_the_kind(self, renderer):
        bad = Message(kind='nope', recipient=Recipient('a@x.edu'), subject='s')
        with pytest.raises(ValueError):
            _notifier(renderer).preview(bad)


class TestTransportSelection:

    def test_unknown_transport_name_fails_loudly(self, renderer):
        """Never a silent fallback to null: a deployment that meant to send
        and typo'd the name must not quietly record rows that look fine."""
        from sam.notify import NotifyError
        notifier = Notifier(config=NotifyConfig(enabled=True, transport='smpt'),
                            renderer=renderer)
        with pytest.raises(NotifyError, match='unknown NOTIFY_TRANSPORT'):
            _ = notifier.transport

    def test_console_transport_writes_the_body(self, renderer, capsys):
        notifier = Notifier(config=NotifyConfig(enabled=True, transport='console'),
                            renderer=renderer)
        notifier.send(_message())
        assert 'Dear A PI' in capsys.readouterr().out


class TestWithALedger:
    """The guard matrix wired to real rows.

    The ledger commits by design (mail cannot be un-sent by a rollback), so
    the factory hands back the test session with `commit` neutered — see
    tests/unit/test_notify_ledger.py.
    """

    @pytest.fixture
    def ledger(self, session):
        @contextmanager
        def factory():
            real_commit = session.commit
            session.commit = session.flush
            try:
                yield session
            finally:
                session.commit = real_commit

        return NotificationLedger(factory, config=NotifyConfig())

    def _notifier(self, renderer, ledger, transport=None, **cfg):
        cfg.setdefault('enabled', True)
        return Notifier(config=NotifyConfig(**cfg), renderer=renderer,
                        transport=transport or NullTransport(), ledger=ledger)

    def test_a_send_writes_one_row_that_ends_sent(self, renderer, ledger, session):
        result = self._notifier(renderer, ledger).send(_message())
        row = session.get(NotificationLog, result.log_id)
        assert (row.status, row.recipient) == ('sent', 'pi@x.edu')
        assert row.sent_time is not None

    def test_the_row_is_queued_before_the_transport_runs(self, renderer, ledger,
                                                         session):
        """Ordering is the whole point: a crash between the two writes must
        leave an honest 'we do not know', not a silent loss."""
        seen = {}

        class Watching(NullTransport):
            def deliver(self, message, rendered):
                seen['status'] = session.get(
                    NotificationLog, max(
                        r.notification_log_id for r in
                        session.query(NotificationLog).all())).status
                super().deliver(message, rendered)

        self._notifier(renderer, ledger, Watching()).send(_message())
        assert seen['status'] == 'queued'

    def test_a_transport_failure_resolves_the_row_to_failed(self, renderer,
                                                            ledger, session):
        class Exploding(NullTransport):
            def deliver(self, message, rendered):
                raise TransportError('550 nope')

        result = self._notifier(renderer, ledger, Exploding()).send(_message())
        row = session.get(NotificationLog, result.log_id)
        assert row.status == 'failed'
        assert '550 nope' in row.error

    def test_disabled_records_suppressed(self, renderer, ledger, session):
        result = self._notifier(renderer, ledger, enabled=False).send(_message())
        row = session.get(NotificationLog, result.log_id)
        assert row.status == 'suppressed'
        assert 'NOTIFY_ENABLED' in row.error

    def test_a_second_send_of_the_same_key_is_suppressed(self, renderer, ledger,
                                                          session):
        """The re-email bug, fixed. `--upcoming-expirations --notify` used to
        re-mail the entire roster on every invocation inside the window."""
        notifier = self._notifier(renderer, ledger)
        key = 'expiration:SCSG0001:2026-09-30:pi@x.edu'
        first = notifier.send(_message(dedup_key=key))
        second = notifier.send(_message(dedup_key=key))
        assert first.status == 'sent'
        assert second.status == 'suppressed'
        assert 'already sent' in second.detail

    def test_force_overrides_suppression(self, renderer, ledger):
        notifier = self._notifier(renderer, ledger)
        key = 'expiration:SCSG0001:2026-09-30:pi@x.edu'
        notifier.send(_message(dedup_key=key))
        assert notifier.send(_message(dedup_key=key), force=True).status == 'sent'

    def test_messages_without_a_key_are_never_suppressed(self, renderer, ledger):
        notifier = self._notifier(renderer, ledger)
        assert notifier.send(_message()).status == 'sent'
        assert notifier.send(_message()).status == 'sent'

    def test_a_redirect_records_the_intended_recipient(self, renderer, ledger,
                                                        session):
        result = self._notifier(renderer, ledger,
                                redirect_to='me@x.edu').send(_message('pi@x.edu'))
        row = session.get(NotificationLog, result.log_id)
        assert (row.status, row.recipient, row.intended_recipient) == \
            ('redirected', 'me@x.edu', 'pi@x.edu')

    def test_the_suppression_key_survives_a_redirect(self, renderer, ledger,
                                                      session):
        """Built from the intended recipient, so staging suppresses the same
        way production does."""
        key = 'expiration:SCSG0001:2026-09-30:pi@x.edu'
        result = self._notifier(renderer, ledger, redirect_to='me@x.edu').send(
            _message('pi@x.edu', dedup_key=key))
        assert session.get(NotificationLog, result.log_id).dedup_key == key

    def test_preview_writes_no_row(self, renderer, ledger, session):
        """A preview is not an attempt, and a stray `suppressed` row would
        poison the dedup query for the real send that follows."""
        before = session.query(NotificationLog).count()
        self._notifier(renderer, ledger).preview(_message())
        assert session.query(NotificationLog).count() == before

    def test_an_unrecordable_send_is_refused_rather_than_sent(self, renderer):
        """Fail-closed, matching NOTIFY_ENABLED one layer up: an unrecorded
        send is one the next run sends again."""
        def broken():
            raise RuntimeError('database unreachable')

        transport = NullTransport()
        notifier = Notifier(config=NotifyConfig(enabled=True), renderer=renderer,
                            transport=transport,
                            ledger=NotificationLedger(broken, config=NotifyConfig()))
        result = notifier.send(_message())
        assert result.status == 'failed'
        assert 'refusing to send' in result.detail
        assert transport.delivered == []

    def test_an_unrecordable_suppression_does_not_raise(self, renderer):
        """Nothing went out, so an unwritable ledger is bookkeeping — it must
        not turn 'we sent nothing' into an exception out of a route."""
        def broken():
            raise RuntimeError('database unreachable')

        notifier = Notifier(config=NotifyConfig(enabled=False), renderer=renderer,
                            transport=NullTransport(),
                            ledger=NotificationLedger(broken, config=NotifyConfig()))
        assert notifier.send(_message()).status == 'suppressed'
