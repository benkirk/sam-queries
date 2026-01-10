"""
Expiration query functions for SAM projects.

This module provides functions for finding projects with allocations
that are expiring soon or have recently expired. These are critical
for administrators to track project lifecycle and send notifications.

Functions:
    get_projects_by_allocation_end_date: Find projects by allocation end date range
    get_projects_expiring_soon: Convenience wrapper for upcoming expirations
    get_projects_with_expired_allocations: Find recently expired projects
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import and_, or_, func, select
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation, AllocationType
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.resources.facilities import Facility, Panel


# ============================================================================
# Project Expiration Queries
# ============================================================================

def _get_latest_allocation_subquery(resource_name: Optional[str] = None):
    """
    Create a correlated subquery to find the allocation with the most recent end_date
    per project (optionally filtered by resource).

    This handles the case where allocation_id order doesn't match chronological order.
    For allocations with NULL end_dates, they are treated as "infinite future" and
    will be selected as the most recent.

    Args:
        resource_name: Optional resource name to filter (e.g., 'Derecho', 'GLADE')

    Returns:
        SQLAlchemy scalar subquery for the allocation_id with latest end_date
    """

    Account2 = Account.__table__.alias('ac2')
    Allocation2 = Allocation.__table__.alias('a2')

    # Subquery to find max end_date for the project
    # COALESCE handles NULL end_dates by treating them as far future ('9999-12-31')
    subquery = (
        select(Allocation2.c.allocation_id)
        .select_from(
            Allocation2.join(Account2, Allocation2.c.account_id == Account2.c.account_id)
        )
        .where(
            Account2.c.project_id == Account.project_id,
            Allocation2.c.deleted == False
        )
        .order_by(
            # NULL end_dates sort last (treated as infinite future)
            func.coalesce(Allocation2.c.end_date, datetime(9999, 12, 31)).desc(),
            # Break ties with allocation_id descending (most recent ID)
            Allocation2.c.allocation_id.desc()
        )
        .limit(1)
    )

    # Add resource filter if specified
    if resource_name:
        Resource2 = Resource.__table__.alias('r2')
        subquery = subquery.join(
            Resource2,
            Account2.c.resource_id == Resource2.c.resource_id
        ).where(Resource2.c.resource_name == resource_name)

    return subquery.correlate(Account).scalar_subquery()


def get_projects_by_allocation_end_date(
    session: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    days_from_now: Optional[int] = None,
    days_until_now: Optional[int] = None,
    facility_names: Optional[List[str]] = None,
    resource_name: Optional[str] = None,
    include_inactive_projects: bool = False,
    include_null_end_dates: bool = False
) -> List[Tuple['Project', 'Allocation', str, Optional[int]]]:
    """
    Find projects whose most recent allocation's end_date falls within a date range.
    Only considers the allocation with the most recent end_date per project.

    Date range can be specified in two ways:

    1. Absolute dates (start_date and/or end_date):
       - start_date: Include allocations ending on or after this date
       - end_date: Include allocations ending on or before this date

    2. Relative to now (days_from_now and/or days_until_now):
       - days_from_now: Include allocations ending within next N days
       - days_until_now: Include allocations that ended within last N days

    If both absolute and relative dates are specified, absolute dates take precedence.

    Examples:
        # Projects expiring in next 30 days
        get_projects_by_allocation_end_date(session, days_from_now=30)

        # Projects that expired 90-180 days ago
        get_projects_by_allocation_end_date(session, days_until_now=180, days_from_now=-90)

        # Projects expiring between specific dates
        get_projects_by_allocation_end_date(
            session,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 12, 31)
        )

        # Projects with Derecho allocations expiring in next 60 days
        get_projects_by_allocation_end_date(
            session,
            days_from_now=60,
            resource_name='Derecho'
        )

    Args:
        session: SQLAlchemy session
        start_date: Include allocations ending on or after this date
        end_date: Include allocations ending on or before this date
        days_from_now: Include allocations ending within next N days (positive = future)
        days_until_now: Include allocations ending within last N days (negative = past)
        facility_names: Optional list of facility names to filter
        resource_name: Optional resource name to filter (e.g., 'Derecho', 'GLADE')
        include_inactive_projects: If True, include projects marked inactive
        include_null_end_dates: If True, include allocations with NULL end_dates

    Returns:
        List of (Project, Allocation, resource_name, days_from_now) tuples,
        sorted by end_date (soonest first). days_from_now is positive for future
        dates, negative for past dates, None for NULL end_dates.
    """

    now = datetime.now()

    # Determine date range
    if start_date is None and end_date is None:
        # Use relative dates
        if days_from_now is not None:
            end_date = now + timedelta(days=days_from_now)
        if days_until_now is not None:
            start_date = now - timedelta(days=days_until_now)

    # Build the subquery for latest allocation
    latest_alloc_subquery = _get_latest_allocation_subquery(resource_name)

    # Main query
    query = (
        session.query(Project, Allocation, Resource.resource_name)
        .join(Account, Project.project_id == Account.project_id)
        .join(Allocation, Account.account_id == Allocation.account_id)
        .join(Resource, Account.resource_id == Resource.resource_id)
        .filter(
            Allocation.deleted == False,
            Allocation.allocation_id == latest_alloc_subquery
        )
    )

    # Filter by project active status
    if not include_inactive_projects:
        query = query.filter(Project.active == True)

    # Build date range filters
    date_filters = []

    if not include_null_end_dates:
        date_filters.append(Allocation.end_date.isnot(None))

    if start_date is not None:
        date_filters.append(
            or_(
                Allocation.end_date >= start_date,
                Allocation.end_date.is_(None) if include_null_end_dates else False
            )
        )

    if end_date is not None:
        date_filters.append(Allocation.end_date <= end_date)

    if date_filters:
        query = query.filter(and_(*date_filters))

    # Filter by facility
    if facility_names:
        query = (
            query
            .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)
            .join(Panel, AllocationType.panel_id == Panel.panel_id)
            .join(Facility, Panel.facility_id == Facility.facility_id)
            .filter(Facility.facility_name.in_(facility_names))
        )

    # Filter by resource
    if resource_name:
        query = query.filter(Resource.resource_name == resource_name)

    # Execute query and calculate days from now
    results = []
    for project, allocation, res_name in query.all():
        if allocation.end_date is not None:
            days_difference = (allocation.end_date - now).days
        else:
            days_difference = None
        results.append((project, allocation, res_name, days_difference))

    # Sort by end_date (NULL end_dates sort last)
    return sorted(
        results,
        key=lambda x: (
            x[1].end_date if x[1].end_date is not None else datetime(9999, 12, 31),
            x[0].projcode  # Secondary sort by project code for stability
        )
    )


# Convenience wrapper functions for common use cases

def get_projects_expiring_soon(
    session: Session,
    days: int = 30,
    facility_names: Optional[List[str]] = None,
    resource_name: Optional[str] = None
) -> List[Tuple['Project', 'Allocation', str, int]]:
    """
    Get projects with allocations expiring within specified days.

    Args:
        session: SQLAlchemy session
        days: Number of days in the future to check
        facility_names: Optional list of facility names to filter
        resource_name: Optional resource name to filter

    Returns:
        List of (Project, Allocation, resource_name, days_remaining) tuples
    """
    return get_projects_by_allocation_end_date(
        session=session,
        days_from_now=days,
        days_until_now=0,  # From now until +days
        facility_names=facility_names,
        resource_name=resource_name,
        include_inactive_projects=False
    )


def get_projects_with_expired_allocations(
    session: Session,
    min_days_expired: int = 90,
    max_days_expired: Optional[int] = None,
    facility_names: Optional[List[str]] = None,
    resource_name: Optional[str] = None,
    include_inactive_projects: bool = False
) -> List[Tuple['Project', 'Allocation', str, int]]:
    """
    Get projects with allocations that expired within a specified date range.

    Args:
        session: SQLAlchemy session
        min_days_expired: Minimum number of days since expiration (default 90)
        max_days_expired: Maximum number of days since expiration (default None = no limit)
        facility_names: Optional list of facility names to filter
        resource_name: Optional resource name to filter
        include_inactive_projects: If True, include projects already marked inactive

    Returns:
        List of (Project, Allocation, resource_name, days_since_expiration) tuples,
        sorted by days_since_expiration (most expired first)
    """
    results = get_projects_by_allocation_end_date(
        session=session,
        days_until_now=max_days_expired,
        days_from_now=-min_days_expired,
        facility_names=facility_names,
        resource_name=resource_name,
        include_inactive_projects=include_inactive_projects
    )

    # Convert to positive days_since_expiration and reverse sort
    return [
        (proj, alloc, res, abs(days))
        for proj, alloc, res, days in results
    ][::-1]  # Most expired first
