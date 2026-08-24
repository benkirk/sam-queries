"""
Expiration query functions for SAM projects.

This module provides functions for finding projects with allocations
that are expiring soon or have recently expired. These are critical
for administrators to track project lifecycle and send notifications.

Functions:
    get_projects_by_allocation_end_date: Find projects by allocation end date range
    get_projects_expiring_soon: Convenience wrapper for upcoming expirations
    get_projects_with_expired_allocations: Find recently expired projects
    unique_projects: Collapse a (project, allocation, ...) result to distinct projects
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, or_, func, select
from sqlalchemy.orm import Session

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation, AllocationType
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.resources.facilities import Facility, Panel


#: The grace period before an expired project is eligible for deactivation,
#: shared by the admin "Deactivate Expired" button and the monthly
#: `deactivate_expired_projects` task so the two cannot drift. There is
#: deliberately **no upper bound** to pair with it: a ceiling exempts exactly
#: the projects that have been dead longest, which is backwards.
#:
#: `sam-admin project --recent-expirations --deactivate` does NOT use this — its
#: floor is 0 and its ceiling comes from `--since`, because that path has a human
#: reading the list and confirming.
DEACTIVATION_MIN_DAYS_EXPIRED = 90


# ============================================================================
# Project Expiration Queries
# ============================================================================


def unique_projects(
    results: List[Tuple['Project', Any, Any, Any]]
) -> List['Project']:
    """Collapse an expirations query result down to distinct projects.

    WARNING: **A guard, not a repair.** `get_projects_by_allocation_end_date` and
    `get_projects_with_expired_allocations` pin one allocation per project
    (`_get_latest_allocation_subquery` ends in `LIMIT 1`), so today they emit no
    duplicates at all — verified against a production snapshot at three windows,
    132/95/6 rows, zero dupes. The pre-existing "a project can have multiple
    expired allocations" comment in the admin route was simply wrong.

    It stays because the *shape* is one row per `(project, allocation)`, and the
    sibling `get_all_expiring_allocations` genuinely returns every allocation —
    so a caller swapping queries would start double-counting silently. It also
    gives `sam-admin` a project count it can quote in a prompt *before* mutating,
    separately from the write, which is why this is its own name rather than a
    step hidden inside one.

    First-seen order is preserved, so a result sorted most-expired-first stays
    that way.

    Args:
        results: Tuples whose first element is a Project, as returned by
            `get_projects_with_expired_allocations` / `..._by_allocation_end_date`.

    Returns:
        Distinct projects, keyed on project_id, in first-seen order.
    """
    seen: Dict[int, 'Project'] = {}
    for row in results:
        project = row[0]
        seen.setdefault(project.project_id, project)
    return list(seen.values())

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
    include_null_end_dates: bool = False,
    now: Optional[datetime] = None
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
        now: Reference instant for the relative-date forms and for the returned
            day counts. Defaults to the wall clock. A **scheduled task must pass
            this explicitly**, derived from its occurrence, or a run dispatched
            late selects a different cohort than a punctual one would — and
            `--occurrence` replay answers "what does today select?" rather than
            "what will that slot select?". Ignored when start_date/end_date are
            given. Mirrors `get_all_expiring_allocations`, which already has it.

    Returns:
        List of (Project, Allocation, resource_name, days_from_now) tuples,
        sorted by end_date (soonest first). days_from_now is positive for future
        dates, negative for past dates, None for NULL end_dates.
    """

    now = now or datetime.now()

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
        query = query.filter(Project.is_active)

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
    include_inactive_projects: bool = False,
    now: Optional[datetime] = None
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
        now: Reference instant for "days expired". Defaults to the wall clock;
            see `get_projects_by_allocation_end_date`.

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
        include_inactive_projects=include_inactive_projects,
        now=now
    )

    # Convert to positive days_since_expiration and reverse sort
    return [
        (proj, alloc, res, abs(days))
        for proj, alloc, res, days in results
    ][::-1]  # Most expired first


def get_all_expiring_allocations(
    session: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    facility_names: Optional[List[str]] = None,
    resource_name: Optional[str] = None,
    include_inactive_projects: bool = False,
    now: Optional[datetime] = None
) -> List[Tuple['Project', 'Allocation', str, Optional[int]]]:
    """
    Find ALL allocations (not just latest per project) with end_dates in a date range.

    Unlike get_projects_by_allocation_end_date(), this returns ALL allocations
    that expire in the date range, not just the latest one per project.
    This is useful for notifications where you want to alert about all
    expiring resources.

    Args:
        session: SQLAlchemy session
        start_date: Include allocations ending on or after this date
        end_date: Include allocations ending on or before this date
        facility_names: Optional list of facility names to filter
        resource_name: Optional resource name to filter
        include_inactive_projects: If True, include projects marked inactive
        now: the instant `days_from_now` is measured against. Defaults to the
            wall clock, which is right for a CLI run. A **scheduled** caller
            must pass its occurrence instead: the notice body says "expires in
            N days", and a dispatch that ran 20 hours late would otherwise
            render 37 where a punctual one rendered 38, for the same run.

    Returns:
        List of (Project, Allocation, resource_name, days_from_now) tuples,
        sorted by end_date then project code. days_from_now is positive for
        future dates, negative for past dates.

    Example:
        # Get all allocations expiring in next 32 days
        expiring = get_all_expiring_allocations(
            session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=32),
            facility_names=['UNIV', 'WNA']
        )
        # This will return multiple allocations per project if they all expire
        # in the date range (e.g., Derecho, Casper, and Derecho GPU)
    """
    now = now if now is not None else datetime.now()

    # Main query - no subquery filter, returns ALL allocations in range
    query = (
        session.query(Project, Allocation, Resource.resource_name)
        .join(Account, Project.project_id == Account.project_id)
        .join(Allocation, Account.account_id == Allocation.account_id)
        .join(Resource, Account.resource_id == Resource.resource_id)
        .filter(Allocation.deleted == False)
    )

    # Filter by project active status
    if not include_inactive_projects:
        query = query.filter(Project.is_active)

    # Build date range filters
    date_filters = [Allocation.end_date.isnot(None)]

    if start_date is not None:
        date_filters.append(Allocation.end_date >= start_date)

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

    # Sort by end_date, then project code for stable ordering
    return sorted(
        results,
        key=lambda x: (
            x[1].end_date if x[1].end_date is not None else datetime(9999, 12, 31),
            x[0].projcode
        )
    )
