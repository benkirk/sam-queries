"""LDAP sync API -- legacy Java SAM's ``/api/protected/admin/{ldapsync,*Purge*,userlifecycle}``.

**LEGACY-COMPAT BLUEPRINT.** ``sam-ldap-syncd`` on sam-app is the sole caller;
cutover and rollback are both its ``SAM_URL``. Contract, legacy behavior and the
deliberate deviations: ``docs/plans/LDAP_SYNC_API.md``. The four rules the daemon
cannot survive breaking: the 401 realm is literally ``Realm``; a mapped path never
404s (it retries a 404 forever, silently); every 2xx GET/PUT body is JSON; a
rejected record is lost until its next full reload, so reject only what is wrong.
"""

from functools import partial

from flask import Blueprint, current_app, request
from marshmallow import ValidationError

from sam.manage.ldapsync import SyncValidationError
from webapp.api.helpers import flatten_errors
from webapp.api.protected import (  # noqa: F401  (route modules import these from here)
    AUTH_REALM, deny, empty_response, error_response, json_response,
    register_protected_handlers,
)
from webapp.utils.api_auth import login_or_token_required

bp = Blueprint('api_ldapsync', __name__)

#: Legacy's ``/api/protected/admin/**`` chain requires ``hasRole('ROLE_API_ADMIN')``.
LDAPSYNC_ROLE = 'ROLE_API_ADMIN'


def validation_message(messages) -> str:
    """Legacy ``ValidationException.getMessage()``: a header line, then each message."""
    return 'ValidationException:' + ''.join(f'\n {m}' for m in messages)


ldapsync_api_required = partial(
    login_or_token_required, roles=(LDAPSYNC_ROLE,), deny=deny,
)

# A malformed JSON body is a 400 here (legacy: 500).
register_protected_handlers(bp, label='ldapsync')


@bp.errorhandler(SyncValidationError)
def _sync_invalid(error):
    current_app.logger.warning('ldapsync rejected %s %s: %s',
                               request.method, request.path, error)
    return error_response(400, validation_message(error.messages))


@bp.errorhandler(ValidationError)
def _schema_invalid(error):
    messages = flatten_errors(error.messages, keep_schema_key=False)
    current_app.logger.warning('ldapsync rejected %s %s: %s',
                               request.method, request.path, messages)
    return error_response(400, validation_message(messages))


def parse_int(raw, message: str) -> int:
    """An id from the path or query as an int; anything else is a 400 with *message*."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SyncValidationError(message)


def read_json_body():
    """The request's JSON body; a missing or malformed body raises 400."""
    data = request.get_json(force=True, silent=True)
    if data is None:
        raise SyncValidationError('Request body is not valid JSON.')
    return data


# Route modules attach to `bp` on import, so they come last.
from . import sync as _sync  # noqa: E402,F401
from . import purge as _purge  # noqa: E402,F401
from . import lifecycle as _lifecycle  # noqa: E402,F401

__all__ = ['bp', 'ldapsync_api_required', 'LDAPSYNC_ROLE', 'AUTH_REALM', 'parse_int']
