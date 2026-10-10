"""HEUV API -- legacy Java SAM's ``/api/protected/heuv/v1`` researcher-portal feed.

**LEGACY-COMPAT BLUEPRINT.** Same path, auth and bytes as legacy, so each caller
repoints by changing the host. Read-only; the three PUTs and the heavy reports are
not ported. Contract, deliberate fixes and traffic: ``docs/apis/HEUV_API.md``.
A 400 is ``{"errorMessage": "<javaMethod>.<param>: <text>"}`` echoing the value as sent.
"""

from functools import partial

from flask import Blueprint, current_app

from webapp.api.protected import deny, error_response, json_response, register_protected_handlers
from webapp.utils.api_auth import login_or_token_required

bp = Blueprint('api_heuv', __name__)

#: Legacy's ``/api/protected/heuv/**`` chain requires ``hasRole('ROLE_API_HEUV')``.
HEUV_ROLE = 'ROLE_API_HEUV'

heuv_api_required = partial(login_or_token_required, roles=(HEUV_ROLE,), deny=deny)

register_protected_handlers(bp, label='heuv', five_hundred_text='Internal server error.')


class HeuvBadRequest(Exception):
    def __init__(self, prefix: str, text: str):
        super().__init__(f'{prefix}: {text}')


@bp.errorhandler(HeuvBadRequest)
def _bad_request(error):
    return error_response(400, str(error))


def found_or_400(value, prefix: str, text: str):
    if value is None:
        raise HeuvBadRequest(prefix, text)
    return value


def dump(schema, obj):
    return json_response(schema.dump(obj))


def not_found():
    """Legacy's bare 404: no body, no content type."""
    resp = current_app.response_class(b'', status=404)
    del resp.headers['Content-Type']
    return resp


def parse_flag(raw):
    """``true``/``false`` in any case; anything else (or absent) means no filter."""
    value = (raw or '').lower()
    return {'true': True, 'false': False}.get(value)


# Route modules attach to `bp` on import, so they come last.
from . import user as _user  # noqa: E402,F401
from . import project as _project  # noqa: E402,F401
from . import report as _report  # noqa: E402,F401
from . import access as _access  # noqa: E402,F401

__all__ = ['bp', 'heuv_api_required', 'HEUV_ROLE']
