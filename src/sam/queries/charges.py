"""
Charge aggregation and usage query functions for SAM.

This module provides functions for querying charge summaries, usage trends,
and job data across HPC, DAV, DISK, and ARCHIVE resources. These queries
power the charges API endpoints and dashboard usage visualizations.

Functions:
    get_adjustment_totals_by_date: Get ChargeAdjustment totals grouped by date
    get_daily_charge_trends_for_accounts: Get daily charge trends by date
    get_raw_charge_summaries_for_accounts: Get raw charge summary records
    get_jobs_for_project: Get job records for a project
    get_user_breakdown_for_project: Get per-user usage breakdown
"""

from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.summaries.comp_summaries import CompChargeSummary
from sam.activity.computational import CompActivityChargeView
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
# Usage Summary Queries
# ============================================================================

# ============================================================================
# Job and Queue Queries
# ============================================================================

def get_jobs_for_project(session, projcode: str,
                         start_date: datetime,
                         end_date: datetime,
                         resource: str,
                         limit: Optional[int] = None) -> List[CompActivityChargeView]:
    """
    Get jobs for a project within a date range using the charge view.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Machine filter (e.g., 'Derecho')
        limit: Optional maximum number of jobs to return (default None = no limit)

    Returns:
        List of CompActivityChargeView view records ordered by submit time (descending)

    Example:
        >>> jobs = get_jobs_for_project(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 1, 31),
        ...     'Derecho',
        ...     limit=50
        ... )
        >>> for job in jobs:
        ...     print(f"{job.job_id}: {job.core_hours} hours, {job.charge} charged")
    """
    query = session.query(CompActivityChargeView).filter(
        CompActivityChargeView.projcode == projcode,
        CompActivityChargeView.machine == resource,
        CompActivityChargeView.activity_date >= start_date,
        CompActivityChargeView.activity_date <= end_date
    ).order_by(
        CompActivityChargeView.activity_date.desc()
    )

    if limit is not None:
        query = query.limit(limit)

    return query.all()


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
