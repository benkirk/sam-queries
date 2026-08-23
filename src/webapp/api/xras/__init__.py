"""
XRAS integration API — a Python reimplementation of legacy Java SAM's
``/api/xras/*`` surface (deployed build ``2.0.3``).

**LEGACY-COMPAT BLUEPRINT — DO NOT REFACTOR.** Like ``api/v1/queue.py`` and
``api/v1/wallclock_exemption.py``, this blueprint intentionally reproduces a
legacy Java wire contract byte for byte. The XRAS broker at
https://admin-ncar.xras.org/ is the sole caller and pulls identity and request
data from these endpoints; response bytes must not change. Additive changes only.

Endpoints — the whole of legacy's mapped surface::

    GET  /api/xras/v1/people                            bare array,  no envelope
    GET  /api/xras/v1/people/{username}                 bare object, no envelope
    GET  /api/xras/v1/requests/request/{requestNumber}  {message, result}
    GET  /api/xras/v1/requests/user/{username}          {message, result}
    GET  /api/xras/v1/requests/role/{role}/{username}   {message, result}
    GET  /api/xras/v1/dates/requests/{requestNumbers}   {message, result}
    POST /api/xras/v1/actions                           {message, result}
    POST /api/xras/v1/roles/{requestNumber}/{role}/{username}   empty body

...plus a catch-all (:mod:`webapp.api.xras.unmapped`) that turns a request for
anything else under this prefix into an ``xras_action_log`` row. It exists because
``/v1/roles`` went unported for a whole build and nothing surfaced it: an unmapped
path left no trace, so only an audit of the deployed ``ROOT.war`` could find it.

Serialization — including why ``jsonify`` is unusable here and why null handling
is per-DTO rather than global — lives in :mod:`webapp.api.xras.serialize`.

Deliberate divergences from legacy, and their reasons, are recorded in
``docs/xras/incoming/XRAS_REIMPLEMENTATION.md`` section 7. In short: we do not reproduce
the 431-byte Tomcat HTML error pages, we answer an unrecognised ``{role}`` with
400 rather than 500, and we sort ``masters[]`` by projcode rather than emulating
Java ``HashMap`` bucket order.
"""

import base64
from functools import partial

from flask import Blueprint, request

from webapp.utils.api_auth import login_or_token_required

from .serialize import unauthenticated, xras_response

bp = Blueprint('api_xras', __name__)

#: Legacy's authority string is literally ``ROLE_XRAS`` — the ``/api/xras/**``
#: security chain uses a plain ``RoleVoter`` with ``use-expressions="false"``,
#: so no prefix is added and the DB ``role.name`` is compared verbatim.
XRAS_ROLE = 'ROLE_XRAS'

_XA_REQUESTER = 'HTTP_XA_REQUESTER'
_XA_API_KEY = 'HTTP_XA_API_KEY'


def _deny(status: int, message: str):
    """Error bodies for the XRAS wire contract.

    401 is the byte-exact legacy literal. 403 is a **deliberate divergence**:
    legacy answers a valid credential lacking ``ROLE_XRAS`` with 431 bytes of
    Tomcat HTML, because Spring's default ``AccessDeniedHandlerImpl`` calls
    ``sendError(403)`` and ``web.xml`` declares no ``<error-page>``. That is a
    servlet-container artifact no real client has ever received on a mapped
    path, so we return the JSON envelope instead.
    """
    if status == 401:
        return unauthenticated()
    return xras_response(message=message, status=status)


#: XRAS routes are API-key-only and must carry ``ROLE_XRAS``. ``roles=`` closes
#: the session path for us (a browser session holds no API-key roles), so this
#: alias is the whole of the blueprint's auth.
xras_api_required = partial(
    login_or_token_required, roles=(XRAS_ROLE,), deny=_deny,
)


@bp.before_request
def _translate_xa_headers():
    """Reproduce ``XrasAuthenticationFilter``.

    The real broker authenticates with ``XA-REQUESTER`` / ``XA-API-KEY`` rather
    than an ``Authorization`` header, and legacy translates the pair into Basic
    before the security chain runs. The rules, in order:

    1. If **neither** XA header is present, pass through untouched.
    2. Otherwise, synthesize ``Authorization: Basic base64(requester:apikey)``
       — but only if ``Authorization`` is absent *and* **both** XA headers are
       present. An explicit ``Authorization`` header always wins.
    3. **Unconditionally remove both XA headers**, whichever branch was taken.

    Rule 3 is the subtle one: supplying only one XA header strips it without
    synthesizing anything, so the request arrives unauthenticated and 401s. That
    is legacy behavior and the case worth testing.

    Werkzeug exposes ``request.headers`` as an immutable view over the WSGI
    environ, so the rewrite happens on ``request.environ`` — which is what
    ``request.authorization`` reads from anyway.
    """
    env = request.environ
    requester = env.get(_XA_REQUESTER)
    api_key = env.get(_XA_API_KEY)

    if requester is None and api_key is None:
        return None

    if 'HTTP_AUTHORIZATION' not in env and requester and api_key:
        token = base64.b64encode(
            f'{requester}:{api_key}'.encode('utf-8')
        ).decode('ascii')
        env['HTTP_AUTHORIZATION'] = f'Basic {token}'

    env.pop(_XA_REQUESTER, None)
    env.pop(_XA_API_KEY, None)
    return None


@bp.errorhandler(404)
def _not_found(error):
    """404s carry a message the caller chose, in the envelope.

    Legacy has two distinct wordings — ``username=<u> not found`` from
    ``/people/{u}`` and ``User <u> not found`` from the ``requests/*`` family —
    so the description is passed through verbatim rather than hardcoded here.
    (Contrast ``webapp.api.helpers.register_error_handlers``, which discards it.)
    """
    return xras_response(message=getattr(error, 'description', None), status=404)


@bp.errorhandler(400)
def _bad_request(error):
    return xras_response(message=getattr(error, 'description', None), status=400)


@bp.errorhandler(409)
def _conflict(error):
    """409 is ours — legacy has no state-conflict code on this surface.

    ``POST /v1/roles`` uses it for "the project/user exists but is inactive".
    Legacy reaches 403 there, via ``XrasController`` mapping ``BadStateException``
    to ``FORBIDDEN`` — an authorization verdict about the *caller*, which on an
    endpoint behind Basic auth is indistinguishable from a bad API key. §7 records
    the divergence; :mod:`webapp.api.xras.roles` records the reasoning.
    """
    return xras_response(message=getattr(error, 'description', None), status=409)


@bp.errorhandler(422)
def _unprocessable(error):
    """422 is new with ``POST /actions`` — legacy 500s where we report the error list.

    The shared ``webapp.api.helpers.register_error_handlers`` has no 422 or 500
    handler, so these are blueprint-local like the two above.
    """
    return xras_response(message=getattr(error, 'description', None), status=422)


@bp.errorhandler(500)
def _server_error(error):
    """Keep an unexpected failure inside the envelope rather than leaking HTML.

    Legacy answers 431 bytes of Tomcat HTML here because ``web.xml`` declares no
    ``<error-page>``; §7 records not reproducing that as a deliberate divergence.
    """
    return xras_response(message='Internal error processing request', status=500)


# Route modules attach to `bp` on import, so they come last — `bp`,
# `xras_api_required` and the serializer must all exist before they run.
from . import people  # noqa: E402,F401
from . import requests as _requests  # noqa: E402,F401
from . import actions as _actions  # noqa: E402,F401
from . import roles as _roles  # noqa: E402,F401
# Last, and deliberately: it registers the `<path:>` catch-all. Werkzeug sorts by
# specificity rather than registration order, so this is belt-and-braces — the
# actual guard is `test_xras_unmapped.py::test_no_mapped_rule_is_shadowed`.
from . import unmapped as _unmapped  # noqa: E402,F401

__all__ = ['bp', 'xras_api_required', 'XRAS_ROLE']
