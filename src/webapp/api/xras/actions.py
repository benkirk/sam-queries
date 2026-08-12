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
action it silently parked for a human. Ours separates the malformed body (400) from the
failed validation (422). The 422 body is the headline deliverable — XRAS admins read it
directly in their "Accounting Service Posts" panel — so it carries the accumulated,
ordered error list rather than a summary. ACCESS confirmed on 2026-08-11 that this is
wanted: *"the response body is saved and made available in xras_admin for the admin to
see, so it's nice to include something informative"*, quoting legacy's opaque
``Unhandled SAM exception ... (timestamp ...)`` as the thing to fix.

⚠️ **A parked action is NOT distinguished on the wire.** ``processed`` and ``manual``
both return ``xras_response(message='OK')``, byte-identical, so an admin who posts an
action SAM quietly deferred to a human is told it worked. That is what legacy does too,
so it is not a regression — and it is now the *common* case rather than a hypothetical,
because ``Date Adjustment`` parks and is 4 of the 41 corpus payloads. The four outcomes
are distinguished in ``xras_action_log`` (``status`` / ``service`` /
``outcome_reason``), not in the response. Whether to change that is an open decision:
``docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md`` § gate 4.
"""

import json
from datetime import datetime

from flask import current_app, request
from marshmallow import ValidationError
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.schemas.forms import XrasActionSchema
from sam.xras.dispatch import (
    dispatch_action,
    parse_enabled_action_types,
    select_service,
)
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
#: ``service`` holds one of ``sam.xras.dispatch.SERVICES``; ``outcome_reason`` holds a
#: sentence written for whoever reads the row at 3am. Both bounded rather than ``TEXT``,
#: deliberately: a sentence that cannot overflow cannot lose the audit row.
_SERVICE_WIDTH = 16
_OUTCOME_REASON_WIDTH = 255

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


def _strip_astral(text):
    """Replace codepoints above the BMP with U+FFFD.

    ⚠️ For the **utf8mb3** columns only. utf8mb3 cannot represent a 4-byte character
    at all, and under ``STRICT_TRANS_TABLES`` that is not a truncation — MySQL raises
    ``1366 Incorrect string value`` and the audit row is lost. Same outcome as the
    ``1406`` overflow the widths guard, reached by encoding rather than by length.

    Deliberately **not** applied to ``raw_payload`` or ``error_messages``: those two
    columns are ``utf8mb4`` (``zz-90-xras_action_log.sql``) precisely so the body XRAS
    sent is stored as sent. The identifiers cannot follow them, because
    ``sam/queries/xras_activation.py`` joins ``request_number`` and ``projcode_result``
    against ``project.projcode`` and a mixed-charset comparison stops using the index
    (measured on production: ``type: const, rows: 1`` → ``type: index, rows: 4650``).

    Lossy, and losing nothing anyone wanted — these columns hold projcodes, usernames
    and a fixed action vocabulary, where a 4-byte glyph carries no meaning. The
    original spelling stays recoverable from ``raw_payload``.
    """
    return ''.join('�' if ord(c) > 0xFFFF else c for c in text)


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


def _fit_int(value):
    """Coerce an untrusted payload value to an int, or ``None``.

    ``actionId`` is read off the raw dict on the 400/422 paths, before the schema has
    had a chance to reject it, so it may be anything at all. Anything non-numeric
    becomes ``None`` rather than raising — the same trade ``_fit`` makes, for the same
    reason: an audit write that 500s is the one failure this table cannot afford.

    Negative and out-of-range values become ``None`` too, because the column is
    ``INT UNSIGNED`` and MySQL would reject them under ``STRICT_TRANS_TABLES``.
    """
    if value is None:
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 4_294_967_295 else None


def _fit(value, width):
    """Coerce an untrusted payload value to a column-safe string, or ``None``.

    XRAS sends absent scalars as JSON ``null``, so ``None`` stays ``None`` — a
    NULL ``action_type`` is meaningful ("we could not parse the body"). Anything
    else is stringified and truncated rather than allowed to raise: an audit write
    that 500s is the one failure mode this table cannot afford.

    Two ways to raise, and this guards both. Length is the obvious one. Encoding is
    the other: every column reached through here is **utf8mb3**, which cannot hold a
    4-byte character — see :func:`_strip_astral`. Sanitise *before* slicing, so a
    replacement character cannot be cut in half.

    ``width`` counts **characters**, correctly: MySQL ``VARCHAR(n)`` is n characters,
    while ``TEXT`` is 65,535 *bytes* — which is why the two TEXT columns go through
    :func:`_fit_payload` / :func:`_fit_error_messages` and count bytes instead.
    """
    if value is None:
        return None
    return _strip_astral(str(value))[:width]


def _record(*, status, raw_payload, action_type=None, request_number=None,
            action_id=None, error_messages=None, http_status=None,
            remote_actor=None, source_action_id=None, processed_by=None):
    """Write one audit row on a private connection and commit. Returns its id.

    Deliberately does **not** use ``db.session``: this row must outlive a rollback of
    whatever transaction the handler runs in. Returns the id rather than the instance
    because the instance detaches when the session closes.

    ``remote_actor`` / ``source_action_id`` / ``processed_by`` exist for the replay path
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
            action_id=_fit_int(action_id),
            error_messages=_fit_error_messages(error_messages),
            http_status=http_status,
            source_action_id=source_action_id,
            processed_by=_fit(processed_by, _PROCESSED_BY_WIDTH),
        )
        session.add(row)
        session.commit()
        return row.xras_action_log_id


def _finish(log_id, *, status, projcode_result=None, error_messages=None,
            http_status=None, service=None, outcome_reason=None):
    """Update an existing audit row to its terminal state, again on its own connection."""
    with Session(db.engine) as session:
        row = session.get(XrasActionLog, log_id)
        if row is None:                      # pragma: no cover - defensive
            # Say so. A row that vanished between _record and _finish means the
            # audit trail lost an action, which is the one thing this table exists
            # to prevent — silence here would make it undiagnosable.
            current_app.logger.error(
                'XRAS audit row %s disappeared before close-out (status=%s)',
                log_id, status)
            return
        row.status = status
        row.processed_time = datetime.now()
        if projcode_result is not None:
            row.projcode_result = _fit(projcode_result, _PROJCODE_RESULT_WIDTH)
        if error_messages:
            row.error_messages = _fit_error_messages(error_messages)
        if http_status is not None:
            row.http_status = http_status
        if service is not None:
            row.service = _fit(service, _SERVICE_WIDTH)
        if outcome_reason is not None:
            row.outcome_reason = _fit(outcome_reason, _OUTCOME_REASON_WIDTH)
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


def _parse_action(raw_payload):
    """Parse and validate one POST body. **The single spelling of the 400/422 ladder.**

    Returns ``(action, audit)``:

    - ``action`` is the validated dict, or ``None`` if the body did not survive.
    - ``audit`` is always the ``_record`` kwargs describing the outcome —
      ``status``, ``http_status``, and (except on the two 400 arms) the three
      identity columns. Callers merge their own kwargs on top and decide what to
      return.

    ⚠️ **The three message strings are on the wire contract.** XRAS administrators
    read them in their "Accounting Service Posts" panel, the same standing as the
    error vocabulary in :mod:`sam.xras.errors`. Reproduce, do not tidy.

    ⚠️ **Identity is read off the *raw* dict on the 422 arm, and off the *validated*
    action on the success arm.** That asymmetry is deliberate: a rejected body never
    passed the schema, so the only ``actionType`` / ``requestNumber`` / ``actionId``
    available are untrusted — which is exactly why :func:`_fit` and :func:`_fit_int`
    guard them. On the 400 arms there is no identity at all, and a NULL
    ``action_type`` is meaningful there: it says "we could not parse the body".

    This exists because there were two copies. ``replay.py`` carried the second, and
    it had already drifted — it never passed ``action_id``, so every replayed row
    stored NULL in the column the runbook's triage section reaches for first ("three
    posts sharing one ``action_id`` are a duplicate, not three awards"). The copy
    predated the column and nothing pointed the two at each other. One spelling now,
    so the next column added here cannot reach one path and miss the other.
    """
    try:
        parsed = json.loads(raw_payload)
    except ValueError as exc:
        return None, {'status': 'failed', 'http_status': 400,
                      'error_messages': [f'Malformed JSON body: {exc}']}

    if not isinstance(parsed, dict):
        return None, {
            'status': 'failed', 'http_status': 400,
            'error_messages': [
                f'Expected a JSON object, got {type(parsed).__name__}'],
        }

    try:
        action = XrasActionSchema().load(parsed)
    except ValidationError as exc:
        return None, {'status': 'failed', 'http_status': 422,
                      'error_messages': _flatten(exc.messages),
                      'action_type': parsed.get('actionType'),
                      'request_number': parsed.get('requestNumber'),
                      'action_id': parsed.get('actionId')}

    return action, {'status': 'received', 'http_status': 200,
                    'action_type': action.get('actionType'),
                    'request_number': action.get('requestNumber'),
                    'action_id': action.get('actionId')}


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

    The four terminal states, and the reason each is what it is:

    - ``XrasActionRejected`` → ``failed`` + **422** carrying the accumulated, ordered
      error list. Nothing was written: the handler contract is assemble → check once →
      execute, so a rejection happens before any transaction opens.
    - **any other exception** → ``failed`` + **500**, with ``outcome_reason`` naming
      the exception class, then re-raised. Every exit from this function must leave a
      terminal status behind: the pre-dispatch row says ``received``, and so does the
      capture-only backlog, so a row left at ``received`` by a crash is invisible
      among rows that are merely waiting.
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
        # The service is known even though it rejected — which service produced a
        # 422 is the first thing an operator wants when the same projcode fails
        # repeatedly.
        _finish(log_id, status='failed', error_messages=exc.messages,
                http_status=422, service=select_service(db.session, action))
        return _errors(exc.messages, 422)
    except Exception as exc:
        # A handler that RAISED is not the capture backlog — but without this arm the
        # row keeps `status='received'` / `http_status=200` / `processed_time IS NULL`,
        # which is byte-identical to what CAPTURE_ONLY writes at :530. Triage week
        # queries exactly that status, so an exhausted projcode counter would read as
        # "captured, not yet dispatched". `XrasProjectCreationFailed` is deliberately
        # not an `XrasActionRejected` *because* "the route's error handling records
        # it" (sam/xras/handlers/new.py) — this is that recording.
        #
        # The whole close-out is best-effort and re-raises the ORIGINAL exception:
        # a diagnostic that throws its own error, masking the failure it was written
        # to explain, is worse than no diagnostic.
        try:
            # `db.session` may need a rollback before it can answer anything.
            # `management_transaction` rolls back on its way out, but `assemble()`
            # runs *before* that transaction opens, so this arm can be reached with
            # a session that is still poisoned.
            db.session.rollback()
            service = select_service(db.session, action)
        except Exception:                    # pragma: no cover - defensive
            service = None
        try:
            _finish(log_id, status='failed', http_status=500, service=service,
                    outcome_reason=f'handler raised: {type(exc).__name__}')
        except Exception:                    # pragma: no cover - defensive
            current_app.logger.exception(
                'XRAS audit close-out failed, row stays received: id=%s', log_id)
        current_app.logger.exception(
            'XRAS action raised: id=%s service=%s type=%s',
            log_id, service, action.get('actionType'))
        raise

    if result.warnings:
        # Non-fatal disagreements the action survived — today, only the legacy defect-3
        # roster/role split. `sam.xras.roster` already logs each one, but against
        # `actionId`; `log_id` is the handle an operator actually has, and only the
        # route knows it. Whether these earn a column of their own is deferred to
        # `docs/xras/incoming/implemented/XRAS_STRESS_AND_SCHEMA.md`.
        current_app.logger.warning(
            'XRAS action completed with %d warning(s): id=%s service=%s — %s',
            len(result.warnings), log_id, result.service,
            '; '.join(result.warnings))

    if result.status == 'processed':
        _finish(log_id, status='processed', projcode_result=result.projcode,
                service=result.service)
        current_app.logger.info(
            'XRAS action processed: id=%s service=%s projcode=%s',
            log_id, result.service, result.projcode)
        return xras_response(message='OK')

    # `service` and `outcome_reason` are the whole reason this arm is worth
    # distinguishing: four causes park an action and, without them, the rows are
    # byte-identical. `service` is NULL when nothing matched at all, which is itself
    # the answer to "why did this park".
    _finish(log_id, status='manual', projcode_result=result.projcode,
            service=result.service, outcome_reason=result.reason)
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

    # Legacy 500s on a malformed body (an unconfigured ObjectMapper inside a
    # RuntimeException). That is the client's error, so 400 — and it still gets a
    # row, with action_type NULL because we genuinely do not know it.
    action, audit = _parse_action(raw_payload)
    if action is None:
        _record(raw_payload=raw_payload, **audit)
        return _errors(audit['error_messages'], audit['http_status'])

    # The row lands here — before dispatch, so a handler that explodes leaves a
    # replayable record behind. http_status is 200 unless dispatch changes it.
    log_id = _record(raw_payload=raw_payload, **audit)

    if _capture_only():
        # Recorded and deliberately not acted on. The row stays 'received', which is
        # precisely true and is distinguishable from 'manual' ("a human must do this"):
        # querying status='received' gives operators the capture backlog.
        current_app.logger.info(
            'XRAS action captured (no dispatch): id=%s type=%s request=%s',
            log_id, action.get('actionType'), action.get('requestNumber'))
        return xras_response(message='OK')

    return _dispatch(log_id, action)
