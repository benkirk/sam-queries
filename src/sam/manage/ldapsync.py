"""Write side of the LDAP sync API: upserts pushed by ``sam-ldap-syncd``.

SAM never originates identities; this module mirrors them. Every rule, and each
deliberate deviation from legacy Java SAM, is in ``docs/plans/LDAP_SYNC_API.md``.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from sam.core.groups import AdhocGroup, AdhocGroupTag, AdhocSystemAccountEntry, GidAllocation
from sam.core.organizations import (
    Institution,
    InstitutionType,
    Organization,
    UserInstitution,
    UserOrganization,
)
from sam.core.users import AcademicStatus, EmailAddress, LoginType, Phone, PhoneType, User
from sam.dates import end_of_day
from sam.geography import Country, StateProv
from sam.manage.employment import Affiliation, match
from sam.projects.projects import Project
from sam.security.access import AccessBranch

logger = logging.getLogger(__name__)


class SyncValidationError(ValueError):
    """A payload SAM rejects; the API answers 400 with legacy's message text."""

    def __init__(self, *messages: str):
        super().__init__('; '.join(messages))
        self.messages = list(messages)


# ---------------------------------------------------------------------------
# shared checks
# ---------------------------------------------------------------------------

def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _check_widths(kind: str, data: dict, widths: dict) -> None:
    """Over-long values as one 400 (legacy let the database reject them with a 500)."""
    errors = [f'{kind} {name} is longer than {width} characters.'
              for name, width in widths.items()
              if data.get(name) is not None and len(str(data[name])) > width]
    if errors:
        raise SyncValidationError(*errors)


def _require(data: dict, *names_and_messages) -> None:
    errors = [msg for name, msg in names_and_messages if _blank(data.get(name))]
    if errors:
        raise SyncValidationError(*errors)


# ---------------------------------------------------------------------------
# institution
# ---------------------------------------------------------------------------

INSTITUTION_WIDTHS = {'name': 128, 'acronym': 40, 'nsf_org_code': 200, 'address': 255,
                      'city': 30, 'zip': 15}


def _institution_type_id(session, type_name):
    if _blank(type_name):
        return None
    row = (session.query(InstitutionType)
           .filter(func.lower(InstitutionType.type) == type_name.strip().lower()).first())
    if row is None:
        raise SyncValidationError(f'Could not find institution type {type_name}.')
    return row.institution_type_id


def _state_prov_id(session, country, state, acronym):
    """By country + state code, then by name; a state equal to the country code is no state."""
    if _blank(country) or _blank(state) or state.strip().upper() == country.strip().upper():
        return None
    base = (session.query(StateProv.ext_state_prov_id).join(Country)
            .filter(func.upper(Country.code) == country.strip().upper())
            .order_by(StateProv.ext_state_prov_id))
    for column in (StateProv.code, StateProv.name):
        found = base.filter(func.upper(column) == state.strip().upper()).first()
        if found is not None:
            return found[0]
    logger.warning('ldapsync: no state_prov for %s, %s (institution=%s)', country, state, acronym)
    return None


def sync_institution(session, data: dict) -> int:
    """Upsert one institution by its IdM id; returns the id."""
    _require(data, ('institution_id', 'Institution id must be specified.'),
             ('acronym', 'Institution acronym must be specified.'))
    _check_widths('Institution', data, INSTITUTION_WIDTHS)
    Institution.upsert_from_sync(
        session, institution_id=data['institution_id'],
        name=data['name'], acronym=data['acronym'], nsf_org_code=data['nsf_org_code'],
        address=data['address'], city=data['city'], zip=data['zip'],
        deleted=data['deleted'],
        institution_type_id=_institution_type_id(session, data['institution_type']),
        state_prov_id=_state_prov_id(session, data['country'], data['state'], data['acronym']),
    )
    return data['institution_id']


# ---------------------------------------------------------------------------
# organization
# ---------------------------------------------------------------------------

ORGANIZATION_WIDTHS = {'name': 100, 'acronym': 15, 'description': 255, 'level': 80,
                       'level_code': 10, 'idms_unique_name': 64}


def _parent_org_id(session, acronym, own_id):
    """The active organization with this acronym (case-insensitive), else None."""
    if _blank(acronym):
        return None
    parent = (session.query(Organization)
              .filter(Organization.is_active,
                      func.lower(Organization.acronym) == acronym.strip().lower())
              .order_by(Organization.organization_id).first())
    if parent is None or parent.organization_id == own_id:
        return None
    return parent.organization_id


def sync_organization(session, data: dict) -> int:
    """Upsert one organization by its IdM id; returns the id."""
    _require(data, ('organization_id', 'Organization id must be specified.'),
             ('name', 'Organization name must be specified.'),
             ('acronym', 'Organization acronym must be specified.'))
    if data.get('active') is None:
        raise SyncValidationError('Organization active must be specified.')
    _check_widths('Organization', data, ORGANIZATION_WIDTHS)
    oid = data['organization_id']
    fields = {name: data[name] for name in ('name', 'acronym', 'active', 'tree_left',
                                            'tree_right', 'level', 'level_code',
                                            'idms_unique_name', 'deleted')}
    if 'description' in data:
        fields['description'] = data['description']
    fields['parent_org_id'] = _parent_org_id(session, data['parent_org_acronym'], oid)
    Organization.upsert_from_sync(session, organization_id=oid, **fields)
    return oid


# ---------------------------------------------------------------------------
# gidAllocation
# ---------------------------------------------------------------------------

def sync_gid_allocation(session, data: dict) -> int:
    """Register a GID block; the identical block is a no-op. Returns startGid."""
    start, end = data.get('start_gid'), data.get('end_gid')
    if start is None or end is None:
        raise SyncValidationError('Gid range must give startGid and endGid.')
    try:
        GidAllocation.create_block(session, start, end)
    except ValueError as exc:
        raise SyncValidationError(str(exc)) from exc
    return start


# ---------------------------------------------------------------------------
# user
# ---------------------------------------------------------------------------

USER_WIDTHS = {'user_name': 35, 'title': 45, 'first_name': 40, 'middle_name': 40,
               'last_name': 50, 'nickname': 50, 'name_suffix': 40, 'token_type': 30}

_BASIC_USER_FIELDS = ('locked', 'title', 'first_name', 'middle_name', 'last_name',
                      'nickname', 'name_suffix', 'charging_exempt', 'token_type',
                      'contact_person_upid', 'deleted')


def user_by_username(session, username: str) -> Optional[User]:
    """Exact match first (indexed, and already case-insensitive on MySQL), then case-folded."""
    user = session.query(User).filter(User.username == username).first()
    if user is None:
        user = (session.query(User)
                .filter(func.lower(User.username) == username.lower()).first())
    return user


def _apply_active_transition(user: User, idm_active: bool, now: datetime) -> Optional[datetime]:
    """Legacy's rule; returns the closure stamp when a finished user comes back (the undo trigger)."""
    sam_active = bool(user.active) and user.deactivate is None
    if idm_active == sam_active:
        return None
    if idm_active:
        closed_at = user.deactivate if not user.active else None
        user.deactivate = None
        user.active = True
        return closed_at
    if user.active:
        user.deactivate = now        # pending: finish_user_deactivation completes it
    return None


def _role_login_names(session, data: dict, login_type_before) -> dict:
    """A role login takes its names from the contact person (legacy ``fixRoleUser``)."""
    upid = data['contact_person_upid']
    if upid is None:
        return {}
    contact = session.query(User).filter(User.upid == upid).first()
    if contact is not None:
        return {'first_name': contact.first_name, 'middle_name': contact.middle_name,
                'last_name': contact.last_name, 'nickname': contact.nickname}
    return {'last_name': 'unknown'} if login_type_before is None else {}


def _lookup_id(session, wanted, column, id_column, label):
    """Legacy: a null clears; a value is looked up case-insensitively, an unknown one clears."""
    if wanted is None:
        return None
    row = session.query(id_column, column).filter(
        func.lower(column) == wanted.strip().lower()).first()
    if row is None:
        logger.warning('ldapsync: unknown %s %r written as NULL', label, wanted)
        return None
    return row[0]


def _sync_emails(user: User, wanted: list) -> None:
    """Match by address, case-insensitively; addresses not sent are deleted (legacy)."""
    keep = {e['email'].lower() for e in wanted}
    for row in list(user.email_addresses):
        if row.email_address.lower() not in keep:
            user.email_addresses.remove(row)
    by_address = {row.email_address.lower(): row for row in user.email_addresses}
    for e in wanted:
        if len(e['email']) > 255:
            raise SyncValidationError('User email is longer than 255 characters.')
        row = by_address.get(e['email'].lower())
        if row is None:
            row = EmailAddress(email_address=e['email'], is_primary=e['primary'])
            user.email_addresses.append(row)
            by_address[e['email'].lower()] = row
        else:
            row.is_primary = e['primary']
            row.email_address = e['email']


def _sync_phones(session, user: User, wanted: list) -> None:
    """Exact number match; numbers not sent are deleted; an unknown type skips that phone."""
    types = {t.phone_type.lower(): t.ext_phone_type_id for t in session.query(PhoneType)}
    keep = {p['phone_number'] for p in wanted}
    for row in list(user.phones):
        if row.phone_number not in keep:
            user.phones.remove(row)
    by_number = {row.phone_number: row for row in user.phones}
    for p in wanted:
        if len(p['phone_number']) > 50:
            raise SyncValidationError('User phone number is longer than 50 characters.')
        type_id = types.get((p['ext_phone_type'] or '').strip().lower())
        row = by_number.get(p['phone_number'])
        if type_id is None:
            logger.warning('ldapsync: %s phone type %r unknown; phone not written',
                           user.username, p['ext_phone_type'])
        elif row is None:
            row = Phone(phone_number=p['phone_number'], ext_phone_type_id=type_id)
            user.phones.append(row)
            by_number[p['phone_number']] = row
        else:
            row.ext_phone_type_id = type_id


def _sync_affiliations(session, user: User, incoming: list, *, positions: bool) -> None:
    """Upsert ``user_organization`` (positions) or ``user_institution`` rows; never end or delete."""
    if positions:
        rows, model, employer_model, id_attr, employer_attr, label = (
            user.organizations, UserOrganization, Organization,
            'user_organization_id', 'organization_id', 'position')
    else:
        rows, model, employer_model, id_attr, employer_attr, label = (
            user.institutions, UserInstitution, Institution,
            'user_institution_id', 'institution_id', 'collaboration')
    wanted_employers = {r['employer_id'] for r in incoming if r['employer_id'] is not None}
    pk = getattr(employer_model, employer_attr)
    known = {v for (v,) in session.query(pk).filter(pk.in_(wanted_employers))} \
        if wanted_employers else set()

    records = []
    for r in incoming:
        if r['employer_id'] not in known or r['start_date'] is None:
            logger.warning('ldapsync: %s %s for %s skipped (employer %s %s, start %s)',
                           label, r['employment_id'], user.username, r['employer_id'],
                           'unknown' if r['employer_id'] not in known else 'known',
                           r['start_date'])
            continue
        records.append(Affiliation(
            r['employment_id'], r['employer_id'], r['start_date'],
            end_of_day(r['end_date']) if positions else r['end_date'],
            r.get('idms_unique_name') if positions else None))

    by_id = {getattr(row, id_attr): row for row in rows}
    existing = [Affiliation(getattr(row, id_attr), getattr(row, employer_attr), row.start_date,
                            row.end_date, row.idms_unique_name if positions else None)
                for row in rows]
    for rec, found in match(records, existing, label=label):
        if found is None:
            values = {'user_id': user.user_id, employer_attr: rec.employer_id,
                      'start_date': rec.start_date, 'end_date': rec.end_date}
            if positions:
                values['idms_unique_name'] = rec.idms_unique_name
            rows.append(model(**values))
            continue
        row = by_id[found.employment_id]
        if row.start_date != rec.start_date:
            row.start_date = rec.start_date
        if row.end_date != rec.end_date:
            row.end_date = rec.end_date
        if positions and row.idms_unique_name != rec.idms_unique_name:
            row.idms_unique_name = rec.idms_unique_name


def sync_user(session, data: dict, *, now: Optional[datetime] = None,
              on_reactivate=None) -> int:
    """Upsert one user by username; returns the user's unixUid.

    ``on_reactivate(user, closed_at)`` runs when IdM brings back a user SAM had
    finished deactivating at ``closed_at`` (the restore hook; ``sam.manage.lifecycle``).
    """
    now = (now or datetime.now()).replace(microsecond=0)
    _require(data, ('user_name', 'User userName must be specified.'))
    _check_widths('User', data, USER_WIDTHS)
    upid = data['upid']
    user = user_by_username(session, data['user_name'])
    if user is None and upid is not None:
        holder = session.query(User).filter(User.upid == upid).first()
        if holder is not None:
            raise SyncValidationError(
                f'Upid {upid} matches username {holder.username} (username change in ID Service?).')

    closed_at = None
    if user is None:
        if data['unix_uid'] is None:
            raise SyncValidationError('User unixUid must be specified.')
        user = User.create(session, username=data['user_name'], unix_uid=data['unix_uid'],
                           upid=upid, active=data['active'])
    else:
        closed_at = _apply_active_transition(user, data['active'], now)

    fields = {name: data[name] for name in _BASIC_USER_FIELDS}
    fields.update(_role_login_names(session, data, user.login_type_id))
    fields['academic_status_id'] = _lookup_id(
        session, data['academic_status'], AcademicStatus.academic_status_code,
        AcademicStatus.academic_status_id, 'academic status')
    fields['login_type_id'] = _lookup_id(
        session, data['type_of_login'], LoginType.type, LoginType.login_type_id, 'login type')
    user.update(**fields)

    _sync_emails(user, data['emails'])
    _sync_phones(session, user, data['phones'])
    _sync_affiliations(session, user, data['collaborations'], positions=False)
    _sync_affiliations(session, user, data['positions'], positions=True)
    session.flush()

    if closed_at is not None and on_reactivate is not None:
        on_reactivate(user, closed_at)
    return user.unix_uid


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------

NCAR_GROUP = 'ncar'
ENTRY_USERNAME_WIDTH = 12     # adhoc_system_account_entry.username


def _ci_unique(values) -> list:
    seen = {}
    for v in values or ():
        if v is not None and v.strip():
            seen.setdefault(v.lower(), v)
    return list(seen.values())


def sync_group(session, data: dict) -> int:
    """Upsert one unix group; returns posixGid.

    A project's group only ever receives a missing gid. Otherwise the group is ad hoc
    only when a tag names an access branch; anything else is dropped with a 200, as
    legacy did (the daemon does not send those).
    """
    key, gid = (data.get('key') or '').strip().lower(), data.get('posix_gid')
    if not key or gid is None:
        raise SyncValidationError('Either posixGid or groupname of group must be specified.')
    if len(key) > 30:
        raise SyncValidationError('Group key is longer than 30 characters.')
    tags = _ci_unique(data.get('tags'))
    usernames = _ci_unique(data.get('usernames'))

    branches = {name.lower(): name for (name,) in session.query(AccessBranch.name)}
    matching = list(dict.fromkeys(branches[t.lower()] for t in tags if t.lower() in branches))
    project = (session.query(Project)
               .filter(func.lower(Project.projcode) == key).first())

    if key != NCAR_GROUP and matching and project is None:
        by_key = (session.query(AdhocGroup)
                  .filter(func.lower(AdhocGroup.group_name) == key).first())
        by_gid = AdhocGroup.get_by_unix_gid(session, gid)
        if by_key is not None and (by_gid is None or by_key is not by_gid):
            raise SyncValidationError(
                f'Inconsistency with group key {key} and unix gid {gid} (constraint violation).')
    elif project is None or project.unix_gid is not None:
        return gid

    if project is not None:
        # project.unix_gid has no unique key; refuse a gid a project or adhoc group holds.
        taken = (session.query(Project.project_id)
                 .filter(Project.unix_gid == gid, Project.project_id != project.project_id)
                 .first() is not None) or AdhocGroup.get_by_unix_gid(session, gid) is not None
        if taken:
            raise SyncValidationError(
                f'Inconsistency with group key {key} and unix gid {gid} (constraint violation).')
        project.update(unix_gid=gid)
        return gid

    group = AdhocGroup.get_by_unix_gid(session, gid)
    if group is None:
        group = AdhocGroup(group_name=key, unix_gid=gid)
        session.add(group)
    group.group_name = key
    group.active = bool(data.get('active'))

    wanted_tags = set(tags)
    for row in list(group.tags):
        if row.tag not in wanted_tags:
            group.tags.remove(row)
    have_tags = {row.tag for row in group.tags}
    for tag in tags:
        if tag not in have_tags:
            if len(tag) > 40:
                raise SyncValidationError('Group tag is longer than 40 characters.')
            group.tags.append(AdhocGroupTag(tag=tag))

    long_names = [u for u in usernames if len(u) > ENTRY_USERNAME_WIDTH]
    if long_names:
        logger.warning('ldapsync: group %s members too long for an entry, skipped: %s',
                       key, long_names)
    wanted = {(b, u) for b in matching for u in usernames if len(u) <= ENTRY_USERNAME_WIDTH}
    for row in list(group.system_accounts):
        if (row.access_branch_name, row.username) not in wanted:
            group.system_accounts.remove(row)
    have = {(row.access_branch_name, row.username) for row in group.system_accounts}
    for branch, username in sorted(wanted - have):
        group.system_accounts.append(
            AdhocSystemAccountEntry(access_branch_name=branch, username=username))
    session.flush()
    return gid
