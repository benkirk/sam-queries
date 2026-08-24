"""SmtpTransport — TLS, timeout, error mapping, and the Bcc regression guard.

The suite's autouse `_no_smtp_sockets` fixture makes `smtplib.SMTP` raise, so
every test here installs its own double explicitly. That is the intended
shape: reaching a real relay should require saying so.
"""

from unittest.mock import MagicMock, patch

import pytest

from sam.notify import (
    Message, NotifyConfig, ORIGINAL_TO_HEADER, Recipient, RenderedMessage,
    SmtpTransport, TransportError,
)

SMTP_PATH = 'sam.notify.transports.smtp.smtplib.SMTP'


def _config(**kwargs):
    kwargs.setdefault('enabled', True)
    kwargs.setdefault('mail_from', 'sam-admin@ucar.edu')
    return NotifyConfig(**kwargs)


def _message(address='pi@x.edu', **kwargs):
    kwargs.setdefault('kind', 'expiration')
    kwargs.setdefault('subject', 'Expiration Notice')
    return Message(recipient=Recipient(address), **kwargs)


def _rendered(text='body text', html=None):
    return RenderedMessage(subject='Expiration Notice', text=text, html=html,
                           template_text='expiration-UNIV.txt')


@pytest.fixture
def smtp():
    """A connected SMTP double whose capabilities can be steered."""
    double = MagicMock()
    double.has_extn.return_value = True
    with patch(SMTP_PATH, return_value=double) as factory:
        double._factory = factory
        yield double


class TestConnection:

    def test_open_connects_with_the_configured_timeout(self, smtp):
        transport = SmtpTransport(_config(mail_server='relay.x.edu',
                                          mail_port=2525, mail_timeout=7))
        transport.open()
        smtp._factory.assert_called_once_with('relay.x.edu', 2525, timeout=7)

    def test_starttls_is_negotiated_when_enabled_and_offered(self, smtp):
        SmtpTransport(_config(mail_use_tls=True)).open()
        smtp.starttls.assert_called_once()
        # Capabilities are re-read after STARTTLS, so EHLO happens twice.
        assert smtp.ehlo.call_count == 2

    def test_starttls_is_skipped_when_the_relay_does_not_offer_it(self, smtp):
        smtp.has_extn.return_value = False
        SmtpTransport(_config(mail_use_tls=True)).open()
        smtp.starttls.assert_not_called()

    def test_starttls_is_skipped_when_disabled(self, smtp):
        SmtpTransport(_config(mail_use_tls=False)).open()
        smtp.starttls.assert_not_called()

    def test_login_only_when_both_credentials_are_set(self, smtp):
        SmtpTransport(_config(mail_username='u')).open()
        smtp.login.assert_not_called()

    def test_login_is_skipped_when_the_relay_advertises_no_auth(self, smtp):
        """ndir advertises no AUTH (§ 9). Attempting it would be an error
        reply on a connection that is otherwise fine."""
        smtp.has_extn.side_effect = lambda ext: ext != 'auth'
        SmtpTransport(_config(mail_username='u', mail_password='p')).open()
        smtp.login.assert_not_called()

    def test_login_when_offered_and_configured(self, smtp):
        SmtpTransport(_config(mail_username='u', mail_password='p')).open()
        smtp.login.assert_called_once_with('u', 'p')

    def test_a_refused_connection_becomes_a_transport_error(self):
        with patch(SMTP_PATH, side_effect=OSError('connection refused')):
            with pytest.raises(TransportError, match='could not connect'):
                SmtpTransport(_config()).open()

    def test_open_is_idempotent_within_a_batch(self, smtp):
        transport = SmtpTransport(_config())
        transport.open()
        transport.open()
        assert smtp._factory.call_count == 1

    def test_close_survives_a_relay_that_hung_up(self, smtp):
        smtp.quit.side_effect = OSError('broken pipe')
        transport = SmtpTransport(_config())
        transport.open()
        transport.close()                       # must not raise
        smtp.close.assert_called_once()

    def test_close_without_open_is_a_no_op(self):
        SmtpTransport(_config()).close()

    def test_check_connects_and_disconnects_without_sending(self, smtp):
        ok, detail = SmtpTransport(_config()).check()
        assert (ok, detail) == (True, None)
        smtp.sendmail.assert_not_called()
        smtp.quit.assert_called_once()

    def test_check_reports_the_failure_rather_than_raising(self):
        with patch(SMTP_PATH, side_effect=OSError('no route to host')):
            ok, detail = SmtpTransport(_config()).check()
        assert ok is False
        assert 'no route to host' in detail


class TestMessageShape:

    def test_plain_text_when_there_is_no_html(self, smtp):
        transport = SmtpTransport(_config())
        msg = transport.build_message(_message(), _rendered())
        assert msg.get_content_type() == 'text/plain'

    def test_multipart_alternative_when_html_is_present(self, smtp):
        transport = SmtpTransport(_config())
        msg = transport.build_message(_message(), _rendered(html='<p>hi</p>'))
        assert msg.get_content_type() == 'multipart/alternative'
        assert [p.get_content_type() for p in msg.get_payload()] == \
            ['text/plain', 'text/html']

    def test_the_original_recipient_header_appears_only_on_a_redirect(self, smtp):
        transport = SmtpTransport(_config())
        plain = transport.build_message(_message(), _rendered())
        assert ORIGINAL_TO_HEADER not in plain

        # Read off the Message rather than a parallel argument: an `intended=`
        # parameter beside a Message that already carries the value is a trap
        # for exactly one caller to forget.
        redirected = transport.build_message(
            _message('me@x.edu', intended_recipient='pi@x.edu'), _rendered())
        assert redirected[ORIGINAL_TO_HEADER] == 'pi@x.edu'


class TestBcc:
    """A regression guard, not a bug fix — see NOTIFICATION_FRAMEWORK.md § 7.

    `smtplib.send_message` strips a Bcc header for you, so the predecessor's
    hardcoded Bcc really was blind. This transport passes `to_addrs`
    explicitly instead, and under *that* call shape every header set is a
    header transmitted. So the header must never be set at all.
    """

    def test_bcc_joins_the_envelope_recipients(self, smtp):
        transport = SmtpTransport(_config(bcc='ops@x.edu'))
        transport.open()
        transport.deliver(_message('pi@x.edu'), _rendered())
        _, to_addrs, _ = smtp.sendmail.call_args[0]
        assert to_addrs == ['pi@x.edu', 'ops@x.edu']

    def test_bcc_is_never_emitted_as_a_header(self, smtp):
        transport = SmtpTransport(_config(bcc='ops@x.edu'))
        transport.open()
        transport.deliver(_message('pi@x.edu'), _rendered())
        _, _, payload = smtp.sendmail.call_args[0]
        headers = payload.split('\n\n')[0]
        assert 'Bcc' not in headers
        assert 'ops@x.edu' not in headers

    def test_several_bcc_addresses_are_supported(self, smtp):
        transport = SmtpTransport(_config(bcc='a@x.edu, b@x.edu'))
        transport.open()
        transport.deliver(_message('pi@x.edu'), _rendered())
        _, to_addrs, _ = smtp.sendmail.call_args[0]
        assert to_addrs == ['pi@x.edu', 'a@x.edu', 'b@x.edu']

    def test_a_bcc_equal_to_the_recipient_is_not_duplicated(self, smtp):
        transport = SmtpTransport(_config(bcc='pi@x.edu'))
        transport.open()
        transport.deliver(_message('pi@x.edu'), _rendered())
        _, to_addrs, _ = smtp.sendmail.call_args[0]
        assert to_addrs == ['pi@x.edu']

    def test_no_bcc_configured_means_one_envelope_recipient(self, smtp):
        transport = SmtpTransport(_config())
        transport.open()
        transport.deliver(_message('pi@x.edu'), _rendered())
        _, to_addrs, _ = smtp.sendmail.call_args[0]
        assert to_addrs == ['pi@x.edu']


class TestPerMessageAddressing:
    """`Message.cc/bcc/sender/reply_to` -- the XRAS shared-mailbox copy."""

    def _headers(self, smtp):
        _, to_addrs, payload = smtp.sendmail.call_args[0]
        return to_addrs, payload.split('\n\n')[0]

    def test_cc_is_a_header_and_an_envelope_recipient(self, smtp):
        transport = SmtpTransport(_config())
        transport.open()
        transport.deliver(_message('pi@x.edu', cc=('alloc@x.edu',)), _rendered())
        to_addrs, headers = self._headers(smtp)
        assert to_addrs == ['pi@x.edu', 'alloc@x.edu']
        assert 'Cc: alloc@x.edu' in headers

    def test_message_bcc_is_envelope_only(self, smtp):
        transport = SmtpTransport(_config())
        transport.open()
        transport.deliver(_message('pi@x.edu', bcc=('alloc@x.edu',)), _rendered())
        to_addrs, headers = self._headers(smtp)
        assert to_addrs == ['pi@x.edu', 'alloc@x.edu']
        assert 'Bcc' not in headers and 'alloc@x.edu' not in headers

    def test_message_and_config_copies_union_without_duplicates(self, smtp):
        transport = SmtpTransport(_config(bcc='ops@x.edu, alloc@x.edu'))
        transport.open()
        transport.deliver(_message('pi@x.edu', cc=('alloc@x.edu',),
                                   bcc=('pi@x.edu',)), _rendered())
        to_addrs, _ = self._headers(smtp)
        assert to_addrs == ['pi@x.edu', 'alloc@x.edu', 'ops@x.edu']

    def test_copies_are_dropped_on_a_redirect(self, smtp):
        """A staging run must never copy the real shared mailbox."""
        transport = SmtpTransport(_config())
        transport.open()
        transport.deliver(_message('me@x.edu', intended_recipient='pi@x.edu',
                                   cc=('alloc@x.edu',), bcc=('ops@x.edu',)),
                          _rendered())
        to_addrs, headers = self._headers(smtp)
        assert to_addrs == ['me@x.edu']
        assert 'Cc' not in headers and 'alloc@x.edu' not in headers

    def test_a_sender_override_moves_header_and_envelope_from_together(self, smtp):
        transport = SmtpTransport(_config(mail_from='sam-admin@x.edu'))
        transport.open()
        transport.deliver(_message(sender='alloc@x.edu'), _rendered())
        assert smtp.sendmail.call_args[0][0] == 'alloc@x.edu'
        _, headers = self._headers(smtp)
        assert 'From: alloc@x.edu' in headers

    def test_reply_to_is_a_header_and_leaves_from_alone(self, smtp):
        transport = SmtpTransport(_config(mail_from='sam-admin@x.edu'))
        transport.open()
        transport.deliver(_message(reply_to='alloc@x.edu'), _rendered())
        assert smtp.sendmail.call_args[0][0] == 'sam-admin@x.edu'
        _, headers = self._headers(smtp)
        assert 'Reply-To: alloc@x.edu' in headers
        assert 'From: sam-admin@x.edu' in headers

    def test_defaults_change_nothing_on_the_wire(self, smtp):
        transport = SmtpTransport(_config())
        transport.open()
        transport.deliver(_message(), _rendered())
        _, headers = self._headers(smtp)
        assert 'Cc' not in headers and 'Reply-To' not in headers


class TestDeliveryErrors:

    def test_envelope_from_is_the_configured_sender(self, smtp):
        transport = SmtpTransport(_config(mail_from='noreply@x.edu'))
        transport.open()
        transport.deliver(_message(), _rendered())
        assert smtp.sendmail.call_args[0][0] == 'noreply@x.edu'

    def test_a_relay_refusal_becomes_a_transport_error(self, smtp):
        import smtplib
        smtp.sendmail.side_effect = smtplib.SMTPRecipientsRefused({})
        transport = SmtpTransport(_config())
        transport.open()
        with pytest.raises(TransportError, match='failed to send to'):
            transport.deliver(_message(), _rendered())

    def test_delivering_before_open_is_a_transport_error_not_an_attribute_error(self):
        with pytest.raises(TransportError, match='before open'):
            SmtpTransport(_config()).deliver(_message(), _rendered())

    def test_the_context_manager_opens_and_closes(self, smtp):
        with SmtpTransport(_config()) as transport:
            transport.deliver(_message(), _rendered())
        smtp.quit.assert_called_once()


class TestTheSuiteCannotReachARelay:

    def test_the_autouse_fixture_blocks_a_real_connection(self):
        """The structural gate. If this ever passes silently, the suite can
        mail real people — § 9 measured ndir accepting arbitrary external
        recipients from any host on the UCAR /16.

        It surfaces as a TransportError because `open()` maps every connect
        failure, but the fixture's sentence survives inside it.
        """
        with pytest.raises(TransportError, match='never reach a real relay'):
            SmtpTransport(_config()).open()
