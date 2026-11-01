"""The request page's overview: one card per allocation type, before anyone starts a request.

Composes SAM's own copy (``allocation_type_guide``) with what is computed: the open
opportunity and its award period (XRAS), the resources it offers with each one's default and
ceiling (``allocation_type_resource_limit``), and the grants and documents its rule book
demands. A type with no open opportunity (Large between calls) still shows, with its SAM
limits and no Start. The active guide row is the publish switch; ``audiences`` narrows the
rows to the types for those audiences (``sam/xras/audiences.py``). ``include_unguided`` (the
admin's page) adds the retired guide rows, the SAM types with limits but no guide row, and the
XRAS types no guide row names. Flask-free. Design: ``docs/plans/implemented/XRAS_SUBMISSION_SPRINT2.md`` § 0 and
``docs/plans/XRAS_SUBMISSION_SPRINT3.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sam.accounting.allocation_type_guide import AllocationTypeGuide, guides_for_audiences


#: Display order of the resources a card lists; any other sorts after, by name.
RESOURCE_ORDER = ('Derecho', 'Derecho GPU', 'Casper', 'Casper GPU', 'Campaign_Store')


def _access_only(resource: Mapping[str, Any]) -> bool:
    """A yes/no resource, or a limit of 1/1 (the seed's "a yes, not a quota")."""
    limit = resource.get('limit')
    return bool(resource.get('yes_no')
                or (limit is not None and limit.max_amount == 1 and limit.default_amount in (None, 1)))


def _order_key(name: str, label: str = ''):
    return (RESOURCE_ORDER.index(name) if name in RESOURCE_ORDER else len(RESOURCE_ORDER),
            label or name)


def _ordered(resources) -> Tuple[Dict[str, Any], ...]:
    return tuple(dict(r, access=_access_only(r)) for r in sorted(
        resources, key=lambda r: _order_key(r.get('sam_name') or '', r.get('name') or '')))


def _ledger(limits: Mapping[str, Any]) -> Tuple[Dict[str, Any], ...]:
    """The SAM type's limit rows, active or not, in resource order (the admin edits these)."""
    return tuple({'name': name, 'limit': limit}
                 for name, limit in sorted(limits.items(), key=lambda kv: _order_key(kv[0])))


@dataclass(frozen=True)
class TypeCard:
    xras_allocation_type_id: Optional[int]
    title: str
    summary: Optional[str]
    funding: Optional[str]
    cadence: Optional[str]
    notes: Optional[str]
    opportunity: Optional[Dict[str, Any]] = None
    resources: Tuple[Dict[str, Any], ...] = ()
    rules: Any = None
    reason: Optional[str] = None
    audience: Optional[str] = None
    allocation_type: Optional[str] = None
    guide: Optional[AllocationTypeGuide] = None
    limits: Tuple[Dict[str, Any], ...] = ()

    @property
    def published(self) -> bool:
        """On the request page: an active guide row."""
        return bool(self.guide is not None and self.guide.active)

    @property
    def anchor(self) -> str:
        if self.xras_allocation_type_id is not None:
            return f'type-{self.xras_allocation_type_id}'
        return 'type-sam-' + re.sub(r'[^a-z0-9]+', '-', (self.allocation_type or '').lower())

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


def _limited_type_names(session) -> List[str]:
    """The active SAM allocation type names with at least one limit row."""
    from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
    from sam.accounting.allocations import AllocationType
    return [n for (n,) in session.query(AllocationType.allocation_type).distinct()
            .join(AllocationTypeResourceLimit, AllocationTypeResourceLimit.allocation_type_id
                  == AllocationType.allocation_type_id)
            .filter(AllocationType.is_active).order_by(AllocationType.allocation_type)]


def _reason(opp: Optional[Mapping[str, Any]]) -> Optional[str]:
    if opp is None:
        return 'Not open for requests now.'
    if not opp.get('can_submit'):
        said = opp.get('reason')
        said = '; '.join(map(str, said)) if isinstance(said, (list, tuple)) else said
        return said or 'XRAS is not taking new requests here now.'
    return None


def allocation_type_overview(session, opportunities: Sequence[Mapping[str, Any]], *,
                             rules_for: Callable[[Optional[int], Optional[str]], Any],
                             audiences: Optional[Iterable[str]],
                             resource_keys: Optional[Mapping[int, int]] = None,
                             include_unguided: bool = False) -> List[TypeCard]:
    """The cards, in the guide's display order; the retired and unguided ones last.

    *opportunities* are ``new_request_opportunities`` views; *rules_for(type_id, type_name)*
    returns the New rule book; *audiences* ``None`` is every audience. The guide rows, the
    resource map and every type's limits are read once for all the cards.
    """
    from sam.accounting.allocation_type_limits import limits_for_allocation_types
    from sam.xras.submission_preflight import annotate_resources, sam_resources_by_xras_id
    by_type = _pick(opportunities)
    retired: List[AllocationTypeGuide] = []
    unguided: List[str] = []
    orphans: List[Mapping[str, Any]] = []
    if include_unguided:
        wanted = None if audiences is None else set(audiences)
        rows = (session.query(AllocationTypeGuide)
                .order_by(AllocationTypeGuide.display_order, AllocationTypeGuide.title).all())
        guides = [g for g in rows if g.active
                  and (wanted is None or g.audience is None or g.audience in wanted)]
        retired = [g for g in rows if not g.active]
        named = {g.allocation_type for g in rows}
        unguided = [n for n in _limited_type_names(session) if n not in named]
        guided = {g.xras_allocation_type_id for g in rows}
        orphans = sorted((o for t, o in by_type.items() if t not in guided),
                         key=lambda o: o.get('allocation_type') or '')
    else:
        guides = guides_for_audiences(session, audiences)
    every = limits_for_allocation_types(
        session, [g.allocation_type for g in guides + retired] + unguided,
        active_only=not include_unguided)
    limits = {name: {r: lim for r, lim in by_resource.items() if lim.active}
              for name, by_resource in every.items()}
    offered = [by_type.get(g.xras_allocation_type_id) for g in guides + retired] + orphans
    sam = sam_resources_by_xras_id(session, {
        r['resource_id'] for opp in offered if opp for r in opp.get('resources') or ()},
        resource_keys)
    cards = []
    for guide in guides + retired:
        opp = by_type.get(guide.xras_allocation_type_id)
        type_limits = limits.get(guide.allocation_type, {})
        if opp is not None:
            resources = _ordered(annotate_resources(
                [dict(r) for r in opp.get('resources') or ()], sam, type_limits))
            rules = rules_for(opp.get('allocation_type_id'), opp.get('allocation_type'))
        else:
            resources, rules = _ordered(_sam_limits(type_limits)), None
        cards.append(TypeCard(
            xras_allocation_type_id=guide.xras_allocation_type_id, title=guide.title,
            summary=guide.summary or (opp or {}).get('description'),
            funding=guide.funding, cadence=guide.cadence, notes=guide.notes,
            opportunity=opp, resources=resources, rules=rules, reason=_reason(opp),
            audience=guide.audience, allocation_type=guide.allocation_type, guide=guide,
            limits=_ledger(every.get(guide.allocation_type, {}))))
    for name in unguided:
        cards.append(TypeCard(
            xras_allocation_type_id=None, title=name, summary=None, funding=None, cadence=None,
            notes=None, resources=_ordered(_sam_limits(limits.get(name, {}))),
            reason='Not on the request page.', allocation_type=name,
            limits=_ledger(every.get(name, {}))))
    for opp in orphans:
        cards.append(TypeCard(
            xras_allocation_type_id=int(opp['allocation_type_id']),
            title=opp.get('allocation_type') or opp.get('opportunity_name') or 'XRAS type',
            summary=opp.get('description'), funding=None, cadence=None, notes=None,
            opportunity=opp, reason=_reason(opp),
            resources=_ordered(annotate_resources([dict(r) for r in opp.get('resources') or ()],
                                                  sam, {})),
            rules=rules_for(opp.get('allocation_type_id'), opp.get('allocation_type'))))
    return cards
