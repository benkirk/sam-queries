"""Push-readiness: would XRAS's next push of this action land, if sent today?

Synthesizes the inbound-wire action from an outbound ``reports/requests`` payload
and runs ``dispatch_action(validate_only=True)`` — the same never-writes path the
re-check surface uses — so the ordered 422 list is known before the 422 exists.
Pure but for the one dispatch call and the ``infer_applied`` reads; Flask-free;
``sam.xras.handlers`` is imported deferred inside :func:`preflight_action`, so
importing this module registers nothing and drags in no webapp/cli graph.

The verdict is advisory. A field it cannot synthesize is a ``gap`` and the row
reads ``incomplete`` — never a guessed green. See
``docs/plans/XRAS_PUSH_READINESS.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import (Any, Dict, Iterator, Mapping, Optional, Tuple)

from sam.queries.xras_actions import canonical_action_type

logger = logging.getLogger(__name__)

__all__ = ['Synthesis', 'Verdict', 'iter_candidate_actions',
           'synthesize_action', 'preflight_action', 'infer_applied',
           'verdict_to_dict']

#: Award trail, best first — the stage whose amounts/dates the push would carry.
_STAGE_ORDER: Tuple[str, ...] = ('Approved', 'Recommended', 'Requested')
_DATE_STAGE_ORDER: Tuple[str, ...] = ('Approved', 'Requested')

#: Dates a given action type must carry on the wire to be synthesizable. Only New
#: and Renewal mint/replace a full window; an Extension supplies the new end alone
#: (the handler inherits the begin from the existing allocation); every other type
#: inherits both. Keyed on the canonical action type. See the handler audit in
#: docs/plans/XRAS_PUSH_READINESS.md.
_REQUIRED_DATES: Dict[str, frozenset] = {
    'New': frozenset({'begin', 'end'}),
    'Renewal': frozenset({'begin', 'end'}),
    'Extension': frozenset({'end'}),
}


@dataclass(frozen=True)
class Synthesis:
    action: Optional[dict]          # the inbound-wire dict, or None if unusable
    gaps: Tuple[str, ...]
    action_id: Optional[int]
    action_type: Optional[str]
    stage: str                      # Approved | Recommended | Requested


@dataclass(frozen=True)
class Verdict:
    status: str                     # rechecked | failed | manual | incomplete
    would_succeed: bool
    messages: Tuple[str, ...]       # the ordered 422 list, verbatim — display-only
    gaps: Tuple[str, ...]
    service: Optional[str]
    warnings: Tuple[str, ...]
    action_id: Optional[int]
    action_type: Optional[str]
    action_status: Optional[str]
    request_status: Optional[str]
    stage: str
    push_state: str                 # seen_in_log | applied_inferred | pending | unknown
    push_detail: Optional[dict]
    checked_at: datetime
    resolved: Optional[dict]


def _parse_date(value: Any) -> Optional[date]:
    """A ``%Y-%m-%d`` (or ISO datetime) wire string to a date, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _entry_date(action: dict) -> Optional[date]:
    return _parse_date(action.get('entryDate') or action.get('submitDate'))


def iter_candidate_actions(report_payload: dict, *,
                           since: Optional[date] = None) -> Iterator[dict]:
    """Yield the actions worth a preflight: not Declined, not deleted, in-window."""
    if not isinstance(report_payload, dict):
        return
    for action in report_payload.get('actions') or ():
        if not isinstance(action, dict):
            continue
        if (action.get('actionStatus') or '') == 'Declined':
            continue
        if action.get('isDeleted'):
            continue
        if since is not None:
            when = _entry_date(action)
            if when is not None and when < since:
                continue
        yield action


def _best_dates(action: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """The action's begin/end at the best available allocationDate stage."""
    by_type: Dict[str, dict] = {}
    for entry in action.get('allocationDates') or ():
        if isinstance(entry, dict):
            by_type[str(entry.get('allocationDateType') or '')] = entry
    for stage in _DATE_STAGE_ORDER:
        entry = by_type.get(stage)
        if entry and (entry.get('beginDate') or entry.get('endDate')):
            begin = _parse_date(entry.get('beginDate'))
            end = _parse_date(entry.get('endDate'))
            return (begin.strftime('%Y-%m-%d') if begin else None,
                    end.strftime('%Y-%m-%d') if end else None, stage)
    return None, None, None


def _best_resource_lines(action: dict) -> Tuple[list, Optional[str]]:
    """One line per resourceId at its best stage: (resourceId, amount, comments)."""
    best: Dict[Any, Tuple[int, dict]] = {}
    for line in action.get('resources') or ():
        if not isinstance(line, dict):
            continue
        rid = line.get('resourceId')
        rank = _STAGE_ORDER.index(line['type']) if line.get('type') in _STAGE_ORDER \
            else len(_STAGE_ORDER)
        prev = best.get(rid)
        if prev is None or rank < prev[0]:
            best[rid] = (rank, line)
    stage = None
    lines = []
    for rid, (rank, line) in best.items():
        if rank < len(_STAGE_ORDER):
            line_stage = _STAGE_ORDER[rank]
            if stage is None or _STAGE_ORDER.index(line_stage) < _STAGE_ORDER.index(stage):
                stage = line_stage
        lines.append((rid, line.get('amount'), line.get('comments')))
    return lines, stage


def _flat_roles(report_payload: dict) -> list:
    """Flatten nested ``roles[].person × roles[].roles`` to the inbound flat shape."""
    from sam.queries.xras_accounts import iter_roster_entries
    out = []
    for person, roles in iter_roster_entries(report_payload):
        username = str(person.get('username') or '').strip()
        if not username:
            continue
        for role in roles:
            out.append({'roleType': role.get('role'),
                        'username': username,
                        'beginDate': role.get('beginDate'),
                        'endDate': role.get('endDate')})
    return out


def synthesize_action(report_payload: dict, action: dict, *,
                      resource_keys: Optional[Mapping[int, int]],
                      opportunities: Optional[Mapping[int, dict]]) -> Synthesis:
    """Build the inbound-wire dict this action would arrive as, or record why not."""
    gaps: list = []
    action_id = action.get('actionId')
    action_type = action.get('actionType')

    begin, end, date_stage = _best_dates(action)
    # Only the dates the handler for this type actually reads off the wire are
    # required; the rest are inherited from the existing allocation, so demanding
    # them (the New-only assumption) is what wrongly stranded every non-New action.
    required = _REQUIRED_DATES.get(canonical_action_type(action_type), frozenset())
    if ('begin' in required and begin is None) or ('end' in required and end is None):
        gaps.append('no_allocation_dates')

    resource_lines, amount_stage = _best_resource_lines(action)
    resources = []
    for rid, amount, comments in resource_lines:
        key = resource_keys.get(rid) if resource_keys else None
        if key is None:
            gaps.append(f'resource_id_unmapped:{rid}')
            continue
        resources.append({'resourceRepositoryKey': key,
                          'awardedAmount': '' if amount is None else str(amount),
                          'comments': comments})

    opp_id = report_payload.get('opportunityId')
    opp = opportunities.get(opp_id) if opportunities and opp_id is not None else None
    allocation_type = opp.get('allocationType') if opp else None

    stage = amount_stage or date_stage or 'Requested'

    # A fatal gap means the synthetic dict cannot faithfully stand in for the wire;
    # report incomplete rather than a fabricated failure.
    fatal = [g for g in gaps
             if g == 'no_allocation_dates' or g.startswith('resource_id_unmapped:')]
    if fatal:
        return Synthesis(None, tuple(gaps), action_id, action_type, stage)

    synthetic = {
        'actionId': action_id,
        'actionType': action_type,
        'actionBeginDate': begin,
        'actionEndDate': end,
        'requestId': report_payload.get('requestId'),
        'requestNumber': report_payload.get('requestNumber'),
        'requestType': report_payload.get('requestType'),
        'requestTitle': report_payload.get('title'),
        'requestAbstract': report_payload.get('abstract'),
        'requestShortTitle': report_payload.get('shortTitle'),
        'opportunityId': opp_id,
        'opportunityName': (report_payload.get('opportunity_name')
                            or report_payload.get('opportunityName')),
        'allocationType': allocation_type,
        'resources': resources,
        'roles': _flat_roles(report_payload),
        'fos': [f for f in (report_payload.get('fos') or ()) if isinstance(f, dict)],
        'grants': [g for g in (report_payload.get('grants') or ()) if isinstance(g, dict)],
    }

    from sam.schemas.forms.xras import XrasActionSchema
    from marshmallow import ValidationError
    try:
        loaded = XrasActionSchema().load(synthetic)
    except (ValidationError, ValueError) as exc:
        logger.debug('preflight: synthesized action %s did not load (%s)',
                     action_id, exc)
        gaps.append('schema_invalid')
        return Synthesis(None, tuple(gaps), action_id, action_type, stage)

    return Synthesis(loaded, tuple(gaps), action_id, action_type, stage)


def infer_applied(session, synthesis: Synthesis) -> Optional[dict]:
    """Does SAM state already reflect this action? Exact for Extension, else heuristic."""
    action = synthesis.action
    if action is None:
        return None
    from sam.projects.projects import Project
    from sam.xras.wire import get_field
    from sam.xras.handlers._allocations import (account_is_active,
                                                effective_end_date,
                                                latest_allocation)

    action_type = synthesis.action_type
    projcode = (get_field(action, 'requestNumber') or '').strip()
    if not projcode:
        return None
    project = Project.get_by_projcode(session, projcode)
    if project is None:
        return None

    if action_type == 'Extension':
        end = _parse_date(get_field(action, 'actionEndDate'))
        if end is None:
            return None
        now = datetime.now()
        targets = 0
        for account in project.accounts:
            if not account_is_active(account, now) or not account.allocations:
                continue
            allocation = latest_allocation(account)
            if allocation is None:
                continue
            targets += 1
            existing = effective_end_date(allocation)
            if existing is None or existing.date() < end:
                return None            # a target still short of the action end
        if targets:
            return {'basis': 'extension_end', 'end': end.isoformat(),
                    'heuristic': False}
        return None

    if action_type in ('Supplement', 'Adjustment'):
        from sam.accounting.allocations import Allocation, AllocationTransaction
        amounts = {str(get_field(r, 'awardedAmount'))
                   for r in (get_field(action, 'resources') or ())}
        floats = set()
        for a in amounts:
            try:
                floats.add(float(a))
            except (ValueError, TypeError):
                continue
        if not floats:
            return None
        account_ids = [acc.account_id for acc in project.accounts]
        if not account_ids:
            return None
        q = (session.query(AllocationTransaction)
             .join(Allocation,
                   AllocationTransaction.allocation_id == Allocation.allocation_id)
             .filter(Allocation.account_id.in_(account_ids),
                     AllocationTransaction.transaction_amount.in_(floats)))
        if q.first() is not None:
            return {'basis': 'transaction_amount', 'heuristic': True}
        return None

    return None


def preflight_action(session, report_payload: dict, action: dict, *,
                     resource_keys: Optional[Mapping[int, int]] = None,
                     opportunities: Optional[Mapping[int, dict]] = None,
                     enabled=None,
                     log_seen: Optional[Mapping[int, dict]] = None) -> Verdict:
    """Synthesize one action and return the never-writes verdict for it."""
    log_seen = log_seen or {}
    synthesis = synthesize_action(report_payload, action,
                                  resource_keys=resource_keys,
                                  opportunities=opportunities)
    action_id = synthesis.action_id
    action_type = synthesis.action_type
    action_status = action.get('actionStatus')
    request_status = report_payload.get('requestStatus')
    checked_at = datetime.now()

    def _verdict(status, *, would_succeed=False, messages=(), service=None,
                 warnings=(), resolved=None) -> Verdict:
        push_state, push_detail = _resolve_push_state(
            session, synthesis, service, log_seen)
        return Verdict(status=status, would_succeed=would_succeed,
                       messages=tuple(messages), gaps=synthesis.gaps,
                       service=service, warnings=tuple(warnings),
                       action_id=action_id, action_type=action_type,
                       action_status=action_status, request_status=request_status,
                       stage=synthesis.stage, push_state=push_state,
                       push_detail=push_detail, checked_at=checked_at,
                       resolved=resolved)

    if synthesis.action is None:
        return _verdict('incomplete')

    from sam.xras.dispatch import dispatch_action, select_service
    from sam.xras.errors import XrasActionRejected
    import sam.xras.handlers  # noqa: F401 — registers handlers by side effect

    try:
        service = select_service(session, synthesis.action)
    except Exception as exc:                     # noqa: BLE001
        logger.warning('preflight: select_service failed for %s (%s)', action_id, exc)
        return _verdict('incomplete')

    # A SAVEPOINT, NOT session.rollback(): the sweep runs this on its own
    # session while an uncommitted opportunity-mapping write is pending, and a
    # full rollback would discard it. validate_only writes nothing, but assembly
    # queries, and containing it in a nested transaction keeps the outer one intact.
    savepoint = session.begin_nested()
    try:
        result = dispatch_action(session, synthesis.action,
                                 enabled=enabled, validate_only=True)
    except XrasActionRejected as exc:
        return _verdict('failed', messages=tuple(exc.messages), service=service,
                        resolved=getattr(exc, 'resolved', None))
    except Exception as exc:                     # noqa: BLE001
        logger.warning('preflight: dispatch raised for action %s (%s)', action_id, exc)
        return _verdict('incomplete', service=service)
    finally:
        if savepoint.is_active:
            savepoint.rollback()

    if result.status == 'manual':
        return _verdict('manual', service=result.service or service,
                        messages=(result.reason,) if result.reason else ())
    return _verdict('rechecked', would_succeed=True, service=result.service or service,
                    warnings=result.warnings, resolved=result.resolved)


def verdict_to_dict(verdict: Verdict) -> dict:
    """The display shape stamped into a snapshot — one function so the sweep and
    the post-write re-check patch produce byte-identical rows (two-consumers)."""
    return {
        'status': verdict.status,
        'would_succeed': verdict.would_succeed,
        'messages': list(verdict.messages),
        'gaps': list(verdict.gaps),
        'service': verdict.service,
        'stage': verdict.stage,
        'action_status': verdict.action_status,
        'request_status': verdict.request_status,
        'push_state': verdict.push_state,
        'push_detail': verdict.push_detail,
        'resolved': verdict.resolved,
        'checked_at': verdict.checked_at.isoformat(),
    }


def _resolve_push_state(session, synthesis: Synthesis, service: Optional[str],
                        log_seen: Mapping[int, dict]) -> Tuple[str, Optional[dict]]:
    """seen_in_log > applied_inferred > pending (New, no project) > unknown."""
    aid = synthesis.action_id
    if aid is not None and aid in log_seen:
        return 'seen_in_log', dict(log_seen[aid])
    try:
        applied = infer_applied(session, synthesis)
    except Exception as exc:                     # noqa: BLE001
        logger.warning('preflight: infer_applied raised for %s (%s)', aid, exc)
        applied = None
    if applied:
        return 'applied_inferred', applied
    if service == 'add':
        return 'pending', None
    return 'unknown', None
