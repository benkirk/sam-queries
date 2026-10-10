"""Wire kit for blueprints under ``/api/protected`` (legacy Java SAM's Spring-secured APIs).

Legacy answers every error as ``{"errorMessage": ...}`` in compact, declaration-ordered
JSON, and its 401 names the realm ``Realm`` (LWP sends a password only after that exact
challenge). Each blueprint builds its own ``login_or_token_required`` partial with
``deny=deny`` and calls :func:`register_protected_handlers`.
"""

from flask import current_app, request
from sqlalchemy.exc import DBAPIError
from werkzeug.exceptions import HTTPException

from webapp.api.helpers import compact_json

#: Spring's default Basic realm.
AUTH_REALM = 'Realm'


def json_response(payload, status: int = 200):
    """Compact JSON, never ``jsonify`` (which sorts keys and would lose legacy order)."""
    body = compact_json(payload, default=str)
    return current_app.response_class(body, status=status, mimetype='application/json')


def empty_response():
    """200 with no body: legacy's answer for a null result and for a purge."""
    return current_app.response_class(b'', status=200)


def error_response(status: int, message):
    return json_response({'errorMessage': message}, status=status)


def deny(status: int, message: str):
    resp = error_response(status, message)
    if status == 401:
        resp.headers['WWW-Authenticate'] = f'Basic realm="{AUTH_REALM}"'
    return resp


def register_protected_handlers(bp, *, label: str, five_hundred_text: str | None = None):
    """HTTP errors and crashes in the envelope. A 500 says *five_hundred_text* when given,
    else the exception type and message (a driver's text carries row values, so never that)."""

    @bp.errorhandler(HTTPException)
    def _http_error(error):
        return error_response(error.code, error.description)

    @bp.errorhandler(Exception)
    def _unexpected(error):
        current_app.logger.exception('%s %s %s failed', label, request.method, request.path)
        if five_hundred_text is not None:
            return error_response(500, five_hundred_text)
        detail = 'database error' if isinstance(error, DBAPIError) else str(error)
        return error_response(500, f'{type(error).__name__}: {detail}')
