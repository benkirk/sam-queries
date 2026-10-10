"""Reads behind the HEUV API (``/api/protected/heuv/v1``), legacy Java SAM's portal feed.

Each function returns the plain dicts ``sam.schemas.heuv`` dumps; rounding is done here
(Java ``Math.round``). Lookups are case-insensitive on both backends, as legacy's
validators are. Rules, sources and the deliberate fixes: ``docs/apis/HEUV_API.md`` and
``docs/plans/HEUV_API_PORT.md`` §5-§6. The usage report reads charges through the
accounting kernel (``anchored_charges`` / ``trailing_window_charges``), never a new sum.
"""

from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from sam.accounting.accounts import Account, AccountUser
from sam.accounting.calculator import anchored_charges, usage_anchor
from sam.core.groups import DEFAULT_COMMON_GROUP, DEFAULT_COMMON_GROUP_GID
from sam.core.users import User
from sam.projects.projects import Project
from sam.queries import account_status as st
from sam.queries.rolling_usage import trailing_window_charges
from sam.resources.resources import Resource
from sam.security.access import AccessBranch

DATA_HOLDINGS = 'DataHoldings'
ACCRUED_CHARGES = 'AccruedCharges'
BYTES_PER_TB = 1e12


# ---------------------------------------------------------------------------
# Lookups (legacy @UsernameExists / @ProjcodeExists / @ResourceNameExists)
# ---------------------------------------------------------------------------

def find_user(session: Session, username: str) -> Optional[User]:
    return session.query(User).filter(func.lower(User.username) == username.lower()).first()


def find_project(session: Session, projcode: str) -> Optional[Project]:
    return Project.get_by_projcode(session, projcode)


def find_resource(session: Session, name: str) -> Optional[Resource]:
    return session.query(Resource).filter(func.lower(Resource.resource_name) == name.lower()).first()


def _today(now: datetime) -> date:
    return now.date()


def _commissioned_on(resource: Resource, now: datetime) -> bool:
    """Legacy ``Resource.isCommissioned``: day-granular, a NULL bound is open."""
    today = _today(now)
    return ((resource.commission_date is None or resource.commission_date.date() <= today)
            and (resource.decommission_date is None or resource.decommission_date.date() >= today))


def _effective_end(allocation, resource: Resource) -> Optional[datetime]:
    """Legacy ``Allocation.getEndDate``: the earlier of the end date and the resource's decommission."""
    decommission = resource.decommission_date if resource is not None else None
    if decommission is None:
        return allocation.end_date
    if allocation.end_date is None:
        return decommission
    return min(allocation.end_date, decommission)


def _memberships(session: Session, user: User) -> List[AccountUser]:
    return (session.query(AccountUser)
            .options(selectinload(AccountUser.account).selectinload(Account.project),
                     selectinload(AccountUser.account).selectinload(Account.resource))
            .filter(AccountUser.user_id == user.user_id, AccountUser.is_active)
            .all())


def _same_gid(user: User, gid) -> bool:
    return user.primary_gid is not None and user.primary_gid == gid


# ---------------------------------------------------------------------------
# user/{u}/...
# ---------------------------------------------------------------------------

def default_projects(user: User) -> List[Dict]:
    rows = [dict(username=user.username, resource_name=dp.resource.resource_name,
                 projcode=dp.project.projcode) for dp in user.default_projects]
    return sorted(rows, key=lambda r: r['resource_name'])


def user_groups(session: Session, user: User) -> List[Dict]:
    """``ncar`` first, then the user's project and adhoc groups by name; an inactive user gets ``ncar`` only."""
    from sam.queries.lookups import get_user_group_access

    found = set()
    if user.active:
        rows = get_user_group_access(session, username=user.username, include_projects=True)
        found = {(r['group_name'], r['unix_gid']) for r in rows.get(user.username, [])
                 if r['group_name'] != DEFAULT_COMMON_GROUP}
    projcodes = set()
    if found:
        names = [name.upper() for name, _gid in found]
        projcodes = {pc for (pc,) in session.query(Project.projcode).filter(Project.projcode.in_(names))}

    def row(name, gid):
        is_project = name != DEFAULT_COMMON_GROUP and name.upper() in projcodes
        return dict(username=user.username, group_name=name, unix_gid=gid, primary=_same_gid(user, gid),
                    project=is_project, projcode=name.upper() if is_project else None)

    return [row(DEFAULT_COMMON_GROUP, DEFAULT_COMMON_GROUP_GID)] + [row(n, g) for n, g in sorted(found)]


def user_access(session: Session, user: User, now: Optional[datetime] = None) -> List[Dict]:
    """Login resources: access branches of the user's configurable resources that are themselves resources."""
    now = now or datetime.now()
    resources = {au.account.resource for au in _memberships(session, user)
                 if au.account.resource is not None and au.account.resource.configurable}
    branch_names = {abr.access_branch.name for r in resources for abr in r.access_branch_resources}
    homes = {h.resource_id: h.home_directory for h in user.resource_homes}
    shells = {s.resource_shell.resource_id: s.resource_shell.shell_name for s in user.resource_shells}
    out = {}
    for name in branch_names:
        res = find_resource(session, name)
        if res is None or res.default_resource_shell_id is None or not _commissioned_on(res, now):
            continue
        base = 'null' if res.default_home_dir_base is None else res.default_home_dir_base  # Java string concat
        out[res.resource_id] = dict(
            username=user.username, resource_name=res.resource_name,
            resource_type=res.resource_type.resource_type,
            home_directory=homes.get(res.resource_id, f'{base}/{user.username}'),
            shell_name=shells.get(res.resource_id, res.default_shell.shell_name if res.default_shell else None),
        )
    return sorted(out.values(), key=lambda r: r['resource_name'])


def _assignments(session: Session, user: User, threshold_limited: Optional[bool],
                 resource: Optional[Resource]):
    rows = []
    for au in _memberships(session, user):
        account = au.account
        if resource is not None and account.resource_id != resource.resource_id:
            continue
        if threshold_limited is not None and (account.first_threshold is not None) != threshold_limited:
            continue
        rows.append(au)
    return rows


def assigned_projects(session: Session, user: User, threshold_limited: Optional[bool] = None,
                      resource: Optional[Resource] = None) -> List[Dict]:
    rows = _assignments(session, user, threshold_limited, resource)
    rows.sort(key=lambda au: (au.account.project.projcode, au.account.resource.resource_name,
                              au.start_date, au.account_user_id))
    out: Dict[str, Dict] = {}
    for au in rows:
        project = au.account.project
        entry = out.setdefault(project.projcode, dict(
            projcode=project.projcode, primary=_same_gid(user, project.unix_gid), title=project.title,
            resource_assignments=[]))
        entry['resource_assignments'].append(dict(
            resource_name=au.account.resource.resource_name, start_date=au.start_date, end_date=au.end_date))
    return list(out.values())


def assigned_resources(session: Session, user: User, threshold_limited: Optional[bool] = None) -> List[Dict]:
    rows = _assignments(session, user, threshold_limited, None)
    rows.sort(key=lambda au: (au.account.resource.resource_name, au.account.project.projcode,
                              au.start_date, au.account_user_id))
    out: Dict[str, Dict] = {}
    for au in rows:
        project = au.account.project
        name = au.account.resource.resource_name
        entry = out.setdefault(name, dict(resource_name=name, project_assignments=[]))
        entry['project_assignments'].append(dict(
            projcode=project.projcode, primary=_same_gid(user, project.unix_gid), title=project.title,
            start_date=au.start_date, end_date=au.end_date))
    return list(out.values())


def role_logins(session: Session, user: User) -> List[str]:
    """The user, then the active role accounts naming them as contact person."""
    if user.upid is None:
        return [user.username]
    # Raw `active`, not User.is_active: legacy does not test `locked` here.
    roles = (session.query(User.username)
             .filter(User.contact_person_upid == user.upid, User.active == True)  # noqa: E712
             .order_by(func.lower(User.username)).all())
    return [user.username] + [u for (u,) in roles]


def wallclock_exemptions(user: User, username_as_sent: str, active: Optional[bool] = None,
                         now: Optional[datetime] = None) -> Dict:
    """Exemptions by resource and queue, newest first; ``username`` echoes the path, as legacy does."""
    today = _today(now or datetime.now())

    def active_today(e):
        return e.start_date.date() <= today <= e.end_date.date()

    rows = [e for e in user.wallclock_exemptions if active is None or active_today(e) == active]
    rows.sort(key=lambda e: e.start_date, reverse=True)
    rows.sort(key=lambda e: (e.queue.resource.resource_name, e.queue.queue_name))
    resources: Dict[int, Dict] = {}
    queues: Dict[int, Dict] = {}
    for e in rows:
        res = resources.setdefault(e.queue.resource_id, dict(resource_name=e.queue.resource.resource_name, queues=[]))
        if e.queue_id not in queues:
            queues[e.queue_id] = dict(queue_name=e.queue.queue_name, exemptions=[])
            res['queues'].append(queues[e.queue_id])
        queues[e.queue_id]['exemptions'].append(dict(
            active=active_today(e), start_date=e.start_date, end_date=e.end_date,
            hour_limit=st.java_round(e.time_limit_hours), comment=e.comment))
    return dict(username=username_as_sent, resources=list(resources.values()))


# ---------------------------------------------------------------------------
# search/projcode, project/{p}/hierarchy, report/project/{p}
# ---------------------------------------------------------------------------

def _escape_like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def search_projcodes(session: Session, fragment: Optional[str], username: Optional[str]) -> List[Dict]:
    pattern = f'%{_escape_like(fragment.strip())}%' if fragment and fragment.strip() else '%'
    projects = Project.search_by_pattern(session, pattern, search_title=False, limit=None, escape='\\',
                                         member_username=username if username and username.strip() else None)
    return [dict(projcode=p.projcode, title=p.title) for p in projects]


class CircularHierarchy(RuntimeError):
    pass


def project_hierarchy(project: Project) -> Dict:
    """The requested project's whole tree from its root; children are the active ones, by projcode."""
    root = project.get_root() or project
    visited = set()

    def node(p):
        if p.projcode in visited:
            raise CircularHierarchy(f'Multiple entry of {p.projcode} in {root.projcode} hierarchy')
        visited.add(p.projcode)
        children = sorted((c for c in p.children if c.is_active), key=lambda c: c.projcode)
        return dict(projcode=p.projcode, parent_projcode=p.parent.projcode if p.parent else None,
                    root_projcode=root.projcode, children=[node(c) for c in children])

    return node(root)


def report_project(project: Project, now: Optional[datetime] = None) -> Dict:
    """Every account (deleted included) with its non-future allocations (deleted included)."""
    now = now or datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    accounts = []
    for account in project.accounts:
        if account.resource is None:
            continue
        allocations = []
        for alloc in sorted(account.allocations, key=lambda a: a.allocation_id, reverse=True):
            if alloc.start_date > start_of_today:
                continue
            end = _effective_end(alloc, account.resource)
            allocations.append(dict(start_date=alloc.start_date, end_date=end,
                                    active=alloc.start_date <= now and (end is None or now <= end)))
        if not allocations:
            continue
        allocations.sort(key=lambda a: a['start_date'].strftime('%Y-%m-%d'), reverse=True)
        accounts.append(dict(resource_name=account.resource.resource_name, allocations=allocations,
                             threshold_limited=account.first_threshold is not None
                             or account.second_threshold is not None))
    accounts.sort(key=lambda a: a['resource_name'])
    lead, admin = project.lead, project.admin
    return dict(
        projcode=project.projcode, title=project.title,
        lead_username=lead.username if lead else None, lead_name=lead.legacy_full_name if lead else None,
        admin_username=admin.username if admin else None, admin_name=admin.legacy_full_name if admin else None,
        abstract_text=project.abstract,
        hierarchical=project.parent_id is not None or bool(project.children),
        accounts=accounts,
    )


# ---------------------------------------------------------------------------
# access, access/resource/{r}
# ---------------------------------------------------------------------------

def accessible_resources(session: Session, name: Optional[str] = None) -> List[Dict]:
    """Configurable resources whose name is also an access-branch name (no commissioned test)."""
    branches = {n.lower() for (n,) in session.query(AccessBranch.name)}
    q = session.query(Resource).filter(Resource.configurable == True)  # noqa: E712
    if name is not None:
        q = q.filter(func.lower(Resource.resource_name) == name.lower())
    out = []
    for res in sorted(q.all(), key=lambda r: r.resource_name):
        if res.resource_name.lower() not in branches:
            continue
        shells = sorted(res.shells, key=lambda s: s.shell_name)
        out.append(dict(resource_name=res.resource_name, resource_type=res.resource_type.resource_type,
                        login=bool(shells),
                        shells=[dict(shell_name=s.shell_name, dflt=s.resource_shell_id == res.default_resource_shell_id)
                                for s in shells]))
    return out


# ---------------------------------------------------------------------------
# report/usage/project/{p}
# ---------------------------------------------------------------------------

def _usage_type(resource: Resource) -> str:
    return DATA_HOLDINGS if resource.resource_type.resource_type == 'DISK' else ACCRUED_CHARGES


def _active_account(project: Project, resource_name: str, now: datetime) -> Optional[Account]:
    """Legacy ``getActiveAccountForResource``: active project, commissioned resource, has allocations."""
    if not project.active:
        return None
    for account in sorted(project.accounts, key=lambda a: a.account_id):
        res = account.resource
        if (res is not None and res.resource_name == resource_name and account.creation_time <= now
                and _commissioned_on(res, now) and account.allocations):
            return account
    return None


def _classify(account: Account, now: datetime):
    """``(active allocation, has_future, has_prior)`` by legacy's day-granular range test, live rows only."""
    today = _today(now)
    active, future, prior = None, False, False
    for alloc in sorted(account.live_allocations, key=lambda a: (a.start_date, a.allocation_id)):
        end = _effective_end(alloc, account.resource)
        if end is not None and today > end.date():
            prior = True
        elif today < alloc.start_date.date():
            future = True
        else:
            active = alloc
    return active, future, prior


def _usernames(project: Project, resource_name: str, now: datetime) -> List[str]:
    """Day-granular membership on any of the project's accounts on the resource; no user-active test."""
    today = _today(now)
    names = [au.user.username
             for account in project.accounts if account.resource and account.resource.resource_name == resource_name
             for au in account.users
             if au.start_date.date() <= today and (au.end_date is None or today <= au.end_date.date())]
    return sorted(names)


def _nodes(resource_name: str, chain: List[Project], now: datetime):
    """Per chain project: its account, active allocation and lifecycle flags on one resource."""
    nodes = []
    for p in chain:
        account = _active_account(p, resource_name, now)
        active, future, prior = _classify(account, now) if account else (None, False, False)
        nodes.append(dict(project=p, account=account, alloc=active, future=future, prior=prior,
                          usage_type=_usage_type(account.resource) if account else None))
    return nodes


def project_usage_report(session: Session, project: Project, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now()
    chain = []
    p = project
    while p is not None and p not in chain:
        chain.insert(0, p)
        p = p.parent
    resource_names = []
    for account in sorted(project.accounts, key=lambda a: a.account_id):
        res = account.resource
        if (res is not None and account.creation_time <= now and _commissioned_on(res, now)
                and account.allocations and res.resource_name not in resource_names):
            resource_names.append(res.resource_name)

    trees = {rn: _nodes(rn, chain, now) for rn in resource_names}
    _attach_usage(session, trees, now)

    rows = [_report_row(rn, trees[rn], now) for rn in resource_names]
    rows.sort(key=lambda r: (r['resource_usage_type'] != DATA_HOLDINGS, r['resource_name']))
    return dict(projcode=project.projcode, report_date=now, account_reports=rows)


def _attach_usage(session: Session, trees: Dict[str, List[Dict]], now: datetime) -> None:
    """Debit (charges + adjustments, or TB held) and the 30/90-day window charges for every allocated node."""
    from sam.queries.disk_usage import bulk_get_subtree_disk_capacity

    anchors, disk_pairs = [], []
    for rn, nodes in trees.items():
        for i, n in enumerate(nodes):
            n['debit'], n['usage'], n['windows'] = 0.0, 0.0, (0.0, 0.0)
            account = n['account']
            if account is None:
                continue
            if n['usage_type'] == DATA_HOLDINGS:
                disk_pairs.append((n['project'], rn))
            elif n['alloc'] is not None:
                a = n['alloc']
                anchors.append(usage_anchor((rn, i), n['project'], account, account.resource.activity_type,
                                            a.start_date, a.end_date or now))
    if anchors:
        sums = anchored_charges(session, anchors)
        windows = {}
        for period in st.THRESHOLD_PERIODS:
            for subtree in (False, True):
                infos = [info for info, sub in anchors if sub is subtree]
                windows[(period, subtree)] = trailing_window_charges(session, infos, period, now, subtree=subtree)
        for info, subtree in anchors:
            rn, i = info['key']
            n = trees[rn][i]
            c = sums[info['key']]
            n['usage'] = sum(c['charges_by_type'].values())
            n['debit'] = n['usage'] + c['adjustment']
            n['windows'] = tuple(windows[(period, subtree)].get(info['key'], 0.0) for period in st.THRESHOLD_PERIODS)
    if disk_pairs:
        caps = bulk_get_subtree_disk_capacity(session, disk_pairs)
        for rn, nodes in trees.items():
            for n in nodes:
                if n['account'] is not None and n['usage_type'] == DATA_HOLDINGS:
                    cap = caps.get((n['project'].project_id, rn)) or {}
                    n['debit'] = n['usage'] = cap.get('used_bytes', 0) / BYTES_PER_TB
                    n['files'] = cap.get('file_count', 0)


def _thresholds(account: Account):
    return (account.first_threshold, account.second_threshold)


def _charging_status(nodes: List[Dict], i: int) -> str:
    """Legacy ``defineStatusFromCharging``, walked pre-order down the chain."""
    n = nodes[i]
    account, alloc = n['account'], n['alloc']
    if account is None:
        return st.NO_ACCOUNT
    if alloc is None:
        return st.NO_ALLOCATION
    if n['project'].charging_exempt and n['usage_type'] == ACCRUED_CHARGES:
        return st.NORMAL
    if i > 0:
        parent = _charging_status(nodes, i - 1)
        if parent != st.NORMAL:
            return parent
    divisor = st.legacy_divisor(alloc.start_date, alloc.end_date) if alloc.end_date else None
    if divisor == 0:
        divisor = None
    return st.charge_status(n['debit'], float(alloc.amount), _thresholds(account), n['windows'], divisor)


def _status(nodes: List[Dict]) -> str:
    """Legacy ``defineOverallStatus`` for the requested (last) node."""
    n = nodes[-1]
    lifecycle = st.lifecycle_status(has_account=n['account'] is not None, has_active=n['alloc'] is not None,
                                    has_future=n['future'], has_prior=n['prior'],
                                    project_active=n['project'].active)
    if lifecycle is not None:
        return lifecycle
    if n['project'].charging_exempt and n['usage_type'] == ACCRUED_CHARGES:
        return st.NORMAL
    return _charging_status(nodes, len(nodes) - 1)


def _threshold_reports(n: Dict) -> List[Dict]:
    alloc, account = n['alloc'], n['account']
    amount = float(alloc.amount)
    divisor = st.legacy_divisor(alloc.start_date, alloc.end_date) if alloc.end_date else 0
    both = account.first_threshold is not None and account.second_threshold is not None
    out = []
    for period, pct, charges in zip(st.THRESHOLD_PERIODS, _thresholds(account), n['windows']):
        share = st.threshold_allocation(period, amount, divisor) if divisor else None
        out.append(dict(
            period=period, percent_limit=pct if both else None,
            allocation_amount=st.java_round(share) if share else None,
            charges=st.java_round(charges),
            percent_usage=st.java_round(100.0 * charges / share) if share else None,
            label=f'{period}-Day'))
    return out


def _report_row(resource_name: str, nodes: List[Dict], now: datetime) -> Dict:
    n = nodes[-1]
    account, alloc = n['account'], n['alloc']
    status = _status(nodes)
    usernames = _usernames(n['project'], resource_name, now)
    amount = float(alloc.amount) if alloc is not None else None
    row = dict(
        resource_name=resource_name, resource_type=account.resource.resource_type.resource_type.upper(),
        resource_usage_type=n['usage_type'], status=status,
        allocation_start_date=alloc.start_date if alloc else None,
        allocation_end_date=_effective_end(alloc, account.resource) if alloc else None,
        allocation_propagated=bool(alloc and alloc.is_inheriting),
        allocation_amount=st.java_round(amount) if amount is not None else None,
        usernames=usernames,
        balance=st.java_round(amount - n['debit']) if amount is not None else None,
        user_count=len(usernames),
    )
    if n['usage_type'] == DATA_HOLDINGS:
        row.update(total_holdings=st.java_round(n['debit']), number_of_files=n.get('files', 0))
        return row
    limited = alloc is not None and status in st.CHARGE_STATUSES
    row.update(total_charges=st.java_round(n['debit']), adjustments=st.java_round(n['debit'] - n['usage']),
               threshold_reports=_threshold_reports(n) if limited else [], threshold_limited=limited)
    return row
