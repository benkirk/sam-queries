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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account, CurrentDiskUsage
from sam.activity.disk import DiskActivity
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


def _load_disk_snapshot_by_account(
    session: Session,
    account_ids: Iterable[int],
) -> Dict[int, CurrentDiskUsage]:
    """Per-account latest disk snapshot in a fixed number of queries.

    Shared kernel for :meth:`Account.current_disk_usage`,
    :func:`bulk_current_disk_usage`, and the snapshot phase of
    :func:`bulk_get_subtree_disk_capacity`. Semantics match the
    single-account method exactly:

      1. ONE ``DiskChargeSummaryStatus`` lookup for the most recent
         ``current=True`` row.
      2. ONE bulk aggregate over ``DiskChargeSummary`` at that date,
         keyed by ``account_id`` (sums bytes, terabyte_years, files).
      3. ONE ``max(activity_date)`` per account that lacked a row at
         the candidate date.
      4. ONE bulk aggregate for those (account_id, max_date) fallback
         pairs.

    Returns ``{account_id: CurrentDiskUsage}``. Accounts with no rows
    at all are absent from the dict — matches the single-account
    method's ``None`` return semantics.
    """
    aids = {int(a) for a in account_ids if a is not None}
    if not aids:
        return {}

    out: Dict[int, CurrentDiskUsage] = {}

    # 1) Current snapshot date.
    current_row = (
        session.query(DiskChargeSummaryStatus.activity_date)
        .filter(DiskChargeSummaryStatus.current == True)  # noqa: E712
        .order_by(DiskChargeSummaryStatus.activity_date.desc())
        .first()
    )
    candidate_date = current_row[0] if current_row else None

    # 2) Bulk aggregate at the candidate date.
    if candidate_date is not None:
        rows = session.query(
            DiskChargeSummary.account_id,
            func.coalesce(func.sum(DiskChargeSummary.bytes), 0).label('bytes'),
            func.coalesce(func.sum(DiskChargeSummary.terabyte_years), 0).label('ty'),
            func.coalesce(func.sum(DiskChargeSummary.number_of_files), 0).label('files'),
        ).filter(
            DiskChargeSummary.account_id.in_(aids),
            DiskChargeSummary.activity_date == candidate_date,
        ).group_by(DiskChargeSummary.account_id).all()
        for r in rows:
            out[r.account_id] = CurrentDiskUsage(
                activity_date=candidate_date,
                bytes=int(r.bytes or 0),
                terabyte_years=float(r.ty or 0.0),
                number_of_files=int(r.files or 0),
            )

    # 3) Per-account max() fallback for accounts without a row on the
    #    current snapshot date. One query covers them all.
    missing = aids - out.keys()
    if missing:
        max_dates = dict(session.query(
            DiskChargeSummary.account_id,
            func.max(DiskChargeSummary.activity_date),
        ).filter(
            DiskChargeSummary.account_id.in_(missing),
        ).group_by(DiskChargeSummary.account_id).all())
        if max_dates:
            # 4) ONE aggregate query for all (account_id, max_date) pairs.
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
                func.coalesce(func.sum(DiskChargeSummary.terabyte_years), 0).label('ty'),
                func.coalesce(func.sum(DiskChargeSummary.number_of_files), 0).label('files'),
            ).filter(or_(*ors)).group_by(
                DiskChargeSummary.account_id, DiskChargeSummary.activity_date,
            ).all()
            for r in rows:
                out[r.account_id] = CurrentDiskUsage(
                    activity_date=r.activity_date,
                    bytes=int(r.bytes or 0),
                    terabyte_years=float(r.ty or 0.0),
                    number_of_files=int(r.files or 0),
                )

    return out


def bulk_current_disk_usage(
    session: Session,
    account_ids: Iterable[int],
) -> Dict[int, CurrentDiskUsage]:
    """Bulk twin of :meth:`Account.current_disk_usage`.

    Returns ``{account_id: CurrentDiskUsage}`` for every requested
    ``account_id`` that has at least one ``disk_charge_summary`` row.
    Use this whenever you need snapshots for >1 account
    (``build_disk_subtree``, ``Project.current_disk_usage``, …) — the
    single-account path delegates here too, so all callers share one
    code path with a fixed query count.

    Caller is responsible for filtering to disk-resource accounts; this
    helper does not check resource type because it's typically called
    after a bulk ``Account`` load that already enforced the filter.
    """
    return _load_disk_snapshot_by_account(session, account_ids)


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
      3-6. Per-account snapshot via :func:`_load_disk_snapshot_by_account`
         (status row + bulk aggregate + per-account max() fallback).

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

    # 3-5) Per-account snapshot in a fixed number of queries (status row
    #      + bulk aggregate + per-account max() fallback). Shared with
    #      Account.current_disk_usage / bulk_current_disk_usage.
    snap_by_account = _load_disk_snapshot_by_account(session, all_account_ids)

    # 6) Roll up per-pair.
    for (pid, rn), aids in accounts_per_pair.items():
        used_bytes = 0
        files = 0
        latest = None
        for aid in aids:
            snap = snap_by_account.get(aid)
            if snap is None:
                continue
            used_bytes += snap.bytes
            files += snap.number_of_files
            if latest is None or snap.activity_date > latest:
                latest = snap.activity_date
        out[(pid, rn)] = {
            'used_bytes':    used_bytes,
            'used_tib':      used_bytes / (1024 ** 4),
            'file_count':    files,
            'activity_date': latest,
            'account_ids':   aids,
        }
    return out


def get_earliest_disk_activity_date(session, account_ids):
    """Earliest DiskChargeSummary.activity_date across the given accounts.

    Anchors the disk chart's "Epoch" button to the full storage history
    of the project (spans allocation boundaries). Returns None when no
    accounts are given or no snapshots exist.
    """
    if not account_ids:
        return None
    return (
        session.query(func.min(DiskChargeSummary.activity_date))
        .filter(DiskChargeSummary.account_id.in_(account_ids))
        .scalar()
    )


def _disk_metric_col(model, metric):
    """Pick the summed column for a disk timeseries by metric.

    ``'files'`` → ``number_of_files``; anything else → ``bytes``. Works for
    both ``DiskChargeSummary`` (project path) and ``DiskActivity`` (fileset
    path), which carry both columns.
    """
    return model.number_of_files if metric == 'files' else model.bytes


def get_disk_usage_timeseries_by_user(
    session: Session,
    *,
    account_ids: List[int],
    start_date: Optional[_stdlib_date] = None,
    end_date: Optional[_stdlib_date] = None,
    top_n: int = 10,
    metric: str = 'bytes',
) -> Dict[str, Any]:
    """Per-user disk time series for a stacked-area chart.

    Sums ``DiskChargeSummary.bytes`` (``metric='bytes'``, default) or
    ``DiskChargeSummary.number_of_files`` (``metric='files'``) grouped by
    ``(activity_date, user_id)`` for the given accounts and date range.
    Picks the top ``top_n`` users by their *latest-snapshot* value for the
    selected metric; everyone else is lumped into a single ``"Others"``
    series. Missing (date, user) pairs are dense-filled with 0 so every
    series has the same length as ``dates``.

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
        func.coalesce(func.sum(_disk_metric_col(DiskChargeSummary, metric)), 0).label('value'),
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


def get_disk_usage_timeseries_for_directory(
    session: Session,
    *,
    resource_name: str,
    directory_name: str,
    start_date: Optional[_stdlib_date] = None,
    end_date: Optional[_stdlib_date] = None,
    top_n: int = 10,
    metric: str = 'bytes',
) -> Dict[str, Any]:
    """Per-user disk timeseries for a single fileset.

    Same return shape, ``metric`` handling, and ranking semantics as
    :func:`get_disk_usage_timeseries_by_user`, but reads from
    ``disk_activity`` directly so it can filter by ``directory_name``.
    Used by the dashboard when the user clicks a fileset row to scope
    the chart.

    Filtering by ``directory_name`` is a range seek on the existing
    ``disk_activity_unique_idx`` (which leads with ``directory_name``);
    no join through ``disk_charge`` / ``account`` is needed since the
    fileset name itself uniquely identifies the data we want. The
    chart only displays the username string, so we don't resolve
    ``user_id`` here — saves a ``users`` join too.

    History depth is bounded by what the importer has populated in
    ``disk_activity`` — older snapshots that pre-date Layer 2 won't
    appear. The summary path stays as the chart source for
    project-level views.
    """
    q = session.query(
        DiskActivity.activity_date,
        DiskActivity.username,
        func.coalesce(func.sum(_disk_metric_col(DiskActivity, metric)), 0).label('value'),
    ).filter(
        DiskActivity.directory_name == directory_name,
        DiskActivity.resource_name == resource_name,
    )
    if start_date is not None:
        q = q.filter(DiskActivity.activity_date >= start_date)
    if end_date is not None:
        q = q.filter(DiskActivity.activity_date <= end_date)
    rows = q.group_by(
        DiskActivity.activity_date,
        DiskActivity.username,
    ).all()

    if not rows:
        return {'dates': [], 'series': []}

    # Pivot keyed by username — disk_activity has no user_id column,
    # only the username string. The chart legend shows usernames so
    # this is sufficient.
    per_user: Dict[str, Dict[str, Any]] = {}
    dates_set: set = set()
    for activity_date, username, b in rows:
        dates_set.add(activity_date)
        key = username or '<unknown>'
        u = per_user.setdefault(key, {'username': key, 'by_date': {}})
        u['by_date'][activity_date] = int(b or 0)

    dates = sorted(dates_set)
    last_date = dates[-1]

    ranked = sorted(
        per_user.items(),
        key=lambda kv: kv[1]['by_date'].get(last_date, 0),
        reverse=True,
    )
    top_users = ranked[:top_n]
    rest_users = ranked[top_n:]

    series: List[Dict[str, Any]] = []
    if rest_users:
        others_values = [0] * len(dates)
        for _key, info in rest_users:
            for i, d in enumerate(dates):
                others_values[i] += info['by_date'].get(d, 0)
        series.append({'username': 'Others', 'values': others_values})
    for _key, info in reversed(top_users):
        series.append({
            'username': info['username'],
            'values':   [info['by_date'].get(d, 0) for d in dates],
        })
    return {'dates': dates, 'series': series}


def get_directory_user_breakdown_at(
    session: Session,
    *,
    resource_name: str,
    directory_name: str,
    activity_date,
) -> List[Dict[str, Any]]:
    """Per-user bytes/files for a single fileset at one snapshot date.

    Used by the dashboard's per-user table when scoped to a fileset.
    Returns one row per username found in ``disk_activity`` for the
    given fileset/date/resource, sorted desc by bytes.

    Reads ``disk_activity`` directly (range seek on
    ``disk_activity_unique_idx`` via ``directory_name``). The
    ``users`` table is OUTER-joined on ``username`` solely to surface
    ``user_id`` in the result for the dashboard's per-user link;
    rows whose username has no matching ``users`` row simply get
    ``user_id = None`` instead of being dropped.
    """
    if activity_date is None:
        return []
    rows = (
        session.query(
            DiskActivity.username.label('username'),
            User.user_id.label('user_id'),
            func.sum(DiskActivity.bytes).label('bytes'),
            func.sum(DiskActivity.number_of_files).label('files'),
        )
        .outerjoin(User, User.username == DiskActivity.username)
        .filter(
            DiskActivity.directory_name == directory_name,
            DiskActivity.resource_name == resource_name,
            DiskActivity.activity_date == activity_date,
        )
        .group_by(DiskActivity.username, User.user_id)
        .all()
    )
    return sorted(
        [{'username': r.username,
          'user_id': r.user_id,
          'bytes': int(r.bytes or 0),
          'files': int(r.files or 0)} for r in rows],
        key=lambda d: d['bytes'],
        reverse=True,
    )


def get_subtree_directory_usage_at(
    session: Session,
    *,
    directory_to_projcode: Dict[str, str],
    resource_name: str,
    activity_date,
) -> List[Dict[str, Any]]:
    """Per-directory snapshot summed across an entire subtree.

    The caller passes ``directory_to_projcode`` — a map from
    ``directory_name`` to its owning ``projcode``, typically built by
    walking the in-memory tree returned by :func:`build_disk_subtree`.
    The query then becomes a single ``disk_activity`` aggregate
    filtered by ``directory_name IN (...)``, which is a range seek on
    the existing ``disk_activity_unique_idx`` (leading column
    ``directory_name``). No join through ``disk_charge`` /
    ``account`` / ``project`` is needed; the projcode mapping happens
    in Python from data the caller already has.

    Returns ``[{name, bytes, files, projcode}, ...]`` sorted desc
    by bytes. Empty list if no directories or ``activity_date`` is None.
    """
    if activity_date is None or not directory_to_projcode:
        return []
    directory_names = list(directory_to_projcode.keys())
    rows = (
        session.query(
            DiskActivity.directory_name.label('name'),
            func.sum(DiskActivity.bytes).label('bytes'),
            func.sum(DiskActivity.number_of_files).label('files'),
        )
        .filter(
            DiskActivity.directory_name.in_(directory_names),
            DiskActivity.resource_name == resource_name,
            DiskActivity.activity_date == activity_date,
        )
        .group_by(DiskActivity.directory_name)
        .all()
    )
    return sorted(
        [{'name': r.name,
          'projcode': directory_to_projcode.get(r.name),
          'bytes': int(r.bytes or 0),
          'files': int(r.files or 0)} for r in rows],
        key=lambda d: d['bytes'],
        reverse=True,
    )


def bulk_get_directory_usage_at(
    session: Session,
    *,
    directories_by_project_id: Dict[int, List[str]],
    resource_name: str,
    activity_date,
) -> Dict[int, List[Dict[str, Any]]]:
    """Per-project per-directory bytes/files at one snapshot.

    ``directories_by_project_id`` maps each project_id of interest to
    its list of active fileset ``directory_name`` strings (typically
    sourced from ``ProjectDirectory`` rows the caller has already
    loaded). Returns ``{project_id: [{name, bytes, files}, ...]}``
    sorted desc by bytes; project_ids with no matching activity rows
    map to an empty list.

    Single ``disk_activity`` aggregate query — range seek on
    ``disk_activity_unique_idx`` via ``directory_name IN (...)``. The
    project_id mapping happens in Python from the input dict.
    """
    out: Dict[int, List[Dict[str, Any]]] = {
        pid: [] for pid in directories_by_project_id
    }
    if not directories_by_project_id or activity_date is None:
        return out

    project_id_by_directory: Dict[str, int] = {}
    for pid, dirs in directories_by_project_id.items():
        for d in dirs:
            project_id_by_directory[d] = pid
    if not project_id_by_directory:
        return out

    rows = (
        session.query(
            DiskActivity.directory_name.label('name'),
            func.sum(DiskActivity.bytes).label('bytes'),
            func.sum(DiskActivity.number_of_files).label('files'),
        )
        .filter(
            DiskActivity.directory_name.in_(project_id_by_directory.keys()),
            DiskActivity.resource_name == resource_name,
            DiskActivity.activity_date == activity_date,
        )
        .group_by(DiskActivity.directory_name)
        .all()
    )

    for r in rows:
        pid = project_id_by_directory.get(r.name)
        if pid is None:
            continue
        out[pid].append(
            {'name': r.name,
             'bytes': int(r.bytes or 0),
             'files': int(r.files or 0)}
        )
    for pid in out:
        out[pid].sort(key=lambda d: d['bytes'], reverse=True)
    return out


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

    # ONE bulk snapshot lookup covers every account on this resource —
    # avoids the N+1 from calling Account.current_disk_usage() per
    # descendant (each call would issue 2-4 queries; on a 20-account
    # subtree that's 60+ queries this single helper replaces).
    snap_by_account_id = bulk_current_disk_usage(
        session, [a.account_id for a in accounts],
    )

    # Build a flat node map, then thread parents → children using the
    # parent_id FK (works alongside the NestedSet coords).
    node_by_pid: Dict[int, Dict[str, Any]] = {}
    account_ids: List[int] = []
    # For multi-fileset nodes, collect (project_id, activity_date) so we
    # can bulk-query per-fileset bytes from disk_activity in one trip.
    multifs_by_date: Dict[Any, List[int]] = {}
    for proj in descendants:
        account = account_by_project_id.get(proj.project_id)
        snapshot = (
            snap_by_account_id.get(account.account_id)
            if account is not None else None
        )
        node = _node_dict(
            proj,
            account=account,
            snapshot=snapshot,
            fileset_paths=sorted(dirs_by_project_id.get(proj.project_id, [])),
        )
        node_by_pid[proj.project_id] = node
        if account is not None:
            account_ids.append(account.account_id)
        if (len(node['fileset_paths']) > 1
                and node.get('activity_date') is not None):
            multifs_by_date.setdefault(node['activity_date'], []).append(
                proj.project_id
            )

    # Per-fileset bytes lookup — only for projects that have >1 active
    # fileset on this resource. Single-fileset projects keep the
    # current per-project rendering and pay nothing. We already have
    # the active fileset names per project in dirs_by_project_id from
    # the ProjectDirectory load above; pass those directly so the
    # bulk helper avoids re-querying ProjectDirectory.
    for activity_dt, pids in multifs_by_date.items():
        per_fs = bulk_get_directory_usage_at(
            session,
            directories_by_project_id={
                pid: dirs_by_project_id.get(pid, []) for pid in pids
            },
            resource_name=resource_name,
            activity_date=activity_dt,
        )
        for pid in pids:
            dirs = per_fs.get(pid, [])
            if dirs:
                node_by_pid[pid]['directories'] = dirs

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
