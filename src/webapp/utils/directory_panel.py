"""Context for the Directory panels: the record as the directory holds it, and SAM's drift.

Read-only. The panel is a dry run of the apply, so each drift row says whether it would
apply under today's levers; personal values (names, emails, phones) are filled here from
the record and the ORM row, never from a log line. Record: docs/plans/DIRECTORY_CLIENT.md step 3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sam.core.organizations import Institution, Organization
from sam.integration.directory import KINDS, DirectoryUnavailable
from sam.integration.directory.config import DirectoryConfig
from sam.manage.directory import ReconcileResult, reconcile_organization, reconcile_person

DISPOSITIONS = ('would apply', 'withheld', 'applied')


def _reader():
    from sam.integration.directory.registry import reader_from_environment
    return reader_from_environment()


def _base(config: DirectoryConfig) -> Dict[str, Any]:
    return {'available': True, 'configured': config.configured, 'error': '', 'found': False,
            'record': None, 'rows': [], 'would_apply': 0, 'withheld': 0,
            'apply_kinds': sorted(config.apply_kinds), 'result': None}


def _read(state: Dict[str, Any], read, key):
    """One directory read into ``state``; ``None`` when there is nothing to compare."""
    if not state['configured']:
        state['available'] = False
        return None
    try:
        record = read(key)
    except DirectoryUnavailable as exc:
        state['available'] = False
        state['error'] = type(exc).__name__
        return None
    state['found'] = record is not None
    state['record'] = record
    return record


def _rows(result: ReconcileResult, values) -> List[Dict[str, Any]]:
    applied = {(c.kind, c.category, c.field, c.sam_id, c.directory_id) for c in result.applied}
    rows = []
    for d in result.report.discrepancies:
        sam_value, directory_value = values(d)
        if not sam_value and d.sam_id is not None:
            sam_value = f'row {d.sam_id}'
        if not directory_value and d.directory_id:
            directory_value = d.directory_id
        key = (d.kind, d.category, d.field, d.sam_id, d.directory_id)
        rows.append({'kind': d.kind, 'category': d.category, 'field': d.field,
                     'sam': sam_value, 'directory': directory_value,
                     'disposition': 'would apply' if key in applied else 'withheld'})
    return rows


def _finish(state: Dict[str, Any], result: ReconcileResult, values,
            applied: Optional[ReconcileResult]) -> Dict[str, Any]:
    state['rows'] = _rows(result, values)
    state['would_apply'] = len(result.applied)
    state['withheld'] = len(result.withheld)
    state['result'] = applied
    return state


def _person_values(user, record):
    def values(d):
        if d.category == 'name':
            return getattr(user, d.field) or '', getattr(record, d.field) or ''
        if d.category == 'email':
            sam = {e.email_address.lower() for e in user.email_addresses}
            if d.field == 'primary':
                return (user.primary_email or '',
                        next((e.address for e in record.emails if e.primary), ''))
            return '', ', '.join(e.address for e in record.emails if e.address.lower() not in sam)
        if d.category == 'phone':
            return (', '.join(f'{p.phone_type.phone_type if p.phone_type else ""}: {p.phone_number}'
                              for p in user.phones),
                    ', '.join(f'{p.label}: {p.number}' for p in record.phones))
        return d.sam_value, d.directory_value
    return values


def person_panel(session, user, *, record=None, applied: Optional[ReconcileResult] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """The person as the directory holds them, their affiliations, and SAM's drift."""
    config = DirectoryConfig.from_environment()
    state = _base(config)
    state['upid'] = user.upid
    if user.upid is None:
        state['available'] = False
        state['error'] = 'no upid'
        return state
    if record is None:
        record = _read(state, _reader().person, user.upid)
    else:
        state['found'] = True
        state['record'] = record
    if record is None:
        return state
    org_ids = {p.organization_id for p in record.positions if p.organization_id is not None}
    orgs = {o.organization_id: o for o in session.query(Organization)
            .filter(Organization.organization_id.in_(org_ids))} if org_ids else {}
    inst_ids = {c.institution_id for c in record.collaborations}
    insts = {i.institution_id: i for i in session.query(Institution)
             .filter(Institution.institution_id.in_(inst_ids))} if inst_ids else {}
    held = {r.idms_unique_name: r.user_organization_id for r in user.organizations
            if r.idms_unique_name}
    state['positions'] = [{
        'position_id': p.position_id, 'organization_id': p.organization_id,
        'acronym': orgs[p.organization_id].acronym if p.organization_id in orgs else '',
        'name': orgs[p.organization_id].name if p.organization_id in orgs else '',
        'start': p.start, 'end': p.end, 'active': p.active, 'source': p.source,
        'sam_row_id': held.get(p.position_id)} for p in record.positions]
    state['collaborations'] = [{
        'institution_id': c.institution_id,
        'acronym': insts[c.institution_id].acronym if c.institution_id in insts else '',
        'name': insts[c.institution_id].name if c.institution_id in insts else '',
        'sponsor': c.admin_org, 'start': c.start, 'end': c.end, 'active': c.active,
        'source': c.source} for c in record.collaborations]
    result = reconcile_person(session, user, record, config=config, now=now, dry_run=True)
    return _finish(state, result, _person_values(user, record), applied)


def organization_panel(session, org, *, record=None,
                       applied: Optional[ReconcileResult] = None) -> Dict[str, Any]:
    """The organization as the directory holds it, and SAM's drift."""
    config = DirectoryConfig.from_environment()
    state = _base(config)
    if record is None:
        record = _read(state, _reader().organization, org.organization_id)
    else:
        state['found'] = True
        state['record'] = record
    if record is None:
        return state
    parent = session.get(Organization, record.parent_org_id) if record.parent_org_id else None
    state['parent'] = parent.acronym if parent else ''
    result = reconcile_organization(session, org, record, config=config, dry_run=True)
    return _finish(state, result, lambda d: (d.sam_value, d.directory_value), applied)


def apply_kind_labels(kinds) -> str:
    return ', '.join(k for k in KINDS if k in set(kinds)) or 'nothing'
