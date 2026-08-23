"""Re-check -- would this action succeed if XRAS posted it now?

A post fails 422 because the PI's organization will not resolve, or the
contract is not found. XRAS owns the retry, so the only thing we control is
whether their resend will land. This answers that before we ask.

Each re-check writes a NEW row pointing at the original via
``source_action_id``; the original is never stamped, because its status IS the
audit record. "Has this been re-checked" derives from the ``replays``
relationship being non-empty.

The stored bytes are re-submitted verbatim -- ``raw_payload`` is ``Text`` and
byte-exact because MySQL ``JSON`` normalizes key order and collapses
duplicates. Re-checking a re-serialization would check a different request.

WARNING: this validates and NEVER applies, and that is structural, not
configured: ``dispatch_action(..., validate_only=True)`` returns before
``management_transaction`` is opened. Do not couple it to
``XRAS_ACTIONS_CAPTURE_ONLY`` -- that would make the flag arming production
ingestion the same flag arming this button. Applying a stored payload would
double-apply on four of the six handlers: Supplement and Adjustment are
additive, and a re-applied successful New routes to Update (the project now
exists) and supplements the allocation it just created. There is no
idempotency key -- ``actionId`` is a column nothing enforces or consults.

Outcomes reuse the ingest vocabulary: ``rechecked`` would succeed now,
``failed`` would still fail (``error_messages`` says why), ``manual`` would run
nothing. Re-checking a ``processed`` row is meaningless -- that action already
changed the data it would run against -- so the UI does not offer it.
"""

from typing import NamedTuple

from flask import current_app
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.xras.dispatch import (
    dispatch_action,
    parse_enabled_action_types,
    select_service,
)
from sam.xras.errors import XrasActionRejected
from webapp.extensions import db


class Recheck(NamedTuple):
    """What a re-check produced: the new row's id, and the verdict.

    The status is returned rather than re-read because ``_finish`` commits on its
    own connection — a caller reading back through the request's session may hold a
    snapshot that predates it. Callers that want the full row (the CLI does) still
    query; callers that only want the verdict (the route does) should not have to.
    """

    log_id: int
    status: str


#: Imported as a MODULE, not as names. ``from .actions import _record`` would bind
#: the function object into this namespace at import time, and
#: ``tests/api/test_xras_access.py``'s ``action_log`` fixture captures audit rows by
#: monkeypatching ``actions._record`` — a name-bound copy would sail straight past
#: it and leak committed rows into the shared xdist database. Every call below goes
#: through the module attribute so the patch is honored.
from . import actions


def _enabled():
    """The ``XRAS_ACTIONS_ENABLED`` triage lever, read the same way ingest reads it.

    A re-check honors it deliberately: if a type is parked by config, "nothing would
    run" is the true answer to *"would this succeed if posted now?"* — and an operator
    who has forgotten the lever is set is exactly who needs telling.
    """
    return parse_enabled_action_types(
        current_app.config.get('XRAS_ACTIONS_ENABLED'))


def _load_original(log_id):
    """Fetch the row being replayed, on its own connection.

    Read on a private session for the same reason :func:`_record` writes on one: the
    caller may be inside a request-scoped transaction, and this must not enrol in it.
    Returns the fields as plain values — the instance detaches when the session
    closes.
    """
    with Session(db.engine) as session:
        row = session.get(XrasActionLog, log_id)
        if row is None:
            raise LookupError(f'no xras_action_log row with id={log_id}')
        return {
            'raw_payload': row.raw_payload,
            'remote_actor': row.remote_actor,
            'action_type': row.action_type,
            'request_number': row.request_number,
        }


def recheck_action(log_id, *, actor) -> 'Recheck':
    """Re-validate the payload stored in row ``log_id``. Applies nothing.

    Args:
        log_id: the ``xras_action_log`` row to re-check. Re-checking a re-check is
            allowed and chains — ``source_action_id`` points at whatever was clicked,
            so the lineage stays a tree rather than being flattened to the root.
        actor: the ``users.username`` of the human who asked for this. Recorded in
            ``processed_by``, which is what distinguishes an operator action from an
            integration one.

    Returns:
        :class:`Recheck` — the new row's id and its verdict (``rechecked`` /
        ``failed`` / ``manual``).

    Raises:
        LookupError: if ``log_id`` does not exist.
    """
    original = _load_original(log_id)
    raw_payload = original['raw_payload']

    #: What every row this function writes carries, on any arm. The bytes still
    #: originated at XRAS so ``remote_actor`` stays theirs; the human goes in
    #: ``processed_by``, which is also the only column wide enough for a username.
    provenance = dict(remote_actor=original['remote_actor'],
                      source_action_id=log_id, processed_by=actor)

    # Parse and validate through the ingest route's own function — a replay must be
    # able to *fail*, and fail the same way. A payload harvested months ago against
    # an older schema is precisely the case worth catching.
    #
    # WARNING: call the shared ladder; do NOT re-inline it. A hand-copied duplicate
    # here drifts — one silently stopped passing `action_id`, so every replayed row
    # stored NULL in the duplicate-detection column.
    action, audit = actions._parse_action(raw_payload)
    if action is None:
        # Never reached a handler: the stored bytes no longer parse, or no longer
        # fit the schema. `audit` already carries status='failed' and the reason.
        return Recheck(actions._record(raw_payload=raw_payload,
                                       **audit, **provenance), 'failed')

    new_id = actions._record(raw_payload=raw_payload, **audit, **provenance)

    # Run the handler's assemble-and-check half, and stop. This is what makes the
    # answer worth having: `_parse_action` above only proves the payload still fits
    # the wire schema, and almost no real failure is a schema failure. The ones
    # operators actually chase — an organization that will not resolve, a contract
    # that is not found, a mnemonic gap — are all reported by `assemble()`, which
    # runs here and nowhere else on this path.
    #
    # `validate_only=True` returns before `management_transaction` is opened, so
    # nothing can be written. That is a property of the handler template
    # (`sam/xras/handlers/base.py`), not of a flag read here, which is what makes it
    # hold under any config.
    try:
        result = dispatch_action(db.session, action,
                                 enabled=_enabled(),
                                 validate_only=True)
    except XrasActionRejected as exc:
        # Would still fail, and here is why. Same status and same ordered error list
        # a live post would produce, so the row reads like the ingest row it would
        # have been — which is the whole point of reusing the vocabulary.
        actions._finish(new_id, status='failed', error_messages=exc.messages,
                        http_status=422,
                        service=select_service(db.session, action))
        current_app.logger.info(
            'XRAS re-check says this would STILL FAIL: id=%s source=%s by=%s — %s',
            new_id, log_id, actor, '; '.join(exc.messages))
        return Recheck(new_id, 'failed')
    finally:
        # `assemble()` is contractually write-free, but that is a docstring promise
        # rather than an enforced one, and this session is the request's. Roll back
        # so a handler that ever does flush something cannot leak it into whatever
        # the request does next.
        db.session.rollback()

    if result.status == 'rechecked':
        # WARNING: `projcode_result` stays NULL, deliberately. It means "the project this
        # action produced", and a re-check produces nothing. Worse, on the New path
        # the handler's `projcode` is the *request token* (`NCAR4253`), not a
        # projcode — writing it here would put a non-projcode into the column that
        # joins against `project.projcode`. `request_number` already says what the
        # action is about. The `manual` arm below does record it, matching ingest:
        # there the value came from a handler that parked a real project.
        actions._finish(new_id, status='rechecked', service=result.service)
        current_app.logger.info(
            'XRAS re-check says this WOULD SUCCEED now: id=%s source=%s by=%s type=%s',
            new_id, log_id, actor, action.get('actionType'))
        return Recheck(new_id, 'rechecked')

    # Nothing would run: no service matches, the type is disabled, or the handler
    # parks by design (Transfer). `manual` is the same answer a live post gets, and
    # `reason` is what distinguishes the four causes.
    actions._finish(new_id, status='manual', projcode_result=result.projcode,
                    service=result.service, outcome_reason=result.reason)
    current_app.logger.info(
        'XRAS re-check: nothing would run for id=%s source=%s (%s)',
        new_id, log_id, result.reason)
    return Recheck(new_id, 'manual')
