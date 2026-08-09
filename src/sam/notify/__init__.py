"""Channel-agnostic notifications for SAM — SMTP today, a ledger beside it.

``sam.notify`` is the mailer both consumers share: ``sam-admin project
--upcoming-expirations --notify`` and the webapp's XRAS activation Notify
button. It replaces ``cli.notifications.EmailNotificationService``, which was
a working mailer trapped behind a ``rich`` progress bar, a CLI ``Context``, a
hardcoded Bcc, and no record of anything it sent.

Typical use::

    from sam.notify import Message, Notifier, Recipient

    notifier = Notifier(ledger=NotificationLedger(session_factory))
    result = notifier.send(Message(
        kind='expiration',
        recipient=Recipient('pi@example.edu', name='A PI', role='lead'),
        subject='NSF NCAR Project SCSG0001 Expiration Notice',
        context={...},
        facility='UNIV',
        entity=('project', project.project_id),
        projcode='SCSG0001',
        dedup_key='expiration:SCSG0001:2026-09-30:pi@example.edu',
        requested_by='benkirk',
    ))

⚠️ **It is fail-closed.** ``NOTIFY_ENABLED`` defaults to ``false`` everywhere,
because every dev container and CI worker runs against an obfuscated copy of
production, obfuscation does not remove the mail relay, and the relay accepts
arbitrary external recipients from the whole UCAR ``/16``. A ``Notifier`` you
did not explicitly enable records ``suppressed`` and mails nobody.

Design, measurements and rationale: ``docs/plans/NOTIFICATION_FRAMEWORK.md``.
"""

from sam.notify.base import (
    Channel,
    DeliveryResult,
    Message,
    NOTIFICATION_STATUSES,
    NotifyError,
    Recipient,
    RenderedMessage,
    Transport,
    TransportError,
)
from sam.notify.config import NotifyConfig
from sam.notify.kinds import NOTIFICATION_KINDS, NotificationKind, get_kind
from sam.notify.registry import TRANSPORTS, build_transport, transport_names
from sam.notify.render import (
    DEFAULT_FACILITY_TEMPLATE,
    TEMPLATE_DIR,
    TemplateError,
    TemplateRenderer,
)
from sam.notify.service import REDIRECT_BANNER, Notifier
from sam.notify.transports import (
    ConsoleTransport,
    NullTransport,
    ORIGINAL_TO_HEADER,
    SmtpTransport,
)

__all__ = [
    'Channel',
    'ConsoleTransport',
    'DEFAULT_FACILITY_TEMPLATE',
    'DeliveryResult',
    'Message',
    'NOTIFICATION_KINDS',
    'NOTIFICATION_STATUSES',
    'NotificationKind',
    'Notifier',
    'NotifyConfig',
    'NotifyError',
    'NullTransport',
    'ORIGINAL_TO_HEADER',
    'REDIRECT_BANNER',
    'Recipient',
    'RenderedMessage',
    'SmtpTransport',
    'TEMPLATE_DIR',
    'TRANSPORTS',
    'TemplateError',
    'TemplateRenderer',
    'Transport',
    'TransportError',
    'build_transport',
    'get_kind',
    'transport_names',
]
