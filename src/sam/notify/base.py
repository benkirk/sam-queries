"""Core vocabulary for :mod:`sam.notify` — the types every other module speaks.

Nothing here imports Flask, SQLAlchemy, ``rich`` or ``smtplib``. That is the
point: the dataclasses below are what a caller builds, what a transport
receives and what the ledger records, so they must be constructible from a CLI
command, a Flask route and a test with equal ease.

See ``docs/plans/NOTIFICATION_FRAMEWORK.md`` § 1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Mapping, Optional, Tuple


class NotifyError(Exception):
    """Base for every error this package raises deliberately."""


class TransportError(NotifyError):
    """A delivery attempt failed in the transport.

    Raised — never returned — so the ledger owns status and a transport's
    only job is "did it go". The predecessor's ``(bool, error_str)`` tuple is
    what made every caller re-invent status handling
    (``cli/notifications/email.py:208,217`` mutates an ``'error'`` key into the
    caller's own dicts to work around it).
    """


class Channel(StrEnum):
    """Delivery channels. ``SLACK`` is declared and has no transport."""

    EMAIL = 'email'
    SLACK = 'slack'


#: Every value ``notification_log.status`` may hold.
#:
#: ``redirected`` is deliberately distinct from ``sent``: under
#: ``NOTIFY_REDIRECT_TO`` a message really was delivered, but not to its
#: subject, and a ledger that called that ``sent`` would be lying about the
#: one fact it exists to record.
NOTIFICATION_STATUSES: Tuple[str, ...] = (
    'queued', 'sent', 'failed', 'suppressed', 'redirected',
)


@dataclass(frozen=True)
class Recipient:
    """One addressee.

    ``role`` is free text from the caller's domain (``lead`` / ``admin`` /
    ``user`` / ``operator``) and is recorded, not interpreted.
    """

    address: str
    name: Optional[str] = None
    role: Optional[str] = None
    channel: Channel = Channel.EMAIL

    def __post_init__(self) -> None:
        if not self.address or not self.address.strip():
            raise ValueError('Recipient.address must be a non-empty string')


@dataclass(frozen=True)
class Message:
    """One notification, addressed to exactly one recipient.

    A "batch" is a list of these, not a message with many recipients: the
    ledger records one row per delivery attempt per person, and suppression
    keys on the recipient.

    Args:
        kind: a :data:`sam.notify.kinds.NOTIFICATION_KINDS` key.
        recipient: who it is for.
        subject: the rendered subject line, built by the caller (it is domain
            text — "NSF NCAR Project X Expiration Notice" — not a template
            concern).
        context: splatted into the template, exactly as the predecessor
            passed its whole notification dict.
        facility: selects the facility-specific template variant.
        entity: ``('project', 4711)`` — recorded on the ledger row as
            ``entity_type`` / ``entity_id``, with no FK. See § 5.
        projcode: denormalised beside ``entity`` because "did we mail anyone
            about SCSG0001" is the query that matters.
        dedup_key: the suppression key. ``None`` means "never suppress".
            Built from the **intended** recipient — see § 5.
        requested_by: ``users.username`` of the human who asked, or
            ``'cli'`` / ``'system'`` when unattended.
        intended_recipient: set by :class:`~sam.notify.service.Notifier` when
            ``NOTIFY_REDIRECT_TO`` rewrote ``recipient``. **Callers leave it
            ``None``** — it is an output of the redirect, not an input to it.
    """

    kind: str
    recipient: Recipient
    subject: str
    context: Mapping[str, Any] = field(default_factory=dict)
    facility: Optional[str] = None
    entity: Optional[Tuple[str, int]] = None
    projcode: Optional[str] = None
    dedup_key: Optional[str] = None
    requested_by: str = 'system'
    intended_recipient: Optional[str] = None

    @property
    def entity_type(self) -> Optional[str]:
        return self.entity[0] if self.entity else None

    @property
    def entity_id(self) -> Optional[int]:
        return self.entity[1] if self.entity else None


@dataclass(frozen=True)
class RenderedMessage:
    """What a :class:`Transport` is handed alongside the :class:`Message`.

    ``template_text`` / ``template_html`` name the files actually chosen,
    which is what makes the facility fallback auditable from the ledger.
    """

    subject: str
    text: str
    html: Optional[str] = None
    template_text: Optional[str] = None
    template_html: Optional[str] = None


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome of one :class:`Message`.

    ``ok`` is narrower than "nothing went wrong": a ``suppressed`` message is
    a correct outcome and reports ``ok=True``, because the caller asked for a
    notification not to be duplicated and it was not.
    """

    ok: bool
    status: str
    message: Optional[Message] = None
    detail: Optional[str] = None
    log_id: Optional[int] = None

    @property
    def recipient(self) -> Optional[str]:
        return self.message.recipient.address if self.message else None


class Transport(ABC):
    """A way of getting a rendered message to a person.

    **Why ``open()`` / ``close()`` exist.** The predecessor opened a fresh
    ``smtplib.SMTP`` *inside* its send loop (``email.py:141-146``) — one TCP
    connect plus one STARTTLS handshake per recipient — and a bare per-message
    ``deliver()`` would reproduce exactly that. :meth:`Notifier.send_many`
    instead opens once, delivers per message, and closes in a ``finally``.

    Subclasses that are genuinely stateless (``null``, ``console``) inherit
    the no-op defaults and cost nothing.
    """

    name: ClassVar[str]
    channel: ClassVar[Channel]

    @abstractmethod
    def deliver(self, message: Message, rendered: RenderedMessage) -> None:
        """Deliver one message, or raise :class:`TransportError`."""

    def open(self) -> None:
        """Acquire whatever the batch shares. Called once per batch."""

    def close(self) -> None:
        """Release it. Called in a ``finally``, so it must not raise."""

    def check(self) -> Tuple[bool, Optional[str]]:
        """Connectivity probe for the admin card: ``(ok, detail)``.

        Must not deliver anything. The default is "nothing to check".
        """
        return (True, None)

    def __enter__(self) -> 'Transport':
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
