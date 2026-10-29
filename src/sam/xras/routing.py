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
