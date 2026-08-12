"""``NotifyConfig`` — one config object, readable from Flask *or* the environment.

This follows the framework-agnostic seam established at
``sam/caching/buckets.py:65-71``: ``try: flask.current_app.config`` /
``except RuntimeError: os.environ``. That is the only pattern in ``sam/`` that
reads Flask config, and it exists for exactly this case — a core library that
must behave identically under ``sam-admin`` and under the webapp.

It also collapses a duplication. ``MAIL_*`` had **two** sources of truth:
``src/config.py:31-37`` (``SAMConfig``, inherited into ``app.config`` via
``SAMWebappConfig``) and ``src/cli/core/context.py``, which re-read the same
six vars off ``os.getenv`` with the same defaults and so never honoured a
``SAMConfig`` change. ``NotifyConfig`` replaces both.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 2.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

_TRUE = ('1', 'true', 'yes', 'on')


def _raw(key: str, default: Any) -> Any:
    """Read a key from Flask app config if we are in an app context, else env.

    ``RuntimeError`` is what ``current_app`` raises outside an application
    context; ``ImportError`` covers a ``sam`` install with no Flask at all,
    which is a supported deployment (the CLI does not depend on Flask).
    """
    try:
        from flask import current_app
        return current_app.config.get(key, os.environ.get(key, default))
    except (RuntimeError, ImportError):
        return os.environ.get(key, default)


def _config_str(key: str, default: str = '') -> str:
    value = _raw(key, default)
    return '' if value is None else str(value).strip()


def _config_bool(key: str, default: bool = False) -> bool:
    value = _raw(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def _config_int(key: str, default: int) -> int:
    value = _raw(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
            enabled=_config_bool('NOTIFY_ENABLED', False),
            transport=_config_str('NOTIFY_TRANSPORT', 'smtp') or 'smtp',
            redirect_to=_config_str('NOTIFY_REDIRECT_TO', ''),
            bcc=_config_str('NOTIFY_BCC', ''),
            queued_stale_seconds=_config_int('NOTIFY_QUEUED_STALE_SECONDS', 300),
            mail_server=_config_str('MAIL_SERVER', 'ndir.ucar.edu'),
            mail_port=_config_int('MAIL_PORT', 25),
            # Defaults true: § 9 measured STARTTLS working on ndir.ucar.edu,
            # the one relay both consumers use. src/config.py agrees.
            mail_use_tls=_config_bool('MAIL_USE_TLS', True),
            # Kept for a future relay that wants them. ndir advertises no
            # AUTH (§ 9), so login is skipped there whatever these say.
            mail_username=_config_str('MAIL_USERNAME', ''),
            mail_password=_config_str('MAIL_PASSWORD', ''),
            mail_from=_config_str('MAIL_DEFAULT_FROM', 'sam-admin@ucar.edu'),
            mail_timeout=_config_int('MAIL_TIMEOUT', 10),
        )

    @property
    def bcc_addresses(self) -> list[str]:
        """``NOTIFY_BCC`` as a list — it accepts a comma-separated string."""
        return [a.strip() for a in self.bcc.split(',') if a.strip()]

    @property
    def is_redirecting(self) -> bool:
        return bool(self.redirect_to)

    def summary(self) -> dict:
        """Config for the Admin → Configuration card. **Never** secrets."""
        return {
            'enabled': self.enabled,
            'transport': self.transport,
            'relay': f'{self.mail_server}:{self.mail_port}',
            'use_tls': self.mail_use_tls,
            'mail_from': self.mail_from,
            'redirect_to': self.redirect_to or None,
            'bcc': ', '.join(self.bcc_addresses) or None,
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
