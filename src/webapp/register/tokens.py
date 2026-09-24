"""Verification tokens for the public registration form.

Two salts on Flask's SECRET_KEY serializer: the PAGE token only identifies
the row to the "check your mail" page, the LINK token verifies it. A leaked
page URL therefore cannot confirm an address. The six-digit code is stored
as an HMAC of ``row id : code`` -- constant-time comparison, fixed width,
and a database read alone cannot validate a code. The brute-force bound is
the rate limit on the page, not hash cost, so no slow hash is wanted here.
The INVITE token is its own salt and carries the row's ``invite_sent_at``, so
a resend (which re-stamps it) invalidates every older link.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional, Tuple

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SALT_PAGE = 'account-verify-page'
SALT_LINK = 'account-verify-link'
SALT_INVITE = 'account-invite'


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=salt)


def _ttl_seconds() -> int:
    return int(current_app.config.get('ACCOUNT_VERIFY_TTL_HOURS', 48)) * 3600


def page_token(row_id: int) -> str:
    return _serializer(SALT_PAGE).dumps({'id': int(row_id)})


def link_token(row_id: int) -> str:
    return _serializer(SALT_LINK).dumps({'id': int(row_id)})


def _read(token: str, salt: str) -> Optional[int]:
    try:
        payload = _serializer(salt).loads(token, max_age=_ttl_seconds())
    except (SignatureExpired, BadSignature, TypeError, ValueError):
        return None
    row_id = payload.get('id') if isinstance(payload, dict) else None
    return int(row_id) if isinstance(row_id, int) else None


def read_page_token(token: str) -> Optional[int]:
    """The row a pending-page URL names, or None when bad or expired."""
    return _read(token, SALT_PAGE)


def read_link_token(token: str) -> Optional[int]:
    """The row a verification link confirms, or None when bad or expired."""
    return _read(token, SALT_LINK)


def invite_stamp(sent_at: datetime) -> str:
    """The ``sent`` binding: seconds only, which is what a DATETIME column keeps."""
    return sent_at.isoformat(timespec='seconds')


def invite_token(row_id: int, sent_at: datetime) -> str:
    return _serializer(SALT_INVITE).dumps({'id': int(row_id), 'sent': invite_stamp(sent_at)})


def read_invite_token(token: str) -> Tuple[Optional[int], Optional[str], str]:
    """``(row_id, sent, problem)``; ``problem`` is '', ``'expired'`` or ``'invalid'``."""
    max_age = int(current_app.config.get('ACCOUNT_INVITE_TTL_DAYS', 30)) * 86400
    try:
        payload = _serializer(SALT_INVITE).loads(token, max_age=max_age)
    except SignatureExpired:
        return None, None, 'expired'
    except (BadSignature, TypeError, ValueError):
        return None, None, 'invalid'
    row_id = payload.get('id') if isinstance(payload, dict) else None
    sent = payload.get('sent') if isinstance(payload, dict) else None
    if not isinstance(row_id, int) or not isinstance(sent, str):
        return None, None, 'invalid'
    return row_id, sent, ''


def new_code() -> str:
    return f'{secrets.randbelow(10 ** 6):06d}'


def code_hash(row_id: int, code: str) -> str:
    key = str(current_app.config['SECRET_KEY']).encode()
    return hmac.new(key, f'{int(row_id)}:{code.strip()}'.encode(), hashlib.sha256).hexdigest()


def code_matches(row, code: str, *, now: Optional[datetime] = None) -> bool:
    """Constant-time check of a typed code against the row, honoring expiry."""
    if not row.verify_code_hash or not row.verify_expires_at:
        return False
    if (now or datetime.now()) > row.verify_expires_at:
        return False
    return hmac.compare_digest(row.verify_code_hash,
                               code_hash(row.account_request_id, code or ''))
