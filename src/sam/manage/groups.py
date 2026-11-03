"""An operator's add/remove over a mirrored unix group (docs/plans/UNIX_GROUP_MASK.md).

Flask-free: the webapp passes the RBAC catalog's group subjects in. An adhoc mask
is written through to ``adhoc_system_account_entry`` here and protected inside
``sync_group``; a project mask is applied by the readers. Several members opened
together form one batch under the head mask (one ticket per batch).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, List, Optional, Sequence, Tuple

from sqlalchemy import func

from sam.core.groups import AdhocGroup, AdhocSystemAccountEntry, UnixGroupMask
from sam.core.users import User
from sam.projects.projects import Project
from sam.queries.group_masks import open_mask_for

logger = logging.getLogger(__name__)

ENTRY_USERNAME_WIDTH = 12     # adhoc_system_account_entry.username
MASK_BATCH_MAX = 20           # members one submit may name


class MaskError(ValueError):
    """A refusal the form shows as written."""


@dataclass(frozen=True)
class MaskTarget:
    kind: str
    gid: int
    name: str
    group: Optional[AdhocGroup] = None
    project: Optional[Project] = None

    @property
    def label(self) -> str:
        return f'{self.name} (gid {self.gid}, {self.kind})'


def resolve_target(session, gid: int) -> MaskTarget:
    """The adhoc group holding ``gid``, else the project; neither is a refusal (SAM never creates a group)."""
    group = AdhocGroup.get_by_unix_gid(session, gid)
    if group is not None:
        return MaskTarget('adhoc', gid, group.group_name, group=group)
    project = session.query(Project).filter(Project.unix_gid == gid).first()
    if project is not None:
        return MaskTarget('project', gid, project.projcode, project=project)
    raise MaskError(f'No adhoc group or project holds gid {gid}.')


def parse_usernames(text: str) -> Tuple[List[str], List[str]]:
    """One username per line, blank and ``#`` lines skipped: ``(names, problems)``, problems naming lines."""
    names: List[str] = []
    problems: List[str] = []
    seen: dict = {}
    for number, raw in enumerate((text or '').splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if ' ' in line or len(line) > 35:
            problems.append(f'line {number}: expected one username')
            continue
        key = line.lower()
        if key in seen:
            problems.append(f'line {number}: duplicate of line {seen[key]}')
            continue
        seen[key] = number
        names.append(line)
    if len(names) > MASK_BATCH_MAX:
        problems.append(f'At most {MASK_BATCH_MAX} members at once; open another mask for the rest.')
    if not names and not problems:
        problems.append('Name at least one member.')
    return names, problems


def check_mask(session, *, gid: int, username: str, op: str, now: Optional[datetime] = None,
               protected_groups: FrozenSet[str] = frozenset(),
               may_touch_protected: bool = False) -> Tuple[MaskTarget, User, Optional[UnixGroupMask]]:
    """The guards of ``open_mask`` without the write: the target, the user, and an overdue
    standing row the open would expire. Raises ``MaskError``."""
    now = now or datetime.now()
    target = resolve_target(session, gid)
    user = User.get_by_username(session, (username or '').strip())
    if user is None:
        raise MaskError(f'No user named {username!r}.')
    if target.kind == 'adhoc':
        group = target.group
        if not group.is_active:
            raise MaskError(f'Group {group.group_name} is inactive.')
        if not group.branch_names(session):
            raise MaskError(f'Group {group.group_name} names no access branch; it has no members to mask.')
        if group.group_name in protected_groups and not may_touch_protected:
            raise MaskError(f'Group {group.group_name} grants a SAMuel role; MANAGE_ROLES is required.')
        if len(user.username) > ENTRY_USERNAME_WIDTH:
            raise MaskError(f'{user.username} is longer than an entry allows ({ENTRY_USERNAME_WIDTH}).')
    if op == 'remove' and user.primary_gid == gid:
        raise MaskError(f'{user.username} has {target.name} as their primary group; change that first.')
    standing = open_mask_for(session, gid=gid, username=user.username)
    if standing is not None and standing.is_open_at(now):
        raise MaskError(f'An open {standing.op} already stands for {user.username} on {target.name}.')
    return target, user, standing


def open_mask(session, *, gid: int, username: str, op: str, requested_by: str,
              note=None, expires_at: Optional[datetime] = None, now: Optional[datetime] = None,
              protected_groups: FrozenSet[str] = frozenset(),
              may_touch_protected: bool = False, batch_id: Optional[int] = None) -> UnixGroupMask:
    """Open one mask after the guards; an adhoc mask is materialized at once. Raises ``MaskError``."""
    now = now or datetime.now()
    target, user, standing = check_mask(session, gid=gid, username=username, op=op, now=now,
                                        protected_groups=protected_groups,
                                        may_touch_protected=may_touch_protected)
    if standing is not None:
        # Overdue and not yet swept by the watcher: it ends here, as the watcher would end it.
        close_mask(session, standing, resolution='expired', by=requested_by, now=now)
    try:
        mask = UnixGroupMask.create(session, unix_gid=gid, kind=target.kind, username=user.username,
                                    op=op, requested_by=requested_by, note=note,
                                    expires_at=expires_at, when=now, batch_id=batch_id)
    except ValueError as exc:
        raise MaskError(str(exc)) from exc
    if target.kind == 'adhoc':
        changed = materialize(session, mask, group=target.group)
        logger.info('group mask %s: %s %s on %s, %d entry rows', mask.mask_id, op,
                    user.username, target.name, changed)
    return mask


def open_masks(session, *, gid: int, usernames: Sequence[str], op: str, requested_by: str,
               note=None, expires_at: Optional[datetime] = None, now: Optional[datetime] = None,
               protected_groups: FrozenSet[str] = frozenset(),
               may_touch_protected: bool = False) -> List[UnixGroupMask]:
    """One batch, head first, every row stamped with the head's id; every guard runs before
    anything is written, so a refusal names the member and leaves nothing behind."""
    if not usernames:
        raise MaskError('Name at least one member.')
    if len(usernames) > MASK_BATCH_MAX:
        raise MaskError(f'At most {MASK_BATCH_MAX} members at once.')
    now = now or datetime.now()
    for name in usernames:
        check_mask(session, gid=gid, username=name, op=op, now=now,
                   protected_groups=protected_groups, may_touch_protected=may_touch_protected)
    masks: List[UnixGroupMask] = []
    for name in usernames:
        masks.append(open_mask(session, gid=gid, username=name, op=op, requested_by=requested_by,
                               note=note, expires_at=expires_at, now=now,
                               protected_groups=protected_groups,
                               may_touch_protected=may_touch_protected,
                               batch_id=masks[0].mask_id if masks else None))
    return masks


def _entries(group: AdhocGroup, username: str):
    return [row for row in group.system_accounts if row.username.lower() == username.lower()]


def _apply(session, group: AdhocGroup, username: str, op: str) -> int:
    """Write ``op`` for one member through the group's entry rows; returns rows changed."""
    changed = 0
    if op == 'add':
        have = {row.access_branch_name for row in _entries(group, username)}
        for branch in group.branch_names(session):
            if branch not in have:
                group.system_accounts.append(
                    AdhocSystemAccountEntry(access_branch_name=branch, username=username))
                changed += 1
    else:
        for row in _entries(group, username):
            group.system_accounts.remove(row)
            changed += 1
    session.flush()
    return changed


def materialize(session, mask: UnixGroupMask, *, group: Optional[AdhocGroup] = None) -> int:
    """Make the entry table say what the adhoc mask says; a no-op for a project mask."""
    if mask.kind != 'adhoc':
        return 0
    group = group or AdhocGroup.get_by_unix_gid(session, mask.unix_gid)
    if group is None:
        return 0
    return _apply(session, group, mask.username, mask.op)


def unmaterialize(session, mask: UnixGroupMask, *, group: Optional[AdhocGroup] = None) -> int:
    """Reverse an adhoc mask's write; the next PUT or a later confirm restores upstream's view."""
    if mask.kind != 'adhoc':
        return 0
    group = group or AdhocGroup.get_by_unix_gid(session, mask.unix_gid)
    if group is None:
        return 0
    return _apply(session, group, mask.username, 'remove' if mask.op == 'add' else 'add')


def close_mask(session, mask: UnixGroupMask, *, resolution: str, by: str,
               now: Optional[datetime] = None) -> UnixGroupMask:
    """Close one mask; a withdrawn or expired adhoc mask takes its entry rows back with it."""
    try:
        mask.close(resolution=resolution, by=by, when=now)
    except ValueError as exc:
        raise MaskError(str(exc)) from exc
    if resolution in ('withdrawn', 'expired'):
        changed = unmaterialize(session, mask)
        logger.info('group mask %s closed %s by %s, %d entry rows reversed',
                    mask.mask_id, resolution, by, changed)
    return mask


# --- the hourly watch ---------------------------------------------------------------

def sweep_masks(session, reader, *, now: datetime, by: str) -> dict:
    """Expire overdue masks, confirm adhoc ones the directory now agrees with, re-assert a
    drifted materialization, and count the rest (docs/plans/UNIX_GROUP_MASK.md § 4).
    ``reader`` is a configured ``DirectoryReader`` or None; nothing here files or comments."""
    from sam.integration.directory import DirectoryUnavailable
    from sam.queries.group_masks import mask_tickets, open_mask_rows

    counts = {'open': 0, 'expired': 0, 'confirmed': 0, 'reasserted': 0, 'flagged': 0,
              'unconfirmable': 0, 'unreadable': 0, 'directory': reader is not None}
    rows = open_mask_rows(session, include_overdue=True)
    counts['open'] = len(rows)
    if not rows:
        return counts
    adhoc = [r for r in rows if r.kind == 'adhoc']
    tickets = mask_tickets(session, adhoc)
    names = sorted({r.username.lower() for r in adhoc})
    upids = dict(session.query(func.lower(User.username), User.upid)
                 .filter(func.lower(User.username).in_(names))) if names else {}
    records: dict = {}
    for mask in rows:
        if not mask.is_open_at(now):
            close_mask(session, mask, resolution='expired', by=by, now=now)
            counts['expired'] += 1
            continue
        if mask.kind != 'adhoc':
            continue
        group = AdhocGroup.get_by_unix_gid(session, mask.unix_gid)
        if group is not None and materialize(session, mask, group=group):
            counts['reasserted'] += 1
            logger.warning('group mask %s: %s %s on %s had drifted; re-asserted',
                           mask.mask_id, mask.op, mask.username, group.group_name)
        upid = upids.get(mask.username.lower())
        if upid is None:
            counts['unconfirmable'] += 1
            continue
        if reader is None:
            continue
        if mask.unix_gid not in records:
            try:
                records[mask.unix_gid] = reader.group(mask.unix_gid)
            except DirectoryUnavailable as exc:
                counts['unreadable'] += 1
                logger.warning('group mask sweep: directory unreadable (%s); confirmations skipped', exc)
                reader = None
                continue
        record = records[mask.unix_gid]
        if record is None:
            continue
        if (upid in record.member_upids) == (mask.op == 'add'):
            close_mask(session, mask, resolution='confirmed', by=by, now=now)
            counts['confirmed'] += 1
        elif (tickets.get(mask.mask_id) or {}).get('closed_at'):
            counts['flagged'] += 1
    return counts


def sweep_headline(counts: dict) -> str:
    parts = [f"{counts['open']} open", f"{counts['expired']} expired",
             f"{counts['confirmed']} confirmed", f"{counts['reasserted']} re-asserted",
             f"{counts['flagged']} ticket closed without the change",
             f"{counts['unconfirmable']} with no upid"]
    line = ', '.join(parts)
    if not counts['directory']:
        line += '; no directory configured'
    elif counts['unreadable']:
        line += '; directory unreadable'
    return line
