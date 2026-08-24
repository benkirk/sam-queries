"""The catch-all: turn a request for an unmapped ``/api/xras/*`` path into a record.

An unmapped path under this prefix otherwise produces Werkzeug's default HTML
404 and leaves no trace -- not a log line, not a row, nothing on the operator
dashboard. That is how ``POST /v1/roles/...``, a *write*, went unported for an
entire build; it took an audit of the deployed ``ROOT.war`` to find. Any
authenticated request under ``/api/xras/`` matching no other rule lands here,
gets a row at ``status='unmapped'`` and a WARN, so "did XRAS start calling
something new?" is a filter on a page operators already watch.

Three things are load-bearing:

**A route, not an errorhandler.** ``@bp.errorhandler(404)`` cannot do this: a
genuinely unmatched URL never reaches a view, so ``request.blueprint`` is None
and Flask dispatches to the app's handler, of which there is none for 404. The
blueprint's handler fires only for ``abort(404)`` inside a matched view.
Registering a real rule is also what buys the ``XA-`` header shim and auth,
since ``before_request`` likewise runs only for a matched blueprint.

**Behind auth, deliberately.** An unauthenticated caller gets the 41-byte 401
and writes NO row. Only an authenticated caller can be XRAS; without the gate
every internet scanner probing ``/api/xras/v1/wp-admin`` would mint audit rows.

**A row AND a log line.** Pod logs in k8s are ephemeral, so the row is the
durable half and the log line is the half that shows up in an incident tail.
"""

from flask import current_app, request

from webapp.extensions import csrf

from . import actions, bp, xras_api_required
from .serialize import xras_response

#: Every verb worth answering. ``HEAD`` and ``OPTIONS`` are added by Werkzeug.
#:
#: This is what makes a wrong-verb call on a *mapped* path visible too: legacy's
#: ``/v1/roles`` is ``@PostMapping``, so a ``DELETE`` there 404s silently today. Here it
#: lands in this module and is recorded — which matters because the ACCESS spec documents
#: a ``DELETE /v1/roles/…`` for revocations that legacy never implemented. If XRAS ever
#: starts sending one, we find out from a row rather than from a user asking why their
#: co-PI is still on a project.
_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']


@bp.route('/', defaults={'unmatched': ''}, methods=_METHODS)
@bp.route('/<path:unmatched>', methods=_METHODS)
@csrf.exempt          # token path is Basic-auth (no cookies); actions.py precedent
@xras_api_required()
def unmapped_path(unmatched):
    """Record an authenticated request for a path this blueprint does not implement.

    Two rules because ``<path:>`` will not match the bare prefix — ``/api/xras/v1/``
    and ``/api/xras/v1`` both need to arrive somewhere.

    Returns the envelope 404 rather than Werkzeug's HTML. That changes response bytes
    only for paths that are currently unmapped, of which production saw **zero requests
    in 58 days** — and legacy's own bytes there are Tomcat HTML that §7 already records
    as something we decline to reproduce.
    """
    detail = f'{request.method} {request.path}'

    # The full request line plus the body: raw_payload is NOT NULL, and for this row it
    # is the only place the query string and any body survive. `_record` truncates.
    body = request.get_data(as_text=True)
    raw_payload = f'{request.method} {request.full_path}\n\n{body}'

    log_id = actions._record(
        status='unmapped',
        raw_payload=raw_payload,
        http_status=404,
    )
    # `_record` takes no `outcome_reason` — it is a terminal-state column, so it arrives
    # through `_finish` exactly as the dispatch arms of `post_action` set it. The status
    # is re-asserted because `_finish` requires one; it does not change.
    actions._finish(log_id, status='unmapped', outcome_reason=detail, http_status=404)

    current_app.logger.warning(
        'XRAS called an unmapped path: id=%s %s', log_id, detail)

    return xras_response(message=f'no route for {detail}', status=404)
