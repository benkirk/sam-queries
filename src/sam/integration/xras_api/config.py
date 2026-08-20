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

⚠️ ``XRAS_API_KEY`` is **not** ``SAM_XRAS_USER`` / ``SAM_XRAS_PASS``. Those are
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

    #: Master lever. Fail-closed — see the module docstring.
    enabled: bool = False
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

    def summary(self) -> Dict[str, Any]:
        """Config for the Admin → Configuration card. **Never** the key.

        ``api_key_set`` is a boolean on purpose — an operator needs to know
        whether the ExternalSecret landed, and nothing more.
        """
        return {
            'enabled': self.enabled,
            'api_key_set': bool(self.api_key),
            'base_url': self.base_url,
            'allocations_process': self.allocations_process,
            'api_user': self.api_user,
            'timeout': self.timeout,
            'max_retries': self.max_retries,
            'configured': self.configured,
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
