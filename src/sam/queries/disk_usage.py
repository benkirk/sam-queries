"""Disk-specific query helpers for the Resource Usage Details view.

These helpers exist because storage has different semantics than HPC/DAV:
the user-facing question is *capacity* (how full is this project right
now, and what does occupancy look like over time), not cumulative
TiB-year burn.

Two helpers:

  * ``get_disk_usage_timeseries_by_user`` — per-user bytes vs time
    pivot for the stacked-area chart, with top-N users carried as
    individual series and the rest lumped into ``"Others"``.

  * ``build_disk_subtree`` — filesystem tree (parent project + child
    sub-projects) annotated with current bytes / file count / fileset
    paths from ``ProjectDirectory``. Mirrors the shape produced by
    ``sam-admin accounting --reconcile-quotas`` but is web-renderable.
"""

from __future__ import annotations

from datetime import date as _stdlib_date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.core.users import User
from sam.projects.projects import Project, ProjectDirectory
from sam.resources.resources import Resource
from sam.summaries.disk_summaries import DiskChargeSummary, DiskChargeSummaryStatus


_EMPTY_CAP: Dict[str, Any] = {
    'used_bytes':    0,
    'used_tib':      0.0,
    'file_count':    0,
    'activity_date': None,
    'account_ids':   [],
}


def get_subtree_disk_capacity(
    session: Session,
    project: Project,
    resource_name: str,
) -> Dict[str, Any]:
    """Point-in-time disk occupancy summed across a project's subtree.

    Thin wrapper over :func:`bulk_get_subtree_disk_capacity` for the
    single-pair case (e.g. resource_details route). Both paths share one
    bulk implementation so query-count behavior is uniform.
    """
    out = bulk_get_subtree_disk_capacity(session, [(project, resource_name)])
    return out.get((project.project_id, resource_name), dict(_EMPTY_CAP))


def bulk_get_subtree_disk_capacity(
    session: Session,
    pairs: List[Tuple[Project, str]],
) -> Dict[Tuple[int, str], Dict[str, Any]]:
    """Bulk variant of :func:`get_subtree_disk_capacity`.

    Resolves all ``(project, resource_name)`` pairs in a fixed number of
    queries (regardless of pair count):

      1. ONE ``Resource`` lookup for the distinct resource names.
      2. ONE OR-combined ``Account``/``Project`` subtree query covering
         every pair's NestedSet range (or fallback project_id match).
      3. ONE ``DiskChargeSummaryStatus`` lookup for the current snapshot date.
      4. ONE bulk ``DiskChargeSummary`` aggregate keyed by account_id.
      5. ONE per-account fallback aggregate for accounts not on the
         current snapshot date (only fires if such accounts exist).

    Returns ``{(project_id, resource_name): cap_dict}``. Pairs whose
    resource doesn't exist or whose subtree has no disk accounts get an
    empty cap dict.
    """
    if not pairs:
        return {}

    out: Dict[Tuple[int, str], Dict[str, Any]] = {
        (p.project_id, rn): dict(_EMPTY_CAP) for p, rn in pairs
    }

    # 1) Resolve resources in one query.
    resource_names = sorted({rn for _, rn in pairs})
    res_rows = session.query(Resource.resource_id, Resource.resource_name).filter(
        Resource.resource_name.in_(resource_names),
    ).all()
    rid_by_name = {rn: rid for rid, rn in res_rows}

    # 2) Build OR-combined subtree filter; tag rows back to their pair via
    #    in-Python matching against the project NestedSet coords.
    conditions = []
    valid_pairs: List[Tuple[Project, str, int]] = []
    for project, rn in pairs:
        rid = rid_by_name.get(rn)
        if rid is None:
            continue
        valid_pairs.append((project, rn, rid))
        is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
        if is_tree_valid:
            conditions.append(and_(
                Project.tree_root == project.tree_root,
                Project.tree_left >= project.tree_left,
                Project.tree_right <= project.tree_right,
                Account.resource_id == rid,
            ))
        else:
            conditions.append(and_(
                Account.project_id == project.project_id,
                Account.resource_id == rid,
            ))

    if not conditions:
        return out

    candidate_rows = session.query(
        Account.account_id,
        Account.resource_id,
        Project.tree_root,
        Project.tree_left,
        Project.tree_right,
        Project.project_id,
    ).join(Project, Account.project_id == Project.project_id).filter(
        Account.deleted == False,  # noqa: E712
        or_(*conditions),
    ).all()

    # Re-attribute candidate accounts to each (project, resource) pair.
    accounts_per_pair: Dict[Tuple[int, str], List[int]] = {
        (p.project_id, rn): [] for p, rn, _ in valid_pairs
    }
    all_account_ids: set = set()
    for project, rn, rid in valid_pairs:
        key = (project.project_id, rn)
        is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
        if is_tree_valid:
            for r in candidate_rows:
                if r.resource_id != rid:
                    continue
                if r.tree_root != project.tree_root:
                    continue
                if r.tree_left is None or r.tree_right is None:
                    continue
                if r.tree_left >= project.tree_left and r.tree_right <= project.tree_right:
                    accounts_per_pair[key].append(r.account_id)
                    all_account_ids.add(r.account_id)
        else:
            for r in candidate_rows:
                if r.resource_id == rid and r.project_id == project.project_id:
                    accounts_per_pair[key].append(r.account_id)
                    all_account_ids.add(r.account_id)

    if not all_account_ids:
        for key, aids in accounts_per_pair.items():
            out[key] = {**_EMPTY_CAP, 'account_ids': aids}
        return out

    # 3) Current snapshot date.
    current_row = (
        session.query(DiskChargeSummaryStatus.activity_date)
        .filter(DiskChargeSummaryStatus.current == True)  # noqa: E712
        .order_by(DiskChargeSummaryStatus.activity_date.desc())
        .first()
    )
    candidate_date = current_row[0] if current_row else None

    # 4) Bulk aggregate at the candidate date.
    snap_by_account: Dict[int, Dict[str, Any]] = {}
    if candidate_date is not None:
        rows = session.query(
            DiskChargeSummary.account_id,
            func.coalesce(func.sum(DiskChargeSummary.bytes), 0).label('bytes'),
            func.coalesce(func.sum(DiskChargeSummary.number_of_files), 0).label('files'),
        ).filter(
            DiskChargeSummary.account_id.in_(all_account_ids),
            DiskChargeSummary.activity_date == candidate_date,
        ).group_by(DiskChargeSummary.account_id).all()
        for r in rows:
            snap_by_account[r.account_id] = {
                'bytes':         int(r.bytes or 0),
                'files':         int(r.files or 0),
                'activity_date': candidate_date,
            }

    # 5) Fallback: any account without a row on the current snapshot date
    #    falls back to its own most-recent row (matches single-account
    #    semantics in Account.current_disk_usage). One query covers all
    #    fallback accounts at once.
    missing = [aid for aid in all_account_ids if aid not in snap_by_account]
    if missing:
        max_dates = dict(session.query(
            DiskChargeSummary.account_id,
            func.max(DiskChargeSummary.activity_date),
        ).filter(
            DiskChargeSummary.account_id.in_(missing),
        ).group_by(DiskChargeSummary.account_id).all())
        if max_dates:
            # ONE aggregate query for all (account_id, max_date) pairs.
            ors = [
                and_(
                    DiskChargeSummary.account_id == aid,
                    DiskChargeSummary.activity_date == d,
                )
                for aid, d in max_dates.items()
            ]
            rows = session.query(
                DiskChargeSummary.account_id,
                DiskChargeSummary.activity_date,
                func.coalesce(func.sum(DiskChargeSummary.bytes), 0).label('bytes'),
                func.coalesce(func.sum(DiskChargeSummary.number_of_files), 0).label('files'),
            ).filter(or_(*ors)).group_by(
                DiskChargeSummary.account_id, DiskChargeSummary.activity_date,
            ).all()
            for r in rows:
                snap_by_account[r.account_id] = {
                    'bytes':         int(r.bytes or 0),
                    'files':         int(r.files or 0),
                    'activity_date': r.activity_date,
                }

    # 6) Roll up per-pair.
    for (pid, rn), aids in accounts_per_pair.items():
        used_bytes = 0
        files = 0
        latest = None
        for aid in aids:
            snap = snap_by_account.get(aid)
            if snap is None:
                continue
            used_bytes += snap['bytes']
            files += snap['files']
            if latest is None or snap['activity_date'] > latest:
                latest = snap['activity_date']
        out[(pid, rn)] = {
            'used_bytes':    used_bytes,
            'used_tib':      used_bytes / (1024 ** 4),
            'file_count':    files,
            'activity_date': latest,
            'account_ids':   aids,
        }
    return out


def get_disk_usage_timeseries_by_user(
    session: Session,
    *,
    account_ids: List[int],
    start_date: Optional[_stdlib_date] = None,
    end_date: Optional[_stdlib_date] = None,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Per-user disk-bytes time series for a stacked-area chart.

    Sums ``DiskChargeSummary.bytes`` grouped by ``(activity_date,
    user_id)`` for the given accounts and date range. Picks the top
    ``top_n`` users by their *latest-snapshot* bytes; everyone else is
    lumped into a single ``"Others"`` series. Missing (date, user) pairs
    are dense-filled with 0 so every series has the same length as
    ``dates``.

    Series order is **stack-friendly**: ``Others`` first (so it renders
    at the bottom of the stacked area in a neutral colour), then named
    users smallest-to-largest by latest-snapshot bytes (so the largest
    user sits on top of the stack — easiest to scan against the
    capacity bar). The chart layer reverses the legend so the visual
    order from top to bottom matches the legend top to bottom.

    Returns::

        {
          'dates':  [date, ...],                   # sorted ascending
          'series': [
            {'username': 'Others', 'values': [bytes, ...]},  # iff > top_n
            {'username': 'alice',  'values': [bytes, ...]},  # smallest named
            ...,                                              # ascending
            {'username': 'zach',   'values': [bytes, ...]},  # largest named
          ],
        }

    Empty input (no accounts, no rows) returns ``{'dates': [], 'series': []}``.
    """
    if not account_ids:
        return {'dates': [], 'series': []}

    q = session.query(
        DiskChargeSummary.activity_date,
        DiskChargeSummary.user_id,
        DiskChargeSummary.username,
        func.coalesce(func.sum(DiskChargeSummary.bytes), 0).label('bytes'),
    ).filter(
        DiskChargeSummary.account_id.in_(account_ids),
    )
    if start_date is not None:
        q = q.filter(DiskChargeSummary.activity_date >= start_date)
    if end_date is not None:
        q = q.filter(DiskChargeSummary.activity_date <= end_date)
    rows = q.group_by(
        DiskChargeSummary.activity_date,
        DiskChargeSummary.user_id,
        DiskChargeSummary.username,
    ).all()

    if not rows:
        return {'dates': [], 'series': []}

    # Pivot: per_user[user_id] = {'username': str, 'by_date': {date: bytes}}
    per_user: Dict[int, Dict[str, Any]] = {}
    dates_set: set = set()
    for activity_date, user_id, username, b in rows:
        dates_set.add(activity_date)
        u = per_user.setdefault(
            user_id,
            {'username': username or f'uid_{user_id}', 'by_date': {}},
        )
        # Same user might appear multiple times if username changed; take
        # the most recent non-empty username.
        if username and not u['username'].startswith('uid_'):
            u['username'] = username
        u['by_date'][activity_date] = int(b or 0)

    dates = sorted(dates_set)
    last_date = dates[-1]

    # Rank users by latest-snapshot bytes (zero if absent on last date).
    ranked = sorted(
        per_user.items(),
        key=lambda kv: kv[1]['by_date'].get(last_date, 0),
        reverse=True,
    )
    top_users = ranked[:top_n]
    rest_users = ranked[top_n:]

    series: List[Dict[str, Any]] = []
    # `Others` first → bottom of the stack, neutral colour at the chart
    # layer.
    if rest_users:
        others_values = [0] * len(dates)
        for _uid, info in rest_users:
            for i, d in enumerate(dates):
                others_values[i] += info['by_date'].get(d, 0)
        series.append({'username': 'Others', 'values': others_values})
    # Named users smallest → largest. `top_users` is sorted descending
    # by latest-snapshot bytes; reverse to put the largest on top of
    # the stack.
    for _uid, info in reversed(top_users):
        series.append({
            'username': info['username'],
            'values':   [info['by_date'].get(d, 0) for d in dates],
        })

    return {'dates': dates, 'series': series}


def build_disk_subtree(
    session: Session,
    root_project: Project,
    resource_name: str,
) -> Dict[str, Any]:
    """Filesystem-tree dict for the Resource Usage Details view.

    Walks ``root_project`` and its descendants (NestedSetMixin), and for
    each node attaches:

      * ``account_id`` of the node's account on this disk resource
        (None if the node has no account on this resource);
      * ``current_bytes`` / ``current_used_tib`` / ``file_count`` /
        ``activity_date`` from ``Account.current_disk_usage()``;
      * ``fileset_paths`` — list of active ``ProjectDirectory.directory_name``
        strings for the node;
      * ``children`` — recursive list of node dicts.

    Returns ``{'tree': <root_node_dict>, 'account_ids': [int, ...]}``,
    where ``account_ids`` aggregates every node's disk account so the
    caller can feed the stacked-area chart in a single query.

    Nodes without a disk account on ``resource_name`` still appear in
    the tree — they just have ``account_id=None`` and zero bytes. This
    keeps the structural hierarchy visible even when an intermediate
    project doesn't itself store data.
    """
    # Resolve the resource_id once so we can filter accounts cheaply.
    resource = session.query(Resource).filter(
        Resource.resource_name == resource_name,
    ).first()
    if resource is None:
        return {
            'tree': _node_dict(root_project, account=None, snapshot=None,
                               fileset_paths=[]),
            'account_ids': [],
        }

    # Filter inactive descendants — the tree gets noisy fast otherwise
    # (decommissioned sub-projects, expired allocations leaving empty
    # nodes). Use the universal `is_active` hybrid (ActiveFlagMixin on
    # Project: ``active == True``).
    descendants = [p for p in root_project.get_descendants(include_self=True)
                   if p.is_active]
    project_ids = [p.project_id for p in descendants]
    if not project_ids:
        return {
            'tree': _node_dict(root_project, account=None, snapshot=None,
                               fileset_paths=[]),
            'account_ids': [],
        }

    # Bulk-load accounts on this resource for the subtree.
    accounts = session.query(Account).filter(
        Account.project_id.in_(project_ids),
        Account.resource_id == resource.resource_id,
        Account.deleted == False,  # noqa: E712 — SQLAlchemy expression
    ).all()
    account_by_project_id: Dict[int, Account] = {
        a.project_id: a for a in accounts
    }

    # Bulk-load active project directories for the subtree. Use the
    # universal `is_active` hybrid rather than hand-rolled date checks
    # (CLAUDE.md "Universal is_active interface" rule).
    dirs = session.query(ProjectDirectory).filter(
        ProjectDirectory.project_id.in_(project_ids),
        ProjectDirectory.is_active,
    ).all()
    dirs_by_project_id: Dict[int, List[str]] = {}
    for d in dirs:
        dirs_by_project_id.setdefault(d.project_id, []).append(d.directory_name)

    # Build a flat node map, then thread parents → children using the
    # parent_id FK (works alongside the NestedSet coords).
    node_by_pid: Dict[int, Dict[str, Any]] = {}
    account_ids: List[int] = []
    for proj in descendants:
        account = account_by_project_id.get(proj.project_id)
        snapshot = account.current_disk_usage() if account is not None else None
        node = _node_dict(
            proj,
            account=account,
            snapshot=snapshot,
            fileset_paths=sorted(dirs_by_project_id.get(proj.project_id, [])),
        )
        node_by_pid[proj.project_id] = node
        if account is not None:
            account_ids.append(account.account_id)

    for proj in descendants:
        if proj.project_id == root_project.project_id:
            continue
        parent = node_by_pid.get(proj.parent_id)
        if parent is not None:
            parent['children'].append(node_by_pid[proj.project_id])

    # Sort each node's children by projcode for deterministic display.
    for n in node_by_pid.values():
        n['children'].sort(key=lambda c: c['projcode'])

    return {
        'tree': node_by_pid[root_project.project_id],
        'account_ids': account_ids,
    }


def _node_dict(
    project: Project,
    *,
    account: Optional[Account],
    snapshot,
    fileset_paths: List[str],
) -> Dict[str, Any]:
    if snapshot is not None:
        bytes_ = snapshot.bytes
        used_tib = snapshot.used_tib
        files = snapshot.number_of_files
        activity_date = snapshot.activity_date
    else:
        bytes_ = 0
        used_tib = 0.0
        files = 0
        activity_date = None
    return {
        'project_id':       project.project_id,
        'projcode':         project.projcode,
        'title':            project.title,
        'account_id':       account.account_id if account is not None else None,
        'current_bytes':    bytes_,
        'current_used_tib': used_tib,
        'file_count':       files,
        'activity_date':    activity_date,
        'fileset_paths':    fileset_paths,
        'children':         [],
    }
