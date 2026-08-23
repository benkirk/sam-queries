"""One request, shaped for the Remediations card -- the single derivation.

Two things build these entries and must agree byte for byte: ``xras_sweep``
builds ~100 an hour from a paginated ``GET /v1/reports/requests`` and publishes
them to the ``xras_pending`` bucket, and ``sam.manage.xras_remediation``
re-fetches ONE request after a verified write and patches its entry back into
that same payload so the operator sees their click immediately. Different dicts
would render a patched row differently from its neighbors, visible only in
production and only on the row somebody just acted on. Hence one function, no
private copy.

Two measured spelling traps in the ``reports/requests`` payload:

* ``opportunity_name`` is **snake_case** here, while the inbound action wire
  spells the sibling field ``opportunityName``. Both are read, in that order.
* ``roles[]`` entries are ``{person, roles[]}`` -- a person plus a *list* of
  role records -- and the inner records spell it ``role``, not ``roleType``.
  Reading ``roleType`` off the outer object returns None, silently.

Full person dicts are deliberately absent. The payload carries email, phone and
``residenceCountry`` inline; putting those in a cache the fragment renders from
would move PII across the ``MANAGE_XRAS`` line the sibling cards enforce at
render time. What the card needs to *decide* is a flag; what it needs to *show*
is fetched live inside a permission-gated route.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional

from sam.integration.xras_api.vocabulary import (
    ADMIN_ROLE_TYPE_ID,
    PI_ROLE_TYPE_ID,
)
from sam.queries.xras_accounts import is_placeholder, iter_roster_entries

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

# ``PI_ROLE_TYPE_ID`` / ``ADMIN_ROLE_TYPE_ID`` come from the dependency-light
# ``xras_api.vocabulary`` module (imported above) — one authoritative table,
# no write client on the read path. Re-exported at module scope so existing
# ``from sam.queries.xras_requests import PI_ROLE_TYPE_ID`` callers still work.


def _as_date(value: Any):
    """XRAS date -> ``date``, or ``None``.

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
    for person, roles in iter_roster_entries(payload):
        username = _text(person.get('username'))
        if not username:
            continue
        for role in roles:
            rows.append({
                'role_id': role.get('roleId'),
                'role_type_id': role.get('roleTypeId'),
                'role_type': _text(role.get('role')),
                'username': username,
                'name': _display_name(person),
                'placeholder': is_placeholder(username),
                # WARNING: Reconciled means XRAS linked this username to a real
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


def actions_from_payload(payload: Dict[str, Any],
                         preflights: Optional[Mapping[Any, dict]] = None
                         ) -> List[Dict[str, Any]]:
    """Actions, snake-cased, with the two offer flags precomputed.

    ``preflights`` maps ``actionId`` to a ``verdict_to_dict`` result; when given,
    each action gains a ``preflight`` cell (``None`` for an action nobody checked).
    """
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
            # The recency signal: an Extension's own submitDate is often null, so
            # the entry stamps when it arrived. This is what a date filter must
            # window on — a 2022 request with an Extension entered 2 days ago is
            # recent activity, like admin's "Recent Submissions" shows it.
            'entry_date': _as_date(action.get('entryDate')),
            # Snapshot-derived *offers*, not permissions. The modal's live read
            # is the authority on legality; these only decide which button to
            # draw, and drawing one that XRAS then refuses is a 4xx the modal
            # renders — not a silent failure.
            'can_withdraw': bool(status) and status != DRAFT_ACTION_STATUS
                            and status not in TERMINAL_ACTION_STATUSES,
            'can_resubmit': status == DRAFT_ACTION_STATUS,
            'preflight': (preflights or {}).get(action.get('actionId')),
        })
    return rows


#: Roll-up precedence, worst first — the badge shows the most urgent verdict on
#: any of a request's candidate actions.
_ROLLUP_ORDER = ('failed', 'manual', 'incomplete', 'rechecked')


def _preflight_rollup(preflights: Optional[Mapping[Any, dict]]) -> Optional[str]:
    """The worst verdict across a request's actions, preferring the PENDING ones.

    The badge answers "what would the NEXT push do", so an old applied action
    (``seen_in_log`` / ``applied_inferred``) that no longer validates must not
    poison a request whose pending push is fine. But when EVERY action is already
    applied there is no pending push to describe — fall back to those verdicts so
    the row shows its known state, not a false "not checked". ``None`` only when
    nothing was checked at all (out of the sweep window).
    """
    verdicts = [v for v in (preflights or {}).values() if v]
    if not verdicts:
        return None
    pending = [v for v in verdicts
               if v.get('push_state') not in ('seen_in_log', 'applied_inferred')]
    statuses = {v.get('status') for v in (pending or verdicts)}
    for status in _ROLLUP_ORDER:
        if status in statuses:
            return status
    return None


def latest_action_type(actions) -> Optional[str]:
    """The request's in-flight action type — what admin.xras.org names it.

    The action still in flight (Submitted / Under Review) if any, else the newest
    by ``action_id``. ``None`` only when no action carries a type.
    """
    typed = [a for a in (actions or []) if a.get('action_type')]
    if not typed:
        return None
    in_flight = [a for a in typed
                 if a.get('action_status') in ('Submitted', 'Under Review')]
    latest = max(in_flight or typed, key=lambda a: a.get('action_id') or 0)
    return latest.get('action_type')


def request_index_entry(payload: Dict[str, Any], *, pending_push: bool = False,
                        refreshed_at: Any = None,
                        preflights: Optional[Mapping[Any, dict]] = None
                        ) -> Optional[Dict[str, Any]]:
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
        preflights:   ``actionId`` -> ``verdict_to_dict`` result, stamped onto
                      each action's ``preflight`` cell. Same reasoning as
                      ``pending_push``: the sweep resolves the whole batch, a
                      patch resolves one.

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
    actions = actions_from_payload(payload, preflights)
    # Most-recent activity across the request's actions — what a date filter must
    # window on. Falls back to the request's own submitDate for a request whose
    # actions carry no date.
    _adates = [d for a in actions
               for d in (a.get('entry_date') or a.get('submit_date'),) if d]
    activity_date = max(_adates) if _adates else _as_date(payload.get('submitDate'))
    return {
        'request_number': number,
        'request_id': request_id,
        'status': _text(payload.get('requestStatus')),
        'request_type': _text(payload.get('requestType')),
        'submit_date': _as_date(payload.get('submitDate')),
        # The date the operator cares about: when the current handoff was
        # submitted, not when the request was first created years ago.
        'activity_date': activity_date,
        'begin_date': _as_date(payload.get('beginDate')),
        'end_date': _as_date(payload.get('endDate')),
        'pending_push': bool(pending_push),
        'opportunity_id': payload.get('opportunityId'),
        'opportunity_name': _text(payload.get('opportunity_name')
                                  or payload.get('opportunityName')),
        'pi': {'username': resolve_pi(roster),
               'name': next((r['name'] for r in roster
                             if r.get('role_type_id') == PI_ROLE_TYPE_ID), None)},
        # The Allocation Manager (SAM: "Project Admin"), when the request names
        # one — often it does not, so both keys are None on those rows.
        'admin': {'username': next((r['username'] for r in roster
                                    if r.get('role_type_id') == ADMIN_ROLE_TYPE_ID),
                                   None),
                  'name': next((r['name'] for r in roster
                                if r.get('role_type_id') == ADMIN_ROLE_TYPE_ID),
                               None)},
        'roster': roster,
        'actions': actions,
        # The in-flight action type, precomputed for the card's Type column and
        # its facet — the single "what kind of handoff is this" admin shows.
        'latest_action_type': latest_action_type(actions),
        # Worst pending verdict, precomputed for the card's roll-up badge:
        # would fail > would park (manual) > incomplete > would land.
        'preflight_rollup': _preflight_rollup(preflights),
        # The conjunction the merge fixup keys on, precomputed so the template
        # does not have to express it — see the roster comment.
        'has_stuck_placeholder': any(r['placeholder'] and r['is_reconciled']
                                     for r in roster),
        'refreshed_at': refreshed_at,
    }


def person_roles_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a ``reports/username/<username>`` payload to role-labeled rows.

    The feed groups a person's requests by role name
    (``{requestRoles: [{roleName, requests[]}]}``); this preserves that
    grouping and keeps only what the XRAS User modal renders — a request is a
    link to the Request modal plus a few identifying fields. Deliberately
    **no ``requestStatus``**: this feed does not carry one (probed 2026-08-22),
    and the modal keys each row to the Request modal by number for live state.

    A group with no usable request, and a request with no ``requestNumber``
    (the modal's only link key), is dropped — the same "cost the row, not the
    view" rule the sweep's :func:`request_index_entry` follows.
    """
    groups: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return groups
    for group in payload.get('requestRoles') or ():
        if not isinstance(group, dict):
            continue
        rows: List[Dict[str, Any]] = []
        for req in group.get('requests') or ():
            if not isinstance(req, dict):
                continue
            number = _text(req.get('requestNumber'))
            if not number:
                continue
            rows.append({
                'request_number': number,
                # The feed spells it both ways; take either.
                'request_id': req.get('requestId') or req.get('requestID'),
                'title': _text(req.get('requestTitle')),
                'action_type': _text(req.get('actionType')),
                'allocation_type': _text(req.get('allocationType')),
                'opportunity': _text(req.get('opportunity')),
                'begin_date': _as_date(req.get('beginDate')),
                'end_date': _as_date(req.get('endDate')),
                'pi': _text(req.get('pi')),
                'pi_username': _text(req.get('piUsername')),
            })
        if rows:
            groups.append({'role_name': _text(group.get('roleName')),
                           'requests': rows})
    return groups
