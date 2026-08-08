"""Replay — re-submit a stored XRAS payload as a new, linked audit row.

Legacy SAM's only replay mechanism is ``XRASPostBean``: an operator pastes the JSON
back into a PrimeFaces form. It persists nothing, so a replay leaves no trace that it
happened, by whom, or what it produced. This module is the replacement, and the audit
chain is the point — a replay is a *new row* pointing at the original via
``replay_of_id``, not an edit of the original.

Three decisions worth stating, because each has a tempting wrong answer.

**1. The stored bytes are re-submitted verbatim.**
``raw_payload`` is ``Text`` and byte-exact on purpose — MySQL ``JSON`` was rejected
precisely because it normalises key order and collapses duplicates (see the
``XrasActionLog`` docstring for the measurements). Replaying a *re-serialisation*
would throw that away and quietly make the replay a different request from the one
that arrived.

**2. The original row is never stamped.**
It is tempting to set the parent's status to ``'replayed'`` so triage can skip it. That
destroys the parent's own outcome — which is the audit record. "Has this been replayed"
is derived instead from the ``replays`` relationship being non-empty; the relationship
is already first-class, so nothing needs to be denormalised onto the parent.

**3. Replay honours ``XRAS_ACTIONS_CAPTURE_ONLY``.**
This is the one that matters. Capture mode is on because **legacy is still the system
of record** until cutover step 4 — it is already applying these actions. A replay that
dispatched while capture was on would apply an action legacy has already applied: a
double-apply against live allocations, one button click away, with no undo.

So under capture mode a replay re-parses and re-validates the stored bytes against the
*current* schema code and records the outcome as ``replayed`` (or ``failed``, with a
fresh error list). That is not a consolation prize: it is a regression check of the
schema against the harvested corpus, which is what the corpus is *for*. Once
``XRAS_ACTIONS_CAPTURE_ONLY=0``, replay dispatches and lands ``processed`` / ``manual``
/ ``failed`` exactly like a fresh post.

The kill switch stays the single safety interlock. A second, replay-specific override
would mean two things to reason about and one of them would eventually be wrong.
"""

import json

from flask import current_app
from marshmallow import ValidationError
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog
from sam.schemas.forms import XrasActionSchema
from webapp.extensions import db

#: Imported as a MODULE, not as names. ``from .actions import _record`` would bind
#: the function object into this namespace at import time, and
#: ``tests/api/test_xras_access.py``'s ``action_log`` fixture captures audit rows by
#: monkeypatching ``actions._record`` — a name-bound copy would sail straight past
#: it and leak committed rows into the shared xdist database. Every call below goes
#: through the module attribute so the patch is honoured.
from . import actions

#: ``xras_action_log.processed_by`` is ``varchar(35)`` (``users.username`` width).
_PROCESSED_BY_WIDTH = 35


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


def replay_action(log_id, *, actor):
    """Re-submit the payload stored in row ``log_id``. Returns the new row's id.

    Args:
        log_id: the ``xras_action_log`` row to replay. Replaying a replay is
            allowed and chains — ``replay_of_id`` points at whatever was clicked,
            so the lineage stays a tree rather than being flattened to the root.
        actor: the ``users.username`` of the human who asked for this. Recorded in
            ``processed_by``, which is what distinguishes an operator action from an
            integration one.

    Raises:
        LookupError: if ``log_id`` does not exist.
    """
    original = _load_original(log_id)
    raw_payload = original['raw_payload']

    # Parse and validate exactly as the ingest route does — a replay must be able to
    # *fail*, and fail the same way. A payload harvested months ago against an older
    # schema is precisely the case worth catching.
    try:
        parsed = json.loads(raw_payload)
    except ValueError as exc:
        message = f'Malformed JSON body: {exc}'
        return actions._record(
            status='failed', raw_payload=raw_payload, http_status=400,
            error_messages=[message], remote_actor=original['remote_actor'],
            replay_of_id=log_id, processed_by=actor[:_PROCESSED_BY_WIDTH],
        )

    if not isinstance(parsed, dict):
        message = f'Expected a JSON object, got {type(parsed).__name__}'
        return actions._record(
            status='failed', raw_payload=raw_payload, http_status=400,
            error_messages=[message], remote_actor=original['remote_actor'],
            replay_of_id=log_id, processed_by=actor[:_PROCESSED_BY_WIDTH],
        )

    try:
        action = XrasActionSchema().load(parsed)
    except ValidationError as exc:
        lines = actions._flatten(exc.messages)
        return actions._record(
            status='failed', raw_payload=raw_payload, http_status=422,
            action_type=parsed.get('actionType'),
            request_number=parsed.get('requestNumber'),
            error_messages=lines, remote_actor=original['remote_actor'],
            replay_of_id=log_id, processed_by=actor[:_PROCESSED_BY_WIDTH],
        )

    new_id = actions._record(
        status='received', raw_payload=raw_payload, http_status=200,
        action_type=action.get('actionType'),
        request_number=action.get('requestNumber'),
        remote_actor=original['remote_actor'],
        replay_of_id=log_id, processed_by=actor[:_PROCESSED_BY_WIDTH],
    )

    if actions._capture_only():
        # Re-validated, deliberately not dispatched — see the module docstring on
        # why this is not an oversight. 'replayed' is precisely true and is
        # distinguishable from 'received' (which means "arrived from XRAS and is
        # awaiting cutover") and from 'manual' ("a human must apply this").
        actions._finish(new_id, status='replayed')
        current_app.logger.info(
            'XRAS action replayed (no dispatch): id=%s replay_of=%s by=%s type=%s',
            new_id, log_id, actor, action.get('actionType'))
        return new_id

    # Capture is off: behave exactly like a fresh post. No handler is registered yet,
    # so everything still takes the manual-fallback path — that arm becomes live as
    # each handler lands, with no change here.
    actions._finish(new_id, status='manual')
    current_app.logger.warning(
        'XRAS replay has no serviceable handler: id=%s replay_of=%s by=%s type=%s',
        new_id, log_id, actor, action.get('actionType'))
    return new_id
