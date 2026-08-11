"""``SmtpTransport`` — the one transport that actually reaches people.

Relay facts this is built against, measured from a pod on nwc1 on 2026-08-09
(``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 9):

* ``ndir.ucar.edu:25`` advertises ``STARTTLS`` and **no ``AUTH``**, so
  ``login()`` is attempted only when both credentials are configured and is
  inert against this relay.
* It relays for the whole ``128.117.0.0/16`` and accepts **arbitrary external
  recipients**. Nothing in the transport prevents mailing the wrong person;
  that is entirely the job of ``NOTIFY_ENABLED`` and ``NOTIFY_REDIRECT_TO``
  one layer up, and of the autouse no-socket fixture in the test tier.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

from sam.notify.base import (
    Channel, Message, RenderedMessage, Transport, TransportError,
)
from sam.notify.config import NotifyConfig

logger = logging.getLogger(__name__)

#: Header carrying the pre-redirect address, so a staging inbox can tell who a
#: message was *for*. The ledger records it too (``intended_recipient``); this
#: is for the human reading the mail.
ORIGINAL_TO_HEADER = 'X-SAM-Original-To'


class SmtpTransport(Transport):
    """Delivers over SMTP, one connection per batch."""

    name = 'smtp'
    channel = Channel.EMAIL

    def __init__(self, config: NotifyConfig) -> None:
        self.config = config
        self._smtp: Optional[smtplib.SMTP] = None

    # ----------------------------------------------------------- connection
    def _connect(self) -> smtplib.SMTP:
        cfg = self.config
        smtp = smtplib.SMTP(cfg.mail_server, cfg.mail_port,
                            timeout=cfg.mail_timeout)
        smtp.ehlo()
        if cfg.mail_use_tls and smtp.has_extn('starttls'):
            smtp.starttls()
            smtp.ehlo()          # capabilities are re-read after STARTTLS
        if cfg.mail_username and cfg.mail_password and smtp.has_extn('auth'):
            smtp.login(cfg.mail_username, cfg.mail_password)
        return smtp

    def open(self) -> None:
        """Open the batch's single connection.

        The predecessor opened one *per recipient* (``email.py:141-146``) —
        a TCP connect and a STARTTLS handshake each — which is what this
        exists to avoid.
        """
        if self._smtp is not None:
            return
        try:
            self._smtp = self._connect()
        except Exception as exc:
            self._smtp = None
            raise TransportError(
                f'could not connect to {self.config.mail_server}:'
                f'{self.config.mail_port}: {exc}') from exc

    def close(self) -> None:
        """Close it. Called from a ``finally``, so it never raises."""
        smtp, self._smtp = self._smtp, None
        if smtp is None:
            return
        try:
            smtp.quit()
        except Exception:               # pragma: no cover - relay hung up
            logger.debug('SMTP quit failed; closing anyway', exc_info=True)
            try:
                smtp.close()
            except Exception:
                pass

    def check(self) -> Tuple[bool, Optional[str]]:
        """Connect and disconnect without delivering anything."""
        try:
            smtp = self._connect()
        except Exception as exc:
            return (False, str(exc))
        try:
            smtp.quit()
        except Exception:               # pragma: no cover
            pass
        return (True, None)

    # -------------------------------------------------------------- message
    def build_message(self, message: Message,
                      rendered: RenderedMessage) -> MIMEMultipart | MIMEText:
        """Build the MIME object.

        ⚠️ **No ``Bcc`` header is ever set.** ``NOTIFY_BCC`` goes into the
        envelope recipient list (:meth:`envelope_recipients`) and nowhere
        else. ``smtplib.send_message`` would strip a ``Bcc`` header for us,
        but this transport passes ``to_addrs`` explicitly, and under that
        call shape every header set is a header transmitted. See
        ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 7.
        """
        if rendered.html:
            msg: MIMEMultipart | MIMEText = MIMEMultipart('alternative')
            msg.attach(MIMEText(rendered.text, 'plain'))
            msg.attach(MIMEText(rendered.html, 'html'))
        else:
            msg = MIMEText(rendered.text, 'plain')

        msg['Subject'] = rendered.subject
        msg['From'] = self.config.mail_from
        msg['To'] = message.recipient.address
        if message.intended_recipient:
            msg[ORIGINAL_TO_HEADER] = message.intended_recipient
        return msg

    def envelope_recipients(self, message: Message) -> List[str]:
        """Who the relay is told to deliver to: the addressee, plus the Bcc."""
        recipients = [message.recipient.address]
        for address in self.config.bcc_addresses:
            if address not in recipients:
                recipients.append(address)
        return recipients

    # -------------------------------------------------------------- deliver
    def deliver(self, message: Message, rendered: RenderedMessage) -> None:
        """Send one message on the open connection.

        Raises:
            TransportError: on any failure, including a closed connection.
                The ledger turns this into a ``failed`` row; nothing above
                here sees an ``smtplib`` exception type.
        """
        if self._smtp is None:
            raise TransportError('SmtpTransport.deliver() before open()')

        msg = self.build_message(message, rendered)
        to_addrs = self.envelope_recipients(message)

        try:
            self._smtp.sendmail(self.config.mail_from, to_addrs,
                                msg.as_string())
        except Exception as exc:
            raise TransportError(
                f'failed to send to {message.recipient.address}: '
                f'{exc}') from exc

        logger.info('notify: sent kind=%s to=%s subject=%r',
                    message.kind, message.recipient.address, rendered.subject)
