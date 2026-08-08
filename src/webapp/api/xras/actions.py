"""``POST /api/xras/v1/actions`` — the only writing surface on the XRAS integration.

The endpoint authenticates, parses, audits, and then dispatches — unless
``XRAS_ACTIONS_CAPTURE_ONLY`` is on, which it is by default. The audit row is written
*in front of* dispatch rather than behind it, which is what turns every production post
into a harvested payload and what makes replay possible when a handler explodes.

Two flags gate dispatch and they are not the same flag. ``XRAS_ACTIONS_CAPTURE_ONLY``
is the interlock — while legacy is still the system of record, dispatching would apply
an action it has already applied. ``XRAS_ACTIONS_ENABLED`` is the per-type triage
lever, checked inside :mod:`sam.xras.dispatch`; see that module for why it keys on
action type.

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
from sam.xras.dispatch import dispatch_action, parse_enabled_action_types
from sam.xras.errors import XrasActionRejected
# Imported for its side effect: every handler module registers itself with the
# dispatcher at import time. Without this the registry is empty and every action
# takes the manual-fallback arm — which fails quietly, as a plausible-looking
# 'manual' row rather than an error.
import sam.xras.handlers  # noqa: F401
from webapp.extensions import csrf, db
from webapp.utils.api_auth import get_auth_actor

from . import bp, xras_api_required
from .serialize import xras_response

#: ``api_credentials.username`` is ``varchar(11)`` and ``xras_action_log.remote_actor``
#: matches it. ``xras_api_required`` closes the browser-session path, so the actor is
#: always an API-credential name and already within width — the slice is a guard
#: against a ``DataError`` turning an audit write into a 500, not an expected case.
_ACTOR_WIDTH = 11

#: ``xras_action_log.action_type`` / ``.request_number`` widths. Unlike the actor,
#: these are read off the *raw* payload dict on the 422 path — before the schema has
#: had a chance to reject them — so they are genuinely untrusted here.
_ACTION_TYPE_WIDTH = 32
_REQUEST_NUMBER_WIDTH = 30
#: ``projcode_result`` is ``varchar(30)``. Handler projcodes come from
#: ``project.projcode`` (also ``varchar(30)``) so this cannot bite today, but the value
#: crosses the same trust boundary as the two above — Transfer's comes straight from
#: ``requestNumber`` — and an audit write that 500s is what ``_fit`` exists to prevent.
_PROJCODE_RESULT_WIDTH = 30
#: ``xras_action_log.processed_by`` is ``varchar(35)`` — ``users.username`` width. The
#: slice lives here rather than at the four ``replay.py`` call sites that used to carry
#: it: a width guard belongs next to the column it guards, or the fifth caller misses it.
_PROCESSED_BY_WIDTH = 35

#: ``raw_payload`` and ``error_messages`` are ``TEXT`` — 65,535 **bytes**, not
#: characters, and the column is utf8mb3.
#:
#: ⚠️ Under ``STRICT_TRANS_TABLES`` an oversized value does **not** truncate, it raises
#: ``1406 Data too long`` — so an unbounded write here loses the audit row entirely.
#: That is measured, not theoretical: ``tests/stress/test_audit_row_survives.py``
#: reproduced it against the test container before this guard existed.
_TEXT_WIDTH = 65_535

#: Room reserved for the truncation marker itself, which must always fit.
_TRUNCATION_MARGIN = 512


def _truncate_bytes(text, width):
    """Cut *text* to *width* encoded bytes without splitting a character."""
    encoded = text.encode('utf-8')
    if len(encoded) <= width:
        return text, False
    return encoded[:width].decode('utf-8', errors='ignore'), True


def _fit_payload(raw_payload):
    """Bound ``raw_payload``, announcing the cut. Returns ``(text, was_truncated)``.

    ⚠️ A truncated payload is **not replayable** — the bytes are no longer valid JSON,
    and replay reads them back through the schema. The marker says so in the stored
    value itself, because an operator deciding whether to click Replay sees the payload
    long before they see any documentation.

    Truncating rather than refusing to record is the lesser evil in both directions:
    the row survives, and the caller is separately told the action was rejected, so
    nothing is silently applied against a payload we could not keep.
    """
    text, truncated = _truncate_bytes(raw_payload, _TEXT_WIDTH - _TRUNCATION_MARGIN)
    if not truncated:
        return text, False
    original = len(raw_payload.encode('utf-8'))
    return (f'{text}\n\n[TRUNCATED — original body was {original:,} bytes, exceeding '
            f'the {_TEXT_WIDTH:,}-byte raw_payload column. THIS PAYLOAD CANNOT BE '
            f'REPLAYED. Ask XRAS to resend.]'), True


def _fit_error_messages(messages):
    """Join the ordered error list, bounded, cutting on **message** boundaries.

    Not a byte slice: half a message is worse than a missing one, because it reads as
    a complete diagnostic. Whatever is dropped is counted in a final line, so a short
    list never passes for a complete one.

    Reachable in practice — the amplification runs the wrong way. One unmapped resource
    costs ~38 bytes of payload and yields ~52 bytes of message, so a legal body well
    inside its own column can produce an error list that is not.
    """
    if not messages:
        return None
    budget = _TEXT_WIDTH - _TRUNCATION_MARGIN
    kept, used = [], 0
    for message in messages:
        cost = len(message.encode('utf-8')) + 1
        if used + cost > budget:
            break
        kept.append(message)
        used += cost
    if len(kept) == len(messages):
        return '\n'.join(messages)
    dropped = len(messages) - len(kept)
    kept.append(f'[… and {dropped:,} more message(s), truncated to fit the '
                f'{_TEXT_WIDTH:,}-byte error_messages column. The 422 response '
                f'carried the complete list.]')
    return '\n'.join(kept)


def _fit(value, width):
    """Coerce an untrusted payload value to a column-safe string, or ``None``.

    XRAS sends absent scalars as JSON ``null``, so ``None`` stays ``None`` — a
    NULL ``action_type`` is meaningful ("we could not parse the body"). Anything
    else is stringified and truncated rather than allowed to raise: an audit write
    that 500s is the one failure mode this table cannot afford.
    """
    if value is None:
        return None
    return str(value)[:width]


def _record(*, status, raw_payload, action_type=None, request_number=None,
            error_messages=None, http_status=None, remote_actor=None,
            replay_of_id=None, processed_by=None):
    """Write one audit row on a private connection and commit. Returns its id.

    Deliberately does **not** use ``db.session``: this row must outlive a rollback of
    whatever transaction the handler runs in. Returns the id rather than the instance
    because the instance detaches when the session closes.

    ``remote_actor`` / ``replay_of_id`` / ``processed_by`` exist for the replay path
    (``webapp/api/xras/replay.py``), which deliberately routes through *this* helper
    rather than its own insert: ``tests/api/test_xras_access.py``'s ``action_log``
    fixture captures rows by monkeypatching this function, so a second insert helper
    would leak committed rows into the shared xdist database.
    """
    with Session(db.engine) as session:
        row = XrasActionLog(
            # Set from the APP clock. The DDL carries NO `DEFAULT CURRENT_TIMESTAMP`
            # any more, and that removal is deliberate: the default resolves in the
            # MySQL server's timezone, which is UTC in the dev/CI container while
            # SAM's convention is naive-Mountain — so a server-defaulted
            # received_time landed 6 hours ahead of the datetime.now() that
            # _finish() writes to processed_time, making a processed row look like
            # it completed before it arrived. With no default, a hand-written INSERT
            # that forgets the column fails loudly instead of lying quietly.
            received_time=datetime.now(),
            remote_actor=(remote_actor or get_auth_actor()
                          or 'unknown')[:_ACTOR_WIDTH],
            status=status,
            raw_payload=_fit_payload(raw_payload)[0],
            # Width guards, same reasoning as _ACTOR_WIDTH: on the 422 path these
            # come straight off an *unvalidated* payload dict, so an over-long or
            # non-string actionType would turn the audit write into a 500 — losing
            # precisely the row this table exists to keep.
            action_type=_fit(action_type, _ACTION_TYPE_WIDTH),
            request_number=_fit(request_number, _REQUEST_NUMBER_WIDTH),
            error_messages=_fit_error_messages(error_messages),
            http_status=http_status,
            replay_of_id=replay_of_id,
            processed_by=_fit(processed_by, _PROCESSED_BY_WIDTH),
        )
        session.add(row)
        session.commit()
        return row.xras_action_log_id


def _finish(log_id, *, status, projcode_result=None, error_messages=None,
            http_status=None):
    """Update an existing audit row to its terminal state, again on its own connection."""
    with Session(db.engine) as session:
        row = session.get(XrasActionLog, log_id)
        if row is None:                      # pragma: no cover - defensive
            return
        row.status = status
        row.processed_time = datetime.now()
        if projcode_result is not None:
            row.projcode_result = _fit(projcode_result, _PROJCODE_RESULT_WIDTH)
        if error_messages:
            row.error_messages = _fit_error_messages(error_messages)
        if http_status is not None:
            row.http_status = http_status
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


def _enabled_action_types():
    """Which action types may be dispatched — the triage lever, not a rollout switch.

    Read here rather than in ``sam.xras.dispatch`` because nothing under ``sam/``
    imports Flask. See that module's docstring for why this keys on action type and
    why an unknown token fails safe.
    """
    return parse_enabled_action_types(
        current_app.config.get('XRAS_ACTIONS_ENABLED'))


def _dispatch(log_id, action):
    """Run the dispatcher and close out the audit row. Returns the HTTP response.

    The three terminal states, and the reason each is what it is:

    - ``XrasActionRejected`` → ``failed`` + **422** carrying the accumulated, ordered
      error list. Nothing was written: the handler contract is assemble → check once →
      execute, so a rejection happens before any transaction opens.
    - ``processed`` → 200, with ``projcode_result`` recorded. This is the status that
      has never once existed in this table.
    - ``manual`` → 200. Legacy's ``catch (BadRequestException)`` arm, except that
      legacy answers a bare 200 and leaves no trace that SAM quietly parked the action.
      The ``reason`` is logged because "nothing matched" and "the type is disabled"
      look identical in the table otherwise.

    ``projcode_result`` is recorded on **both** terminal arms. On the manual arm the
    dispatcher-level parks carry no projcode and correctly leave the column NULL; the
    one handler that parks by design (Transfer) does carry one, and a row that cannot
    say which project it is about defeats its own triage query.
    """
    try:
        result = dispatch_action(db.session, action,
                                 enabled=_enabled_action_types())
    except XrasActionRejected as exc:
        _finish(log_id, status='failed', error_messages=exc.messages,
                http_status=422)
        return _errors(exc.messages, 422)

    if result.warnings:
        # Non-fatal disagreements the action survived — today, only the legacy defect-3
        # roster/role split. `sam.xras.roster` already logs each one, but against
        # `actionId`; `log_id` is the handle an operator actually has, and only the
        # route knows it. Whether these earn a column of their own is deferred to
        # `docs/plans/XRAS_STRESS_AND_SCHEMA.md`.
        current_app.logger.warning(
            'XRAS action completed with %d warning(s): id=%s service=%s — %s',
            len(result.warnings), log_id, result.service,
            '; '.join(result.warnings))

    if result.status == 'processed':
        _finish(log_id, status='processed', projcode_result=result.projcode)
        current_app.logger.info(
            'XRAS action processed: id=%s service=%s projcode=%s',
            log_id, result.service, result.projcode)
        return xras_response(message='OK')

    _finish(log_id, status='manual', projcode_result=result.projcode)
    current_app.logger.warning(
        'XRAS action parked for a human: id=%s service=%s projcode=%s reason=%s',
        log_id, result.service, result.projcode, result.reason)
    return xras_response(message='OK')


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

    if len(raw_payload.encode('utf-8')) > _TEXT_WIDTH - _TRUNCATION_MARGIN:
        # A body we cannot store is a body we cannot audit or replay. Applying it
        # anyway would write allocations against a record that does not survive, so
        # this refuses and says why — in the 422 list, which is where an XRAS admin
        # reads it. The row is still written, with the payload truncated and marked.
        #
        # 422 rather than 413: the response envelope is a wire contract and the error
        # list is the part XRAS's panel renders. A status code their panel does not
        # expect would be an unreadable rejection.
        #
        # Cannot bite normal traffic — the largest payload ever observed is 4,819
        # bytes, roughly 13x under the limit.
        message = (
            f'Payload is {len(raw_payload.encode("utf-8")):,} bytes, which exceeds '
            f'the {_TEXT_WIDTH:,}-byte limit SAM can record. The action was not '
            f'applied. Please split the request or contact CISL.')
        _record(status='failed', raw_payload=raw_payload, http_status=422,
                error_messages=[message])
        return _errors([message], 422)

    try:
        parsed = json.loads(raw_payload)
    except ValueError as exc:
        # Legacy 500s here (an unconfigured ObjectMapper inside a RuntimeException).
        # A malformed body is the client's error, so 400 — and it still gets a row,
        # with action_type NULL because we genuinely do not know it.
        _record(status='failed', raw_payload=raw_payload, http_status=400,
                error_messages=[f'Malformed JSON body: {exc}'])
        return _errors([f'Malformed JSON body: {exc}'], 400)

    if not isinstance(parsed, dict):
        message = f'Expected a JSON object, got {type(parsed).__name__}'
        _record(status='failed', raw_payload=raw_payload, http_status=400,
                error_messages=[message])
        return _errors([message], 400)

    try:
        action = XrasActionSchema().load(parsed)
    except ValidationError as exc:
        lines = _flatten(exc.messages)
        _record(status='failed', raw_payload=raw_payload, http_status=422,
                action_type=parsed.get('actionType'),
                request_number=parsed.get('requestNumber'),
                error_messages=lines)
        return _errors(lines, 422)

    # The row lands here — before dispatch, so a handler that explodes leaves a
    # replayable record behind. http_status is 200 unless dispatch changes it.
    log_id = _record(status='received', raw_payload=raw_payload, http_status=200,
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

    return _dispatch(log_id, action)
