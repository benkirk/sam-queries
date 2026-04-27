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
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.core.users import User
from sam.projects.projects import Project, ProjectDirectory
from sam.resources.resources import Resource
from sam.summaries.disk_summaries import DiskChargeSummary


def get_subtree_disk_capacity(
    session: Session,
    project: Project,
    resource_name: str,
) -> Dict[str, Any]:
    """Point-in-time disk occupancy summed across a project's subtree.

    For NMMM0003-shaped parents, the parent's own account holds 0 bytes
    while the actual occupancy lives on the children — `Account
    .current_disk_usage()` against just the parent reads 0%. This helper
    walks ``project`` + descendants (NestedSet coords), finds each
    descendant's account on ``resource_name``, sums the snapshot bytes
    / file count, and reports the latest activity_date across them.

    Returns ``{used_bytes, used_tib, file_count, activity_date,
    account_ids}``. ``activity_date`` is None and totals are zero if no
    descendant has a snapshot yet.
    """
    resource = session.query(Resource).filter(
        Resource.resource_name == resource_name,
    ).first()
    empty = {
        'used_bytes':    0,
        'used_tib':      0.0,
        'file_count':    0,
        'activity_date': None,
        'account_ids':   [],
    }
    if resource is None:
        return empty

    is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
    if is_tree_valid:
        accounts = session.query(Account).join(
            Project, Account.project_id == Project.project_id,
        ).filter(
            Project.tree_root == project.tree_root,
            Project.tree_left >= project.tree_left,
            Project.tree_right <= project.tree_right,
            Account.resource_id == resource.resource_id,
            Account.deleted == False,  # noqa: E712
        ).all()
    else:
        accounts = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.resource_id == resource.resource_id,
            Account.deleted == False,  # noqa: E712
        ).all()

    used_bytes = 0
    files = 0
    latest = None
    account_ids = []
    for acct in accounts:
        snap = acct.current_disk_usage(session)
        if snap is None:
            continue
        used_bytes += snap.bytes
        files += snap.number_of_files
        if latest is None or snap.activity_date > latest:
            latest = snap.activity_date
        account_ids.append(acct.account_id)

    return {
        'used_bytes':    used_bytes,
        'used_tib':      used_bytes / (1024 ** 4),
        'file_count':    files,
        'activity_date': latest,
        'account_ids':   account_ids,
    }


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
