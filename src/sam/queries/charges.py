"""
Charge aggregation and usage query functions for SAM.

This module provides functions for querying charge summaries, usage trends,
and job data across HPC, DAV, DISK, and ARCHIVE resources. These queries
power the charges API endpoints and dashboard usage visualizations.

Functions:
    get_daily_charge_trends_for_accounts: Get daily charge trends by date
    get_raw_charge_summaries_for_accounts: Get raw charge summary records
    get_user_charge_summary: Get charges for a user
    get_project_usage_summary: Get aggregated usage for a project
    get_daily_usage_trend: Get daily usage trend for a project
    get_jobs_for_project: Get job records for a project
    get_queue_usage_breakdown: Get usage breakdown by queue
    get_user_usage_on_project: Get top users by usage on a project
    get_user_breakdown_for_project: Get per-user usage breakdown
"""

from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary
from sam.activity.computational import CompActivityChargeView
from sam.accounting.adjustments import ChargeAdjustment


# ============================================================================
# Charge Aggregation Queries
# ============================================================================

def get_daily_charge_trends_for_accounts(
    session: Session,
    account_ids: List[int],
    start_date: datetime,
    end_date: datetime,
    resource_type: Optional[str] = None
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

    Returns:
        A dictionary where keys are date strings (YYYY-MM-DD) and values are
        dictionaries containing charge totals for each type (comp, dav, disk, archive).
        Example: {'2024-01-01': {'comp': 100.0, 'dav': 10.0, 'disk': 0.0, 'archive': 0.0}}
    """
    daily_data = {}

    charge_models = {
        'comp': CompChargeSummary,
        'dav': DavChargeSummary,
        'disk': DiskChargeSummary,
        'archive': ArchiveChargeSummary,
    }

    resource_type_map = {
        'HPC': ['comp'],  # HPC charges are covered by comp
        'DAV': ['comp', 'dav'], # dav charges are their own, and also count under general comp charges
        'DISK': ['disk'],
        'ARCHIVE': ['archive'],
        None: ['comp', 'dav', 'disk', 'archive'] # Default to all
    }

    # Determine which charge types to query based on resource_type filter
    charge_types_to_query = resource_type_map.get(resource_type, [])
    # For HPC, DAV, or None, include comp charges.
    # If resource_type is DAV, it should only include CompChargeSummary and DavChargeSummary
    if resource_type == 'DAV':
        charge_types_to_query = ['comp', 'dav']
    # If resource_type is HPC, it should only include CompChargeSummary
    elif resource_type == 'HPC':
        charge_types_to_query = ['comp']

    for charge_type_key, model in charge_models.items():
        if charge_type_key in charge_types_to_query:
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
                daily_data[date_str][charge_type_key] += float(charges or 0.0) # Use += for multiple types contributing to 'comp'

    return daily_data


def get_raw_charge_summaries_for_accounts(
    session: Session,
    account_ids: List[int],
    start_date: datetime,
    end_date: datetime,
    resource_type: Optional[str] = None
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

    Returns:
        A dictionary where keys are charge type strings (comp, dav, disk, archive)
        and values are lists of raw summary objects.
    """
    charge_data = {
        'comp': [],
        'dav': [],
        'disk': [],
        'archive': [],
    }

    charge_models = {
        'comp': CompChargeSummary,
        'dav': DavChargeSummary,
        'disk': DiskChargeSummary,
        'archive': ArchiveChargeSummary,
    }

    resource_type_map = {
        'HPC': ['comp'],
        'DAV': ['dav'],
        'DISK': ['disk'],
        'ARCHIVE': ['archive'],
        None: ['comp', 'dav', 'disk', 'archive']
    }

    charge_types_to_query = resource_type_map.get(resource_type, [])

    # For HPC, DAV, or None, include comp charges. This logic needs to be careful.
    # If resource_type is DAV, it should only include DavChargeSummary
    if resource_type == 'DAV':
        charge_types_to_query = ['dav']
    # If resource_type is HPC, it should only include CompChargeSummary
    elif resource_type == 'HPC':
        charge_types_to_query = ['comp']


    for charge_type_key, model in charge_models.items():
        if charge_type_key in charge_types_to_query:
            data = session.query(model).filter(
                model.account_id.in_(account_ids),
                model.activity_date >= start_date,
                model.activity_date <= end_date
            ).all()
            charge_data[charge_type_key] = data

    return charge_data


# ============================================================================
# Usage Summary Queries
# ============================================================================

def get_user_charge_summary(session, user_id: int,
                            start_date: datetime,
                            end_date: datetime,
                            resource: Optional[str] = None) -> List[CompChargeSummary]:
    """
    Get charge summary for a user within a date range.

    Args:
        session: SQLAlchemy session
        user_id: User ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter (e.g., 'Derecho')

    Returns:
        List of CompChargeSummary records ordered by date

    Example:
        >>> summaries = get_user_charge_summary(
        ...     session, 12345,
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31)
        ... )
        >>> total = sum(s.charges for s in summaries)
    """
    query = session.query(CompChargeSummary).filter(
        CompChargeSummary.user_id == user_id,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    return query.order_by(CompChargeSummary.activity_date).all()


def get_project_usage_summary(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              resource: str) -> Dict[str, float]:
    """
    Get aggregated usage summary for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code (e.g., 'UCUB0001')
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Resource filter (e.g., 'Derecho')

    Returns:
        Dictionary with keys:
        - total_jobs: Number of jobs
        - total_core_hours: Total core hours consumed
        - total_charges: Total charges incurred

    Example:
        >>> summary = get_project_usage_summary(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     resource='Derecho'
        ... )
        >>> print(f"Project used {summary['total_core_hours']:.2f} core hours")
    """
    query = session.query(
        func.sum(CompChargeSummary.num_jobs).label('total_jobs'),
        func.sum(CompChargeSummary.core_hours).label('total_core_hours'),
        func.sum(CompChargeSummary.charges).label('total_charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    ).filter(CompChargeSummary.resource == resource)

    result = query.first()

    total_jobs = result.total_jobs or 0
    total_core_hours = result.total_core_hours or 0.0
    total_charges = result.total_charges or 0.0

    return {
        'total_jobs': total_jobs,
        'total_core_hours': total_core_hours,
        'total_charges': total_charges,
    }


def get_daily_usage_trend(session, projcode: str,
                         start_date: datetime,
                         end_date: datetime,
                         resource: Optional[str] = None) -> List[Dict[str, any]]:
    """
    Get daily usage trend for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter

    Returns:
        List of dicts with keys: date, jobs, core_hours, charges
        Ordered by date ascending

    Example:
        >>> trend = get_daily_usage_trend(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 1, 31)
        ... )
        >>> for day in trend:
        ...     print(f"{day['date']}: {day['charges']} charges")
    """
    query = session.query(
        func.date(CompChargeSummary.activity_date).label('date'),
        func.sum(CompChargeSummary.num_jobs).label('jobs'),
        func.sum(CompChargeSummary.core_hours).label('core_hours'),
        func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    query = query.group_by(
        func.date(CompChargeSummary.activity_date)
    ).order_by(
        func.date(CompChargeSummary.activity_date)
    )

    results = query.all()

    return [
        {
            'date': row.date,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]


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


def get_queue_usage_breakdown(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              machine: Optional[str] = None) -> List[Dict[str, any]]:
    """
    Get usage breakdown by queue for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        machine: Optional machine filter

    Returns:
        List of dicts with keys: queue, machine, jobs, core_hours, charges
        Ordered by charges descending

    Example:
        >>> breakdown = get_queue_usage_breakdown(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     machine='Derecho'
        ... )
        >>> for queue in breakdown:
        ...     print(f"{queue['queue']}: {queue['jobs']} jobs")
    """
    query = session.query(
        CompChargeSummary.queue,
        CompChargeSummary.machine,
        func.sum(CompChargeSummary.num_jobs).label('jobs'),
        func.sum(CompChargeSummary.core_hours).label('core_hours'),
        func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if machine:
        query = query.filter(CompChargeSummary.machine == machine)

    results = query.group_by(
        CompChargeSummary.queue,
        CompChargeSummary.machine
    ).order_by(
        func.sum(CompChargeSummary.charges).desc()
    ).all()

    return [
        {
            'queue': row.queue,
            'machine': row.machine,
            'jobs': row.jobs or 0,
            'core_hours': float(row.core_hours or 0.0),
            'charges': float(row.charges or 0.0)
        }
        for row in results
    ]


# ============================================================================
# User Usage Queries
# ============================================================================

def get_user_usage_on_project(session, projcode: str,
                              start_date: datetime,
                              end_date: datetime,
                              limit: int = 10) -> List[Dict[str, any]]:
    """
    Get top users by usage on a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        limit: Maximum number of users to return (default 10)

    Returns:
        List of dicts with keys: username, user_id, jobs, core_hours, charges
        Ordered by charges descending

    Example:
        >>> top_users = get_user_usage_on_project(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     limit=5
        ... )
        >>> for user in top_users:
        ...     print(f"{user['username']}: {user['charges']:.2f}")
    """
    results = session.query(
        CompChargeSummary.username,
        CompChargeSummary.user_id,
        func.sum(CompChargeSummary.num_jobs).label('jobs'),
        func.sum(CompChargeSummary.core_hours).label('core_hours'),
        func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    ).group_by(
        CompChargeSummary.username,
        CompChargeSummary.user_id
    ).order_by(
        func.sum(CompChargeSummary.charges).desc()
    ).limit(limit).all()

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
