"""The request page's overview: one card per allocation type, before anyone starts a request.

Composes SAM's own copy (``allocation_type_guide``) with what is computed: the open
opportunity and its award period (XRAS), the resources it offers with each one's default and
ceiling (``allocation_type_resource_limit``), and the grants and documents its rule book
demands. A type with no open opportunity (Large between calls) still shows, with its SAM
limits and no Start. Only guided types appear: the active guide row is the publish switch.
Flask-free. Design: ``docs/plans/XRAS_SUBMISSION_SPRINT2.md`` § 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sam.accounting.allocation_type_guide import AllocationTypeGuide, public_guides


@dataclass(frozen=True)
class TypeCard:
    xras_allocation_type_id: int
    title: str
    summary: Optional[str]
    funding: Optional[str]
    cadence: Optional[str]
    notes: Optional[str]
    opportunity: Optional[Dict[str, Any]] = None
    resources: Tuple[Dict[str, Any], ...] = ()
    rules: Any = None
    reason: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return bool(self.opportunity and self.opportunity.get('can_submit'))


def _pick(opportunities: Sequence[Mapping[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """One opportunity per XRAS allocation type: an open one first, then by name."""
    chosen: Dict[int, Dict[str, Any]] = {}
    for opp in sorted(opportunities, key=lambda o: (not o.get('can_submit'),
                                                     o.get('opportunity_name') or '')):
        type_id = opp.get('allocation_type_id')
        if type_id is not None:
            chosen.setdefault(int(type_id), dict(opp))
    return chosen


def _sam_limits(session, type_name: Optional[str]) -> Tuple[Dict[str, Any], ...]:
    """A closed type's resources: its SAM limits by resource name."""
    from sam.accounting.allocation_type_limits import limits_for_allocation_type
    if not type_name:
        return ()
    return tuple({'resource_id': None, 'name': name, 'units': '', 'sam_name': name,
                  'limit': limit, 'yes_no': False}
                 for name, limit in sorted(limits_for_allocation_type(session, type_name).items()))


def allocation_type_overview(session, opportunities: Sequence[Mapping[str, Any]], *,
                             rules_for: Callable[[Optional[int], Optional[str]], Any],
                             resource_keys: Optional[Mapping[int, int]] = None,
                             eligible: Optional[Callable[[AllocationTypeGuide], bool]] = None
                             ) -> List[TypeCard]:
    """The cards, in the guide's display order.

    *opportunities* are ``new_request_opportunities`` views; *rules_for(type_id, type_name)*
    returns the New rule book; *eligible* narrows the public rows (the logged-in filter).
    """
    from sam.xras.submission_preflight import resources_with_limits
    by_type = _pick(opportunities)
    cards = []
    for guide in public_guides(session):
        if eligible is not None and not eligible(guide):
            continue
        opp = by_type.get(guide.xras_allocation_type_id)
        if opp is not None:
            resources = tuple(resources_with_limits(
                session, [dict(r) for r in opp.get('resources') or ()], resource_keys,
                guide.allocation_type))
            rules = rules_for(opp.get('allocation_type_id'), opp.get('allocation_type'))
        else:
            resources, rules = _sam_limits(session, guide.allocation_type), None
        reason = None
        if opp is None:
            reason = 'Not open for requests now.'
        elif not opp.get('can_submit'):
            said = opp.get('reason')
            said = '; '.join(map(str, said)) if isinstance(said, (list, tuple)) else said
            reason = said or 'XRAS is not taking new requests here now.'
        cards.append(TypeCard(
            xras_allocation_type_id=guide.xras_allocation_type_id, title=guide.title,
            summary=guide.summary or (opp or {}).get('description'),
            funding=guide.funding, cadence=guide.cadence, notes=guide.notes,
            opportunity=opp, resources=resources, rules=rules, reason=reason))
    return cards
