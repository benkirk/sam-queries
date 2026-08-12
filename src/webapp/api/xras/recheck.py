"""Re-check — answer *"would this action succeed if XRAS posted it now?"*

The question this exists for. A post fails 422 because the PI's organization will not
resolve, or the contract is not found. We fix the data. Then what? **XRAS owns the
retry** — the resend comes from their side — so the only thing we control is whether
that resend will land. Without this, we find out by asking them to re-send and
watching. With it, we know first:

    post fails 422  →  fix the data here  →  re-check  →  green?  →  ask XRAS to resend
                                                      ↘  red? → the error list says what is still missing

The result is a **new row** pointing at the original via ``source_action_id``, never an
edit of the original. Legacy's equivalent is ``XRASPostBean`` — paste the JSON back into
a PrimeFaces form — which persists nothing, so it leaves no trace of what was tried,
by whom, or what came back.

Three decisions, each with a tempting wrong answer.

**1. The stored bytes are re-submitted verbatim.**
``raw_payload`` is ``Text`` and byte-exact on purpose — MySQL ``JSON`` was rejected
precisely because it normalizes key order and collapses duplicates (see the
``XrasActionLog`` docstring for the measurements). Re-checking a *re-serialization*
would quietly make it a different request from the one that arrived.

**2. The original row is never stamped.**
Setting the parent's status would destroy the parent's own outcome, which *is* the
audit record. "Has this been re-checked" is derived from the ``replays`` relationship
being non-empty; the relationship is already first-class, so nothing is denormalized.

**3. It validates but NEVER applies — and that is structural, not configured.**

``dispatch_action(..., validate_only=True)`` returns before ``management_transaction``
is ever opened (``sam/xras/handlers/base.py``). No config can change that, which is the
property worth having: at cutover ``XRAS_ACTIONS_CAPTURE_ONLY`` flips off, and nothing
about this surface changes.

⚠️ **This reverses a Sprint B decision, and the premise is what changed.** That version
tied re-checking to ``XRAS_ACTIONS_CAPTURE_ONLY`` and argued: *"The kill switch stays the
single safety interlock. A second override would mean two things to reason about and one
of them would eventually be wrong."* Correct while nothing dispatched at all. With
handlers live the conclusion inverts — coupling them means **the flag that turns on
production ingestion is the same flag that arms this button**.

**Applying a stored payload would double-apply on four of the six handlers.**
Supplement and Adjustment are additive by definition, so re-applying a 250,000-hour
supplement makes it 500,000. Worse, re-applying a successful **New** does not re-create
the project — it now exists, so ``(New, exists)`` routes to **Update**, which supplements
the allocation it just created. Only Extension is near-idempotent, and only because of
its equal-end-date skip. There is also no idempotency key to prevent it: ``actionId`` is
a column, but nothing enforces uniqueness on it and nothing consults it before applying.

**What it checks, and why that is the interesting half.** ``_parse_action`` proves the
payload still fits the wire schema — but almost no real failure is a schema failure.
The ones operators chase are reported by the handler's ``assemble()``: organization and
mnemonic resolution, contract lookup, allocation type, roster, resource mapping. Those
run here, and a payload that would still be rejected raises exactly as it would live,
carrying the same ordered error list.

Outcomes reuse the ingest vocabulary, so a re-check row reads like the ingest row it
would have been:

===============  =========================================================
``rechecked``    would succeed now
``failed``       would still fail — ``error_messages`` says why
``manual``       nothing would run (no service, disabled type, or Transfer)
===============  =========================================================

⚠️ **Re-checking a ``processed`` row is meaningless**, which is why the UI does not
offer it there: that action already changed the data it would run against, so a
successful New now routes to Update and the answer describes a different action.

If a production *remediation* path is ever wanted — actually re-applying — it needs an
idempotency key and an agreement with ACCESS about who owns resend. That is a
conversation, not a flag.
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
#: through the module attribute so the patch is honoured.
from . import actions


def _enabled():
    """The ``XRAS_ACTIONS_ENABLED`` triage lever, read the same way ingest reads it.

    A re-check honours it deliberately: if a type is parked by config, "nothing would
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
    # ⚠️ This used to be a hand-copied duplicate of that ladder, and the copy had
    # already drifted: it never passed `action_id`, so every replayed row stored NULL
    # in the duplicate-detection column. Call the shared one; do not re-inline it.
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
        # ⚠️ `projcode_result` stays NULL, deliberately. It means "the project this
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
