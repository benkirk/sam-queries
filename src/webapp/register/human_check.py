"""Human-verification for the public registration gate -- a STUB.

The gate (ACCOUNT_REGISTRATION_GATE_ENABLED) requires a human check before the
open input fields are reachable. This module is the seam a real challenge
(Cloudflare Turnstile, hCaptcha, reCAPTCHA) drops into: ``issue()`` mints the
value the widget/stub carries, ``verify()`` decides server-side. Today it is a
same-origin nonce signed on SECRET_KEY -- proof the challenge was served by us
and used inside its window, paired with the gate form's "I'm not a robot" box.

Wiring a real provider replaces ``verify()`` with a call to the provider's
siteverify endpoint AND adds its ``script-src``/``connect-src`` origins to
webapp/utils/csp.py (the stub stays same-origin so it needs no CSP change).
That real challenge, plus real client-IP forwarding, is the precondition in
docs/plans/implemented/ACCOUNT_REGISTRATION.md 6.1 before a prod switch-on.
"""

from __future__ import annotations

from typing import Optional

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SALT = 'register-human-check'
#: How long an issued challenge stays acceptable (a filled gate, not a session).
MAX_AGE_SECONDS = 30 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=SALT)


def issue() -> str:
    """A signed token to embed in the gate; the real widget's response field."""
    return _serializer().dumps({'v': 1})


def verify(token: Optional[str]) -> bool:
    """True when the token is one we issued, inside its window. The real
    provider check swaps in here (response token -> siteverify)."""
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=MAX_AGE_SECONDS)
    except (SignatureExpired, BadSignature, TypeError, ValueError):
        return False
    return True
