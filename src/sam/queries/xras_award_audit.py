"""XRAS awarded vs SAM allocated: one row per (projcode, XRAS resource) award, with its mismatches.

The XRAS side folds a request family's approved actions (``GET /v1/reports/requests`` shape)
in ``actionId`` order: New/Renewal open a period, Supplement and Adjustment add their amount
(an increment, never a total), Extension and Date Adjustment move dates. The SAM side is the
non-deleted allocation on the project's account for the mapped resource whose window best
overlaps the award. ALLOCATED amounts only: no charge table is read. Pure and read-only;
:func:`fetch_award_lines` is the one network call, kept separate so a caller holding reports
payloads (the recommender's ``sam_crosscheck``) passes them straight to :func:`audit_awards`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Award-side kinds that mean SAM does not hold what XRAS awarded.
MISMATCH_KINDS = ('project_missing', 'resource_unmapped', 'allocation_missing',
                  'amount_differs', 'begin_differs', 'end_differs')
#: Worth an operator's eye, not a discrepancy by themselves.
INFO_KINDS = ('renamed_pending', 'project_inactive', 'approximate', 'sam_only')

#: Amounts are float(15,2) on both sides; anything under a cent is representation noise.
AMOUNT_TOLERANCE = 0.01
#: ``reports/requests`` page budget; hitting it marks the audit incomplete, never silently short.
MAX_PAGES = 25
PAGE_SIZE = 200

_STAGES = ('Approved', 'Recommended', 'Requested')
_OPENING = ('New', 'Renewal')
_INCREMENT = ('Supplement', 'Adjustment', 'Transfer')
_DATING = ('Extension', 'Date Adjustment')


@dataclass
class Award:
    """What XRAS awarded on one resource of one projcode, folded over the family's actions."""
    projcode: str
    resource_id: Any
    resource_label: str
    units: str = ''
    amount: float = 0.0
    begin: Optional[date] = None
    end: Optional[date] = None
    action_ids: List[int] = field(default_factory=list)
    action_types: List[str] = field(default_factory=list)
    approximate: bool = False


@dataclass
class AuditRow:
    """One award checked against SAM (or one SAM allocation XRAS never awarded: ``sam_only``)."""
    projcode: str
    xras_resource: Optional[str]
    sam_resource: Optional[str]
    issues: List[str]
    xras_amount: Optional[float] = None
    sam_amount: Optional[float] = None
    xras_begin: Optional[date] = None
    xras_end: Optional[date] = None
    sam_begin: Optional[date] = None
    sam_end: Optional[date] = None
    allocation_id: Optional[int] = None
    sam_projcode: Optional[str] = None
    units: str = ''
    action_ids: List[int] = field(default_factory=list)

    @property
    def mismatched(self) -> bool:
        return any(k in MISMATCH_KINDS for k in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        for key in ('xras_begin', 'xras_end', 'sam_begin', 'sam_end'):
            out[key] = out[key].isoformat() if out[key] else None
        return out


@dataclass
class AwardAudit:
    rows: List[AuditRow]
    today: date
    include_expired: bool = False
    complete: bool = True

    @property
    def mismatches(self) -> List[AuditRow]:
        return [r for r in self.rows if r.mismatched]

    def counts(self) -> Dict[str, int]:
        out = {k: 0 for k in MISMATCH_KINDS + INFO_KINDS}
        for row in self.rows:
            for kind in row.issues:
                out[kind] = out.get(kind, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {'kind': 'xras_award_audit', 'as_of': self.today.isoformat(),
                'include_expired': self.include_expired, 'complete': self.complete,
                'checked': len(self.rows), 'mismatched': len(self.mismatches),
                'counts': self.counts(), 'rows': [r.to_dict() for r in self.rows]}


def _amount(line: Mapping[str, Any]) -> Optional[float]:
    try:
        return float(line.get('amount'))
    except (TypeError, ValueError):
        return None


def fold_awards(lines: Sequence[Mapping[str, Any]]) -> List[Award]:
    """The current award per resource for one projcode's request lines (pure)."""
    from sam.queries.xras_accounts import live_actions
    from sam.queries.xras_actions import canonical_action_type
    from sam.queries.xras_requests import live_lines
    from sam.xras.preflight import best_dates, best_stage_lines
    from sam.xras.wire import resource_label

    live = live_lines(lines)
    if not live:
        return []
    projcode = str(live[0].get('requestNumber') or '').strip()
    actions = sorted((a for ln in live for a in live_actions(ln)
                      if a.get('actionStatus') == 'Approved'),
                     key=lambda a: a.get('actionId') or 0)
    awards: Dict[Any, Award] = {}
    for action in actions:
        kind = canonical_action_type(action.get('actionType'))
        # Dates fall back through Recommended too, which preflight's own order skips.
        begin, end, _ = best_dates(action, _STAGES)
        if kind in _DATING:
            # Extension walks every account, not resources[] (handlers/extension.py).
            for award in awards.values():
                award.end = end or award.end
                if kind == 'Date Adjustment':
                    award.begin = begin or award.begin
                award.action_ids.append(action.get('actionId'))
                award.action_types.append(kind)
            continue
        staged = best_stage_lines(action)
        # The action's best stage only: a resource absent from Approved was not awarded.
        top = min((rank for rank, _ in staged.values()), default=None)
        for rid, (rank, line) in staged.items():
            if rid is None or rank != top:
                continue
            amount = _amount(line)
            if amount is None:
                continue
            award = awards.get(rid)
            if kind in _OPENING:
                # Update's overlap rule: a window touching the held one extends and adds to it.
                if (award is not None and award.end and begin and award.end >= begin
                        and end and end > award.end):
                    award.amount += amount
                    award.end = end
                else:
                    award = awards[rid] = Award(projcode, rid, resource_label(line, rid),
                                                units=line.get('resourceUnits') or '',
                                                amount=amount, begin=begin, end=end)
            elif kind in _INCREMENT:
                if award is None:
                    award = awards[rid] = Award(projcode, rid, resource_label(line, rid),
                                                units=line.get('resourceUnits') or '',
                                                begin=begin, end=end)
                award.amount += amount
                award.approximate |= kind == 'Transfer'
            else:
                continue
            award.action_ids.append(action.get('actionId'))
            award.action_types.append(kind)
    return sorted(awards.values(), key=lambda a: a.resource_label)


def _in_scope(award: Award, today: date, include_expired: bool) -> bool:
    return include_expired or award.end is None or award.end >= today


def _overlap_days(alloc, begin: Optional[date], end: Optional[date]) -> int:
    a_begin = alloc.start_date.date()
    a_end = alloc.end_date.date() if alloc.end_date else date.max
    return (min(a_end, end or date.max) - max(a_begin, begin or date.min)).days


def _best_allocation(allocations, award: Award, today: date):
    """The allocation whose window overlaps the award's most; ``None`` when nothing overlaps."""
    if not allocations:
        return None
    if award.begin is None and award.end is None:
        current = [a for a in allocations if _overlap_days(a, today, today) >= 0]
        return max(current or allocations, key=lambda a: a.start_date)
    best = max(allocations, key=lambda a: (_overlap_days(a, award.begin, award.end), a.start_date))
    return best if _overlap_days(best, award.begin, award.end) >= 0 else None


def _landed_as(session, numbers: Iterable[str]) -> Dict[str, str]:
    """``request_number -> projcode`` a processed push landed on under another name (newest wins)."""
    from sam.integration.xras import XrasActionLog
    numbers = {n for n in numbers if n}
    if not numbers:
        return {}
    rows = (session.query(XrasActionLog.request_number, XrasActionLog.projcode_result)
            .filter(XrasActionLog.status == 'processed',
                    XrasActionLog.projcode_result.isnot(None),
                    XrasActionLog.request_number.in_(numbers))
            .order_by(XrasActionLog.received_time).all())
    return {n: p for n, p in rows if p and p != n}


def audit_awards(session, payloads: Iterable[Mapping[str, Any]],
                 resource_keys: Optional[Mapping[int, int]], *,
                 today: Optional[date] = None, include_expired: bool = False,
                 complete: bool = True) -> AwardAudit:
    """Check every in-scope XRAS award in *payloads* (reports-shape request lines) against SAM.

    *resource_keys* is ``resource_key_map(get_resources())``: XRAS ``resourceId`` -> key.
    """
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    from sam.integration.xras import XrasResourceRepositoryKeyResource
    from sam.projects.projects import Project
    from sam.xras.submission_preflight import sam_resources_by_xras_id

    today = today or date.today()
    families: Dict[str, List[Mapping[str, Any]]] = {}
    for payload in payloads or ():
        number = str((payload or {}).get('requestNumber') or '').strip()
        if number:
            families.setdefault(number, []).append(payload)

    awards = {n: [a for a in fold_awards(lines) if _in_scope(a, today, include_expired)]
              for n, lines in families.items()}
    awards = {n: a for n, a in awards.items() if a}
    resource_ids = {a.resource_id for group in awards.values() for a in group}
    sam_resources = sam_resources_by_xras_id(session, resource_ids, resource_keys or {})

    projects = {p.projcode: p for p in session.query(Project)
                .filter(Project.projcode.in_(set(awards))).all()} if awards else {}
    renamed = _landed_as(session, set(awards) - set(projects))
    if renamed:
        projects.update({number: p for number, code in renamed.items()
                         for p in session.query(Project).filter(Project.projcode == code)})

    held: Dict[Tuple[int, int], list] = {}
    if projects:
        rows = (session.query(Account.project_id, Account.resource_id, Allocation)
                .join(Allocation, Allocation.account_id == Account.account_id)
                .filter(Account.project_id.in_({p.project_id for p in projects.values()}),
                        Allocation.deleted.is_(False))  # noqa: E712 -- soft-delete, not an active check
                .all())
        for project_id, resource_id, alloc in rows:
            held.setdefault((project_id, resource_id), []).append(alloc)
    mapped_ids = {rid for (rid,) in session.query(XrasResourceRepositoryKeyResource.resource_id)}

    out: List[AuditRow] = []
    for number in sorted(awards):
        project = projects.get(number)
        claimed = set()
        for award in awards[number]:
            resource = sam_resources.get(award.resource_id)
            row = AuditRow(projcode=number, xras_resource=award.resource_label,
                           sam_resource=resource.resource_name if resource else None,
                           issues=[], xras_amount=award.amount, xras_begin=award.begin,
                           xras_end=award.end, units=award.units,
                           action_ids=list(award.action_ids))
            out.append(row)
            if award.approximate:
                row.issues.append('approximate')
            if project is None:
                row.issues.append('project_missing')
                continue
            row.sam_projcode = project.projcode
            if project.projcode != number:
                row.issues.append('renamed_pending')
            if not project.is_active:
                row.issues.append('project_inactive')
            if resource is None:
                row.issues.append('resource_unmapped')
                continue
            claimed.add(resource.resource_id)
            alloc = _best_allocation(held.get((project.project_id, resource.resource_id)),
                                     award, today)
            if alloc is None:
                row.issues.append('allocation_missing')
                continue
            row.allocation_id = alloc.allocation_id
            row.sam_amount = alloc.amount
            row.sam_begin = alloc.start_date.date()
            row.sam_end = alloc.end_date.date() if alloc.end_date else None
            if abs((alloc.amount or 0.0) - award.amount) > AMOUNT_TOLERANCE:
                row.issues.append('amount_differs')
            if award.begin and row.sam_begin != award.begin:
                row.issues.append('begin_differs')
            if award.end and row.sam_end != award.end:
                row.issues.append('end_differs')
        if project is not None:
            out.extend(_sam_only(project, number, held, mapped_ids - claimed, today))
    return AwardAudit(rows=out, today=today, include_expired=include_expired,
                      complete=complete)


def _sam_only(project, number, held, unclaimed_ids, today) -> List[AuditRow]:
    """Allocations live today on an XRAS-mapped resource that no in-scope award names."""
    rows = []
    for (project_id, resource_id), allocs in held.items():
        if project_id != project.project_id or resource_id not in unclaimed_ids:
            continue
        for alloc in allocs:
            if _overlap_days(alloc, today, today) < 0:
                continue
            rows.append(AuditRow(
                projcode=number, xras_resource=None,
                sam_resource=alloc.account.resource.resource_name, issues=['sam_only'],
                sam_amount=alloc.amount, sam_begin=alloc.start_date.date(),
                sam_end=alloc.end_date.date() if alloc.end_date else None,
                allocation_id=alloc.allocation_id, sam_projcode=project.projcode))
    return rows


def fetch_award_lines(client, *, projcode: Optional[str] = None, include_expired: bool = False,
                      max_pages: int = MAX_PAGES) -> Tuple[List[Dict[str, Any]], bool]:
    """``(request lines, complete)`` from XRAS: one projcode's family, else every approved request.

    ``active=true`` (approved, end date ahead) unless *include_expired*. It omits an approved
    request with no end date yet, which is the not-yet-pushed population ``--readiness`` covers.
    """
    if projcode:
        return client.get_request_family_by_number(projcode), True
    kwargs = {'status': 'Approved'} if include_expired else {'status': None, 'active': True}
    lines: List[Dict[str, Any]] = []
    pages, last_size = 0, 0
    for page in client.iter_request_pages(page_size=PAGE_SIZE, max_pages=max_pages, **kwargs):
        pages, last_size = pages + 1, len(page)
        lines.extend(r for r in page if isinstance(r, dict))
    return lines, pages < max_pages or last_size < PAGE_SIZE
