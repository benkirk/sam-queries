"""``NotifyConfig``: one config object, readable from Flask *or* the environment.

A core library that must behave identically under ``sam-admin`` and the webapp,
so every value goes through the Flask-then-env readers in ``sam.integration._config``.
It is also the one source for ``MAIL_*``: the CLI and the webapp both read them here.
See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sam.integration._config import config_bool, config_int, config_str


@dataclass(frozen=True)
class Addressing:
    """Per-family extra addressing, from ``NOTIFY_<FAMILY>_{CC,BCC,FROM,REPLY_TO}``."""

    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    sender: Optional[str] = None
    reply_to: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not (self.cc or self.bcc or self.sender or self.reply_to)

    def as_dict(self) -> dict:
        return {'cc': ', '.join(self.cc) or None,
                'bcc': ', '.join(self.bcc) or None,
                'from': self.sender, 'reply_to': self.reply_to}


def _split(value: str) -> tuple[str, ...]:
    return tuple(a.strip() for a in value.split(',') if a.strip())


@dataclass(frozen=True)
class NotifyConfig:
    """A snapshot of notification config, resolved at construction.

    Constructed per :class:`~sam.notify.service.Notifier`, not memoised at
    import: the webapp's config is not readable at import time, and tests
    change the environment between cases.
    """

    # ---------------------------------------------------------------- notify
    #: Master switch. **Fail-closed** — see § 3. Nothing sends unless a
    #: deployment explicitly opts in, because every dev container and CI
    #: worker runs against an obfuscated copy of production and obfuscation
    #: does not remove the mail relay.
    enabled: bool = False
    transport: str = 'smtp'
    #: When set, *every* message is re-addressed here and recorded
    #: ``redirected``. The staging/dev mode.
    redirect_to: str = ''
    #: An **envelope** Bcc — added to the recipient list, never emitted as a
    #: header. Replaces the address hardcoded at ``email.py:127,138``.
    bcc: str = ''
    #: How long a ``queued`` row blocks its own retry. An order of magnitude
    #: above ``mail_timeout``: fresh means "in flight, leave it", stale means
    #: "we never learned the outcome, try again". See § 5.
    queued_stale_seconds: int = 300

    # ------------------------------------------------------------------ mail
    mail_server: str = 'ndir.ucar.edu'
    mail_port: int = 25
    mail_use_tls: bool = True
    mail_username: str = ''
    mail_password: str = ''
    mail_from: str = 'sam-admin@ucar.edu'
    mail_timeout: int = 10

    @classmethod
    def from_environment(cls) -> 'NotifyConfig':
        """Build from Flask config or the environment, whichever is available."""
        return cls(
            enabled=config_bool('NOTIFY_ENABLED', False),
            transport=config_str('NOTIFY_TRANSPORT', 'smtp') or 'smtp',
            redirect_to=config_str('NOTIFY_REDIRECT_TO', ''),
            bcc=config_str('NOTIFY_BCC', ''),
            queued_stale_seconds=config_int('NOTIFY_QUEUED_STALE_SECONDS', 300, minimum=None),
            mail_server=config_str('MAIL_SERVER', 'ndir.ucar.edu'),
            mail_port=config_int('MAIL_PORT', 25, minimum=None),
            # Defaults true: § 9 measured STARTTLS working on ndir.ucar.edu,
            # the one relay both consumers use. src/config.py agrees.
            mail_use_tls=config_bool('MAIL_USE_TLS', True),
            # Kept for a future relay that wants them. ndir advertises no
            # AUTH (§ 9), so login is skipped there whatever these say.
            mail_username=config_str('MAIL_USERNAME', ''),
            mail_password=config_str('MAIL_PASSWORD', ''),
            mail_from=config_str('MAIL_DEFAULT_FROM', 'sam-admin@ucar.edu'),
            mail_timeout=config_int('MAIL_TIMEOUT', 10, minimum=None),
        )

    @property
    def bcc_addresses(self) -> list[str]:
        """``NOTIFY_BCC`` as a list — it accepts a comma-separated string."""
        return [a.strip() for a in self.bcc.split(',') if a.strip()]

    @staticmethod
    def addressing(family: str) -> Addressing:
        """The family's extra addressing, read live so the CLI and webapp agree.

        The ``Notifier`` fills a ``Message``'s empty ``cc``/``bcc``/``sender``/
        ``reply_to`` from this; a builder-set value wins. ``family`` comes from
        ``NotificationKind.family``; an empty one has no addressing.
        """
        if not family:
            return Addressing()
        prefix = f'NOTIFY_{family.upper()}_'
        return Addressing(
            cc=_split(config_str(prefix + 'CC', '')),
            bcc=_split(config_str(prefix + 'BCC', '')),
            sender=config_str(prefix + 'FROM', '') or None,
            reply_to=config_str(prefix + 'REPLY_TO', '') or None,
        )

    def addressing_summary(self) -> dict:
        """``{family: Addressing.as_dict()}`` for every family with anything set."""
        from sam.notify.kinds import families     # kinds imports base only; no cycle
        out = {}
        for family in families():
            addressing = self.addressing(family)
            if not addressing.is_empty:
                out[family] = addressing.as_dict()
        return out

    def sender_for(self, message) -> str:
        """The From a message leaves with: its own override, else ``MAIL_DEFAULT_FROM``."""
        return message.sender or self.mail_from

    @property
    def is_redirecting(self) -> bool:
        return bool(self.redirect_to)

    def summary(self) -> dict:
        """Config for the Admin -> Configuration card. **Never** secrets."""
        return {
            'enabled': self.enabled,
            'transport': self.transport,
            'relay': f'{self.mail_server}:{self.mail_port}',
            'use_tls': self.mail_use_tls,
            'mail_from': self.mail_from,
            'redirect_to': self.redirect_to or None,
            'bcc': ', '.join(self.bcc_addresses) or None,
            'addressing': self.addressing_summary(),
            'timeout': self.mail_timeout,
        }

    def resolve_recipient(self, address: str) -> tuple[str, Optional[str]]:
        """Apply ``NOTIFY_REDIRECT_TO``.

        Returns ``(address_to_use, intended_address_or_None)``. The second
        element is non-``None`` only when a redirect actually happened, which
        is exactly when ``notification_log.intended_recipient`` is set.
        """
        if self.is_redirecting and address != self.redirect_to:
            return (self.redirect_to, address)
        return (address, None)
