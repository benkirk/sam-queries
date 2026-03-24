"""
Charge aggregation and usage query functions for SAM.

This module provides functions for querying charge summaries, usage trends,
and job data across HPC, DAV, DISK, and ARCHIVE resources. These queries
power the charges API endpoints and dashboard usage visualizations.

Functions:
    get_adjustment_totals_by_date: Get ChargeAdjustment totals grouped by date
    get_daily_charge_trends_for_accounts: Get daily charge trends by date
    get_raw_charge_summaries_for_accounts: Get raw charge summary records
    get_user_breakdown_for_project: Get per-user usage breakdown
"""

from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.summaries.comp_summaries import CompChargeSummary
from sam.accounting.adjustments import ChargeAdjustment
from sam.accounting.calculator import get_charge_models_for_resource


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


def get_user_queue_breakdown_for_project(
    session: Session,
    projcode: str,
    resource: str,
    start_date: datetime,
    end_date: datetime,
) -> List[Dict]:
    """
    Get per-user usage breakdown with per-queue sub-rows for a project on a specific resource.

    Wraps query_comp_charge_summaries(), grouping results by username and collecting
    queue-level sub-rows — suitable for collapsible table display.

    Returns:
        List of dicts sorted by charges desc:
            {username, jobs, core_hours, charges,
             queues: [{queue, jobs, core_hours, charges}] (sorted by charges desc)}
    """
    rows = query_comp_charge_summaries(
        session, start_date, end_date, projcode=projcode, resource=resource
    )

    user_map: Dict[str, Dict] = {}
    for row in rows:
        username = row['username']
        if username not in user_map:
            user_map[username] = {
                'username': username,
                'jobs': 0,
                'core_hours': 0.0,
                'charges': 0.0,
                'queues': [],
            }
        user_map[username]['jobs'] += row['total_jobs']
        user_map[username]['core_hours'] += row['total_core_hours']
        user_map[username]['charges'] += row['total_charges']
        user_map[username]['queues'].append({
            'queue': row['queue'],
            'jobs': row['total_jobs'],
            'core_hours': row['total_core_hours'],
            'charges': row['total_charges'],
        })

    for entry in user_map.values():
        entry['queues'].sort(key=lambda q: q['charges'], reverse=True)

    return sorted(user_map.values(), key=lambda u: u['charges'], reverse=True)


def get_daily_breakdown_for_project(
    session: Session,
    projcode: str,
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
                'jobs': 0,
                'core_hours': 0.0,
                'charges': 0.0,
                'rows': [],
            }
        day_map[date_str]['jobs'] += row['total_jobs']
        day_map[date_str]['core_hours'] += row['total_core_hours']
        day_map[date_str]['charges'] += row['total_charges']
        day_map[date_str]['rows'].append(row)

    return sorted(day_map.values(), key=lambda d: d['date'], reverse=True)


def query_comp_charge_summaries(
    session: Session,
    start_date,
    end_date,
    username: Optional[str] = None,
    projcode: Optional[str] = None,
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
