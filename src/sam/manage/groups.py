"""An operator's add/remove over a mirrored unix group (docs/plans/UNIX_GROUP_MASK.md).

Flask-free: the webapp passes the RBAC catalog's group subjects in. An adhoc mask
is written through to ``adhoc_system_account_entry`` here and protected inside
``sync_group``; a project mask is applied by the readers.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Optional

from sam.core.groups import AdhocGroup, AdhocSystemAccountEntry, UnixGroupMask
from sam.core.users import User
from sam.projects.projects import Project
from sam.queries.group_masks import open_mask_for

logger = logging.getLogger(__name__)

ENTRY_USERNAME_WIDTH = 12     # adhoc_system_account_entry.username


class MaskError(ValueError):
    """A refusal the form shows as written."""


@dataclass(frozen=True)
class MaskTarget:
    kind: str
    gid: int
    name: str
    group: Optional[AdhocGroup] = None
    project: Optional[Project] = None


def resolve_target(session, gid: int) -> MaskTarget:
    """The adhoc group holding ``gid``, else the project; neither is a refusal (SAM never creates a group)."""
    group = AdhocGroup.get_by_unix_gid(session, gid)
    if group is not None:
        return MaskTarget('adhoc', gid, group.group_name, group=group)
    project = session.query(Project).filter(Project.unix_gid == gid).first()
    if project is not None:
        return MaskTarget('project', gid, project.projcode, project=project)
    raise MaskError(f'No adhoc group or project holds gid {gid}.')


def open_mask(session, *, gid: int, username: str, op: str, requested_by: str,
              note=None, expires_at: Optional[datetime] = None, now: Optional[datetime] = None,
              protected_groups: FrozenSet[str] = frozenset(),
              may_touch_protected: bool = False) -> UnixGroupMask:
    """Open one mask after the guards; an adhoc mask is materialized at once. Raises ``MaskError``."""
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
    if standing is not None:
        # Overdue and not yet swept by the watcher: it ends here, as the watcher would end it.
        close_mask(session, standing, resolution='expired', by=requested_by, now=now)
    try:
        mask = UnixGroupMask.create(session, unix_gid=gid, kind=target.kind, username=user.username,
                                    op=op, requested_by=requested_by, note=note,
                                    expires_at=expires_at, when=now)
    except ValueError as exc:
        raise MaskError(str(exc)) from exc
    if target.kind == 'adhoc':
        changed = materialize(session, mask, group=target.group)
        logger.info('group mask %s: %s %s on %s, %d entry rows', mask.mask_id, op,
                    user.username, target.name, changed)
    return mask


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
