"""Read side of the LDAP sync API: the collections ``sam-ldap-syncd`` loads from SAM.

Each function returns plain dicts for ``sam.schemas.ldapsync``. Wire contract,
legacy behavior and the deliberate deviations: ``docs/plans/LDAP_SYNC_API.md``.
Bulk SQL keyed by id, assembled in Python: the ``user`` collection is ~28k users
with nested rows, and an ORM graph of it would not fit the worker timeout.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from sam.core.groups import GidAllocation
from sam.dates import end_of_day, from_epoch_millis
from sam.queries.directory_access import ACCESS_GRACE_PERIOD, grace_cutoff
from sam.text import ci_unique

logger = logging.getLogger(__name__)

USER_LOGIN = 'user_login'
ROLE_LOGIN = 'role_login'
EXCLUDE_FROM_GOOGLE_TAG = 'exclude-from-google'
AUTO_RENEWED_PROJECT_TAG = 'auto-renewed-project'
#: Legacy tests `"CN".contains(code)`; facility.code is one character, so this is the same set.
AUTO_RENEW_FACILITY_CODES = frozenset({'C', 'N'})


# ---------------------------------------------------------------------------
# institution / organization / gidAllocation / groupTag
# ---------------------------------------------------------------------------

def institutions(session: Session) -> list:
    rows = session.execute(text("""
        SELECT i.institution_id, i.name, i.acronym, i.nsf_org_code, i.address,
               i.city, i.zip, c.code AS country, sp.code AS state,
               it.type AS institution_type, i.deleted
          FROM institution i
          LEFT JOIN state_prov sp ON sp.ext_state_prov_id = i.state_prov_id
          LEFT JOIN country c ON c.ext_country_id = sp.ext_country_id
          LEFT JOIN institution_type it ON it.institution_type_id = i.institution_type_id
         ORDER BY i.institution_id
    """)).mappings().all()
    return [dict(r) for r in rows]


def organizations(session: Session) -> list:
    rows = session.execute(text("""
        SELECT o.acronym, o.active, o.description, o.level_code, o.level, o.name,
               o.organization_id, p.acronym AS parent_org_acronym,
               o.tree_left, o.tree_right, o.idms_unique_name, o.deleted
          FROM organization o
          LEFT JOIN organization p ON p.organization_id = o.parent_org_id
         ORDER BY o.organization_id
    """)).mappings().all()
    return [dict(r) for r in rows]


def gid_allocations(session: Session) -> list:
    # ORM, not text(): the columns are camelCase, which Postgres folds unless quoted.
    return [{'gid_allocation_id': b.gid_allocation_id, 'start_gid': b.start_gid,
             'next_gid': b.next_gid, 'end_gid': b.end_gid,
             'creation_time': b.creation_time, 'modified_time': b.modified_time}
            for b in GidAllocation.list_blocks(session)]


def access_branch_names(session: Session) -> list:
    return list(session.execute(text('SELECT name FROM access_branch ORDER BY name')).scalars())


def group_tags(session: Session) -> list:
    """Every access branch (accessBranch true), then the two SAM-only project tags."""
    tags = [{'name': n, 'access_branch': True} for n in access_branch_names(session)]
    return tags + [{'name': EXCLUDE_FROM_GOOGLE_TAG, 'access_branch': False},
                   {'name': AUTO_RENEWED_PROJECT_TAG, 'access_branch': False}]


# ---------------------------------------------------------------------------
# user
# ---------------------------------------------------------------------------

_USER_COLUMNS = """
    SELECT u.user_id, u.username, u.unix_uid, u.upid, u.active,
           u.locked, u.charging_exempt, u.title, u.first_name, u.middle_name,
           u.last_name, u.nickname, u.name_suffix, u.contact_person_upid,
           u.token_type, u.deleted, lt.type AS login_type,
           acs.academic_status_code
      FROM users u
      LEFT JOIN login_type lt ON lt.login_type_id = u.login_type_id
      LEFT JOIN academic_status acs ON acs.academic_status_id = u.academic_status_id
"""


def _child_rows(session: Session, sql: str, user_ids: Optional[list]):
    """Run a per-user child query, filtered to *user_ids* when given; rows grouped by user_id."""
    if user_ids is not None:
        stmt = text(sql + ' WHERE t.user_id IN :ids ORDER BY 1').bindparams(
            bindparam('ids', expanding=True))
        rows = session.execute(stmt, {'ids': user_ids}).mappings().all()
    else:
        rows = session.execute(text(sql + ' ORDER BY 1')).mappings().all()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['user_id']].append(row)
    return grouped


def _assemble_users(session: Session, user_rows, user_ids: Optional[list]) -> list:
    emails = _child_rows(session, """
        SELECT t.email_address_id, t.user_id, t.email_address, t.is_primary
          FROM email_address t""", user_ids)
    phones = _child_rows(session, """
        SELECT t.ext_phone_id, t.user_id, pt.phone_type, t.phone_number
          FROM phone t
          LEFT JOIN phone_type pt ON pt.ext_phone_type_id = t.ext_phone_type_id""", user_ids)
    collabs = _child_rows(session, """
        SELECT t.user_institution_id, t.user_id, t.institution_id, t.start_date, t.end_date
          FROM user_institution t""", user_ids)
    positions = _child_rows(session, """
        SELECT t.user_organization_id, t.user_id, t.organization_id, t.start_date,
               t.end_date, t.idms_unique_name
          FROM user_organization t""", user_ids)

    out = []
    for u in user_rows:
        uid = u['user_id']
        ucollabs, upositions = collabs.get(uid, []), positions.get(uid, [])
        out.append({
            'academic_status': u['academic_status_code'],
            # Pending users keep active=1; a finished user keeps the stamp as the
            # closure record, so legacy's `OR deactivate IS NOT NULL` is not applied (D25).
            'active': bool(u['active']),
            'charging_exempt': u['charging_exempt'],
            'collaborations': [{
                'collaboration_id': c['user_institution_id'],
                'institution_id': c['institution_id'], 'upid': u['upid'],
                'start_date': c['start_date'], 'end_date': c['end_date'],
            } for c in ucollabs],
            'contact_person_upid': u['contact_person_upid'],
            'emails': [{
                'email_address_id': e['email_address_id'], 'user_id': uid,
                'email': e['email_address'], 'primary': e['is_primary'],
            } for e in emails.get(uid, [])],
            'firstname': u['first_name'],
            'institution_ids': [c['institution_id'] for c in ucollabs if c['end_date'] is None],
            'lastname': u['last_name'],
            'locked': u['locked'],
            'middlename': u['middle_name'],
            'name_suffix': u['name_suffix'],
            'nickname': u['nickname'],
            'org_ids': [p['organization_id'] for p in upositions if p['end_date'] is None],
            'phones': [{
                'ext_phone_id': p['ext_phone_id'], 'ext_phone_user_id': uid,
                'ext_phone_type': p['phone_type'], 'phone_number': p['phone_number'],
            } for p in phones.get(uid, [])],
            'positions': [{
                'position_id': p['user_organization_id'],
                'organization_id': p['organization_id'], 'upid': u['upid'],
                # Legacy's EndDateTimeUserType reads a position end as 23:59:59 that day.
                'start_date': p['start_date'], 'end_date': end_of_day(p['end_date']),
                'idms_unique_name': p['idms_unique_name'],
            } for p in upositions],
            'title': u['title'],
            'token_type': u['token_type'],
            'type_of_login': u['login_type'],
            'unix_uid': u['unix_uid'],
            'upid': u['upid'],
            'user_id': uid,
            'user_name': u['username'],
            'deleted': u['deleted'],
            'preferred_name': (u['nickname'] if u['nickname'] and u['nickname'].strip()
                               else u['first_name']),
        })
    return out


def users(session: Session) -> list:
    """Every user, inactive and deleted included, with all affiliation history."""
    rows = session.execute(text(_USER_COLUMNS + ' ORDER BY u.user_id')).mappings().all()
    return _assemble_users(session, rows, None)


def user_by_unix_uid(session: Session, unix_uid: int) -> Optional[dict]:
    """The user holding *unix_uid* (lowest user_id when duplicated), or None."""
    rows = session.execute(text(_USER_COLUMNS + ' WHERE u.unix_uid = :uid ORDER BY u.user_id'),
                           {'uid': unix_uid}).mappings().all()
    if not rows:
        return None
    return _assemble_users(session, rows[:1], [rows[0]['user_id']])[0]


# ---------------------------------------------------------------------------
# group (adhoc)
# ---------------------------------------------------------------------------

def _login_identities(session: Session) -> dict:
    """``{lower(username): (upid, contact_person_upid)}`` for every user."""
    rows = session.execute(text('SELECT username, upid, contact_person_upid FROM users'))
    return {r.username.lower(): (r.upid, r.contact_person_upid) for r in rows}


def groups(session: Session) -> list:
    """Every adhoc group; tags are the branches its entries name, not ``adhoc_group_tag``."""
    group_rows = session.execute(text("""
        SELECT group_id, group_name, unix_gid, active FROM adhoc_group ORDER BY group_name
    """)).all()
    entries = defaultdict(list)
    for e in session.execute(text("""
        SELECT group_id, username, access_branch_name FROM adhoc_system_account_entry
         ORDER BY entry_id
    """)):
        entries[e.group_id].append(e)
    ident = _login_identities(session)

    out = []
    for g in group_rows:
        names = [e.username for e in entries.get(g.group_id, [])]
        upids, rolenames = set(), []
        for name in ci_unique(names, sort=True):
            found = ident.get(name.lower())
            if found is None:
                continue
            upid, contact = found
            if contact is not None:
                rolenames.append(name)
            elif upid is not None:
                upids.add(upid)
        out.append({
            'name': g.group_name, 'key': g.group_name,
            'description': None, 'org': None,
            'active': bool(g.active), 'posix_gid': g.unix_gid,
            'usernames': ci_unique(names, sort=True),
            'upids': sorted(upids),
            'rolenames': rolenames,
            'tags': ci_unique((e.access_branch_name for e in entries.get(g.group_id, [])), sort=True),
        })
    return out


# ---------------------------------------------------------------------------
# projectGroup
# ---------------------------------------------------------------------------

#: Active members on a live allocation of a configurable resource (legacy jdbcQuery.xml:394).
#: Deviation: a NULL allocation end counts as live (legacy's NULL arithmetic dropped it).
_SQL_PROJECT_MEMBERS = text("""
    SELECT DISTINCT a.project_id, u.upid, u.username, lt.type AS login_type
      FROM account a
      JOIN project p ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN allocation al ON (a.account_id = al.account_id
           AND (al.end_date IS NULL OR al.end_date > :grace_cutoff))
      JOIN account_user au ON (a.account_id = au.account_id
           AND au.start_date <= :now
           AND (au.end_date IS NULL OR au.end_date > :now))
      JOIN users u ON (au.user_id = u.user_id AND u.active IS TRUE)
      JOIN login_type lt ON (u.login_type_id = lt.login_type_id)
""")

#: Access branches of each active project's live allocations. Deviation: legacy also
#: required an active member, so a project held only by its lead got no branch tag.
_SQL_PROJECT_BRANCHES = text("""
    SELECT DISTINCT a.project_id, ab.name AS branch
      FROM account a
      JOIN project p ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource abr ON abr.resource_id = r.resource_id
      JOIN access_branch ab ON ab.access_branch_id = abr.access_branch_id
      JOIN allocation al ON (a.account_id = al.account_id
           AND (al.end_date IS NULL OR al.end_date > :grace_cutoff))
""")

_SQL_PROJECTS = text("""
    SELECT p.project_id, p.projcode, p.unix_gid, p.active, p.modified_time,
           p.creation_time, p.project_lead_user_id, p.project_admin_user_id,
           f.code AS facility_code
      FROM project p
      LEFT JOIN allocation_type aty ON aty.allocation_type_id = p.allocation_type_id
      LEFT JOIN panel pn ON pn.panel_id = aty.panel_id
      LEFT JOIN facility f ON f.facility_id = pn.facility_id
     ORDER BY p.projcode
""")

_SQL_MEMBERSHIP_TIMES = text("""
    SELECT a.project_id, MAX(au.modified_time) AS max_modified,
           MAX(au.creation_time) AS max_created
      FROM account a
      JOIN account_user au ON au.account_id = a.account_id
     GROUP BY a.project_id
""")


def read_since(value) -> Optional[datetime]:
    """Legacy ``DateUtil``: a value above 2^31-1 is epoch ms, else epoch seconds; junk is None."""
    try:
        n = int(value)
        return from_epoch_millis(n if n > 2147483647 else n * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _member_key(login_type, upid, username):
    """``('upid', n)`` for a user login, ``('role', name)`` for a role login, else None."""
    if login_type == ROLE_LOGIN:
        return ('role', username) if username else None
    return ('upid', upid) if upid is not None else None


def project_groups(session: Session, since: Optional[datetime] = None) -> list:
    """Every project as a unix group with SAM's membership; lead and admin always members."""
    now = datetime.now()
    params = {'grace_cutoff': grace_cutoff(ACCESS_GRACE_PERIOD), 'now': now}

    members = defaultdict(set)
    for r in session.execute(_SQL_PROJECT_MEMBERS, params):
        key = _member_key(r.login_type, r.upid, r.username)
        if key is None:
            logger.warning('projectGroup: project_id=%s member %s has no upid; skipped',
                           r.project_id, r.username)
            continue
        members[r.project_id].add(key)

    branches = defaultdict(set)
    for r in session.execute(_SQL_PROJECT_BRANCHES, params):
        branches[r.project_id].add(r.branch)

    times = {r.project_id: r for r in session.execute(_SQL_MEMBERSHIP_TIMES)}
    projects = session.execute(_SQL_PROJECTS).all()

    lead_admin_ids = {uid for p in projects
                      for uid in (p.project_lead_user_id, p.project_admin_user_id) if uid}
    people = {}
    if lead_admin_ids:
        stmt = text("""
            SELECT u.user_id, u.upid, u.username, lt.type AS login_type
              FROM users u LEFT JOIN login_type lt ON lt.login_type_id = u.login_type_id
             WHERE u.user_id IN :ids
        """).bindparams(bindparam('ids', expanding=True))
        people = {r.user_id: r for r in session.execute(stmt, {'ids': sorted(lead_admin_ids)})}

    out = []
    for p in projects:
        last_modified = p.modified_time or p.creation_time
        t = times.get(p.project_id)
        if t is not None:
            last_modified = max(x for x in (last_modified, t.max_modified, t.max_created) if x)
        if since is not None and last_modified is not None and last_modified < since:
            continue

        keys = set(members.get(p.project_id, ()))
        for uid in (p.project_lead_user_id, p.project_admin_user_id):
            person = people.get(uid)
            if person is None:
                continue
            key = _member_key(person.login_type, person.upid, person.username)
            if key is None:
                logger.warning('projectGroup: %s lead/admin %s has no upid; skipped',
                               p.projcode, person.username)
                continue
            keys.add(key)

        tags = sorted(branches.get(p.project_id, ()), key=str.lower)
        tags.append(EXCLUDE_FROM_GOOGLE_TAG)
        if p.facility_code in AUTO_RENEW_FACILITY_CODES:
            tags.append(AUTO_RENEWED_PROJECT_TAG)

        out.append({
            'name': p.projcode, 'key': p.projcode.lower(),
            'active': bool(p.active), 'posix_gid': p.unix_gid,
            'upids': sorted(v for k, v in keys if k == 'upid'),
            'rolenames': ci_unique((v for k, v in keys if k == 'role'), sort=True),
            'tags': tags,
            'last_modified': last_modified,
        })
    return out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

_STATUS_QUERIES = (
    ('institution_update_time',
     'SELECT MAX(creation_time), MAX(modified_time) FROM institution'),
    ('organization_update_time',
     'SELECT MAX(creation_time), MAX(modified_time) FROM organization'),
    ('user_update_time', 'SELECT MAX(creation_time), MAX(modified_time) FROM users'),
    # adhoc_group has no modified_time column.
    ('group_update_time', 'SELECT MAX(creation_time) FROM adhoc_group'),
    ('gid_allocation_update_time',
     'SELECT MAX(creation_time), MAX(modified_time) FROM gid_allocation'),
)


def sync_status(session: Session) -> dict:
    """Latest write time per synced table, and the access-branch names."""
    out = {}
    for key, sql in _STATUS_QUERIES:
        values = [v for v in session.execute(text(sql)).one() if v]
        out[key] = max(values) if values else None
    out['access_branches'] = access_branch_names(session)
    return out


__all__ = [
    'USER_LOGIN', 'ROLE_LOGIN', 'EXCLUDE_FROM_GOOGLE_TAG', 'AUTO_RENEWED_PROJECT_TAG',
    'institutions', 'organizations', 'gid_allocations', 'group_tags',
    'access_branch_names', 'users', 'user_by_unix_uid', 'groups',
    'read_since', 'project_groups', 'sync_status',
]
