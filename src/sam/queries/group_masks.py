"""Reads over ``unix_group_mask``: the rows the mirror and the two group derivations
honor (docs/plans/UNIX_GROUP_MASK.md § 3). SQLAlchemy and ``sam`` models only."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import bindparam, func, or_, text

from sam.core.groups import UnixGroupMask
from sam.core.users import User
from sam.projects.projects import Project
from sam.sqlcompat import ci_like


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


def project_mask_feed(session, *, now: datetime) -> Tuple[List[ProjectMask], Dict[int, datetime]]:
    """One read of every project mask: the open ones resolved, and ``{gid: latest open or
    close stamp}`` over all of them (the sync's incremental ``since`` must see a grant
    appear and vanish). A gid no project holds is skipped."""
    rows = session.query(UnixGroupMask).filter(UnixGroupMask.kind == 'project').all()
    times: Dict[int, datetime] = {}
    for r in rows:
        stamp = max(t for t in (r.creation_time, r.closed_at) if t)
        if r.unix_gid not in times or times[r.unix_gid] < stamp:
            times[r.unix_gid] = stamp
    open_rows = [r for r in rows if r.is_open_at(now)]
    if not open_rows:
        return [], times
    projects = {p.unix_gid: p for p in session.query(Project)
                .filter(Project.unix_gid.in_(sorted({r.unix_gid for r in open_rows})))}
    names = sorted({r.username.lower() for r in open_rows})
    users = {u.username.lower(): u for u in session.query(User)
             .filter(func.lower(User.username).in_(names))}
    branches = defaultdict(set)
    ids = sorted({p.project_id for p in projects.values()})
    if ids:
        for r in session.execute(_SQL_PROJECT_ACCOUNT_BRANCHES, {'ids': ids}):
            branches[r.project_id].add(r.branch)
    out = []
    for r in open_rows:
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
    return out, times


def project_masks(session, *, now: datetime) -> List[ProjectMask]:
    """Every open project mask at ``now``, resolved."""
    return project_mask_feed(session, now=now)[0]


def project_mask_times(session) -> Dict[int, datetime]:
    return project_mask_feed(session, now=datetime.now())[1]


# --- the masks tab ---------------------------------------------------------------

def search_mask_targets(session, q: str, *, limit: int = 15) -> List[dict]:
    """The picker's rows: active adhoc groups, then projects holding a gid, matched on
    name, code, title or gid. An inactive project stays offered: the straggler's grant
    is the point."""
    from sam.queries.lookups import search_groups_by_pattern
    q = (q or '').strip()
    if not q:
        return []
    out = [{'kind': 'adhoc', 'gid': g.unix_gid, 'name': g.group_name, 'detail': ''}
           for g in search_groups_by_pattern(session, q, limit=limit, active_only=True)]
    match = or_(ci_like(Project.projcode, f'%{q}%'), ci_like(Project.title, f'%{q}%'))
    if q.isdigit():
        match = or_(match, Project.unix_gid == int(q))
    projects = (session.query(Project).filter(Project.unix_gid.isnot(None), match)
                .order_by(Project.projcode).limit(limit).all())
    out += [{'kind': 'project', 'gid': p.unix_gid, 'name': p.projcode, 'detail': p.title or ''}
            for p in projects]
    return out


def batch_siblings_open(session, mask: UnixGroupMask) -> int:
    """Open rows that share ``mask``'s ticket, ``mask`` itself excluded."""
    return (session.query(func.count(UnixGroupMask.mask_id))
            .filter(UnixGroupMask.batch_id == mask.head_id, UnixGroupMask.mask_id != mask.mask_id,
                    UnixGroupMask.is_active).scalar() or 0)


MASK_STATES = ('pending', 'confirmed', 'expired', 'withdrawn')
#: Conditions an open row can carry beside its state; the tab's Flags facet.
MASK_FLAGS = ('ticket closed', 'no upid', 'no ticket')
MASK_SORTS = ('created', 'expires', 'group', 'user', 'state')


def group_names(session, gids: Iterable[int]) -> Dict[int, str]:
    """``{gid: name}`` over adhoc groups and projects; a gid nobody holds is absent."""
    gids = sorted(set(gids))
    if not gids:
        return {}
    from sam.core.groups import AdhocGroup
    names = {g.unix_gid: g.group_name for g in session.query(AdhocGroup)
             .filter(AdhocGroup.unix_gid.in_(gids))}
    for p in session.query(Project).filter(Project.unix_gid.in_([g for g in gids if g not in names])):
        names[p.unix_gid] = p.projcode
    return names


def mask_views(session, *, now: datetime, gids: Optional[Iterable[int]] = None,
               open_only: bool = False) -> List[dict]:
    """Every mask (or those on ``gids``) as a display row: the group's name, the member's
    upid (``None`` means the directory can never confirm it), the state at ``now``."""
    q = session.query(UnixGroupMask)
    if gids is not None:
        gids = list(gids)
        if not gids:
            return []
        q = q.filter(UnixGroupMask.unix_gid.in_(gids))
    if open_only:
        q = q.filter(UnixGroupMask.is_active)
    rows = q.order_by(UnixGroupMask.creation_time.desc(), UnixGroupMask.mask_id.desc()).all()
    names = group_names(session, (r.unix_gid for r in rows))
    lower = sorted({r.username.lower() for r in rows})
    upids = {u.lower(): upid for u, upid in session.query(User.username, User.upid)
             .filter(func.lower(User.username).in_(lower))} if lower else {}
    tickets = mask_tickets(session, [r for r in rows if r.kind == 'adhoc'])
    out = []
    for r in rows:
        upid = upids.get(r.username.lower())
        ticket = tickets.get(r.mask_id)
        is_open = r.is_open_at(now)
        flags = []
        if r.kind == 'adhoc' and is_open:
            if ticket and ticket['closed_at']:
                flags.append('ticket closed')
            if upid is None:
                flags.append('no upid')
            if ticket is None:
                flags.append('no ticket')
        out.append({
            'mask': r, 'mask_id': r.mask_id, 'gid': r.unix_gid, 'kind': r.kind,
            'group_name': names.get(r.unix_gid, ''), 'username': r.username, 'op': r.op,
            'state': r.state, 'open': is_open,
            'unconfirmable': r.kind == 'adhoc' and upid is None,
            'ticket': ticket, 'flags': flags,
        })
    return out


def filter_mask_views(rows: List[dict], *, kind: Optional[str] = None, states=(), flags=(),
                      q: Optional[str] = None) -> List[dict]:
    needle = (q or '').strip().lower()
    states, flags = set(states or ()), set(flags or ())
    return [r for r in rows
            if (not kind or r['kind'] == kind)
            and (not states or r['state'] in states)
            and (not flags or flags & set(r['flags']))
            and (not needle or needle in r['group_name'].lower() or needle in r['username'].lower())]


def sort_mask_views(rows: List[dict], sort_by: Optional[str], sort_dir: str) -> List[dict]:
    keys = {
        'created': lambda r: r['mask'].creation_time,
        'expires': lambda r: r['mask'].expires_at or datetime.max,
        'group': lambda r: (r['group_name'].lower(), r['username'].lower()),
        'user': lambda r: (r['username'].lower(), r['group_name'].lower()),
        'state': lambda r: (MASK_STATES.index(r['state']) if r['state'] in MASK_STATES else 9,
                            r['mask'].creation_time),
    }
    key = keys.get(sort_by or 'created', keys['created'])
    return sorted(rows, key=key, reverse=(sort_dir != 'asc'))


# --- NUSD's ticket (adhoc masks only) ------------------------------------------------

MASK_ENTITY = 'unix_group_mask'
MASK_TICKET_KIND = 'hpc_group_ticket'
UPSTREAM_SOURCE = 'Google Groups'


def mask_ticket_handle(mask_id: int) -> str:
    """``SAM-GM-<id>``: the ticket's lookup handle (``ticket_handle`` for account requests)."""
    return f'SAM-GM-{mask_id}'


def _oxford(parts: Sequence[str]) -> str:
    parts = list(parts)
    return (', '.join(parts[:-1]) + ' and ' + parts[-1]) if len(parts) > 1 else ''.join(parts)


def mask_ticket_subject(masks: Sequence[UnixGroupMask], group_name: str) -> str:
    head = masks[0]
    who = head.username if len(masks) == 1 else f'{len(masks)} members'
    return (f'Unix group change: {head.op} {who} '
            f'{"to" if head.op == "add" else "from"} {group_name} '
            f'[{mask_ticket_handle(head.mask_id)}]')


def build_mask_ticket_message(masks: Sequence[UnixGroupMask], *, group_name: str,
                              branches: Iterable[str], display_names: Dict[str, str],
                              recipient: str, requested_by: str,
                              sender: Optional[str] = None, masks_url: str = ''):
    """NUSD's ticket for one batch of adhoc masks (head first): every field one line, no HTML."""
    from sam import fmt
    from sam.notify.base import Message, Recipient
    head = masks[0]
    branches = list(branches)
    where = _oxford(branches)
    members = [{'username': m.username, 'display_name': display_names.get(m.username) or ''}
               for m in masks]
    members_line = _oxford([f"'{m['username']}'" + (f" ({m['display_name']})" if m['display_name'] else '')
                            for m in members])
    verb = 'wrote' if head.op == 'add' else 'removed'
    joiner = 'into' if head.op == 'add' else 'from'
    # Kept in the ledger context for the operator's record; the ticket text does not use it,
    # since NUSD acts on the group name and never sees a branch.
    applied = (f"SAM {verb} {_oxford([m.username for m in masks])} {joiner} {group_name}'s entries"
               f" on {where or 'its branches'} at {fmt.date_str(head.creation_time, fmt='%Y-%m-%d %H:%M')}.")
    return Message(
        kind=MASK_TICKET_KIND,
        recipient=Recipient(recipient.strip(), name='NUSD', role='operator'),
        subject=mask_ticket_subject(masks, group_name),
        context={
            'group_name': group_name, 'gid': head.unix_gid, 'kind': head.kind,
            'source': UPSTREAM_SOURCE, 'members': members, 'members_line': members_line,
            'member_count': len(members), 'op': head.op,
            'opened_at': fmt.date_str(head.creation_time, fmt='%Y-%m-%d %H:%M'),
            'expires_at': fmt.date_str(head.expires_at) if head.expires_at else '',
            'note': head.note or '', 'applied': applied, 'mask_id': head.mask_id,
            'masks_url': masks_url, 'requested_by': head.requested_by,
        },
        entity=(MASK_ENTITY, head.mask_id),
        dedup_key=f'{MASK_TICKET_KIND}:{head.mask_id}',
        requested_by=requested_by,
        sender=sender,
    )


def mask_tickets(session, masks: Iterable[UnixGroupMask]) -> Dict[int, dict]:
    """``{mask_id: {key url status closed_at}}``: the oldest ticket on each mask's batch head."""
    from sam.integration.tickets.queries import tickets_for
    head_of = {m.mask_id: m.head_id for m in masks}
    found = tickets_for(session, MASK_ENTITY, set(head_of.values())) if head_of else {}
    if not found:
        return {}
    from sam.integration.tickets.registry import provider_for_stored   # imports requests
    providers = {name: provider_for_stored(name)
                 for name in {t.provider for ts in found.values() for t in ts}}

    def view(ts):
        return {'key': ts[0].ticket_key,
                'url': providers[ts[0].provider].browse_url(ts[0].ticket_key)
                       if providers[ts[0].provider] else '',
                'status': ts[0].status or '', 'closed_at': ts[0].closed_at}
    return {mid: view(found[head]) for mid, head in head_of.items() if head in found}
