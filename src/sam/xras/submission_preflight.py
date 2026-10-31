"""The review step of a request SAM files: what it would reach, and would its post land.

``preflight_draft`` builds the reports-shaped action a draft would become (no action id yet)
and runs ``preflight_action``, the never-writes dispatch the sweep uses, so the handoff's own
422 list shows while the requester is still at the form. The verdict is advisory; XRAS's
validate on submit and the inbound handler stay the real gates. Flask-free.
Design: ``docs/plans/XRAS_SUBMISSION_SPRINT1.md`` § 2.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime

from sam.dates import format_ymd
from sam.xras.wire import resource_label
from typing import Any, Dict, Iterable, List, Mapping, Optional


def line_resources(line: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The XRAS resources a line's live actions name, once each: ``resource_id``, ``name``, ``units``."""
    from sam.queries.xras_accounts import live_actions
    seen: Dict[int, Dict[str, Any]] = {}
    for action in live_actions(line or {}):
        for res in action.get('resources') or ():
            rid = res.get('resourceId') if isinstance(res, dict) else None
            if rid is None or rid in seen:
                continue
            seen[rid] = {'resource_id': rid,
                         'name': resource_label(res, rid),
                         'units': res.get('resourceUnits') or ''}
    return sorted(seen.values(), key=lambda r: r['name'])


def sam_resources_by_xras_id(session, resource_ids: Iterable[int],
                             keys: Optional[Mapping[int, int]]) -> Dict[int, Any]:
    """XRAS ``resourceId`` -> SAM ``Resource``, through *keys* (``resource_key_map``) and the mapping table."""
    from sam.integration.xras import XrasResourceRepositoryKeyResource
    keys = keys or {}
    wanted = {rid: keys[rid] for rid in resource_ids if rid in keys}
    if not wanted:
        return {}
    rows = (session.query(XrasResourceRepositoryKeyResource)
            .filter(XrasResourceRepositoryKeyResource.resource_repository_key.in_(
                set(wanted.values()))).all())
    by_key = {r.resource_repository_key: r.resource for r in rows}
    return {rid: by_key[key] for rid, key in wanted.items() if key in by_key}


def resources_with_limits(session, resources: List[Dict[str, Any]],
                          keys: Optional[Mapping[int, int]],
                          type_name: Optional[str]) -> List[Dict[str, Any]]:
    """Annotate XRAS resource views in place: ``sam_name``, the type's ``limit``, ``yes_no``."""
    from sam.accounting.allocation_type_limits import limits_for_allocation_type
    sam = sam_resources_by_xras_id(session, [r['resource_id'] for r in resources], keys)
    limits = limits_for_allocation_type(session, type_name) if type_name else {}
    for r in resources:
        resource = sam.get(r['resource_id'])
        r['sam_name'] = resource.resource_name if resource is not None else None
        r['limit'] = limits.get(r['sam_name']) if r['sam_name'] else None
        # XRAS's "Yes/No" unit is a checkbox, not an amount.
        r['yes_no'] = (r.get('units') or '').replace(' ', '').lower() == 'yes/no'
    return resources


def extension_floor(project) -> Optional[date]:
    """The earliest end an Extension may ask for: the latest end across the extendable accounts."""
    from sam.xras.handlers._allocations import (
        account_is_extendable,
        effective_end_date,
        latest_allocation,
    )
    now = datetime.now()
    ends = []
    for account in project.accounts:
        if not account_is_extendable(account, now) or not account.allocations:
            continue
        allocation = latest_allocation(account)
        end = effective_end_date(allocation) if allocation is not None else None
        if end is not None:
            ends.append(end.date() if isinstance(end, datetime) else end)
    return max(ends) if ends else None


def limit_refusals(session, allocation_type_name: Optional[str],
                   amounts: Mapping[str, float]) -> Dict[str, str]:
    """``{resource name: why}`` for each requested amount its type's limit refuses."""
    from sam.accounting.allocation_type_limits import limits_for_allocation_type
    limits = limits_for_allocation_type(session, allocation_type_name) \
        if allocation_type_name else {}
    out = {}
    for name, amount in amounts.items():
        row = limits.get(name)
        why = row.refusal(amount) if row is not None else None
        if why:
            out[name] = why
    return out


def draft_action(draft) -> Dict[str, Any]:
    """The reports-shaped action *draft* (an ``ActionRequest``) would become."""
    begin, end = draft.dates if draft.dates else (None, None)
    action: Dict[str, Any] = {
        'actionId': None, 'actionType': draft.action_type, 'actionStatus': 'Incomplete',
        'resources': [{'resourceId': r.resource_id, 'amount': r.amount,
                       'comments': r.comments, 'type': 'Requested'}
                      for r in draft.resources]}
    if begin or end:
        action['allocationDates'] = [{'allocationDateType': 'Requested',
                                      'beginDate': format_ymd(begin),
                                      'endDate': format_ymd(end)}]
    return action


def _wire_opportunity(opportunity: Mapping[str, Any]) -> Dict[str, Any]:
    return {'opportunityId': opportunity.get('opportunity_id'),
            'opportunityName': opportunity.get('opportunity_name'),
            'allocationType': opportunity.get('allocation_type'), 'requestNumber': None}


def institution_mnemonic(session, name: Optional[str]):
    """The active code of the one live institution named *name* (the ladder's institution rule), else None."""
    from sqlalchemy import func
    from sam.core.organizations import Institution, MnemonicCode
    name = (name or '').strip()
    if not name:
        return None
    rows = (session.query(Institution).filter(Institution.deleted.isnot(True),
                                              func.lower(Institution.name) == name.lower())
            .limit(2).all())
    code = (MnemonicCode.resolve_for_institution(rows[0], MnemonicCode.build_lookup(session))
            if len(rows) == 1 else None)
    return (session.query(MnemonicCode).filter(MnemonicCode.is_active, MnemonicCode.code == code)
            .first() if code else None)


def new_request_sam_state(session, opportunity: Mapping[str, Any], pi, *,
                          institution_name: Optional[str] = None) -> Dict[str, Any]:
    """What the New handler's ladder resolves for *pi* under *opportunity*: ``allocation_type``,
    ``mnemonic`` (rows or ``None``), ``affiliation`` and ``mnemonic_errors``.

    With no *pi* (a visitor's New), *institution_name* stands in: its code is preset on the
    row, which the handler takes ahead of a ladder that cannot see the visitor.
    """
    from sam.xras.errors import ActionErrors
    from sam.xras.extractors import (
        best_institution,
        best_organization,
        resolve_allocation_type,
        resolve_mnemonic_code,
    )
    action = _wire_opportunity(opportunity)
    type_errors, mnemonic_errors = ActionErrors(), ActionErrors()
    allocation_type = resolve_allocation_type(session, action, type_errors)
    mnemonic = resolve_mnemonic_code(session, action, mnemonic_errors,
                                     pi_username=pi.username if pi else None, pi=pi)
    where = (best_institution(pi) or best_organization(pi)) if pi else None
    affiliation = getattr(where, 'name', None)
    if pi is None and institution_name:
        mnemonic, affiliation = institution_mnemonic(session, institution_name), institution_name
    return {'allocation_type': allocation_type, 'mnemonic': mnemonic,
            'affiliation': affiliation, 'mnemonic_errors': list(mnemonic_errors)}


def new_request_line(draft, *, pi_username: str, opportunity: Mapping[str, Any],
                     fos: Optional[Mapping[str, Any]],
                     agency_names: Optional[Mapping[int, str]] = None) -> Dict[str, Any]:
    """The reports-shaped line a New *draft* would become: no ids, the roster SAM will add.

    The push spells a grant's agency by name, so *agency_names* maps ``fundingAgencyId`` to it.
    """
    roles = [{'person': {'username': pi_username}, 'roles': [{'role': 'PI'}]}]
    if draft.admin_username:
        roles.append({'person': {'username': draft.admin_username},
                      'roles': [{'role': 'Allocation Manager'}]})
    roles += [{'person': {'username': u}, 'roles': [{'role': 'User'}]}
              for u in draft.roster_usernames]
    names = agency_names or {}
    grants = [{**{k: format_ymd(v) if isinstance(v, date) else v for k, v in g.items()
                  if k != 'fundingAgencyId'}, 'fundingAgency': names.get(g.get('fundingAgencyId'))}
              for g in draft.grants]
    return {**_wire_opportunity(opportunity), 'requestId': None, 'requestType': 'New',
            'requestStatus': 'Incomplete', 'title': draft.title, 'abstract': draft.abstract,
            'roles': roles, 'fos': [dict(fos, isPrimary=True)] if fos else [],
            'grants': grants}


def preflight_draft(session, line: Mapping[str, Any], draft, *,
                    resource_keys: Optional[Mapping[int, int]] = None,
                    opportunities: Optional[Mapping[int, dict]] = None):
    """The never-writes verdict for *draft* landing on *line*."""
    from sam.xras.preflight import preflight_action
    return preflight_action(session, dict(line), draft_action(draft),
                            resource_keys=resource_keys, opportunities=opportunities,
                            enabled=None)


_PAGE_OBJECT = re.compile(rb'/Type\s*/Page(?![a-zA-Z])')


def _env_int(name: str) -> Optional[int]:
    raw = (os.environ.get(name) or '').strip()
    return int(raw) if raw.isdigit() else None


def document_refusal(filename: str, content: bytes) -> Optional[str]:
    """Why SAM refuses an upload, or ``None``. The PDF check always runs; the size and page
    hooks (``SUBMISSION_DOCUMENT_MAX_BYTES``, ``_MAX_PAGES``) are dark unless set.

    NOTE: pages are counted from ``/Type /Page`` objects, which a compressed object stream
    hides; a count of zero passes and XRAS's own 400 is the backstop.
    """
    if not content:
        return f'{filename} is empty.'
    if not content.startswith(b'%PDF-'):
        return f'{filename} is not a PDF.'
    max_bytes = _env_int('SUBMISSION_DOCUMENT_MAX_BYTES')
    if max_bytes and len(content) > max_bytes:
        return f'{filename} is larger than {max_bytes // 1_000_000} MB.'
    max_pages = _env_int('SUBMISSION_DOCUMENT_MAX_PAGES')
    pages = len(_PAGE_OBJECT.findall(content)) if max_pages else 0
    if max_pages and pages > max_pages:
        return f'{filename} has {pages} pages; the limit is {max_pages}.'
    return None
