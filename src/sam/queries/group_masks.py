"""Reads over ``unix_group_mask``: the rows the mirror and the two group derivations
honor (docs/plans/UNIX_GROUP_MASK.md § 3). SQLAlchemy and ``sam`` models only."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from sqlalchemy import bindparam, func, or_, text

from sam.core.groups import UnixGroupMask
from sam.core.users import User
from sam.projects.projects import Project


def _open_at(query, now: datetime):
    return query.filter(UnixGroupMask.is_active,
                        or_(UnixGroupMask.expires_at.is_(None), UnixGroupMask.expires_at > now))


def open_masks(session, *, now: datetime, gids: Optional[Iterable[int]] = None,
               kind: Optional[str] = None) -> Dict[int, Tuple[set, set]]:
    """``{gid: (adds, removes)}`` of lowercased usernames for the masks open at ``now``."""
    q = _open_at(session.query(UnixGroupMask.unix_gid, UnixGroupMask.username, UnixGroupMask.op), now)
    if gids is not None:
        gids = list(gids)
        if not gids:
            return {}
        q = q.filter(UnixGroupMask.unix_gid.in_(gids))
    if kind:
        q = q.filter(UnixGroupMask.kind == kind)
    out: Dict[int, Tuple[set, set]] = {}
    for gid, username, op in q:
        adds, removes = out.setdefault(gid, (set(), set()))
        (adds if op == 'add' else removes).add(username.lower())
    return out


def open_mask_rows(session, *, now: Optional[datetime] = None, gids: Optional[Iterable[int]] = None,
                   username: Optional[str] = None, include_overdue: bool = False) -> List[UnixGroupMask]:
    """Open rows oldest first; ``include_overdue`` keeps rows past their expiry (the watcher's view)."""
    q = session.query(UnixGroupMask).filter(UnixGroupMask.is_active)
    if not include_overdue:
        q = _open_at(q, now or datetime.now())
    if gids is not None:
        gids = list(gids)
        if not gids:
            return []
        q = q.filter(UnixGroupMask.unix_gid.in_(gids))
    if username:
        q = q.filter(func.lower(UnixGroupMask.username) == username.lower())
    return q.order_by(UnixGroupMask.creation_time, UnixGroupMask.mask_id).all()


def open_mask_for(session, *, gid: int, username: str) -> Optional[UnixGroupMask]:
    """The one open row for (gid, username), whatever its expiry; the app-level uniqueness rule."""
    return (session.query(UnixGroupMask)
            .filter(UnixGroupMask.is_active, UnixGroupMask.unix_gid == gid,
                    func.lower(UnixGroupMask.username) == username.lower())
            .order_by(UnixGroupMask.mask_id).first())


# --- project masks ----------------------------------------------------------------

@dataclass(frozen=True)
class ProjectMask:
    """One open project mask with what the derivations need: the member's identity and
    the branches the project holds accounts on, whatever the allocation's end."""
    mask_id: int
    project_id: int
    projcode: str
    gid: int
    username: str
    upid: Optional[int]
    is_role: bool
    op: str
    branches: FrozenSet[str]


_SQL_PROJECT_ACCOUNT_BRANCHES = text("""
    SELECT DISTINCT a.project_id, ab.name AS branch
      FROM account a
      JOIN resources r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource abr ON abr.resource_id = r.resource_id
      JOIN access_branch ab ON ab.access_branch_id = abr.access_branch_id
     WHERE a.project_id IN :ids
""").bindparams(bindparam('ids', expanding=True))


def project_masks(session, *, now: datetime) -> List[ProjectMask]:
    """Every open project mask at ``now``, resolved; a gid no project holds is skipped."""
    rows = _open_at(session.query(UnixGroupMask), now).filter(UnixGroupMask.kind == 'project').all()
    if not rows:
        return []
    projects = {p.unix_gid: p for p in session.query(Project)
                .filter(Project.unix_gid.in_(sorted({r.unix_gid for r in rows})))}
    names = sorted({r.username.lower() for r in rows})
    users = {u.username.lower(): u for u in session.query(User)
             .filter(func.lower(User.username).in_(names))}
    branches = defaultdict(set)
    ids = sorted({p.project_id for p in projects.values()})
    if ids:
        for r in session.execute(_SQL_PROJECT_ACCOUNT_BRANCHES, {'ids': ids}):
            branches[r.project_id].add(r.branch)
    out = []
    for r in rows:
        project = projects.get(r.unix_gid)
        if project is None:
            continue
        user = users.get(r.username.lower())
        out.append(ProjectMask(
            mask_id=r.mask_id, project_id=project.project_id, projcode=project.projcode,
            gid=r.unix_gid, username=user.username if user else r.username,
            upid=user.upid if user else None,
            is_role=bool(user is not None and user.login_type is not None
                         and user.login_type.type == 'role_login'),
            op=r.op, branches=frozenset(branches.get(project.project_id, ()))))
    return out


def project_mask_times(session) -> Dict[int, datetime]:
    """``{gid: latest open or close stamp}`` over every project mask: the sync's
    incremental ``since`` must see a grant appear and vanish."""
    rows = session.query(UnixGroupMask.unix_gid,
                         func.max(UnixGroupMask.creation_time),
                         func.max(UnixGroupMask.closed_at)) \
        .filter(UnixGroupMask.kind == 'project').group_by(UnixGroupMask.unix_gid)
    return {gid: max(t for t in (created, closed) if t) for gid, created, closed in rows}
