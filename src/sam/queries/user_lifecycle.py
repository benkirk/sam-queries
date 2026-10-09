"""Collaborator keep-alive: ``GET userlifecycle/collabexpiryupdates``.

For each external collaborator (an open ``user_institution`` row and no open
``user_organization`` row), the latest date SAM still needs them: contracts as
PI/monitor, projects they lead or administer (their contracts and latest
allocation), current memberships, and disk holdings (+90 days). The daemon then
extends, never shortens, the directory's end date. Legacy
``DefaultUserLifecycleManager``; its defects are fixed here (the admin was never
counted, an open end date was an NPE, and ``now`` froze at server start).
See ``docs/plans/LDAP_SYNC_API.md``.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from sam.dates import end_of_day, format_ymd
from sam.queries.ldapsync import USER_LOGIN

#: Legacy ``userlifecycle.{disk,deactivation}.gracePeriod.days`` defaults.
DISK_GRACE_DAYS = 90
DEACTIVATION_GRACE_DAYS = 90

_FOREVER = datetime.max


def _grace_end(day: datetime, days: int) -> datetime:
    """``days`` after *day*, at 23:59:59 (legacy: start of day + days + 1, minus a second)."""
    return end_of_day(day + timedelta(days=days))


def _flag(value) -> bool:
    """``disk_activity.processing_status`` is BIT(1) on MySQL, and a zero byte is truthy."""
    if isinstance(value, (bytes, bytearray)):
        return any(value)
    return bool(value)


def _latest(rows):
    """``{user_id: end_date}`` keeping each user's latest end; an open end is latest."""
    out = {}
    for user_id, end in rows:
        if user_id not in out or (end or _FOREVER) > (out[user_id] or _FOREVER):
            out[user_id] = end
    return out


def _in(sql: str, name: str):
    return text(sql).bindparams(bindparam(name, expanding=True))


class _Snapshot:
    """Every table the computation reads, loaded once per request."""

    def __init__(self, session: Session, now: datetime):
        ex = session.execute
        self.now = now
        p = {'now': now}

        self.contracts = {r.contract_id: r for r in ex(text("""
            SELECT contract_id, contract_number, principal_investigator_user_id AS pi,
                   contract_monitor_user_id AS monitor, end_date
              FROM contract WHERE end_date > :now"""), p)}
        self.user_contracts = defaultdict(set)
        for c in self.contracts.values():
            for uid in (c.pi, c.monitor):
                if uid is not None:
                    self.user_contracts[uid].add(c.contract_id)

        self.projects = {r.project_id: r for r in ex(text("""
            SELECT project_id, projcode, project_lead_user_id AS lead_id,
                   project_admin_user_id AS admin_id
              FROM project WHERE active IS TRUE"""))}
        self.projects_by_code = {r.projcode: r for r in self.projects.values()}
        self.user_projects = defaultdict(set)
        for proj in self.projects.values():
            for uid in (proj.lead_id, proj.admin_id):
                if uid is not None:
                    self.user_projects[uid].add(proj.project_id)

        self.project_contracts = defaultdict(list)
        if self.contracts and self.projects:
            for r in ex(_in('SELECT contract_id, project_id FROM project_contract '
                            'WHERE contract_id IN :cids', 'cids'),
                        {'cids': sorted(self.contracts)}):
                if r.project_id in self.projects:
                    self.project_contracts[r.project_id].append(r.contract_id)

        self.collab_end = _latest(ex(text("""
            SELECT user_id, end_date FROM user_institution
             WHERE end_date IS NULL OR end_date > :now"""), p))
        self.position_end = _latest(ex(text("""
            SELECT user_id, end_date FROM user_organization
             WHERE end_date IS NULL OR end_date > :now"""), p))

        self.account_users = defaultdict(list)
        for r in ex(text("""
            SELECT au.user_id, au.account_id, a.project_id, r.resource_name, au.end_date
              FROM account_user au
              JOIN account a ON a.account_id = au.account_id
              JOIN resources r ON r.resource_id = a.resource_id
             WHERE au.end_date IS NULL OR au.end_date > :now"""), p):
            self.account_users[r.user_id].append(r)

        account_ids = sorted({r.account_id for rows in self.account_users.values() for r in rows})
        self.alloc_by_account, self.alloc_by_project_resource = {}, {}
        self.latest_project_alloc = {}
        if account_ids:
            for r in ex(_in("""
                SELECT al.account_id, r.resource_name, a.project_id, al.end_date
                  FROM allocation al
                  JOIN account a ON a.account_id = al.account_id
                  JOIN resources r ON r.resource_id = a.resource_id
                 WHERE (al.end_date IS NULL OR al.end_date > :now)
                   AND al.account_id IN :aids""", 'aids'), {**p, 'aids': account_ids}):
                self.alloc_by_account[r.account_id] = r
                self.alloc_by_project_resource[(r.project_id, r.resource_name)] = r
                best = self.latest_project_alloc.get(r.project_id)
                if best is None or (r.end_date or _FOREVER) > (best.end_date or _FOREVER):
                    self.latest_project_alloc[r.project_id] = r

        latest_disk = ex(text('SELECT MAX(activity_date) FROM disk_charge_summary_status')).scalar()
        self.disk = list(ex(text("""
            SELECT username, projcode AS groupname, resource_name, directory_name,
                   activity_date, number_of_files, processing_status AS charged
              FROM disk_activity WHERE activity_date = :d"""), {'d': latest_disk})) \
            if latest_disk is not None else []
        self.project_directories = {r.directory_name: r.project_id for r in ex(text(
            'SELECT directory_name, project_id FROM project_directory'))}

        user_login = ex(text("SELECT login_type_id FROM login_type WHERE type = :t"),
                        {'t': USER_LOGIN}).scalar()
        candidate_ids = (set(self.user_contracts) | set(self.user_projects)
                         | set(self.collab_end) | set(self.position_end)
                         | set(self.account_users))
        disk_names = sorted({d.username for d in self.disk if d.username})
        self.users = {}
        if candidate_ids:
            for r in ex(_in("""
                SELECT user_id, upid, unix_uid, username FROM users
                 WHERE active IS TRUE AND (deleted IS NULL OR deleted IS FALSE)
                   AND login_type_id = :ul AND user_id IN :ids""", 'ids'),
                        {'ul': user_login, 'ids': sorted(candidate_ids)}):
                self.users[r.user_id] = r
        if disk_names:
            for r in ex(_in('SELECT user_id, upid, unix_uid, username FROM users '
                            'WHERE username IN :names', 'names'), {'names': disk_names}):
                self.users[r.user_id] = r
        self.users_by_name = {u.username: u for u in self.users.values()}

        self.legit_disk, self.rogue_disk = defaultdict(list), defaultdict(list)
        for d in self.disk:
            charged = _flag(d.charged)
            if charged:
                user = self.users_by_name.get(d.username)
                if user is None:
                    continue
                project_id = self.project_directories.get(d.directory_name)
                if project_id is None:
                    proj = self.projects_by_code.get((d.groupname or '').upper())
                    project_id = proj.project_id if proj else None
                if project_id is not None:
                    self.legit_disk[user.user_id].append((project_id, d))
                    continue
            self.rogue_disk[d.username].append(d)


def _assoc(kind: str, description: str, end: Optional[datetime]) -> dict:
    return {'type': kind, 'description': description, 'end_date': format_ymd(end)}


def _associations(snap: _Snapshot, user) -> list:
    uid, out = user.user_id, []
    for cid in snap.user_contracts.get(uid, ()):
        c = snap.contracts[cid]
        if c.monitor == uid:
            out.append(_assoc('Contract Monitor', f'Contract {c.contract_number}', c.end_date))
        if c.pi == uid:
            out.append(_assoc('Contract PI', f'Contract {c.contract_number}', c.end_date))

    for pid in snap.user_projects.get(uid, ()):
        proj = snap.projects[pid]
        for role, holder in (('Project Lead', proj.lead_id), ('Project Admin', proj.admin_id)):
            if holder != uid:
                continue
            for cid in snap.project_contracts.get(pid, ()):
                c = snap.contracts[cid]
                out.append(_assoc(f'{role} Project Contract',
                                  f'Project:Contract {proj.projcode}:{c.contract_number}',
                                  c.end_date))
            alloc = snap.latest_project_alloc.get(pid)
            if alloc is not None:
                out.append(_assoc(f'{role} Project Allocation',
                                  f'Project:Resource {proj.projcode}:{alloc.resource_name}',
                                  alloc.end_date))

    for au in snap.account_users.get(uid, ()):
        proj = snap.projects.get(au.project_id)
        if proj is None:
            continue
        alloc = snap.alloc_by_account.get(au.account_id)
        if au.end_date is not None and (alloc is None
                                        or au.end_date < (alloc.end_date or _FOREVER)):
            out.append(_assoc('AccountUser', f'Project:Resource {proj.projcode}:{au.resource_name}',
                              au.end_date))
        elif alloc is not None:
            out.append(_assoc('Allocation', f'Project:Resource {proj.projcode}:{alloc.resource_name}',
                              alloc.end_date))

    for project_id, d in snap.legit_disk.get(uid, ()):
        proj = snap.projects.get(project_id)
        name = proj.projcode if proj else f'(id={project_id})'
        day = format_ymd(d.activity_date)
        expiry = _grace_end(d.activity_date, DISK_GRACE_DAYS)
        alloc = snap.alloc_by_project_resource.get((project_id, d.resource_name))
        if alloc is None or expiry > (alloc.end_date or _FOREVER):
            out.append(_assoc('Disk Holdings', f'Project:Directory {name}:{d.directory_name} '
                              f'from {day} ({d.number_of_files} files)', expiry))
        else:
            out.append(_assoc('ProjectDirectory Allocation',
                              f'Project:Directory {name}:{d.directory_name} '
                              f'({d.number_of_files} files as of {day})', alloc.end_date))

    for d in snap.rogue_disk.get(user.username, ()):
        out.append(_assoc('Disk Holdings (rogue)',
                          f'Directory {d.directory_name} group {d.groupname} from '
                          f'{format_ymd(d.activity_date)} ({d.number_of_files} files)',
                          _grace_end(d.activity_date, DISK_GRACE_DAYS)))

    # Legacy order: an open end first, then latest first (yyyy-MM-dd sorts as a date).
    open_ended = [a for a in out if a['end_date'] is None]
    dated = sorted((a for a in out if a['end_date'] is not None),
                   key=lambda a: a['end_date'], reverse=True)
    return open_ended + dated


def user_status(snap: _Snapshot, user) -> dict:
    associations = _associations(snap, user)
    uid = user.user_id
    active_collab, active_staff = uid in snap.collab_end, uid in snap.position_end
    collab_end, position_end = snap.collab_end.get(uid), snap.position_end.get(uid)
    if not active_staff and not associations and collab_end is None:
        today = format_ymd(snap.now)
        associations.append(_assoc('(None)', f'Deactivation grace period from {today}',
                                   _grace_end(snap.now, DEACTIVATION_GRACE_DAYS)))
    nominal = None if active_staff or not associations else associations[0]['end_date']
    return {
        'user_id': uid, 'upid': user.upid, 'unix_uid': user.unix_uid,
        'username': user.username,
        'current_collaboration_end_date': format_ymd(collab_end),
        'current_position_end_date': format_ymd(position_end),
        'type': 'Staff' if active_staff else 'Collaborator' if active_collab else 'Orphan',
        'dated_associations': associations,
        'nominal_expiry': nominal,
        'active_collaborator': active_collab,
        'active_staff': active_staff,
    }


def collab_expiry_updates(session: Session, now: Optional[datetime] = None) -> list:
    """Collaborators whose directory end date is earlier than SAM's nominal expiry."""
    snap = _Snapshot(session, now or datetime.now())
    out = []
    for uid in sorted(snap.collab_end):
        user = snap.users.get(uid)
        if user is None:
            continue
        status = user_status(snap, user)
        nominal, current = status['nominal_expiry'], status['current_collaboration_end_date']
        if status['type'] != 'Collaborator' or nominal is None:
            continue
        if current is None or current < nominal:
            out.append(status)
    return out
