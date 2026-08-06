"""``POST /api/xras/v1/actions`` — the only writing surface on the XRAS integration.

This slice ships the endpoint **capture-only**: it authenticates, parses, audits and
returns 200, dispatching nothing. That is deliberate and it is the point — the audit
row is what turns every subsequent production post into a harvested payload, so it
wants to be in front of the handlers rather than behind them. Handlers are enabled one
at a time by ``XRAS_ACTIONS_CAPTURE_ONLY``.

Order of operations, and the part that is not negotiable
--------------------------------------------------------

::

    read raw body
      │
      ├─ JSON parse fails ──> write row (status='failed', action_type=NULL) ──> 400
      ├─ schema rejects   ──> write row (status='failed')                   ──> 422
      │
      ├─ write row (status='received')          ← BEFORE dispatch
      │
      └─ dispatch
             ├─ capture-only    → row stays 'received'                      → 200
             ├─ success         → 'processed' + projcode_result             → 200
             ├─ validation errs → 'failed' + error_messages                 → 422
             └─ no serviceable  → 'manual'                                  → 200

Legacy's only record of an action is an email and its only replay mechanism is pasting
JSON into a form, so a row written *only on success* would be a success log rather than
an audit trail. Persisting before dispatch is what makes replay possible when a handler
explodes.

⚠️  **The row is committed on its own connection, outside the handler transaction.**
``management_transaction`` rolls the entire session back on exception
(``sam/manage/transaction.py``), so an audit row enrolled in it would vanish in exactly
the case it exists for. :func:`_record` and :func:`_finish` therefore open short-lived
sessions of their own and commit immediately. This is the one behaviour here that no
happy-path test would catch.

Status codes are a deliberate improvement, not a port: legacy answers 500 with an
opaque timestamp for both a malformed body and a failed validation, and 200 for an
action it silently parked for a human. Ours distinguishes all four. The 422 body is the
headline deliverable — XRAS admins read it directly in their "Accounting Service Posts"
panel — so it carries the accumulated, ordered error list rather than a summary.
"""

import json
from datetime import datetime

from flask import current_app, request
from marshmallow import ValidationError
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.schemas.forms import XrasActionSchema
from webapp.extensions import csrf, db
from webapp.utils.api_auth import get_auth_actor

from . import bp, xras_api_required
from .serialize import xras_response

#: ``api_credentials.username`` is ``varchar(11)`` and ``xras_action_log.remote_actor``
#: matches it. ``xras_api_required`` closes the browser-session path, so the actor is
#: always an API-credential name and already within width — the slice is a guard
#: against a ``DataError`` turning an audit write into a 500, not an expected case.
_ACTOR_WIDTH = 11


def _record(*, status, raw_payload, action_type=None, request_number=None,
            error_messages=None):
    """Write one audit row on a private connection and commit. Returns its id.

    Deliberately does **not** use ``db.session``: this row must outlive a rollback of
    whatever transaction the handler runs in. Returns the id rather than the instance
    because the instance detaches when the session closes.
    """
    with Session(db.engine) as session:
        row = XrasActionLog(
            # Set from the APP clock, not the column's CURRENT_TIMESTAMP default.
            # That default resolves in the MySQL server's timezone, which is UTC in
            # the dev/CI container while SAM's convention is naive-Mountain — so a
            # server-defaulted received_time lands 6 hours ahead of the
            # datetime.now() that _finish() writes to processed_time, making a
            # processed row look like it completed before it arrived. The DDL keeps
            # its default as a safety net for rows inserted by hand in SQL.
            received_time=datetime.now(),
            remote_actor=(get_auth_actor() or 'unknown')[:_ACTOR_WIDTH],
            status=status,
            raw_payload=raw_payload,
            action_type=action_type,
            request_number=request_number,
            error_messages='\n'.join(error_messages) if error_messages else None,
        )
        session.add(row)
        session.commit()
        return row.xras_action_log_id


def _finish(log_id, *, status, projcode_result=None, error_messages=None):
    """Update an existing audit row to its terminal state, again on its own connection."""
    with Session(db.engine) as session:
        row = session.get(XrasActionLog, log_id)
        if row is None:                      # pragma: no cover - defensive
            return
        row.status = status
        row.processed_time = datetime.now()
        if projcode_result is not None:
            row.projcode_result = projcode_result
        if error_messages:
            row.error_messages = '\n'.join(error_messages)
        session.commit()


def _flatten(messages, path=()):
    """Flatten marshmallow's nested error dict into ordered ``field: message`` lines.

    Errors **accumulate** on this surface rather than short-circuiting — legacy gathers
    every problem into an ordered ``LinkedHashSet`` and raises once with the full list,
    which is what lets an operator fix a request in one pass instead of five. This
    preserves that for the schema layer; the handler layer accumulates its own.
    """
    lines = []
    if isinstance(messages, dict):
        for key, value in messages.items():
            lines.extend(_flatten(value, path + (str(key),)))
    elif isinstance(messages, list):
        for item in messages:
            lines.extend(_flatten(item, path))
    else:
        label = '.'.join(path) if path else '_schema'
        lines.append(f'{label}: {messages}')
    return lines


def _errors(error_messages, status):
    """The 422 body: an ordered list an XRAS admin can act on, in the envelope."""
    count = len(error_messages)
    summary = f'{count} error{"" if count == 1 else "s"} processing action'
    return xras_response({'errors': error_messages}, message=summary, status=status)


def _capture_only():
    """Whether dispatch is suppressed. Default **on** until handlers land."""
    return current_app.config.get('XRAS_ACTIONS_CAPTURE_ONLY', True)


@bp.route('/actions', methods=['POST'])
@bp.route('/actions/<int:action_id>/<int:request_id>/<action_type>', methods=['POST'])
@csrf.exempt
@xras_api_required()
def post_action(action_id=None, request_id=None, action_type=None):
    """Ingest one XRAS action.

    Two URL forms are mapped on purpose. All 175 real production posts go to the bare
    ``/v1/actions``, the only form legacy maps — but the ACCESS/XRAS specification
    documents ``/v1/actions/<actionId>/<requestId>/<actionType>``. If the broker is
    ever corrected to match its own published spec, every post would 404. The path
    segments are ignored in favour of the body, which is authoritative and which the
    bare form has always relied on.

    ``@csrf.exempt`` is required: ``CSRFProtect`` covers every POST, and a token-auth
    caller has no cookie to carry a token. ``webapp/api/v1/status.py`` is the
    precedent.

    ``xras_api_required`` rather than ``api_key_required`` — the latter's challenge
    emits a ``WWW-Authenticate`` header and an ``{'error': ...}`` body, neither of
    which is byte-compatible with legacy's 41-byte 401.
    """
    # request.get_data() rather than request.get_json(): the raw bytes are what the
    # audit row must store, verbatim, before anything interprets them.
    raw_payload = request.get_data(as_text=True)

    try:
        parsed = json.loads(raw_payload)
    except ValueError as exc:
        # Legacy 500s here (an unconfigured ObjectMapper inside a RuntimeException).
        # A malformed body is the client's error, so 400 — and it still gets a row,
        # with action_type NULL because we genuinely do not know it.
        _record(status='failed', raw_payload=raw_payload,
                error_messages=[f'Malformed JSON body: {exc}'])
        return _errors([f'Malformed JSON body: {exc}'], 400)

    if not isinstance(parsed, dict):
        message = f'Expected a JSON object, got {type(parsed).__name__}'
        _record(status='failed', raw_payload=raw_payload, error_messages=[message])
        return _errors([message], 400)

    try:
        action = XrasActionSchema().load(parsed)
    except ValidationError as exc:
        lines = _flatten(exc.messages)
        _record(status='failed', raw_payload=raw_payload,
                action_type=parsed.get('actionType'),
                request_number=parsed.get('requestNumber'),
                error_messages=lines)
        return _errors(lines, 422)

    # The row lands here — before dispatch, so a handler that explodes leaves a
    # replayable record behind.
    log_id = _record(status='received', raw_payload=raw_payload,
                     action_type=action.get('actionType'),
                     request_number=action.get('requestNumber'))

    if _capture_only():
        # Recorded and deliberately not acted on. The row stays 'received', which is
        # precisely true and is distinguishable from 'manual' ("a human must do this"):
        # querying status='received' gives operators the capture backlog.
        current_app.logger.info(
            'XRAS action captured (no dispatch): id=%s type=%s request=%s',
            log_id, action.get('actionType'), action.get('requestNumber'))
        return xras_response(message='OK')

    # No handler is registered yet, so every action type takes the manual-fallback
    # path. This is legacy's `catch (BadRequestException)` branch — except that legacy
    # answers a bare 200 there, leaving no trace that SAM quietly parked the action.
    _finish(log_id, status='manual')
    current_app.logger.warning(
        'XRAS action has no serviceable handler: id=%s type=%s request=%s',
        log_id, action.get('actionType'), action.get('requestNumber'))
    return xras_response(message='OK')
