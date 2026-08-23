"""``XrasApiConfig`` — outbound XRAS credentials, from Flask config *or* the environment.

Follows ``sam/notify/config.py`` exactly, which in turn follows the
framework-agnostic seam at ``sam/caching/buckets.py:65-71``: try
``flask.current_app.config``, fall back to ``os.environ``. That is what lets
one client serve both the webapp and ``sam-admin`` without either importing
the other.

Read **per call**, never memoised at import: the webapp's config is not
readable at import time, and the scheduled task reads its environment once
per run by design.

Fail-closed
-----------
``XRAS_OUTGOING_ENABLED`` defaults **off** everywhere and ``helm/values.yaml``
pins it ``"0"`` visibly. With the lever off the card renders its unconfigured
state, the sweep records a skip, and the CLI degrades — nothing raises for
lack of a key. This mirrors ``XRAS_ACTIONS_CAPTURE_ONLY`` on the inbound side.

Two levers, not one
-------------------
``XRAS_OUTGOING_ENABLED`` governs *reading*. ``XRAS_WRITE_ENABLED`` governs the
admin client's *writes* and is a **second, independent** switch that defaults
off and is pinned ``"0"`` in ``helm/values.yaml``. Reading is on in production
today; writing must stay a separate, deliberate act, because the same key that
reads reports can merge one person into another irreversibly.

The write lever is webapp-only by design: ``cronjob-tasks.yaml`` never sets it,
so no scheduled task can write to XRAS even if one imported the admin client.

WARNING: ``XRAS_API_KEY`` is **not** ``SAM_XRAS_USER`` / ``SAM_XRAS_PASS``. Those are
XRAS's credential for calling *SAM* (a production write credential in the
inbound direction). This is SAM's credential for calling *XRAS*, and the same
key can create requests, merge people and modify roles — which is why
:class:`~sam.integration.xras_api.client.XrasApiClient` has no verb method
other than an internal ``_get``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

_TRUE = ('1', 'true', 'yes', 'on')

DEFAULT_BASE_URL = 'https://api.xras.org'
DEFAULT_ALLOCATIONS_PROCESS = 'NCAR'

#: The header value the existing operator scripts use. Outside ``/v1/requests``
#: it scopes nothing — the reports endpoints return process-wide data whatever
#: it says — but it is a required header on every call.
DEFAULT_API_USER = 'arcguest'

#: Seconds. Short on purpose: this can run inside an htmx round-trip, so a
#: slow XRAS must degrade to "source unavailable" rather than hold a worker.
DEFAULT_TIMEOUT = 10

DEFAULT_MAX_RETRIES = 3


def _raw(key: str, default: Any) -> Any:
    """Read a key from Flask app config if we are in an app context, else env.

    ``RuntimeError`` is what ``current_app`` raises outside an application
    context; ``ImportError`` covers a ``sam`` install with no Flask at all,
    which is a supported deployment (neither the CLI nor ``src/scheduling/``
    depends on Flask).
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
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class XrasApiConfig:
    """A snapshot of outbound-XRAS config, resolved at construction."""

    #: Master lever for *reads*. Fail-closed — see the module docstring.
    enabled: bool = False
    #: Second lever, for *writes* only. Independent of :attr:`enabled` and
    #: also fail-closed: a deployment that reads XRAS is not thereby allowed
    #: to mutate it.
    write_enabled: bool = False
    #: Third lever, for the **admin/review** XRAS contexts — the ones that can
    #: touch the Approved/Recommended stages. Phase 0.5 (2026-08-22) proved our
    #: current key grants only ``submit``+``report``, so ``review``/``admin``
    #: return 401 for every identity; the Approved-stage editors are built
    #: fail-visible and this lever stays **off** until a new admin/review-
    #: provisioned XRAS key lands. Flipping it on without that key just surfaces
    #: XRAS's 401 — it is the flip-point, not the fix.
    admin_context_enabled: bool = False
    api_key: str = ''
    base_url: str = DEFAULT_BASE_URL
    allocations_process: str = DEFAULT_ALLOCATIONS_PROCESS
    api_user: str = DEFAULT_API_USER
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES

    @classmethod
    def from_environment(cls) -> 'XrasApiConfig':
        """Build from Flask config or the environment, whichever is available."""
        return cls(
            enabled=_config_bool('XRAS_OUTGOING_ENABLED', False),
            write_enabled=_config_bool('XRAS_WRITE_ENABLED', False),
            admin_context_enabled=_config_bool('XRAS_ADMIN_CONTEXT_ENABLED',
                                               False),
            api_key=_config_str('XRAS_API_KEY', ''),
            base_url=(_config_str('XRAS_API_BASE', DEFAULT_BASE_URL)
                      or DEFAULT_BASE_URL).rstrip('/'),
            allocations_process=(_config_str('XRAS_ALLOCATIONS_PROCESS',
                                             DEFAULT_ALLOCATIONS_PROCESS)
                                 or DEFAULT_ALLOCATIONS_PROCESS),
            api_user=(_config_str('XRAS_API_USER', DEFAULT_API_USER)
                      or DEFAULT_API_USER),
            timeout=_config_int('XRAS_API_TIMEOUT', DEFAULT_TIMEOUT),
            max_retries=_config_int('XRAS_API_MAX_RETRIES', DEFAULT_MAX_RETRIES),
        )

    @property
    def configured(self) -> bool:
        """True when a call can actually be attempted.

        Both halves matter: a key with the lever off must stay silent, and
        the lever on with no key would fail at the first request instead of
        at the predicate.
        """
        return bool(self.enabled and self.api_key)

    @property
    def write_configured(self) -> bool:
        """True when a *write* may be attempted.

        PRIVILEGE(#11) — if XRAS ever issues the separately-scoped write
        credential on the ask register, this becomes its own key and the
        conjunction below is revisited.

        Three-way and deliberately conjunctive with :attr:`configured`: every
        write is followed by a verifying read, so a deployment that may write
        but may not read could not confirm its own effects. There is no
        write-without-read mode.
        """
        return bool(self.enabled and self.write_enabled and self.api_key)

    @property
    def admin_context_available(self) -> bool:
        """True when the Approved/Recommended-stage editors may actually write.

        Strictly narrower than :attr:`write_configured`: it additionally needs
        the ``admin_context_enabled`` lever, which stays off until the elevated
        XRAS key exists (Phase 0.5). With it off, the Approved editors render
        disabled with an explanation rather than firing a call that XRAS 401s.
        """
        return bool(self.write_configured and self.admin_context_enabled)

    def summary(self) -> Dict[str, Any]:
        """Config for the Admin -> Configuration card. **Never** the key.

        ``api_key_set`` is a boolean on purpose — an operator needs to know
        whether the ExternalSecret landed, and nothing more.
        """
        return {
            'enabled': self.enabled,
            'write_enabled': self.write_enabled,
            'admin_context_enabled': self.admin_context_enabled,
            'api_key_set': bool(self.api_key),
            'base_url': self.base_url,
            'allocations_process': self.allocations_process,
            'api_user': self.api_user,
            'timeout': self.timeout,
            'max_retries': self.max_retries,
            'configured': self.configured,
            'write_configured': self.write_configured,
            'admin_context_available': self.admin_context_available,
        }


def xras_api_configured(config: Optional[XrasApiConfig] = None) -> bool:
    """The cheap predicate callers branch on before building a client.

    Two layers, deliberately: this one lets the card, the CLI and the sweep
    choose a degraded path *without* constructing anything or catching
    anything; :meth:`XrasApiClient.from_environment` raising
    :class:`~sam.integration.xras_api.base.XrasApiNotConfigured` is the
    backstop for the paths that did not check.
    """
    return (config or XrasApiConfig.from_environment()).configured


def xras_write_configured(config: Optional[XrasApiConfig] = None) -> bool:
    """The cheap predicate the remediation card and its routes branch on.

    Same two-layer arrangement as :func:`xras_api_configured`: this lets the
    card render **disabled** controls with an explanation instead of hiding
    itself, while
    :meth:`~sam.integration.xras_api.admin_client.XrasAdminClient.from_environment`
    raising :class:`~sam.integration.xras_api.base.XrasWriteNotConfigured` is
    the backstop for any path that did not check.
    """
    return (config or XrasApiConfig.from_environment()).write_configured


def xras_admin_context_available(config: Optional[XrasApiConfig] = None) -> bool:
    """The predicate the Approved-stage editors branch on.

    Off until an admin/review-provisioned XRAS key lands (Phase 0.5). The
    Approved editors render disabled-with-reason while this is False; the
    client's per-call ``context='admin'`` is what flips them live on that day.
    """
    return (config or XrasApiConfig.from_environment()).admin_context_available
