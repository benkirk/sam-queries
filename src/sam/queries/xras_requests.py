"""One request, shaped for the Remediations card — **the single derivation**.

Why this module exists at all
-----------------------------
Two things build these entries and they must agree byte for byte:

1. ``xras_sweep`` builds ~100 of them once an hour from a paginated
   ``GET /v1/reports/requests`` enumeration, and publishes the lot to the
   ``xras_pending`` cache bucket;
2. after every verified write, ``sam.manage.xras_remediation`` re-fetches **one**
   request and patches its entry back into that same published payload, so the
   operator sees the effect of their click immediately instead of waiting up to
   an hour for the next sweep.

If those two produced even slightly different dicts, a patched row would render
differently from its neighbours — different keys missing, a role list shaped
another way — and the difference would show up only in production, only on the
row somebody had just acted on. So the derivation is a function, called by both,
and the sweep has no private copy.

The input shape
---------------
A ``reports/requests`` payload. Two spelling traps live in it, both measured:

* ``opportunity_name`` is **snake_case** here while the inbound action wire
  spells the sibling field ``opportunityName``. Both are read, in that order.
* ``roles[]`` entries are ``{person, roles[]}`` — a person plus a *list* of role
  records — and the inner records spell the role ``role``, not ``roleType``.
  Reading ``roleType`` off the outer object returns ``None``, silently.

What is deliberately absent
---------------------------
**Full person dicts.** The roster carries a username, a display name and two
flags, and nothing else. The payload has complete person objects inline —
email, phone, ``residenceCountry`` — and putting them in a cache the fragment
renders from would move PII across the ``MANAGE_XRAS`` line that the sibling
cards enforce at render time. What the card needs to *decide* something
(placeholder, reconciled) is a flag; what it needs to *show* a human is fetched
live, inside a permission-gated route.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sam.queries.xras_accounts import is_placeholder

#: Action fields carried into the entry. The states are what the card's
#: withdraw/re-submit offers key on, since the authoritative legal-moves read
#: (``rules{allowedOperations}`` on ``GET /v1/requests/<rid>``) is 401 for our
#: credential — PRIVILEGE(#1).
_ACTION_KEYS = ('actionId', 'actionType', 'actionStatus', 'submitDate')

#: An action in one of these states is finished; the card offers nothing on it.
TERMINAL_ACTION_STATUSES = ('Rejected', 'Cancelled', 'Withdrawn')

#: The state a withdrawn action lands in, and therefore the one a re-submit
#: offer keys on. Measured: a re-submit lands in ``Under Review``, not
#: ``Submitted``, so nothing may test for the latter.
DRAFT_ACTION_STATUS = 'Incomplete'

PI_ROLE_TYPE_ID = 13


def _as_date(value: Any):
    """XRAS date → ``date``, or ``None``.

    Parsed here rather than left as a string because the entry is **pickled
    into a cache and read straight by a Jinja ``fmt_date``**, which needs a
    real date object — the same contract the sweep's ``generated_at`` already
    follows. Doing it in the builder means both consumers get it and neither
    template has to know the wire format.

    Three shapes arrive: ``2015-07-09T19:16:58.481Z``, ``2026-01-01T00:00:00Z``
    and a bare ``2015-07-09``. All three are answered by taking the first ten
    characters, which is also why an unparsable value returns ``None`` rather
    than raising — a malformed date must cost that field, not the row.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _display_name(person: Dict[str, Any]) -> Optional[str]:
    parts = [person.get('firstName'), person.get('lastName')]
    return ' '.join(p for p in (str(x or '').strip() for x in parts) if p) or None


def roster_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten ``roles[].roles[]`` to one row per **role**, not per person.

    One person can hold two roles on a request, and role removal is keyed on
    ``roleId`` — so collapsing to one row per person would lose exactly the
    identifier the remove button needs.
    """
    rows: List[Dict[str, Any]] = []
    for entry in payload.get('roles') or ():
        if not isinstance(entry, dict):
            continue
        person = entry.get('person') if isinstance(entry.get('person'), dict) else {}
        username = _text(person.get('username'))
        if not username:
            continue
        for role in entry.get('roles') or ():
            if not isinstance(role, dict):
                continue
            rows.append({
                'role_id': role.get('roleId'),
                'role_type_id': role.get('roleTypeId'),
                'role_type': _text(role.get('role')),
                'username': username,
                'name': _display_name(person),
                'placeholder': is_placeholder(username),
                # ⚠️ Reconciled means XRAS linked this username to a real
                # identity — NOT that SAM has an account. A placeholder that is
                # *also* reconciled is the contradiction the merge fixup exists
                # for: reconciliation in XRAS is a merge, and a merged
                # placeholder would not still be here.
                'is_reconciled': person.get('isReconciled'),
            })
    return rows


def resolve_pi(roster: List[Dict[str, Any]]) -> Optional[str]:
    """The PI's username, or ``None``.

    Every request-scoped XRAS write authorizes on ``XA-USER`` holding a role on
    that request, and the PI is the impersonation the modals default to —
    measured, because the same action validated as the PI and failed as the
    Allocation Manager (PRIVILEGE(#5)).
    """
    for row in roster:
        if row.get('role_type_id') == PI_ROLE_TYPE_ID:
            return row.get('username')
    return None


def actions_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Actions, snake-cased, with the two offer flags precomputed."""
    rows: List[Dict[str, Any]] = []
    for action in payload.get('actions') or ():
        if not isinstance(action, dict):
            continue
        if action.get('actionId') is None:
            # Same rule the entry itself follows for requestNumber/requestId:
            # every offer routes through url_for(..., action_id=<int>), so an
            # action with no id cannot support a single button — and a None
            # here is a BuildError that costs the whole card, not the row.
            continue
        status = _text(action.get('actionStatus'))
        rows.append({
            'action_id': action.get('actionId'),
            'action_type': _text(action.get('actionType')),
            'action_status': status,
            'submit_date': _as_date(action.get('submitDate')),
            # Snapshot-derived *offers*, not permissions. The modal's live read
            # is the authority on legality; these only decide which button to
            # draw, and drawing one that XRAS then refuses is a 4xx the modal
            # renders — not a silent failure.
            'can_withdraw': bool(status) and status != DRAFT_ACTION_STATUS
                            and status not in TERMINAL_ACTION_STATUSES,
            'can_resubmit': status == DRAFT_ACTION_STATUS,
        })
    return rows


def request_index_entry(payload: Dict[str, Any], *, pending_push: bool = False,
                        refreshed_at: Any = None) -> Optional[Dict[str, Any]]:
    """Build one Remediations-card entry. ``None`` for an unusable payload.

    Args:
        payload:      a ``reports/requests`` row, or a
                      ``reports/request_numbers/<n>`` result — the same shape.
        pending_push: whether SAM still has no ``project`` for this number.
                      Passed in rather than derived, because the sweep resolves
                      the whole set in one query and a single-entry patch
                      resolves one; neither should be doing the other's lookup.
        refreshed_at: set only by the post-write patch. Its presence is what
                      makes an entry render its "updated since the sweep" tell,
                      so the operator can see which row they just changed.

    Returning ``None`` rather than raising: the sweep builds ~100 of these from
    a paginated remote enumeration, and one malformed row must cost that row,
    not the run.
    """
    if not isinstance(payload, dict):
        return None
    number = _text(payload.get('requestNumber'))
    request_id = payload.get('requestId')
    if not number or request_id is None:
        # Both are load-bearing and for different reasons: writes key on the
        # id, the readable reports family keys on the number (PRIVILEGE(#3)).
        # An entry missing either cannot support a single button.
        return None

    roster = roster_from_payload(payload)
    return {
        'request_number': number,
        'request_id': request_id,
        'status': _text(payload.get('requestStatus')),
        'request_type': _text(payload.get('requestType')),
        'submit_date': _as_date(payload.get('submitDate')),
        'begin_date': _as_date(payload.get('beginDate')),
        'end_date': _as_date(payload.get('endDate')),
        'pending_push': bool(pending_push),
        'opportunity_id': payload.get('opportunityId'),
        'opportunity_name': _text(payload.get('opportunity_name')
                                  or payload.get('opportunityName')),
        'pi': {'username': resolve_pi(roster),
               'name': next((r['name'] for r in roster
                             if r.get('role_type_id') == PI_ROLE_TYPE_ID), None)},
        'roster': roster,
        'actions': actions_from_payload(payload),
        # The conjunction the merge fixup keys on, precomputed so the template
        # does not have to express it — see the roster comment.
        'has_stuck_placeholder': any(r['placeholder'] and r['is_reconciled']
                                     for r in roster),
        'refreshed_at': refreshed_at,
    }
