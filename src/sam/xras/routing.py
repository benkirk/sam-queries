"""Which projects may request an action through XRAS, and why not when they cannot.

Two gates. The **policy**, keyed by allocation-type name, says where a project's requests
go (``xras``, ``email:<address>``, ``staff``). For ``xras`` the **family read** must hold a
live line and the user must hold an active role on it: every request-scoped write
authorizes on ``XA-USER``. Roster drift is refused with its reason, never worked around.
Flask-free. Design: ``docs/plans/XRAS_SUBMISSION.md`` § 4.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: Route per SAM allocation-type name. An unlisted name routes ``staff``.
XRAS_REQUEST_ROUTES: Dict[str, str] = {
    'CHAP': 'xras',
    'Small': 'xras',
    'Small (No NSF award)': 'xras',
    'Data': 'xras',
    'Classroom': 'xras',
    'NSC': 'xras',
    'External Project': 'xras',
    # Extensions are a Wyoming committee decision; flip to xras when WNA opts in.
    'WRAP': 'email:wrap@uwyo.edu',
    'Divisional': 'staff',
    'Staff': 'staff',
    'CISL Reserve': 'staff',
    'Director Reserve': 'staff',
}

#: ``RoutingDecision.reason`` values, one vocabulary for the picker and the tests.
ROUTING_REASONS = {
    'lever_off': 'Requests through SAM are not enabled here.',
    'policy': 'Requests for this project are handled by CISL staff.',
    'email': 'Requests for this project go by email.',
    'no_family': 'XRAS holds no live request for this project.',
    'not_on_roster': 'You hold no role on this project\'s XRAS request.',
    'pending': 'A request on this project is already in progress.',
}


@dataclass(frozen=True)
class RoutingDecision:
    route: str                                  # xras | email | staff
    reason: Optional[str] = None                # a ROUTING_REASONS key; None = may file
    detail: Optional[str] = None
    email: Optional[str] = None
    request_number: Optional[str] = None
    request_id: Optional[int] = None
    pi_username: Optional[str] = None
    line: Optional[Dict[str, Any]] = None

    @property
    def allowed(self) -> bool:
        return self.route == 'xras' and self.reason is None

    @property
    def message(self) -> Optional[str]:
        return ROUTING_REASONS.get(self.reason) if self.reason else None


def route_for(allocation_type_name: Optional[str]) -> str:
    return XRAS_REQUEST_ROUTES.get(allocation_type_name or '', 'staff')


def request_routing(session, project, family: Optional[List[Dict[str, Any]]],
                    username: str, *, armed: bool) -> RoutingDecision:
    """The decision for *username* filing on *project*, given its family read."""
    from sam.integration.xras_submission import XrasSubmission
    from sam.queries.xras_requests import (
        live_lines,
        primary_line,
        resolve_pi,
        roster_from_payload,
    )

    type_name = project.allocation_type.allocation_type if project.allocation_type else None
    route = route_for(type_name)
    if route.startswith('email:'):
        return RoutingDecision('email', reason='email', email=route.split(':', 1)[1])
    if route != 'xras':
        detail = None if type_name in XRAS_REQUEST_ROUTES else \
            f'allocation type {type_name or "(none)"} has no XRAS route'
        return RoutingDecision('staff', reason='policy', detail=detail)
    if not armed:
        return RoutingDecision('xras', reason='lever_off')

    line = primary_line(live_lines(family))
    if line is None:
        return RoutingDecision('xras', reason='no_family')
    roster = roster_from_payload(line)
    keys = dict(request_number=line.get('requestNumber'), request_id=line.get('requestId'),
                pi_username=resolve_pi(roster), line=line)
    if not any(r['username'] == username and r.get('active', True) for r in roster):
        return RoutingDecision('xras', reason='not_on_roster', **keys)
    pending = (session.query(XrasSubmission.xras_submission_id)
               .filter(XrasSubmission.is_active)
               .filter(XrasSubmission.project_id == project.project_id).first())
    if pending is not None:
        return RoutingDecision('xras', reason='pending',
                               detail=f'xras_submission {pending[0]}', **keys)
    return RoutingDecision('xras', **keys)


#: The actions the picker offers on an existing project, in display order.
#: Kept as a literal: the inbound boundary gate keeps ``xras_api.vocabulary`` out of
#: ``sam.xras``; ``test_xras_routing`` pins it to ``SUBMIT_ACTION_TYPES`` minus ``New``.
PICKER_ACTION_TYPES = ('Extension', 'Supplement', 'Renewal')


@dataclass(frozen=True)
class Offer:
    action_type: str
    reason: Optional[str] = None                # None = may file
    opportunity_id: Optional[int] = None
    resource_ids: tuple = ()

    @property
    def enabled(self) -> bool:
        return self.reason is None


def offer_candidates(projects, username: str, index_rows, *, armed: bool) -> list:
    """The *projects* whose row may carry request buttons: XRAS-routed, *username* on its roster.

    No I/O: the caller reads XRAS (the Renewal offers) only when this is non-empty.
    """
    if not armed:
        return []
    on_roster = {row.get('request_number') for row in index_rows or ()
                 if any((p.get('username') or '').casefold() == username.casefold()
                        and p.get('active', True) for p in row.get('roster') or ())}
    return [p for p in projects if p.projcode in on_roster and route_for(
        p.allocation_type.allocation_type if p.allocation_type else None) == 'xras']


def inline_offers(session, projects, username: str, index_rows, available, allows,
                  *, armed: bool) -> Dict[str, List[str]]:
    """``{projcode: [action types]}`` a project row may open, without a family read per row.

    The roster comes from the sweep's index and the pending check from SAM, so a row can be a
    day stale; the form's own gate (``request_routing`` on a live read) stays the authority.
    """
    from sam.integration.xras_submission import XrasSubmission
    candidates = offer_candidates(projects, username, index_rows, armed=armed)
    if not candidates:
        return {}
    pending = {pid for (pid,) in session.query(XrasSubmission.project_id)
               .filter(XrasSubmission.is_active)
               .filter(XrasSubmission.project_id.in_([p.project_id for p in candidates]))}
    renewable = {a.get('request_number') for a in available or ()
                 if a.get('action_type') == 'Renewal'}
    out = {}
    for project in candidates:
        if project.project_id in pending:
            continue
        out[project.projcode] = [t for t in PICKER_ACTION_TYPES if allows(t) and (
            t != 'Renewal' or project.projcode in renewable)]
    return out


def action_offers(decision: RoutingDecision, available: Optional[List[Dict[str, Any]]],
                  allows) -> List[Offer]:
    """One offer per picker action, disabled with its reason, never hidden.

    *available* is ``available_action_requests`` for the user (``None`` when XRAS did not
    answer); a Renewal needs its entry, which names the opportunity to renew under.
    *allows* is ``XrasApiConfig.submit_allows``.
    """
    offers = []
    for action_type in PICKER_ACTION_TYPES:
        if not decision.allowed:
            offers.append(Offer(action_type, decision.message))
        elif not allows(action_type):
            offers.append(Offer(action_type, f'{action_type} requests through SAM are not '
                                             'enabled here yet.'))
        elif action_type != 'Renewal':
            offers.append(Offer(action_type))
        elif available is None:
            offers.append(Offer(action_type, 'XRAS did not answer, so renewals cannot be '
                                             'offered right now.'))
        else:
            entry = next((a for a in available if a.get('action_type') == 'Renewal'
                          and a.get('request_number') == decision.request_number), None)
            offers.append(Offer(action_type, None if entry else
                                'XRAS offers no renewal for this project right now.',
                                opportunity_id=(entry or {}).get('opportunity_id'),
                                resource_ids=tuple((entry or {}).get('resource_ids') or ())))
    return offers
