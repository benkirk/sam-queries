"""Human verification (CAPTCHA) for the public registration form.

Turnstile, hCaptcha and reCAPTCHA share one protocol: a widget div plus one
script, and a server-side ``siteverify`` POST of ``secret`` + ``response``
returning JSON ``{"success": bool, "error-codes": [...]}``. A provider is
therefore a row of static facts in ``PROVIDERS`` and ``verify()`` is generic.
Selected by ``HUMAN_CHECK_PROVIDER`` (``none`` = off); keys arrive as
``HUMAN_CHECK_SITE_KEY`` / ``HUMAN_CHECK_SECRET_KEY``. Fails closed.
Operator setup: docs/plans/implemented/ACCOUNT_REGISTRATION.md 6.3.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

import requests
from flask import current_app

logger = logging.getLogger(__name__)

SITEVERIFY_TIMEOUT = 5


class Provider(NamedTuple):
    script: str           # widget loader, <script src> on the form page
    verify_url: str       # server-side siteverify endpoint
    response_field: str   # form field the widget writes its token into
    widget_class: str     # class of the div the loader renders into
    csp: dict             # directive -> origin the widget needs


_CLOUDFLARE = 'https://challenges.cloudflare.com'

PROVIDERS = {
    'turnstile': Provider(
        script=f'{_CLOUDFLARE}/turnstile/v0/api.js',
        verify_url=f'{_CLOUDFLARE}/turnstile/v0/siteverify',
        response_field='cf-turnstile-response',
        widget_class='cf-turnstile',
        csp={'script-src': _CLOUDFLARE, 'frame-src': _CLOUDFLARE},
    ),
}

#: Legal HUMAN_CHECK_PROVIDER values.
CHOICES = ('none', *PROVIDERS)


def provider_name(config) -> str:
    return (config.get('HUMAN_CHECK_PROVIDER') or 'none').strip().lower()


def provider(config=None) -> Optional[Provider]:
    """The configured provider, or None when the check is off."""
    return PROVIDERS.get(provider_name(config if config is not None else current_app.config))


def csp_sources(config) -> dict:
    p = provider(config)
    return dict(p.csp) if p else {}


def _siteverify(url: str, data: dict) -> dict:
    """The one outbound call; tests replace this function."""
    resp = requests.post(url, data=data, timeout=SITEVERIFY_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def verify(form) -> Optional[str]:
    """None when the posted form passes (or the check is off), else the
    message to show the visitor."""
    p = provider()
    if p is None:
        return None
    token = (form.get(p.response_field) or '').strip()
    if not token:
        return 'Please complete the verification below and submit again.'
    # No remoteip: the ingress collapses every client to one address, and a
    # wrong IP is worse than none.
    try:
        result = _siteverify(p.verify_url, {
            'secret': current_app.config.get('HUMAN_CHECK_SECRET_KEY', ''),
            'response': token,
        })
    except (requests.RequestException, ValueError) as exc:
        logger.error('human check: siteverify unreachable (%s); failing closed', exc)
        return ('The verification service is unavailable right now. '
                'Please try again in a few minutes.')
    if not result.get('success'):
        logger.warning('human check: rejected, error-codes=%s', result.get('error-codes'))
        return 'Verification failed. Please complete the check again and resubmit.'
    return None
