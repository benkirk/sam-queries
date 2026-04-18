"""
Charge aggregation and usage query functions for SAM.

This module provides functions for querying charge summaries, usage trends,
and job data across HPC, DAV, DISK, and ARCHIVE resources. These queries
power the charges API endpoints and dashboard usage visualizations.

Functions:
    get_adjustment_totals_by_date: Get ChargeAdjustment totals grouped by date
    get_recent_charge_adjustments: Cross-project charge-adjustment query with
        flexible filters (date range, project, resource, facility, user, type)
    get_daily_charge_trends_for_accounts: Get daily charge trends by date
    get_raw_charge_summaries_for_accounts: Get raw charge summary records
    get_user_breakdown_for_project: Get per-user usage breakdown
"""

from datetime import datetime
from typing import Any, List, Optional, Dict, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.summaries.comp_summaries import CompChargeSummary
from sam.accounting.accounts import Account
from sam.accounting.adjustments import ChargeAdjustment, ChargeAdjustmentType
from sam.accounting.allocations import AllocationType
from sam.accounting.calculator import get_charge_models_for_resource
from sam.projects.projects import Project
from sam.resources.facilities import Facility, Panel
from sam.resources.resources import Resource


# ============================================================================
# Charge Aggregation Queries
# ============================================================================

def get_adjustment_totals_by_date(
    session: Session,
    account_ids: List[int],
    start_date: datetime,
    end_date: datetime
) -> Dict:
    """
    Get total charge adjustments grouped by date for a list of accounts.

    Args:
        session: SQLAlchemy session.
        account_ids: List of account IDs to query (pass [single_id] for one account).
        start_date: Start date (inclusive).
        end_date: End date (inclusive).

    Returns:
        Dict mapping date objects to total adjustment amounts.
        Example: {date(2024, 1, 1): -100.0, date(2024, 1, 15): 250.0}
    """
    rows = session.query(
        ChargeAdjustment.adjustment_date,
        func.sum(ChargeAdjustment.amount).label('total')
    ).filter(
        ChargeAdjustment.account_id.in_(account_ids),
        ChargeAdjustment.adjustment_date >= start_date,
        ChargeAdjustment.adjustment_date <= end_date
    ).group_by(ChargeAdjustment.adjustment_date).all()

    result = {}
    for adj_date, amount in rows:
        d = adj_date.date() if hasattr(adj_date, 'date') else adj_date
        result[d] = result.get(d, 0.0) + float(amount or 0.0)
    return result


CHARGE_ADJUSTMENT_SORT_COLUMNS = {
    'adjustment_date': ChargeAdjustment.adjustment_date,
    'adjustment_type': ChargeAdjustmentType.type,
    'amount': ChargeAdjustment.amount,
    'projcode': Project.projcode,
    'resource_name': Resource.resource_name,
    'facility_name': Facility.facility_name,
    'username': User.username,
}


def _apply_adjustment_filters(
    query,
    *,
    adjustment_id,
    projcode,
    resource_name,
    facility_name,
    adjustment_types,
    start_date,
    end_date,
    user_id,
    username,
    include_deleted,
):
    """Apply the shared WHERE clauses. ``query`` must already have the standard
    JOINs (see ``_join_adjustment_query``)."""
    if user_id is not None and username is not None:
        raise ValueError("Pass user_id OR username, not both")

    if adjustment_id is not None:
        query = query.filter(ChargeAdjustment.charge_adjustment_id == adjustment_id)

    if projcode and projcode != "TOTAL":
        if isinstance(projcode, list):
            query = query.filter(Project.projcode.in_(projcode))
        else:
            query = query.filter(Project.projcode == projcode)

    if resource_name and resource_name != "TOTAL":
        if isinstance(resource_name, list):
            query = query.filter(Resource.resource_name.in_(resource_name))
        else:
            query = query.filter(Resource.resource_name == resource_name)

    if facility_name and facility_name != "TOTAL":
        if isinstance(facility_name, list):
            query = query.filter(Facility.facility_name.in_(facility_name))
        else:
            query = query.filter(Facility.facility_name == facility_name)

    if adjustment_types is not None:
        if isinstance(adjustment_types, (list, tuple, set)):
            values = [str(t) for t in adjustment_types]
            query = query.filter(ChargeAdjustmentType.type.in_(values))
        else:
            query = query.filter(ChargeAdjustmentType.type == str(adjustment_types))

    if start_date is not None:
        query = query.filter(ChargeAdjustment.adjustment_date >= start_date)
    if end_date is not None:
        query = query.filter(ChargeAdjustment.adjustment_date <= end_date)

    if user_id is not None:
        query = query.filter(ChargeAdjustment.adjusted_by_id == user_id)
    elif username is not None:
        query = query.filter(User.username == username)

    if not include_deleted:
        query = query.filter(Account.deleted == False)

    return query


def _join_adjustment_query(query):
    """Apply the shared JOIN chain used by get/count variants."""
    return query.join(Account, ChargeAdjustment.account_id == Account.account_id)\
                .join(Project, Account.project_id == Project.project_id)\
                .join(Resource, Account.resource_id == Resource.resource_id)\
                .join(
                    ChargeAdjustmentType,
                    ChargeAdjustment.charge_adjustment_type_id
                        == ChargeAdjustmentType.charge_adjustment_type_id,
                )\
                .outerjoin(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
                .outerjoin(Panel, AllocationType.panel_id == Panel.panel_id)\
                .outerjoin(Facility, Panel.facility_id == Facility.facility_id)\
                .outerjoin(User, ChargeAdjustment.adjusted_by_id == User.user_id)


def get_recent_charge_adjustments(
    session: Session,
    *,
    adjustment_id: Optional[int] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    adjustment_types: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    include_deleted: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    offset: int = 0,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Query charge adjustments across projects/resources with flexible filters.

    Mirrors ``get_recent_allocation_transactions`` but for ``ChargeAdjustment``:
    scalar-or-list scope filters (``"TOTAL"`` is a no-op), inclusive
    ``adjustment_date`` range, responsible-user filter via mutually-exclusive
    ``user_id`` / ``username``, server-side sort + offset/limit pagination.

    Args:
        projcode, resource_name, facility_name: scope filters.
        adjustment_types: single name or list of ``ChargeAdjustmentType.type``
            values (e.g. ``"Credit"``, ``["Debit", "Refund"]``).
        start_date, end_date: inclusive bounds on ``adjustment_date``.
        user_id: filter by ``adjusted_by_id``. Mutually exclusive with ``username``.
        username: filter by adjusted-by user's username.
        include_deleted: when False (default), hide adjustments whose parent
            ``Account`` is soft-deleted. ``ChargeAdjustment`` itself has no
            ``deleted`` column.
        sort_by: column name to sort by. Must be a key in
            ``CHARGE_ADJUSTMENT_SORT_COLUMNS``. Defaults to ``adjustment_date``.
        sort_dir: ``"asc"`` or ``"desc"``. Default ``"desc"``.
        offset: zero-based row offset for pagination.
        limit: optional row cap.

    Returns:
        A list of dicts, one per adjustment, with keys: ``adjustment_id``,
        ``account_id``, ``amount``, ``adjustment_date``, ``comment``,
        ``adjustment_type``, ``projcode``, ``project_id``, ``resource_name``,
        ``resource_id``, ``facility_name``, ``user_id``, ``username``,
        ``user_display_name``. User fields are ``None`` when
        ``adjusted_by_id`` is NULL; ``facility_name`` is ``None`` when the
        project has no allocation-type chain.
    """
    if sort_by is not None and sort_by not in CHARGE_ADJUSTMENT_SORT_COLUMNS:
        raise ValueError(
            f"Unknown sort_by={sort_by!r}; allowed: "
            f"{sorted(CHARGE_ADJUSTMENT_SORT_COLUMNS)}"
        )
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir must be 'asc' or 'desc', got {sort_dir!r}")

    sort_col = CHARGE_ADJUSTMENT_SORT_COLUMNS[sort_by] if sort_by \
        else ChargeAdjustment.adjustment_date

    query = _join_adjustment_query(session.query(
        ChargeAdjustment,
        Project,
        Resource,
        ChargeAdjustmentType.type.label('adjustment_type_name'),
        Facility.facility_name.label('facility_name_val'),
        User,
    ))
    query = _apply_adjustment_filters(
        query,
        adjustment_id=adjustment_id,
        projcode=projcode, resource_name=resource_name, facility_name=facility_name,
        adjustment_types=adjustment_types,
        start_date=start_date, end_date=end_date,
        user_id=user_id, username=username,
        include_deleted=include_deleted,
    )

    order = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    query = query.order_by(
        order,
        ChargeAdjustment.charge_adjustment_id.desc(),
    )

    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    rows = query.all()

    return [
        {
            'adjustment_id': adj.charge_adjustment_id,
            'account_id': adj.account_id,
            'amount': adj.amount,
            'adjustment_date': adj.adjustment_date,
            'comment': adj.comment,
            'adjustment_type': at_name,
            'projcode': project.projcode,
            'project_id': project.project_id,
            'resource_name': resource.resource_name,
            'resource_id': resource.resource_id,
            'facility_name': fac_name,
            'user_id': user.user_id if user is not None else None,
            'username': user.username if user is not None else None,
            'user_display_name': user.display_name if user is not None else None,
        }
        for adj, project, resource, at_name, fac_name, user in rows
    ]


def count_recent_charge_adjustments(
    session: Session,
    *,
    adjustment_id: Optional[int] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    adjustment_types: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    include_deleted: bool = False,
) -> int:
    """Return the total number of charge adjustments matching the filters.

    Shares filter semantics with :func:`get_recent_charge_adjustments`;
    intended for pagination ("page 2 of 47") and summary displays.
    """
    query = _join_adjustment_query(
        session.query(func.count(ChargeAdjustment.charge_adjustment_id))
    )
    query = _apply_adjustment_filters(
        query,
        adjustment_id=adjustment_id,
        projcode=projcode, resource_name=resource_name, facility_name=facility_name,
        adjustment_types=adjustment_types,
        start_date=start_date, end_date=end_date,
        user_id=user_id, username=username,
        include_deleted=include_deleted,
    )
    return query.scalar() or 0


def get_daily_charge_trends_for_accounts(
    session: Session,
    account_ids: List[int],
    start_date: datetime,
    end_date: datetime,
    resource_type: Optional[str] = None,
    include_adjustments: bool = True
) -> Dict[str, Dict[str, float]]:
    """
    Get daily charge trends for a list of accounts across all charge types.

    Args:
        session: SQLAlchemy session.
        account_ids: List of account IDs to query.
        start_date: Start date for the charge data.
        end_date: End date for the charge data.
        resource_type: Optional filter by resource type ('HPC', 'DAV', 'DISK', 'ARCHIVE').
                       If None, all applicable resource types are included.
        include_adjustments: If True (default), include manual charge adjustments
                             as an 'adjustments' key in each day's dict.

    Returns:
        A dictionary where keys are date strings (YYYY-MM-DD) and values are
        dictionaries containing charge totals for each type (comp, dav, disk, archive,
        and optionally adjustments when include_adjustments=True).
        Example: {'2024-01-01': {'comp': 100.0, 'dav': 10.0, 'disk': 0.0, 'archive': 0.0,
                                  'adjustments': 0.0}}
    """
    daily_data = {}

    # Use centralized registry
    charge_models = get_charge_models_for_resource(resource_type)

    for charge_type_key, model in charge_models.items():
        data = session.query(
            model.activity_date,
            func.sum(model.charges).label('total_charges')
        ).filter(
            model.account_id.in_(account_ids),
            model.activity_date >= start_date,
            model.activity_date <= end_date
        ).group_by(model.activity_date).all()

        for date, charges in data:
            date_str = date.strftime('%Y-%m-%d')
            if date_str not in daily_data:
                daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_data[date_str][charge_type_key] += float(charges or 0.0)

    if include_adjustments:
        for d, amount in get_adjustment_totals_by_date(session, account_ids, start_date, end_date).items():
            date_str = d.strftime('%Y-%m-%d')
            if date_str not in daily_data:
                daily_data[date_str] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0, 'adjustments': 0.0}
            daily_data[date_str]['adjustments'] = daily_data[date_str].get('adjustments', 0.0) + amount

    return daily_data


def get_raw_charge_summaries_for_accounts(
    session: Session,
    account_ids: List[int],
    start_date: datetime,
    end_date: datetime,
    resource_type: Optional[str] = None,
    include_adjustments: bool = True
) -> Dict[str, List[any]]:
    """
    Get raw charge summaries for a list of accounts across all charge types.

    Args:
        session: SQLAlchemy session.
        account_ids: List of account IDs to query.
        start_date: Start date for the charge data.
        end_date: End date for the charge data.
        resource_type: Optional filter by resource type ('HPC', 'DAV', 'DISK', 'ARCHIVE').
                       If None, all applicable resource types are included.
        include_adjustments: If True (default), include ChargeAdjustment records
                             under the 'adjustments' key.

    Returns:
        A dictionary where keys are charge type strings (comp, dav, disk, archive,
        and optionally adjustments when include_adjustments=True) and values are
        lists of raw summary/adjustment objects.
    """
    charge_data = {
        'comp': [],
        'dav': [],
        'disk': [],
        'archive': [],
    }

    # Use centralized registry
    charge_models = get_charge_models_for_resource(resource_type)

    for charge_type_key, model in charge_models.items():
        data = session.query(model).filter(
            model.account_id.in_(account_ids),
            model.activity_date >= start_date,
            model.activity_date <= end_date
        ).all()
        charge_data[charge_type_key] = data

    if include_adjustments:
        charge_data['adjustments'] = session.query(ChargeAdjustment).filter(
            ChargeAdjustment.account_id.in_(account_ids),
            ChargeAdjustment.adjustment_date >= start_date,
            ChargeAdjustment.adjustment_date <= end_date
        ).all()

    return charge_data



# ============================================================================
# User Usage Queries
# ============================================================================

def get_user_breakdown_for_project(session, projcode: str,
                                   start_date: datetime,
                                   end_date: datetime,
                                   resource: str) -> List[Dict[str, any]]:
    """
    Get per-user usage breakdown for a project on a specific resource.
    Returns all users with nonzero usage within the date range.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Resource filter (e.g., 'Derecho')

    Returns:
        List of dicts with keys: username, user_id, display_name, jobs, core_hours, charges
        Ordered by charges descending

    Example:
        >>> users = get_user_breakdown_for_project(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 1, 31),
        ...     resource='Derecho'
        ... )
        >>> for user in users:
        ...     print(f"{user['username']}: {user['jobs']} jobs, {user['charges']} charges")
    """
    # Join with User table to get display_name
    results = session.query(
        CompChargeSummary.username,
        CompChargeSummary.user_id,
        func.sum(CompChargeSummary.num_jobs).label('jobs'),
        func.sum(CompChargeSummary.core_hours).label('core_hours'),
        func.sum(CompChargeSummary.charges).label('charges')
    ).outerjoin(
        User, CompChargeSummary.user_id == User.user_id
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date,
        CompChargeSummary.resource == resource
    ).group_by(
        CompChargeSummary.username,
        CompChargeSummary.user_id,
    ).having(
        func.sum(CompChargeSummary.charges) > 0
    ).order_by(
        func.sum(CompChargeSummary.charges).desc()
    ).all()

    return [
        {
            'username': row.username,
            'user_id': row.user_id,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]


def get_charges_by_projcode(
    session: Session,
    projcodes: List[str],
    resource: str,
    start_date: datetime,
    end_date: datetime,
) -> Dict[str, float]:
    """
    Get total direct charges per project code over a date range.

    Used to annotate project hierarchy trees with per-node charge totals.
    Returns only direct charges (not subtree aggregates); callers should
    roll up subtree totals themselves by traversing the tree structure.

    Returns:
        Dict mapping projcode → total_charges for the period/resource.
    """
    rows = query_comp_charge_summaries(
        session, start_date, end_date, projcode=projcodes, resource=resource
    )
    result: Dict[str, float] = {}
    for row in rows:
        pc = row['projcode']
        result[pc] = result.get(pc, 0.0) + row['total_charges']
    return result


def get_user_queue_breakdown_for_project(
    session: Session,
    projcode: Union[str, List[str]],
    resource: str,
    start_date: datetime,
    end_date: datetime,
) -> List[Dict]:
    """
    Get per-user usage breakdown with per-queue and per-date sub-rows for a project on a
    specific resource.

    Wraps query_comp_charge_summaries(per_day=True), grouping results by username, then
    queue, then date — suitable for 3-level collapsible table display.

    Returns:
        List of dicts sorted by charges desc:
            {username, jobs, core_hours, charges,
             queues: [{queue, jobs, core_hours, charges,
                       dates: [{date, jobs, core_hours, charges}] (sorted by date desc)}]
             (sorted by charges desc)}
    """
    rows = query_comp_charge_summaries(
        session, start_date, end_date, projcode=projcode, resource=resource, per_day=True
    )

    user_map: Dict[str, Dict] = {}
    for row in rows:
        username = row['username']
        queue    = row['queue']
        date_str = row['activity_date'].strftime('%Y-%m-%d')

        if username not in user_map:
            user_map[username] = {
                'username': username,
                'jobs': 0,
                'core_hours': 0.0,
                'charges': 0.0,
                'queues': {},
            }
        u = user_map[username]
        u['jobs']       += row['total_jobs']
        u['core_hours'] += row['total_core_hours']
        u['charges']    += row['total_charges']

        if queue not in u['queues']:
            u['queues'][queue] = {
                'queue': queue,
                'jobs': 0,
                'core_hours': 0.0,
                'charges': 0.0,
                'dates': [],
            }
        q = u['queues'][queue]
        q['jobs']       += row['total_jobs']
        q['core_hours'] += row['total_core_hours']
        q['charges']    += row['total_charges']
        q['dates'].append({
            'date': date_str,
            'jobs': row['total_jobs'],
            'core_hours': row['total_core_hours'],
            'charges': row['total_charges'],
        })

    for entry in user_map.values():
        for q in entry['queues'].values():
            q['dates'].sort(key=lambda d: d['date'], reverse=True)
        entry['queues'] = sorted(entry['queues'].values(), key=lambda q: q['charges'], reverse=True)

    return sorted(user_map.values(), key=lambda u: u['charges'], reverse=True)


def get_daily_breakdown_for_project(
    session: Session,
    projcode: Union[str, List[str]],
    resource: str,
    start_date: datetime,
    end_date: datetime,
) -> List[Dict]:
    """
    Get per-day usage with per-user-per-queue sub-rows for a project on a specific resource.

    Wraps query_comp_charge_summaries(per_day=True), grouping results by date and
    collecting user+queue sub-rows — suitable for collapsible table display.

    Returns:
        List of dicts sorted by date desc:
            {date (str YYYY-MM-DD), jobs, core_hours, charges,
             rows: [{username, queue, total_jobs, total_core_hours, total_charges}]}
    """
    rows = query_comp_charge_summaries(
        session, start_date, end_date, projcode=projcode, resource=resource, per_day=True
    )

    day_map: Dict[str, Dict] = {}
    for row in rows:
        date_str = row['activity_date'].strftime('%Y-%m-%d')
        if date_str not in day_map:
            day_map[date_str] = {
                'date': date_str,
                'month': date_str[:7],   # YYYY-MM — used by template groupby
                'jobs': 0,
                'core_hours': 0.0,
                'charges': 0.0,
                'rows': [],
            }
        day_map[date_str]['jobs'] += row['total_jobs']
        day_map[date_str]['core_hours'] += row['total_core_hours']
        day_map[date_str]['charges'] += row['total_charges']
        day_map[date_str]['rows'].append(row)

    for entry in day_map.values():
        entry['user_count'] = len({row['username'] for row in entry['rows']})

    return sorted(day_map.values(), key=lambda d: d['date'], reverse=True)


def query_comp_charge_summaries(
    session: Session,
    start_date,
    end_date,
    username: Optional[str] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    resource: Optional[str] = None,
    queue: Optional[str] = None,
    machine: Optional[str] = None,
    per_day: bool = False,
) -> List[Dict]:
    """
    Query comp_charge_summary with optional filters, aggregated by dimension.

    Unlike get_user_breakdown_for_project() (which is scoped to one project/resource)
    this function supports cross-project, cross-resource queries with flexible filters.

    Args:
        session: SQLAlchemy session
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        username: Optional filter; supports % wildcard (e.g. 'benk%')
        projcode: Optional filter; supports % wildcard (e.g. 'SCSG%')
        resource: Optional filter; supports % wildcard (e.g. 'Derecho%')
        queue: Optional exact filter on queue name
        machine: Optional filter; supports % wildcard (e.g. 'derecho%')
        per_day: If True, include activity_date in GROUP BY for per-day rows

    Returns:
        List of dicts with keys: username, projcode, resource, machine, queue,
        total_jobs, total_core_hours, total_charges, and activity_date (if per_day).
        Ordered by total_charges descending.

    Example:
        >>> rows = query_comp_charge_summaries(
        ...     session, date(2025, 1, 1), date(2025, 3, 1),
        ...     resource='Derecho', username='benk%'
        ... )
        >>> for row in rows:
        ...     print(f"{row['username']}/{row['projcode']}: {row['total_charges']:.1f}")
    """
    group_cols = [
        CompChargeSummary.username,
        CompChargeSummary.projcode,
        CompChargeSummary.resource,
        CompChargeSummary.machine,
        CompChargeSummary.queue,
    ]
    select_cols = list(group_cols) + [
        func.sum(CompChargeSummary.num_jobs).label('total_jobs'),
        func.sum(CompChargeSummary.core_hours).label('total_core_hours'),
        func.sum(CompChargeSummary.charges).label('total_charges'),
    ]

    if per_day:
        group_cols = [CompChargeSummary.activity_date] + group_cols
        select_cols = [CompChargeSummary.activity_date] + select_cols

    query = session.query(*select_cols).filter(
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date,
    )

    # Apply optional filters (LIKE when % present, exact otherwise)
    def _apply_filter(col, val):
        if val is None:
            return
        nonlocal query
        query = query.filter(col.like(val) if '%' in val else col == val)

    _apply_filter(CompChargeSummary.username, username)
    if isinstance(projcode, list):
        query = query.filter(CompChargeSummary.projcode.in_(projcode))
    else:
        _apply_filter(CompChargeSummary.projcode, projcode)
    _apply_filter(CompChargeSummary.resource, resource)
    _apply_filter(CompChargeSummary.machine, machine)
    if queue is not None:
        query = query.filter(CompChargeSummary.queue == queue)

    query = query.group_by(*group_cols).having(
        func.sum(CompChargeSummary.charges) > 0
    ).order_by(
        func.sum(CompChargeSummary.charges).desc()
    )

    rows = query.all()

    result = []
    for row in rows:
        d = {
            'username': row.username,
            'projcode': row.projcode,
            'resource': row.resource,
            'machine': row.machine,
            'queue': row.queue,
            'total_jobs': int(row.total_jobs or 0),
            'total_core_hours': float(row.total_core_hours or 0.0),
            'total_charges': float(row.total_charges or 0.0),
        }
        if per_day:
            d['activity_date'] = row.activity_date
        result.append(d)

    return result
