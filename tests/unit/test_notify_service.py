"""Notifier — the guard matrix and the batch lifecycle.

Ledger interaction is covered separately (tests/unit/test_notification_log.py);
here the ledger is absent or a stub, so these tests pin the *decisions*
rather than the recording.
"""

import pytest

from sam.notify import (
    Message, Notifier, NotifyConfig, NullTransport, Recipient, TransportError,
    TemplateRenderer,
)


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
