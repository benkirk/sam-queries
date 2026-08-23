"""
Wire format for the XRAS API — the single place response bytes are decided.

This is a **legacy-compat** surface (see the package docstring): the XRAS broker
receives byte-identical responses from the Java implementation and this one, so
serialization is a contract, not a detail.

Why not ``jsonify``
-------------------

Flask's ``DefaultJSONProvider`` cannot produce these bytes. It sorts keys
alphabetically, appends a trailing newline, and selects separators from
``app.debug`` — so ``DevelopmentConfig`` and ``ProductionConfig`` already emit
*different* bytes from the same call. Legacy emits Java field-declaration order,
compact separators, no trailing newline, and raw UTF-8 (the 3.8 MB roster carries
78 non-ASCII bytes and zero ``\\uXXXX`` escapes).

The envelope is a flag, not a fork
----------------------------------

Endpoints 1-2 (``/people``, ``/people/{username}``) return bare payloads;
endpoints 3-6 wrap in ``{"message": ..., "result": ...}``. That asymmetry comes
from the Java controllers — ``IdentityServiceController`` returns the DTO raw
while the others ``return new ResponseWrapper(response)``. Rather than two code
paths, the envelope is a keyword argument, so re-standardizing the outliers later
is deleting one argument.

Null handling is per-DTO
------------------------

There is deliberately **no** global "drop nulls" pass. The Java mapper is
unconfigured, so its inclusion is ``ALWAYS``, and ``NON_NULL`` is applied per
class by ``@JsonSerialize``. Four classes omit nulls; the rest emit them:

===========================  ==========================================
emits ``null``               ``ResponseWrapper.message``,
                             ``AccountingRequestResponse.projectIdLabel``,
                             ``RequestDatesDTO.requestEndDate``
omits the key entirely       ``PersonDTO``, ``Request``, ``Allocation``,
                             ``Action``
===========================  ==========================================

DTO builders therefore call :func:`omit_none` explicitly where the Java class
carries the annotation, and not otherwise. Note the empty string is *emitted* —
``"" is not null`` (one roster email proves it).
"""

import json
from typing import Any, Mapping, Optional

from flask import Response

#: Legacy content type for every 2xx/4xx JSON body on this surface. Note the
#: absence of a charset: Jackson's ``MappingJackson2HttpMessageConverter``
#: emits a bare ``application/json`` for controller returns.
MIMETYPE = 'application/json'

#: The unauthenticated 401, verified byte-for-byte against production with
#: ``od -c``: exactly 41 bytes, and the only pretty-printed body on the surface
#: (Jackson's ``DefaultPrettyPrinter``, hence the space *before* each colon).
#: Emitted as a literal rather than through :func:`compact` precisely because it
#: is the one response that does not share the compact format.
#:
#: It also carries a charset, unlike every other body here, and deliberately no
#: ``WWW-Authenticate`` header — see ``XrasAuthenticationEntryPoint``'s javadoc.
UNAUTHENTICATED_BODY = '{\n  "message" : null,\n  "result" : null\n}'
UNAUTHENTICATED_MIMETYPE = 'application/json;charset=UTF-8'


def omit_none(mapping: Mapping[str, Any]) -> dict:
    """Drop keys whose value is ``None``, preserving insertion order.

    Models a Java DTO annotated ``@JsonSerialize(include=NON_NULL)``. Apply it
    only to the four classes that carry that annotation — using it everywhere
    would swallow the ``null``s legacy genuinely emits.

    Empty strings, empty lists and zero are kept: Jackson's ``NON_NULL`` tests
    for null, not for emptiness.
    """
    return {k: v for k, v in mapping.items() if v is not None}


def compact(payload: Any) -> str:
    """Render *payload* exactly as the legacy Jackson mapper would.

    ``sort_keys=False`` preserves the caller's insertion order, which the DTO
    builders set to match Java field-declaration order; ``ensure_ascii=False``
    keeps UTF-8 raw; the separators drop every space.
    """
    return json.dumps(
        payload, separators=(',', ':'), ensure_ascii=False, sort_keys=False,
    )


def xras_response(
    result: Any = None,
    *,
    message: Optional[str] = None,
    status: int = 200,
    envelope: bool = True,
) -> Response:
    """Build an XRAS response.

    Args:
        result:   the payload. Under ``envelope=True`` it becomes the ``result``
                  member; otherwise it *is* the body.
        message:  the envelope's ``message`` member. Always emitted, ``null``
                  included — ``ResponseWrapper`` carries no ``NON_NULL``.
        status:   HTTP status.
        envelope: ``False`` for ``/people`` and ``/people/{username}``, the only
                  two endpoints Java returns unwrapped.
    """
    payload = {'message': message, 'result': result} if envelope else result
    return Response(compact(payload), status=status, mimetype=MIMETYPE)


def unauthenticated() -> Response:
    """The byte-exact legacy 401 (41 bytes, no ``WWW-Authenticate``)."""
    return Response(
        UNAUTHENTICATED_BODY, status=401, mimetype=UNAUTHENTICATED_MIMETYPE,
    )


def empty_ok() -> Response:
    """The 200 that carries nothing at all — ``POST /v1/roles``'s success.

    ``BaseController.createOkResponse()`` is ``new ResponseEntity(HttpStatus.OK)``:
    no body, and — because Spring never invokes a message converter — **no**
    ``Content-Type`` header either. Every other response on this surface has one,
    which is why this cannot go through :func:`xras_response`.

    Flask will not produce that on its own. ``Response('')`` applies the app's
    ``default_mimetype`` (``text/html; charset=utf-8``), so the header is popped
    explicitly after construction. ``tests/api/test_xras_roles.py`` pins both the
    zero length and the header's absence.
    """
    response = Response('', status=200)
    response.headers.pop('Content-Type', None)
    return response
