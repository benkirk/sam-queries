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


#: Display order of the resources a card lists; any other sorts after, by name.
RESOURCE_ORDER = ('Derecho', 'Derecho GPU', 'Casper', 'Casper GPU', 'Campaign_Store')


def _access_only(resource: Mapping[str, Any]) -> bool:
    """A yes/no resource, or a limit of 1/1 (the seed's "a yes, not a quota")."""
    limit = resource.get('limit')
    return bool(resource.get('yes_no')
                or (limit is not None and limit.max_amount == 1 and limit.default_amount in (None, 1)))


def _ordered(resources) -> Tuple[Dict[str, Any], ...]:
    def key(r):
        name = r.get('sam_name') or ''
        return (RESOURCE_ORDER.index(name) if name in RESOURCE_ORDER else len(RESOURCE_ORDER),
                r.get('name') or '')
    return tuple(dict(r, access=_access_only(r)) for r in sorted(resources, key=key))


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
    def amounts(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(r for r in self.resources if not r['access'])

    @property
    def access(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(r for r in self.resources if r['access'])

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


def _sam_limits(limits: Mapping[str, Any]) -> Tuple[Dict[str, Any], ...]:
    """A closed type's resources: its SAM limits by resource name."""
    return tuple({'resource_id': None, 'name': name, 'units': '', 'sam_name': name,
                  'limit': limit, 'yes_no': False} for name, limit in sorted(limits.items()))


def allocation_type_overview(session, opportunities: Sequence[Mapping[str, Any]], *,
                             rules_for: Callable[[Optional[int], Optional[str]], Any],
                             resource_keys: Optional[Mapping[int, int]] = None,
                             eligible: Optional[Callable[[AllocationTypeGuide], bool]] = None
                             ) -> List[TypeCard]:
    """The cards, in the guide's display order.

    *opportunities* are ``new_request_opportunities`` views; *rules_for(type_id, type_name)*
    returns the New rule book; *eligible* narrows the public rows (the logged-in filter).
    The resource map and every type's limits are read once for all the cards.
    """
    from sam.accounting.allocation_type_limits import limits_for_allocation_types
    from sam.xras.submission_preflight import annotate_resources, sam_resources_by_xras_id
    by_type = _pick(opportunities)
    guides = [g for g in public_guides(session) if eligible is None or eligible(g)]
    limits = limits_for_allocation_types(session, [g.allocation_type for g in guides])
    sam = sam_resources_by_xras_id(session, {
        r['resource_id'] for g in guides
        for r in (by_type.get(g.xras_allocation_type_id) or {}).get('resources') or ()},
        resource_keys)
    cards = []
    for guide in guides:
        opp = by_type.get(guide.xras_allocation_type_id)
        type_limits = limits.get(guide.allocation_type, {})
        if opp is not None:
            resources = _ordered(annotate_resources(
                [dict(r) for r in opp.get('resources') or ()], sam, type_limits))
            rules = rules_for(opp.get('allocation_type_id'), opp.get('allocation_type'))
        else:
            resources, rules = _ordered(_sam_limits(type_limits)), None
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
