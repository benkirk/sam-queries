"""Operator write operations against XRAS, audited and made visible.

Each function here is one thing a human can do to the XRAS side from SAM:
merge a mis-reconciled placeholder into the real identity, withdraw or
re-submit an action, add or remove a role. Flask-free and import-light — the
webapp routes call these; the client is never called raw from a route.

The shape every operation follows
---------------------------------
::

    build client  ──►  open an `attempted` audit row  ──►  COMMIT it
                  ──►  dispatch the write (the client verifies it)
                  ──►  close the audit row on a FRESH session  ──►  COMMIT
                  ──►  best-effort: invalidate caches, patch the snapshot

**The audit row is committed before the write goes out, on its own session.**
This is the ``NotificationLedger`` idiom and it is here for the same reason: a
200 from XRAS is irreversible, so the record of it must not be rollback-able.
If the request handler explodes, if the pod is killed mid-call, if the write
succeeds and the response never arrives — a row exists saying an operator sent
that write. A row still reading ``attempted`` is not noise; it is the only
signal that a write went out and SAM never learned how it ended.

That is the inverse of ``xras_activation_event``, which commits *inside*
``management_transaction`` because it records something SAM itself did and can
therefore legitimately un-do.

Why a *session factory* rather than a session
---------------------------------------------
Callers pass ``session_factory``, not a session, precisely so this module can
open its own short-lived connections that no caller can roll back. In the
webapp that is ``lambda: Session(db.engine)``. The cost — a couple of extra
connections per operation — is the price of an audit trail that survives the
thing it audits.

Making the write visible
------------------------
The Remediations card renders from an hourly sweep snapshot, so after a
verified write the service re-fetches the affected request and patches its
entry back into that snapshot (``sam.queries.xras_requests`` builds the entry,
shared with the sweep so the two cannot drift). Without it a withdrawn action
would read "Approved" for another fifty minutes and the operator would
reasonably click again. The patch is **best-effort**: it happens after the
write is already done, verified and recorded, so its failure downgrades the
success message and nothing more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Role vocabulary for the form layer, in the order the UI should offer them.
#: Re-exported from the client so the schema, the template and the wire cannot
#: disagree about what ``User`` means.
def role_choices() -> List[Dict[str, Any]]:
    """``[{'id', 'name', 'display'}]`` — every spelling the UI or wire needs."""
    from sam.integration.xras_api.admin_client import ROLE_TYPES
    return [{'id': r.type_id, 'name': r.name, 'display': r.display}
            for r in ROLE_TYPES]


@dataclass(frozen=True)
class RemediationOutcome:
    """What a route needs to render, and nothing it should not act on."""

    event_id: Optional[int]
    result: Any = None
    status: str = 'error'
    error: Optional[str] = None
    #: False when the write landed but the card could not be refreshed — the
    #: modal appends "the card may lag until the next hourly sweep".
    patched: bool = True

    @property
    def succeeded(self) -> bool:
        return self.status == 'verified'


# ── audit plumbing ──────────────────────────────────────────────────────

def _open_event(session_factory, **fields) -> Optional[int]:
    """Write the ``attempted`` row and commit it. Returns its id.

    Returns ``None`` if the audit write itself fails — logged loudly, but it
    must not stop the remediation. An operator blocked from fixing production
    because a logging table was unreachable is the worse outcome, and the XRAS
    side keeps its own history either way.
    """
    from sam.integration.xras import XrasRemediationEvent
    try:
        with session_factory() as session:
            event = XrasRemediationEvent.create(session, **fields)
            session.commit()
            return event.xras_remediation_event_id
    except Exception:                                   # noqa: BLE001
        logger.exception('xras remediation: could not open the audit row for %r',
                          fields.get('operation'))
        return None


def _close_event(session_factory, event_id, **fields) -> None:
    """Close the row on a **fresh** session — the opener's is long committed."""
    if event_id is None:
        return
    from sam.integration.xras import XrasRemediationEvent
    try:
        with session_factory() as session:
            XrasRemediationEvent.complete(session, event_id, **fields)
            session.commit()
    except Exception:                                   # noqa: BLE001
        logger.exception('xras remediation: could not close audit row %s',
                          event_id)


def _outcome_fields(result) -> Dict[str, Any]:
    """Map an :class:`XrasWriteResult` onto the audit row's closing columns.

    ⚠️ ``before_state`` travels here rather than at ``create()``. The client
    makes its pre-capture *inside* the call, so the ``attempted`` row cannot
    carry it — and for a merge it is the whole point of the column: the source
    person's detail sheet, including ``residenceCountry``, exists nowhere else
    SAM can reach once the merge has deleted them.
    """
    return {
        'status': result.status,
        'http_status': result.http_status or None,
        'outcome_reason': result.verify_detail or result.write_error,
        'before_state': result.before,
        'after_state': result.after,
    }


def _client(client=None):
    from sam.integration.xras_api.admin_client import XrasAdminClient
    return client or XrasAdminClient.from_environment()


# ── cache coherence ─────────────────────────────────────────────────────

def _refresh_index_entry(request_number: str, *, reader=None) -> bool:
    """Re-read one request and patch its snapshot entry. ``True`` if patched.

    Uses the **read** client (report context) because the readable state source
    is the reports family. Builds the entry through the same function the sweep
    uses, so a patched row is indistinguishable from a swept one apart from its
    ``refreshed_at`` stamp.

    ⚠️ A request patched into a state that puts it *outside* the sweep's cohort
    — a whole request flipping to ``Incomplete``, say — **stays on the card**
    with its new status until the next sweep drops it naturally. The operator
    must see what their click did; a row that silently vanishes reads as a bug
    and invites a second click.

    Never raises. Every caller reaches here having already completed and
    recorded an irreversible write.
    """
    from datetime import datetime

    from sam.integration.xras_api.cache import patch_requests_index
    from sam.integration.xras_api.client import XrasApiClient
    from sam.queries.xras_requests import request_index_entry

    try:
        reader = reader or XrasApiClient.from_environment()
        payload = reader.get_request_by_number(request_number)
        if payload is None:
            # XRAS no longer knows this number. Dropping the row is right here
            # — there is nothing to render and nothing left to act on.
            return patch_requests_index(request_number, None)

        entry = request_index_entry(payload, pending_push=_still_pending(request_number),
                                    refreshed_at=datetime.now())
        if entry is None:
            return False
        return patch_requests_index(request_number, entry)
    except Exception as exc:                            # noqa: BLE001
        logger.warning('xras remediation: could not refresh the card entry '
                       'for %s (%s); it will lag until the next sweep',
                       request_number, exc)
        return False


def _still_pending(request_number: str) -> bool:
    """Does SAM still lack a project for this request number?

    Read straight from the published snapshot rather than the database: the
    entry being patched is replacing one the sweep produced, and this flag is
    the sweep's own set-difference. Re-deriving it from a fresh DB query would
    make a patched row disagree with its neighbours the moment a project is
    created between two sweeps — which is exactly the drift the shared builder
    exists to prevent. Defaults to ``True``: a row on this card is there
    because its handoff has not happened.
    """
    from sam.integration.xras_api.cache import load_requests_index
    payload = load_requests_index()
    rows = (payload or {}).get('rows') or []
    wanted = str(request_number).strip()
    for row in rows:
        if isinstance(row, dict) and str(row.get('request_number') or '').strip() == wanted:
            return bool(row.get('pending_push'))
    return True


# ── operations ──────────────────────────────────────────────────────────

def merge_placeholder(session_factory, *, source_username, target_username,
                      operator, comment=None, client=None) -> RemediationOutcome:
    """Merge an XRAS placeholder into the real identity. **Destructive.**

    XRAS deletes *source* and folds its roles into *target*, after which XRAS
    sends the real username and the blocked handoff can proceed. This does not
    create a SAM account — SAM never creates users — so a person with no SAM
    row correctly stays on *Accounts Needed*, re-classified from "erroneously
    reconciled placeholder" to an ordinary "create".

    The client refuses if *target* does not already resolve in XRAS: the API
    will happily *"merge a username into an existing/new username"*, so a typo
    would mint a fresh identity holding the placeholder's roles, which is worse
    than the bug being fixed.

    Both people-cache entries are invalidated afterwards, source and target.
    Without it the four-hour ``xras_people`` TTL keeps serving the placeholder
    to the very card the merge just fixed, and a second click 404s.
    """
    from sam.integration.xras_api.base import XrasSourceUnavailable, XrasWriteRejected

    admin = _client(client)
    event_id = _open_event(session_factory, operation='merge_person',
                           created_by=operator, username=source_username,
                           target_username=target_username, comment=comment)

    try:
        result = admin.merge_person(source_username, target_username)
    except XrasWriteRejected as exc:
        _close_event(session_factory, event_id, status='rejected',
                     http_status=getattr(exc, 'status', None) or None,
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='rejected', error=str(exc))
    except XrasSourceUnavailable as exc:
        _close_event(session_factory, event_id, status='error',
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='error', error=str(exc))

    _close_event(session_factory, event_id, **_outcome_fields(result))

    patched = True
    if result.succeeded:
        from sam.integration.xras_api.cache import invalidate_person
        invalidate_person(source_username)
        invalidate_person(target_username)
        patched = _patch_requests_naming(source_username)

    return RemediationOutcome(event_id, result=result, status=result.status,
                              patched=patched)


def _patch_requests_naming(username: str) -> bool:
    """Refresh every index entry whose roster carries *username*.

    A merged placeholder is usually on one or two requests, so this is a
    handful of reads at most. Done by re-fetching rather than editing the
    cached rosters in place, because the *whole* entry changes: the username on
    the wire is now the real one.
    """
    from sam.integration.xras_api.cache import load_requests_index

    payload = load_requests_index()
    rows = (payload or {}).get('rows') or []
    wanted = str(username).strip().casefold()
    numbers = [row.get('request_number') for row in rows
               if isinstance(row, dict)
               and any(str(r.get('username') or '').casefold() == wanted
                       for r in (row.get('roster') or []))]
    if not numbers:
        return True
    return all(_refresh_index_entry(number) for number in numbers if number)


def withdraw_action(session_factory, *, request_number, request_id, action_id,
                    pi_username, operator, comment,
                    client=None) -> RemediationOutcome:
    """De-approve one action back to a draft (``Incomplete``) in XRAS.

    ⚠️ Not archival and not deletion — it reverts the award to a draft and
    **rewrites the XRAS record** so the history no longer shows an approval.
    A PI can re-submit it. For a single-action request the whole request follows
    to ``Incomplete``, which is what drops it out of the Approved enumeration;
    a request with a surviving approved sibling stays ``Approved``, so "close
    this request" is really "withdraw each stuck action".

    *comment* is required by the caller's schema, not by this function — a
    de-approval of someone's award with no stated reason is not an audit trail.
    """
    return _action_op('withdraw_action', session_factory,
                      request_number=request_number, request_id=request_id,
                      action_id=action_id, pi_username=pi_username,
                      operator=operator, comment=comment, client=client)


def resubmit_action(session_factory, *, request_number, request_id, action_id,
                    pi_username, operator, comment=None, preflight=True,
                    client=None) -> RemediationOutcome:
    """Re-submit a drafted action. It lands in ``Under Review``, not ``Submitted``.

    The client validates first as the same impersonated user and refuses on a
    failure, carrying XRAS's own ``errors[]`` for the modal to render.

    ⚠️ That verdict is a function of *who* is impersonated — the same action can
    validate as the PI and fail as the Allocation Manager — so the preflight is
    only meaningful alongside the user it ran as.
    """
    return _action_op('submit_action', session_factory,
                      request_number=request_number, request_id=request_id,
                      action_id=action_id, pi_username=pi_username,
                      operator=operator, comment=comment, client=client,
                      preflight=preflight)


def _action_op(operation, session_factory, *, request_number, request_id,
               action_id, pi_username, operator, comment, client,
               preflight=None) -> RemediationOutcome:
    from sam.integration.xras_api.base import XrasSourceUnavailable, XrasWriteRejected

    admin = _client(client)
    event_id = _open_event(session_factory, operation=operation,
                           created_by=operator, xa_user=pi_username,
                           request_number=request_number, request_id=request_id,
                           action_id=action_id, comment=comment)

    try:
        if operation == 'withdraw_action':
            result = admin.withdraw_action(request_id, action_id,
                                           request_number=request_number,
                                           xa_user=pi_username)
        else:
            result = admin.submit_action(request_id, action_id,
                                         request_number=request_number,
                                         xa_user=pi_username,
                                         preflight=bool(preflight))
    except XrasWriteRejected as exc:
        _close_event(session_factory, event_id, status='rejected',
                     http_status=getattr(exc, 'status', None) or None,
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='rejected', error=str(exc),
                                  result=exc)
    except XrasSourceUnavailable as exc:
        _close_event(session_factory, event_id, status='error',
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='error', error=str(exc))

    _close_event(session_factory, event_id, **_outcome_fields(result))
    patched = _refresh_index_entry(request_number) if result.succeeded else True
    return RemediationOutcome(event_id, result=result, status=result.status,
                              patched=patched)


def change_role(session_factory, *, add, request_number, request_id, username,
                operator, xa_user, role=None, role_id=None, comment=None,
                client=None) -> RemediationOutcome:
    """Add *username* to the request's roster in *role*, or remove *role_id*.

    Add and remove are one function because they are one operator gesture — a
    roster fixup — and share every piece of plumbing. They differ only in which
    identifier is authoritative: an add names a **username** and a role, while a
    removal names a **roleId**, since one person can hold two roles and only the
    id says which one goes.

    No person parameters are ever sent. The route accepts an optional set that
    XRAS uses to *create* the person, with ``isReconciled`` defaulting to
    **true** — the precise mechanism that mints the stuck placeholders this
    whole feature exists to clean up.
    """
    from sam.integration.xras_api.admin_client import role_type
    from sam.integration.xras_api.base import XrasSourceUnavailable, XrasWriteRejected

    admin = _client(client)
    resolved = role_type(role) if role is not None else None

    event_id = _open_event(
        session_factory, operation='add_role' if add else 'remove_role',
        created_by=operator, xa_user=xa_user, username=username,
        request_number=request_number, request_id=request_id,
        role_id=None if add else role_id,
        role_type=resolved.name if resolved else None, comment=comment)

    try:
        if add:
            result = admin.add_role(request_id, resolved, username,
                                    request_number=request_number,
                                    xa_user=xa_user)
        else:
            result = admin.remove_role(request_id, role_id,
                                       request_number=request_number,
                                       xa_user=xa_user)
    except XrasWriteRejected as exc:
        _close_event(session_factory, event_id, status='rejected',
                     http_status=getattr(exc, 'status', None) or None,
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='rejected', error=str(exc))
    except XrasSourceUnavailable as exc:
        _close_event(session_factory, event_id, status='error',
                     outcome_reason=str(exc))
        return RemediationOutcome(event_id, status='error', error=str(exc))

    # The roleId only exists once XRAS has assigned it, so an add's audit row
    # learns its own identifier at completion — and that identifier is what an
    # undo would need.
    _close_event(session_factory, event_id,
                 role_id=result.extra.get('role_id') if add else None,
                 **_outcome_fields(result))
    patched = _refresh_index_entry(request_number) if result.succeeded else True
    return RemediationOutcome(event_id, result=result, status=result.status,
                              patched=patched)
