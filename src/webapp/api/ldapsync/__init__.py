"""LDAP sync API -- legacy Java SAM's ``/api/protected/admin/{ldapsync,*Purge*,userlifecycle}``.

**LEGACY-COMPAT BLUEPRINT.** ``sam-ldap-syncd`` on sam-app is the sole caller;
cutover and rollback are both its ``SAM_URL``. Contract, legacy behavior and the
deliberate deviations: ``docs/plans/LDAP_SYNC_API.md``. The four rules the daemon
cannot survive breaking: the 401 realm is literally ``Realm``; a mapped path never
404s (it retries a 404 forever, silently); every 2xx GET/PUT body is JSON; a
rejected record is lost until its next full reload, so reject only what is wrong.
"""

import json
from functools import partial

from flask import Blueprint, current_app, request
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException

from sam.manage.ldapsync import SyncValidationError
from webapp.utils.api_auth import login_or_token_required

bp = Blueprint('api_ldapsync', __name__)

#: Legacy's ``/api/protected/admin/**`` chain requires ``hasRole('ROLE_API_ADMIN')``.
LDAPSYNC_ROLE = 'ROLE_API_ADMIN'

#: LWP sends the password only after a 401 naming this exact realm (Spring's default).
AUTH_REALM = 'Realm'


def json_response(payload, status: int = 200):
    """Compact JSON, never ``jsonify`` (which sorts keys and would lose legacy order)."""
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype='application/json')


def empty_response():
    """200 with no body: legacy's answer for a null result and for a purge."""
    return current_app.response_class(b'', status=200)


def error_response(status: int, message):
    return json_response({'errorMessage': message}, status=status)


def validation_message(messages) -> str:
    """Legacy ``ValidationException.getMessage()``: a header line, then each message."""
    return 'ValidationException:' + ''.join(f'\n {m}' for m in messages)


def _deny(status: int, message: str):
    resp = error_response(status, message)
    if status == 401:
        resp.headers['WWW-Authenticate'] = f'Basic realm="{AUTH_REALM}"'
    return resp


ldapsync_api_required = partial(
    login_or_token_required, roles=(LDAPSYNC_ROLE,), deny=_deny,
)


def _flatten(messages, path=()):
    """marshmallow's nested error dict as ``field.sub: message`` lines."""
    if isinstance(messages, dict):
        for key, value in messages.items():
            yield from _flatten(value, path if key == '_schema' else path + (str(key),))
    elif isinstance(messages, list):
        for value in messages:
            yield from _flatten(value, path)
    else:
        yield f"{'.'.join(path)}: {messages}" if path else str(messages)


@bp.errorhandler(SyncValidationError)
def _sync_invalid(error):
    current_app.logger.warning('ldapsync rejected %s %s: %s',
                               request.method, request.path, error)
    return error_response(400, validation_message(error.messages))


@bp.errorhandler(ValidationError)
def _schema_invalid(error):
    messages = list(_flatten(error.messages))
    current_app.logger.warning('ldapsync rejected %s %s: %s',
                               request.method, request.path, messages)
    return error_response(400, validation_message(messages))


@bp.errorhandler(HTTPException)
def _http_error(error):
    """Keep every HTTP error in the envelope; a malformed JSON body is a 400 here (legacy: 500)."""
    return error_response(error.code, error.description)


@bp.errorhandler(Exception)
def _unexpected(error):
    current_app.logger.exception('ldapsync %s %s failed', request.method, request.path)
    return error_response(500, f'{type(error).__name__}: {error}')


def read_json_body():
    """The request's JSON body; a missing or malformed body raises 400."""
    data = request.get_json(force=True, silent=True)
    if data is None:
        raise SyncValidationError('Request body is not valid JSON.')
    return data


# Route modules attach to `bp` on import, so they come last.
from . import sync as _sync  # noqa: E402,F401

__all__ = ['bp', 'ldapsync_api_required', 'LDAPSYNC_ROLE', 'AUTH_REALM']
