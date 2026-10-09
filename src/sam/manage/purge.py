"""Purge permits and hard deletes for the LDAP sync API (``*PurgePermit`` / ``*Purge``).

The daemon asks for a permit when an LDAP entry disappears, then DELETEs if
purgeable and otherwise PUTs a tombstone. Messages are legacy's, verbatim. The
permit blocks on every row that references the entity and is not deleted with it:
an ORM delete would otherwise NULL those foreign keys and orphan history.
``enabled=False`` (the ``LDAPSYNC_PURGE_ENABLED`` lever) refuses every purge of an
existing row. See ``docs/plans/LDAP_SYNC_API.md``.
"""

from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from sam.core.groups import AdhocGroup
from sam.core.organizations import Institution, Organization
from sam.core.users import User
from sam.manage.ldapsync import SyncValidationError

PURGE_DISABLED = 'Purge disabled in SAM.'

#: (EXISTS query on :uid, legacy message). Order is legacy's, then the additions.
_USER_BLOCKERS = (
    ('SELECT 1 FROM account_user WHERE user_id = :uid', 'SAM user %s is assigned.'),
    ('SELECT 1 FROM wallclock_exemption WHERE user_id = :uid',
     'SAM user %s has wallclock exemptions.'),
    ('SELECT 1 FROM users r JOIN users u ON u.upid = r.contact_person_upid WHERE u.user_id = :uid',
     'SAM user %s is contact person for role login.'),
    ('SELECT 1 FROM project WHERE project_admin_user_id = :uid', 'SAM user %s is project admin.'),
    ('SELECT 1 FROM project WHERE project_lead_user_id = :uid', 'SAM user %s is project lead.'),
    ('SELECT 1 FROM contract WHERE contract_monitor_user_id = :uid',
     'SAM user %s is contract monitor.'),
    ('SELECT 1 FROM contract WHERE principal_investigator_user_id = :uid',
     'SAM user %s is contract principal investigator.'),
    ('SELECT 1 FROM allocation_transaction WHERE user_id = :uid',
     'SAM user %s is allocation transaction author.'),
    ('SELECT 1 FROM resources WHERE prim_sys_admin_user_id = :uid', 'SAM user %s is resource admin.'),
    ('SELECT 1 FROM charge_adjustment WHERE adjusted_by_id = :uid', 'SAM user %s is charge adjuster.'),
    # Not in legacy, which would have failed on the foreign key instead.
    ('SELECT 1 FROM role_user WHERE user_id = :uid', 'SAM user %s holds SAM roles.'),
    ('SELECT 1 FROM user_alias WHERE user_id = :uid', 'SAM user %s has an alias record.'),
    ('SELECT 1 FROM responsible_party WHERE user_id = :uid', 'SAM user %s is a responsible party.'),
    ('SELECT 1 FROM hpc_charge WHERE user_id = :uid', 'SAM user %s has hpc charge records.'),
    ('SELECT 1 FROM dav_charge WHERE user_id = :uid', 'SAM user %s has dav charge records.'),
    ('SELECT 1 FROM disk_charge WHERE user_id = :uid', 'SAM user %s has disk charge records.'),
    ('SELECT 1 FROM archive_charge WHERE user_id = :uid', 'SAM user %s has archive charge records.'),
)

_USER_SUMMARY_COUNTS = (
    ('archive', 'SELECT COUNT(*) FROM archive_charge_summary WHERE user_id = :uid'),
    ('dav', 'SELECT COUNT(*) FROM dav_charge_summary WHERE user_id = :uid'),
    ('disk', 'SELECT COUNT(*) FROM disk_charge_summary WHERE user_id = :uid'),
    ('hpc', 'SELECT COUNT(*) FROM hpc_charge_summary WHERE user_id = :uid'),
    ('comp', 'SELECT COUNT(*) FROM comp_charge_summary WHERE user_id = :uid'),
)

_ORG_BLOCKERS = (
    ('SELECT 1 FROM user_organization WHERE organization_id = :oid',
     'SAM organization id %s has associated users.'),
    ('SELECT 1 FROM project_organization WHERE organization_id = :oid',
     'SAM organization id %s has associated projects.'),
    # Not in legacy: a delete would NULL these references.
    ('SELECT 1 FROM organization WHERE parent_org_id = :oid',
     'SAM organization id %s has child organizations.'),
    ('SELECT 1 FROM resources WHERE prim_responsible_org_id = :oid',
     'SAM organization id %s is responsible for resources.'),
)


def _exists(session, sql: str, **params) -> bool:
    return session.execute(text(sql + ' LIMIT 1'), params).first() is not None


def _permit(key: str, value, purgeable: bool, message: Optional[str]) -> dict:
    return {key: value, 'purgeable': purgeable, 'message': message}


def _delete(session, obj, label: str) -> None:
    """Hard delete in a savepoint; a foreign key we did not foresee becomes a 400."""
    try:
        with session.begin_nested():
            session.delete(obj)
            session.flush()
    except IntegrityError as exc:
        raise SyncValidationError(f'{label} is still referenced by other records.') from exc


# ---------------------------------------------------------------------------
# user
# ---------------------------------------------------------------------------

def find_user(session, *, unix_uid=None, upid=None, username=None,
              order=('unix_uid', 'username', 'upid')) -> Optional[User]:
    """The user named by the first identifier given, in *order*; lowest user_id on a shared uid."""
    for key in order:
        if key == 'unix_uid' and unix_uid is not None:
            return (session.query(User).filter(User.unix_uid == unix_uid)
                    .order_by(User.user_id).first())
        if key == 'username' and username:
            return User.get_by_username(session, username)
        if key == 'upid' and upid is not None:
            return User.get_by_upid(session, upid)
    return None


def user_violations(session, user: User) -> list:
    out = ['SAM user %s is active.'] if user.active else []
    out += [msg for sql, msg in _USER_BLOCKERS if _exists(session, sql, uid=user.user_id)]
    for kind, sql in _USER_SUMMARY_COUNTS:
        count = session.execute(text(sql), {'uid': user.user_id}).scalar()
        if count:
            out.append(f'SAM user %s has {count} {kind} charge summary records')
    return [m % user.username for m in out]


def user_permit(session, *, unix_uid=None, upid=None, username=None, enabled=True) -> dict:
    """Legacy resolves a username from unixUid then upid; an unknown user is purgeable (D11)."""
    user = find_user(session, unix_uid=unix_uid, upid=upid, username=username,
                     order=('username', 'unix_uid', 'upid'))
    if user is None:
        name = username or (f'with unixUid {unix_uid}' if unix_uid is not None
                            else f'with upid {upid}')
        return _permit('username', username, True, f'Username {name} does not exist in SAM.')
    if not enabled:
        return _permit('username', user.username, False, PURGE_DISABLED)
    violations = user_violations(session, user)
    return _permit('username', user.username, not violations,
                   '\n'.join(violations) if violations else None)


def purge_user(session, *, unix_uid=None, upid=None, username=None, enabled=True) -> None:
    if unix_uid is None and upid is None and not username:
        raise SyncValidationError('Either unixUid, upid, or username of user must be specified.')
    user = find_user(session, unix_uid=unix_uid, upid=upid, username=username)
    if user is None:
        return
    if not enabled:
        raise SyncValidationError(PURGE_DISABLED)
    violations = user_violations(session, user)
    if violations:
        raise SyncValidationError('\n'.join(violations))
    _delete(session, user, f'SAM user {user.username}')


# ---------------------------------------------------------------------------
# institution / organization
# ---------------------------------------------------------------------------

def institution_permit(session, institution_id: int, *, enabled=True) -> dict:
    inst = session.get(Institution, institution_id)
    if inst is None:
        return _permit('institution_id', institution_id, True,
                       f'Institution id {institution_id} does not exist in SAM.')
    if not enabled:
        return _permit('institution_id', institution_id, False, PURGE_DISABLED)
    if _exists(session, 'SELECT 1 FROM user_institution WHERE institution_id = :iid',
               iid=institution_id):
        return _permit('institution_id', institution_id, False,
                       f'SAM institution id {institution_id} has associated users.')
    return _permit('institution_id', institution_id, True, None)


def purge_institution(session, institution_id: int, *, enabled=True) -> None:
    inst = session.get(Institution, institution_id)
    if inst is None:
        return
    permit = institution_permit(session, institution_id, enabled=enabled)
    if not permit['purgeable']:
        raise SyncValidationError(permit['message'])
    _delete(session, inst, f'SAM institution id {institution_id}')


def organization_permit(session, organization_id: int, *, enabled=True) -> dict:
    org = session.get(Organization, organization_id)
    if org is None:
        return _permit('organization_id', organization_id, True,
                       f'Organization id {organization_id} does not exist in SAM.')
    if not enabled:
        return _permit('organization_id', organization_id, False, PURGE_DISABLED)
    violations = [msg % organization_id for sql, msg in _ORG_BLOCKERS
                  if _exists(session, sql, oid=organization_id)]
    return _permit('organization_id', organization_id, not violations,
                   '\n'.join(violations) if violations else None)


def purge_organization(session, organization_id: int, *, enabled=True) -> None:
    org = session.get(Organization, organization_id)
    if org is None:
        return
    permit = organization_permit(session, organization_id, enabled=enabled)
    if not permit['purgeable']:
        raise SyncValidationError(permit['message'])
    _delete(session, org, f'SAM organization id {organization_id}')


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------

def _project_owns_gid(session, gid: int) -> bool:
    return _exists(session, 'SELECT 1 FROM project WHERE unix_gid = :gid', gid=gid)


def find_group(session, *, gid=None, name=None) -> Optional[AdhocGroup]:
    if gid is not None:
        group = AdhocGroup.get_by_unix_gid(session, gid)
        if group is not None:
            return group
    if name:
        return (session.query(AdhocGroup)
                .filter(func.lower(AdhocGroup.group_name) == name.lower()).first())
    return None


def group_permit(session, gid: int, *, enabled=True) -> dict:
    if _project_owns_gid(session, gid):
        return _permit('unix_gid', gid, False, f'unix gid {gid} belongs to a project group.')
    if not enabled and AdhocGroup.get_by_unix_gid(session, gid) is not None:
        return _permit('unix_gid', gid, False, PURGE_DISABLED)
    return _permit('unix_gid', gid, True, f'unix gid {gid} does not belong to a project group.')


def purge_group(session, *, gid=None, name=None, enabled=True) -> None:
    """Legacy runs no permit check on a group purge; only the lever guards it."""
    if gid is None and not name:
        raise SyncValidationError('Either posixGid or groupname of group must be specified.')
    group = find_group(session, gid=gid, name=name)
    if group is None:
        return
    if not enabled:
        raise SyncValidationError(PURGE_DISABLED)
    _delete(session, group, f'group {group.group_name}')
