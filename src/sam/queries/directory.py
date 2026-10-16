"""Directory checks: SAM's rows and the daemon's increment, each against the directory record.

The read side of docs/plans/DIRECTORY_CLIENT.md step 1. ``run_check`` is the ldapsync
hook's body: one read, two comparisons, one log line, never a write. A failure to
reach the directory sets the outage latch so a PUT burst does not wait out a timeout each.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy import func

from sam.core.groups import AdhocGroup
from sam.core.organizations import Institution, Organization
from sam.core.users import User
from sam.integration.directory import outage
from sam.integration.directory.base import (DirectoryReader, DirectoryUnavailable,
                                            EmailRecord, PhoneRecord)
from sam.integration.directory.compare import (
    AffiliationSnapshot, DriftReport, GroupSnapshot, InstitutionSnapshot,
    OrganizationSnapshot, PersonSnapshot, compare_group, compare_institution,
    compare_organization, compare_person,
)
from sam.integration.directory.config import DirectoryConfig
from sam.integration.directory.ucar import academic_status_code
from sam.projects.projects import Project

logger = logging.getLogger(__name__)

Reports = Tuple[DriftReport, DriftReport]


# --- SAM snapshots ------------------------------------------------------------------

def snapshot_from_user(user: User) -> PersonSnapshot:
    status = user.academic_status.academic_status_code if user.academic_status else None
    return PersonSnapshot(
        upid=user.upid, username=user.username or '', unix_uid=user.unix_uid,
        active=bool(user.active) and user.deactivate is None,
        first_name=user.first_name or '', last_name=user.last_name or '',
        middle_name=user.middle_name or '', nickname=user.nickname or '',
        name_suffix=user.name_suffix or '', academic_status_code=status,
        token_type=user.token_type or '',
        emails=tuple(EmailRecord(e.email_address, bool(e.is_primary)) for e in user.email_addresses),
        phones=tuple(PhoneRecord(p.phone_type.phone_type if p.phone_type else '', p.phone_number)
                     for p in user.phones),
        positions=tuple(AffiliationSnapshot(r.user_organization_id, r.organization_id,
                                            r.start_date, r.end_date, r.idms_unique_name or '')
                        for r in user.organizations),
        collaborations=tuple(AffiliationSnapshot(r.user_institution_id, r.institution_id,
                                                 r.start_date, r.end_date)
                             for r in user.institutions),
        user_id=user.user_id,
    )


def snapshot_from_organization(org: Organization) -> OrganizationSnapshot:
    return OrganizationSnapshot(
        organization_id=org.organization_id, acronym=org.acronym or '', name=org.name or '',
        level=org.level or '', level_code=org.level_code or '',
        parent_org_id=org.parent_org_id, active=bool(org.active))


def snapshot_from_institution(inst: Institution) -> InstitutionSnapshot:
    state = inst.state_prov
    return InstitutionSnapshot(
        institution_id=inst.institution_id, acronym=inst.acronym or '', name=inst.name or '',
        country=(state.country.code if state and state.country else '') or '',
        state_code=(state.code if state else '') or '',
        org_type=inst.institution_type.type if inst.institution_type else '',
        nsf_org_code=inst.nsf_org_code or '', city=inst.city or '', street=inst.address or '',
        postal_code=inst.zip or '')


def _upids_for(session, usernames) -> frozenset:
    names = [n.lower() for n in usernames if n]
    if not names:
        return frozenset()
    rows = session.query(User.upid).filter(func.lower(User.username).in_(names),
                                           User.upid.isnot(None))
    return frozenset(upid for (upid,) in rows)


def snapshot_from_group(session, gid: int) -> Optional[GroupSnapshot]:
    """An adhoc group by gid (members and branch tags from its entries), else the project holding it."""
    group = session.query(AdhocGroup).filter(AdhocGroup.unix_gid == gid).first()
    if group is not None:
        entries = list(group.system_accounts)
        return GroupSnapshot(gid=gid, name=group.group_name, active=bool(group.active),
                             tags=frozenset(e.access_branch_name for e in entries),
                             member_upids=_upids_for(session, {e.username for e in entries}))
    project = session.query(Project).filter(Project.unix_gid == gid).first()
    if project is None:
        return None
    return GroupSnapshot(gid=gid, name=project.projcode, active=bool(project.active),
                         member_upids=frozenset(u.upid for u in project.users
                                                if u.upid is not None and u.is_active))


# --- increment snapshots (the ``*SyncInput.load()`` dicts) -------------------------------

def snapshot_from_increment(data: dict) -> PersonSnapshot:
    return PersonSnapshot(
        upid=data.get('upid'), username=data.get('user_name') or '',
        unix_uid=data.get('unix_uid'), active=bool(data.get('active')),
        first_name=data.get('first_name') or '', last_name=data.get('last_name') or '',
        middle_name=data.get('middle_name') or '', nickname=data.get('nickname') or '',
        name_suffix=data.get('name_suffix') or '',
        academic_status_code=academic_status_code(data.get('academic_status') or ''),
        token_type=data.get('token_type') or '',
        emails=tuple(EmailRecord(e['email'], bool(e.get('primary'))) for e in data.get('emails') or ()),
        phones=tuple(PhoneRecord(p.get('ext_phone_type') or '', p['phone_number'])
                     for p in data.get('phones') or ()),
        positions=tuple(AffiliationSnapshot(r.get('employment_id'), r.get('employer_id'),
                                            r.get('start_date'), r.get('end_date'),
                                            r.get('idms_unique_name') or '')
                        for r in data.get('positions') or ()),
        collaborations=tuple(AffiliationSnapshot(r.get('employment_id'), r.get('employer_id'),
                                                 r.get('start_date'), r.get('end_date'))
                             for r in data.get('collaborations') or ()),
    )


def organization_snapshot_from_increment(session, data: dict) -> OrganizationSnapshot:
    parent = None
    acronym = (data.get('parent_org_acronym') or '').strip().lower()
    if acronym:
        parent = (session.query(Organization.organization_id)
                  .filter(func.lower(Organization.acronym) == acronym)
                  .order_by(Organization.organization_id).first())
    return OrganizationSnapshot(
        organization_id=data['organization_id'], acronym=data.get('acronym') or '',
        name=data.get('name') or '', level=data.get('level') or '',
        level_code=data.get('level_code') or '',
        parent_org_id=parent[0] if parent else None, active=bool(data.get('active')))


def institution_snapshot_from_increment(data: dict) -> InstitutionSnapshot:
    country = (data.get('country') or '').upper()
    state = (data.get('state') or '').upper()
    return InstitutionSnapshot(
        institution_id=data['institution_id'], acronym=data.get('acronym') or '',
        name=data.get('name') or '', country=country,
        state_code='' if state == country else state,
        org_type=data.get('institution_type') or '', nsf_org_code=data.get('nsf_org_code') or '',
        city=data.get('city') or '', street=data.get('address') or '',
        postal_code=data.get('zip') or '')


def group_snapshot_from_increment(session, data: dict) -> GroupSnapshot:
    return GroupSnapshot(gid=data['posix_gid'], name=data.get('key') or '',
                         active=bool(data.get('active')),
                         tags=frozenset(data.get('tags') or ()),
                         member_upids=_upids_for(session, data.get('usernames') or ()))


# --- the checks -----------------------------------------------------------------------

def check_person(session, reader: DirectoryReader, data: dict, *,
                 now: Optional[datetime] = None) -> Optional[Reports]:
    """Both sides against one read; ``None`` for a record with no upid (a role login)."""
    upid = data.get('upid')
    if upid is None:
        return None
    record = reader.person(upid)
    key = data.get('user_name') or str(upid)
    user = User.get_by_upid(session, upid)
    sam = snapshot_from_user(user) if user is not None else None
    return (compare_person(record, snapshot_from_increment(data), key=key, side='increment', now=now),
            compare_person(record, sam, key=key, side='sam', now=now))


def check_organization(session, reader: DirectoryReader, data: dict) -> Optional[Reports]:
    oid = data.get('organization_id')
    if oid is None:
        return None
    record = reader.organization(oid)
    org = session.get(Organization, oid)
    return (compare_organization(record, organization_snapshot_from_increment(session, data),
                                 key=str(oid), side='increment'),
            compare_organization(record, snapshot_from_organization(org) if org else None,
                                 key=str(oid), side='sam'))


def check_institution(session, reader: DirectoryReader, data: dict) -> Optional[Reports]:
    iid = data.get('institution_id')
    if iid is None:
        return None
    record = reader.institution(iid)
    inst = session.get(Institution, iid)
    return (compare_institution(record, institution_snapshot_from_increment(data),
                                key=str(iid), side='increment'),
            compare_institution(record, snapshot_from_institution(inst) if inst else None,
                                key=str(iid), side='sam'))


def check_group(session, reader: DirectoryReader, data: dict) -> Optional[Reports]:
    gid = data.get('posix_gid')
    if gid is None:
        return None
    record = reader.group(gid)
    key = data.get('key') or str(gid)
    return (compare_group(record, group_snapshot_from_increment(session, data),
                          key=key, side='increment'),
            compare_group(record, snapshot_from_group(session, gid), key=key, side='sam'))


CHECKS = {'person': check_person, 'organization': check_organization,
          'institution': check_institution, 'group': check_group}


def run_check(session, kind: str, data: dict, key, *, reader: Optional[DirectoryReader] = None,
              config: Optional[DirectoryConfig] = None,
              now: Optional[datetime] = None) -> Optional[Reports]:
    """The hook body: skip while the latch is down or nothing is configured, else read, compare, log."""
    check = CHECKS.get(kind)
    if check is None or outage.is_down():
        return None
    if reader is None:
        from sam.integration.directory.registry import reader_from_environment
        reader = reader_from_environment()
    if not reader.configured:
        return None
    try:
        reports = check(session, reader, data, now=now) if kind == 'person' \
            else check(session, reader, data)
    except DirectoryUnavailable as exc:
        outage.mark_down()
        logger.warning('ldapsync: directory unavailable (%s); checks paused %ds',
                       type(exc).__name__, int(outage.BACKOFF_SECONDS))
        return None
    if reports is None:
        return None
    increment, sam = reports
    logger.info('ldapsync: directory check %s %s key=%s found=%s increment=%s sam=%s',
                kind, increment.key, key, sam.found_in_directory, increment.fragment(),
                sam.fragment())
    return reports
