"""
Example queries and operations for SAM database using SQLAlchemy ORM.

This module demonstrates common patterns for querying and managing users,
projects, allocations, and related entities.
"""

from sam import *

from sam.accounting.accounts import *
from sam.accounting.adjustments import *
from sam.accounting.allocations import *
from sam.activity.archive import *
from sam.activity.computational import *
from sam.activity.dataset import *
from sam.activity.dav import *
from sam.activity.disk import *
from sam.activity.hpc import *
from sam.core.groups import *
from sam.core.organizations import *
from sam.core.users import *
from sam.projects.areas import *
from sam.projects.contracts import *
from sam.projects.projects import *
from sam.resources.facilities import *
from sam.resources.machines import *
from sam.resources.resources import *
from sam.security.access import *
from sam.security.roles import *
from sam.summaries.archive_summaries import *
from sam.summaries.comp_summaries import *
from sam.summaries.dav_summaries import *
from sam.summaries.disk_summaries import *
from sam.summaries.hpc_summaries import *
from sam.integration.xras_views import *

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

from sqlalchemy import and_, or_, func, desc, select
from sqlalchemy.orm import Session, joinedload, selectinload


# ============================================================================
# Resource Queries
# ============================================================================

def get_available_resources(session: Session) -> List[Dict]:
    """
    Get list of all available resources with their types.

    Returns:
        List of dicts with keys: resource_id, resource_name, resource_type,
        commission_date, decommission_date, active
    """
    results = session.query(
        Resource.resource_id,
        Resource.resource_name,
        ResourceType.resource_type,
        Resource.commission_date,
        Resource.decommission_date
    )\
        .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)\
        .order_by(ResourceType.resource_type, Resource.resource_name)\
        .all()

    return [
        {
            'resource_id': r.resource_id,
            'resource_name': r.resource_name,
            'resource_type': r.resource_type,
            'commission_date': r.commission_date,
            'decommission_date': r.decommission_date,
            'active': r.decommission_date is None or r.decommission_date >= datetime.now()
        }
        for r in results
    ]


def get_resources_by_type(session: Session, resource_type: str) -> List[str]:
    """
    Get list of resource names for a specific resource type.

    Args:
        resource_type: Type of resource ('HPC', 'DISK', 'ARCHIVE', etc.)

    Returns:
        List of resource names
    """
    results = session.query(Resource.resource_name)\
        .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)\
        .filter(ResourceType.resource_type == resource_type)\
        .order_by(Resource.resource_name)\
        .all()

    return [r.resource_name for r in results]


# ============================================================================
# User Queries
# ============================================================================

def find_user_by_username(session: Session, username: str) -> Optional[User]:
    """Find a user by username."""
    return session.query(User).filter(User.username == username).first()


def find_users_by_name(session: Session, name_part: str) -> List[User]:
    """Find users whose first or last name contains the given string."""
    search = f"%{name_part}%"
    return session.query(User).filter(
        or_(
            User.first_name.like(search),
            User.last_name.like(search)
        )
    ).all()


def get_active_users(session: Session, limit: int = 100) -> List[User]:
    """Get active, non-deleted users with their institutions."""
    return session.query(User)\
        .options(selectinload(User.institutions))\
        .filter(
            User.active == True,
            User.deleted.in_([False, None])
        )\
        .limit(limit)\
        .all()


def get_user_with_details(session: Session, username: str) -> Optional[User]:
    """Get user with all related data eagerly loaded."""
    return session.query(User)\
        .options(
            joinedload(User.email_addresses),
            joinedload(User.institutions).joinedload(Institution),
            joinedload(User.organizations).joinedload(Organization),
            joinedload(User.academic_status),
            joinedload(User.accounts).joinedload(AccountUser.account)
        )\
        .filter(User.username == username)\
        .first()


def get_users_by_institution(session: Session, institution_name: str) -> List[User]:
    """Find all users affiliated with a specific institution."""
    return session.query(User)\
        .join(User.institutions)\
        .join(Institution)\
        .filter(Institution.name.like(f"%{institution_name}%"))\
        .filter(User.active == True)\
        .all()


def get_users_by_organization(session: Session, org_acronym: str) -> List[User]:
    """Find all users in a specific organization."""
    return session.query(User)\
        .join(User.organizations)\
        .join(Organization)\
        .filter(Organization.acronym == org_acronym)\
        .filter(User.active == True)\
        .all()


def search_users_by_email(session: Session, email_part: str) -> List[User]:
    """Find users by partial email address match."""
    return session.query(User)\
        .join(User.email_addresses)\
        .filter(EmailAddress.email_address.like(f"%{email_part}%"))\
        .distinct()\
        .all()


def get_user_emails(session: Session, username: str) -> List[str]:
    """
    Get all email addresses for a user.
    Simple convenience function that returns just the email strings.
    """
    user = find_user_by_username(session, username)
    if user:
        return user.all_emails
    return []


def get_user_emails_detailed(session: Session, username: str) -> List[dict]:
    """
    Get detailed email information for a user.
    Returns list of dicts with email, is_primary, active, and created fields.
    """
    user = find_user_by_username(session, username)
    if user:
        return user.get_emails_detailed()
    return []


def get_users_with_multiple_emails(session: Session, min_emails: int = 2) -> List[Tuple[User, int]]:
    """
    Find users who have multiple email addresses.
    Returns list of (User, email_count) tuples.
    """
    results = session.query(
        User,
        func.count(EmailAddress.email_address_id).label('email_count')
    )\
        .join(User.email_addresses)\
        .group_by(User.user_id)\
        .having(func.count(EmailAddress.email_address_id) >= min_emails)\
        .order_by(desc('email_count'))\
        .all()

    return [(user, count) for user, count in results]


def get_users_without_primary_email(session: Session) -> List[User]:
    """
    Find active users who don't have a primary email set.
    Useful for data cleanup.
    """
    # Find users with emails but none marked as primary
    from sqlalchemy import exists, select

    has_email = exists(
        select(1).select_from(EmailAddress)
        .where(EmailAddress.user_id == User.user_id)
    )

    has_primary = exists(
        select(1).select_from(EmailAddress)
        .where(
            EmailAddress.user_id == User.user_id,
            EmailAddress.is_primary == True
        )
    )

    return session.query(User)\
                  .filter(
                      User.active == True,
                      has_email,
                      ~has_primary
                  )\
                  .all()


# ============================================================================
# Project Queries
# ============================================================================

def find_project_by_code(session: Session, projcode: str) -> Optional[Project]:
    """Find a project by its code."""
    return session.query(Project).filter(Project.projcode == projcode).first()


def get_project_with_full_details(session: Session, projcode: str) -> Optional[Project]:
    """Get project with all related data."""
    return session.query(Project)\
        .options(
            joinedload(Project.lead),
            joinedload(Project.admin),
            joinedload(Project.accounts).joinedload(Account.allocations),
            joinedload(Project.directories),
            joinedload(Project.area_of_interest),
            joinedload(Project.allocation_type).joinedload(AllocationType.panel)
        )\
        .filter(Project.projcode == projcode)\
        .first()


def get_active_projects(session: Session, facility_name: str = None) -> List[Project]:
    """Get all active projects, optionally filtered by facility."""
    query = session.query(Project)\
        .filter(Project.active == True)

    if facility_name:
        query = query\
            .join(AllocationType)\
            .join(Panel)\
            .join(Facility)\
            .filter(Facility.facility_name == facility_name)

    return query.all()


def get_projects_by_lead(session: Session, username: str) -> List[Project]:
    """Get all projects led by a specific user."""
    return session.query(Project)\
        .join(User, Project.project_lead_user_id == User.user_id)\
        .filter(User.username == username)\
        .filter(Project.active == True)\
        .all()


def get_project_members(session: Session, projcode: str) -> List[User]:
    """Get all users who have access to a project."""
    return session.query(User)\
        .join(AccountUser, User.user_id == AccountUser.user_id)\
        .join(Account, AccountUser.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .filter(
            Project.projcode == projcode,
            or_(
                AccountUser.end_date.is_(None),
                AccountUser.end_date >= datetime.now()
            )
        )\
        .distinct()\
        .all()


def get_users_on_project(session: Session, projcode: str) -> List[Dict]:
    """
    Get all users associated with a project with their contact information.
    Includes lead, admin (if different from lead), and all active members.

    Args:
        session: SQLAlchemy session
        projcode: Project code

    Returns:
        List of dicts with keys: username, display_name, email, role
        Roles: 'Lead', 'Admin', 'Member'
    """
    project = session.query(Project)\
        .options(
            joinedload(Project.lead).joinedload(User.email_addresses),
            joinedload(Project.admin).joinedload(User.email_addresses)
        )\
        .filter(Project.projcode == projcode)\
        .first()

    if not project:
        return []

    users_dict = {}  # Use dict to avoid duplicates, keyed by user_id

    # Add project lead
    lead = project.lead
    users_dict[lead.user_id] = {
        'username': lead.username,
        'unix_id': lead.unix_uid,
        'display_name': lead.display_name,
        'email': lead.primary_email,
        'role': 'Lead'
    }

    # Add project admin if exists and different from lead
    if project.admin and project.project_admin_user_id != project.project_lead_user_id:
        admin = project.admin
        users_dict[admin.user_id] = {
            'username': admin.username,
            'unix_id': admin.unix_uid,
            'display_name': admin.display_name,
            'email': admin.primary_email,
            'role': 'Admin'
        }

    # Add all project members with active access
    members = session.query(User)\
        .options(selectinload(User.email_addresses))\
        .join(AccountUser, User.user_id == AccountUser.user_id)\
        .join(Account, AccountUser.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .filter(
            Project.projcode == projcode,
            or_(
                AccountUser.end_date.is_(None),
                AccountUser.end_date >= datetime.now()
            ),
            User.active == True
        )\
        .distinct()\
        .all()

    # Add members (don't overwrite lead/admin roles)
    for member in members:
        if member.user_id not in users_dict:
            users_dict[member.user_id] = {
                'username': member.username,
                'unix_id': member.unix_uid,
                'display_name': member.display_name,
                'email': member.primary_email,
                'role': 'Member'
            }

    # Convert to list and sort by role priority then username
    role_priority = {'Lead': 0, 'Admin': 1, 'Member': 2}
    users_list = sorted(
        users_dict.values(),
        key=lambda x: (role_priority[x['role']], x['username'])
    )

    return users_list


def search_projects_by_title(session: Session, search_term: str) -> List[Project]:
    """Search projects by title."""
    return session.query(Project)\
        .filter(Project.title.like(f"%{search_term}%"))\
        .filter(Project.active == True)\
        .all()


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
        days_until_now=min_days_expired,
        days_from_now=-max_days_expired if max_days_expired else None,
        facility_names=facility_names,
        resource_name=resource_name,
        include_inactive_projects=include_inactive_projects
    )

    # Convert to positive days_since_expiration and reverse sort
    return [
        (proj, alloc, res, abs(days))
        for proj, alloc, res, days in results
    ][::-1]  # Most expired first



# ============================================================================
# Allocation Queries
# ============================================================================

def get_project_allocations(session: Session, projcode: str, resource_name: str = None) -> List[Tuple[Allocation, str]]:
    """
    Get all allocations for a project, optionally filtered by resource.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        resource_name: Optional resource name to filter

    Returns:
        List of (Allocation, resource_name) tuples
    """
    query = session.query(Allocation, Resource.resource_name)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .filter(
            Project.projcode == projcode,
            Allocation.deleted == False
        )

    if resource_name:
        query = query.filter(Resource.resource_name == resource_name)

    return query.order_by(Resource.resource_name, Allocation.start_date.desc()).all()


def get_active_allocation(session: Session, projcode: str) -> Optional[Allocation]:
    """Get the current active allocation for a project."""
    now = datetime.now()
    return session.query(Allocation)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .filter(
            Project.projcode == projcode,
            Allocation.deleted == False,
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )\
        .order_by(Allocation.allocation_id.desc())\
        .first()


def get_latest_allocation_for_project(session: Session, project_id: int) -> Optional[Allocation]:
    """
    Helper function to get the most recent allocation for a project.
    This is a simpler alternative when you just need one project's latest allocation.
    """
    return session.query(Allocation)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .filter(
            Account.project_id == project_id,
            Allocation.deleted == False
        )\
        .order_by(Allocation.allocation_id.desc())\
        .first()


def get_allocation_history(
    session: Session,
    projcode: str
) -> List[Dict]:
    """Get complete allocation transaction history for a project."""
    results = session.query(
        AllocationTransaction,
        Allocation,
        User
    )\
        .join(Allocation, AllocationTransaction.allocation_id == Allocation.allocation_id)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .outerjoin(User, AllocationTransaction.user_id == User.user_id)\
        .filter(Project.projcode == projcode)\
        .order_by(AllocationTransaction.creation_time)\
        .all()

    history = []
    for txn, alloc, user in results:
        history.append({
            'transaction_date': txn.creation_time,
            'transaction_type': txn.transaction_type,
            'requested_amount': txn.requested_amount,
            'transaction_amount': txn.transaction_amount,
            'start_date': txn.alloc_start_date,
            'end_date': txn.alloc_end_date,
            'processed_by': user.full_name if user else None,
            'comment': txn.transaction_comment,
            'auth_at_panel': txn.auth_at_panel_mtg
        })

    return history


def get_allocations_by_type(
    session: Session,
    allocation_type: str,
    active_only: bool = True
) -> List[Tuple[Project, Allocation]]:
    """Get all allocations of a specific type."""
    now = datetime.now()

    query = session.query(Project, Allocation)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
        .filter(
            AllocationType.allocation_type == allocation_type,
            Allocation.deleted == False
        )

    if active_only:
        query = query.filter(
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )

    return query.all()

def get_allocations_by_resource(
    session: Session,
    resource_name: str,
    active_only: bool = True
) -> List[Tuple[Project, Allocation]]:
    """
    Get all allocations for a specific resource.

    Args:
        session: SQLAlchemy session
        resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')
        active_only: If True, only return currently active allocations

    Returns:
        List of (Project, Allocation) tuples for the specified resource

    Examples:
        >>> # Get all active Derecho allocations
        >>> results = get_allocations_by_resource(session, 'Derecho')
        >>> for project, allocation in results:
        ...     print(f"{project.projcode}: {allocation.amount}")

        >>> # Get all GLADE allocations including expired ones
        >>> results = get_allocations_by_resource(session, 'GLADE', active_only=False)
    """
    now = datetime.now()

    # Build the query joining through the account table to reach resource
    query = session.query(Project, Allocation)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .filter(
            Resource.resource_name == resource_name,
            Allocation.deleted == False
        )

    # Optionally filter to only currently active allocations
    if active_only:
        query = query.filter(
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )

    return query.all()

def get_allocation_summary_by_facility(
    session: Session,
    start_date: datetime,
    end_date: datetime
) -> List[Dict]:
    """Get allocation summary statistics by facility."""
    results = session.query(
        Facility.facility_name,
        AllocationType.allocation_type,
        func.count(Allocation.allocation_id).label('num_allocations'),
        func.sum(Allocation.amount).label('total_amount'),
        func.avg(Allocation.amount).label('avg_amount')
    )\
        .join(Panel, Facility.facility_id == Panel.facility_id)\
        .join(AllocationType, Panel.panel_id == AllocationType.panel_id)\
        .join(Project, AllocationType.allocation_type_id == Project.allocation_type_id)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .filter(
            Allocation.deleted == False,
            Allocation.start_date.between(start_date, end_date)
        )\
        .group_by(Facility.facility_name, AllocationType.allocation_type)\
        .all()

    return [
        {
            'facility': r[0],
            'allocation_type': r[1],
            'count': r[2],
            'total_amount': float(r[3]) if r[3] else 0,
            'avg_amount': float(r[4]) if r[4] else 0
        }
        for r in results
    ]


# ============================================================================
# Group Queries
# ============================================================================

def get_group_by_name(session: Session, group_name: str) -> Optional[AdhocGroup]:
    """Find a group by name."""
    return session.query(AdhocGroup)\
        .filter(AdhocGroup.group_name == group_name)\
        .first()


def get_groups_by_tag(session: Session, tag: str) -> List[AdhocGroup]:
    """Find all groups with a specific tag."""
    return session.query(AdhocGroup)\
        .join(AdhocGroupTag)\
        .filter(AdhocGroupTag.tag == tag)\
        .all()


def get_active_groups(session: Session) -> List[AdhocGroup]:
    """Get all active groups."""
    return session.query(AdhocGroup)\
        .filter(AdhocGroup.active == True)\
        .order_by(AdhocGroup.group_name)\
        .all()


# ============================================================================
# Statistics and Reporting
# ============================================================================

def get_user_statistics(session: Session) -> Dict:
    """Get overall user statistics."""
    total = session.query(func.count(User.user_id)).scalar()
    active = session.query(func.count(User.user_id))\
        .filter(User.active == True).scalar()
    locked = session.query(func.count(User.user_id))\
        .filter(User.locked == True).scalar()

    return {
        'total_users': total,
        'active_users': active,
        'locked_users': locked,
        'inactive_users': total - active
    }


def get_project_statistics(session: Session) -> Dict:
    """Get overall project statistics."""
    total = session.query(func.count(Project.project_id)).scalar()
    active = session.query(func.count(Project.project_id))\
        .filter(Project.active == True).scalar()

    by_facility = session.query(
        Facility.facility_name,
        func.count(Project.project_id)
    )\
        .join(Panel, Facility.facility_id == Panel.facility_id)\
        .join(AllocationType, Panel.panel_id == AllocationType.panel_id)\
        .join(Project, AllocationType.allocation_type_id == Project.allocation_type_id)\
        .filter(Project.active == True)\
        .group_by(Facility.facility_name)\
        .all()

    return {
        'total_projects': total,
        'active_projects': active,
        'inactive_projects': total - active,
        'by_facility': dict(by_facility)
    }


def get_institution_project_count(session: Session) -> List[Dict]:
    """Get project count by institution."""
    results = session.query(
        Institution.name,
        func.count(func.distinct(Project.project_id)).label('project_count')
    )\
        .join(UserInstitution, Institution.institution_id == UserInstitution.institution_id)\
        .join(User, UserInstitution.user_id == User.user_id)\
        .join(Project, or_(
            Project.project_lead_user_id == User.user_id,
            Project.project_admin_user_id == User.user_id
        ))\
        .filter(Project.active == True)\
        .group_by(Institution.name)\
        .order_by(desc('project_count'))\
        .limit(20)\
        .all()

    return [
        {'institution': r[0], 'project_count': r[1]}
        for r in results
    ]


# ============================================================================
# Usage Query Helper Functions
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
                              resource: Optional[str] = None) -> Dict[str, float]:
    """
    Get aggregated usage summary for a project.

    Args:
        session: SQLAlchemy session
        projcode: Project code (e.g., 'UCUB0001')
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        resource: Optional resource filter (e.g., 'Derecho')

    Returns:
        Dictionary with keys:
        - total_jobs: Number of jobs
        - total_core_hours: Total core hours consumed
        - total_charges: Total charges incurred
        - average_charge_per_job: Average charge per job
        - average_core_hours_per_job: Average core hours per job

    Example:
        >>> summary = get_project_usage_summary(
        ...     session, 'UCUB0001',
        ...     datetime(2024, 1, 1),
        ...     datetime(2024, 12, 31),
        ...     resource='Derecho'
        ... )
        >>> print(f"Project used {summary['total_core_hours']:.2f} core hours")
    """
    from sqlalchemy import func as sql_func

    query = session.query(
        sql_func.sum(CompChargeSummary.num_jobs).label('total_jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('total_core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('total_charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    result = query.first()

    total_jobs = result.total_jobs or 0
    total_core_hours = result.total_core_hours or 0.0
    total_charges = result.total_charges or 0.0

    return {
        'total_jobs': total_jobs,
        'total_core_hours': total_core_hours,
        'total_charges': total_charges,
        'average_charge_per_job': (float(total_charges) / float(total_jobs)) if total_jobs > 0 else 0.0,
        'average_core_hours_per_job': (float(total_core_hours) / float(total_jobs) ) if total_jobs > 0 else 0.0
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
    from sqlalchemy import func as sql_func

    query = session.query(
        sql_func.date(CompChargeSummary.activity_date).label('date'),
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    )

    if resource:
        query = query.filter(CompChargeSummary.resource == resource)

    query = query.group_by(
        sql_func.date(CompChargeSummary.activity_date)
    ).order_by(
        sql_func.date(CompChargeSummary.activity_date)
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


def get_recent_jobs_for_project(session, projcode: str,
                               limit: int = 100,
                               resource: Optional[str] = None) -> List[CompActivityChargeView]:
    """
    Get recent jobs for a project using the charge view.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        limit: Maximum number of jobs to return (default 100)
        resource: Optional machine filter (e.g., 'Derecho')

    Returns:
        List of CompActivityChargeView view records ordered by submit time (descending)

    Example:
        >>> jobs = get_recent_jobs_for_project(session, 'UCUB0001', limit=50)
        >>> for job in jobs:
        ...     print(f"{job.job_id}: {job.core_hours} hours, {job.charge} charged")
    """
    query = session.query(CompActivityChargeView).filter(
        CompActivityChargeView.projcode == projcode
    )

    if resource:
        query = query.filter(CompActivityChargeView.machine == resource)

    return query.order_by(
        CompActivityChargeView.submit_time.desc()
    ).limit(limit).all()


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
    from sqlalchemy import func as sql_func

    query = session.query(
        CompChargeSummary.queue,
        CompChargeSummary.machine,
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
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
        sql_func.sum(CompChargeSummary.charges).desc()
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
    from sqlalchemy import func as sql_func

    results = session.query(
        CompChargeSummary.username,
        CompChargeSummary.user_id,
        sql_func.sum(CompChargeSummary.num_jobs).label('jobs'),
        sql_func.sum(CompChargeSummary.core_hours).label('core_hours'),
        sql_func.sum(CompChargeSummary.charges).label('charges')
    ).filter(
        CompChargeSummary.projcode == projcode,
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    ).group_by(
        CompChargeSummary.username,
        CompChargeSummary.user_id
    ).order_by(
        sql_func.sum(CompChargeSummary.charges).desc()
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


# ============================================================================
# Complex Queries
# ============================================================================

def get_user_project_access(session: Session, username: str) -> List[Dict]:
    """Get all projects a user has access to with their roles."""
    user = find_user_by_username(session, username)
    if not user:
        return []

    # Projects where user is lead or admin
    led_projects = session.query(Project)\
        .filter(
            or_(
                Project.project_lead_user_id == user.user_id,
                Project.project_admin_user_id == user.user_id
            ),
            Project.active == True
        ).all()

    # Projects where user has account access
    member_projects = session.query(Project, AccountUser)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(AccountUser, Account.account_id == AccountUser.account_id)\
        .filter(
            AccountUser.user_id == user.user_id,
            or_(
                AccountUser.end_date.is_(None),
                AccountUser.end_date >= datetime.now()
            ),
            Project.active == True
        ).all()

    access_list = []

    for proj in led_projects:
        role = 'Lead' if proj.project_lead_user_id == user.user_id else 'Admin'
        access_list.append({
            'projcode': proj.projcode,
            'title': proj.title,
            'role': role,
            'access_start': None,
            'access_end': None
        })

    for proj, acc_user in member_projects:
        # Skip if already listed as lead/admin
        if proj.project_id in [p.project_id for p in led_projects]:
            continue

        access_list.append({
            'projcode': proj.projcode,
            'title': proj.title,
            'role': 'Member',
            'access_start': acc_user.start_date,
            'access_end': acc_user.end_date
        })

    return access_list


# ============================================================================
# Dashboard Query Helpers
# ============================================================================

def get_user_dashboard_data(session: Session, user_id: int) -> Dict:
    """
    Get all dashboard data for a user in one optimized query set.

    Loads user, their active projects, and allocation usage for each project.
    Optimized for server-side dashboard rendering with minimal database queries.

    Args:
        session: SQLAlchemy session
        user_id: User ID to fetch dashboard for

    Returns:
        Dictionary with structure:
        {
            'user': User object,
            'projects': [
                {
                    'project': Project object,
                    'resources': List[Dict],  # From get_detailed_allocation_usage()
                    'has_children': bool
                }
            ],
            'total_projects': int
        }

    Example:
        >>> data = get_user_dashboard_data(session, 12345)
        >>> print(f"User {data['user'].username} has {data['total_projects']} projects")
        >>> for proj_data in data['projects']:
        ...     proj = proj_data['project']
        ...     print(f"{proj.projcode}: {len(proj_data['resources'])} resources")
    """
    # Get user with active projects eagerly loaded
    user = session.query(User)\
        .options(
            selectinload(User.email_addresses),
            joinedload(User.led_projects).joinedload(Project.lead),
            joinedload(User.admin_projects).joinedload(Project.admin)
        )\
        .filter(User.user_id == user_id)\
        .first()

    if not user:
        return {
            'user': None,
            'projects': [],
            'total_projects': 0
        }

    # Get active projects
    projects = user.active_projects

    # Build project data with resource usage
    project_data_list = []
    for project in projects:
        # Get allocation usage for all resources
        resources = []
        usage_data = project.get_detailed_allocation_usage(include_adjustments=True)

        for resource_name, usage in usage_data.items():
            resources.append({
                'resource_name': resource_name,
                'allocated': usage.get('allocated', 0.0),
                'used': usage.get('used', 0.0),
                'remaining': usage.get('remaining', 0.0),
                'percent_used': usage.get('percent_used', 0.0),
                'charges_by_type': usage.get('charges_by_type', {}),
                'adjustments': usage.get('adjustments', 0.0),
                'status': usage.get('status', 'Unknown'),
                'start_date': usage.get('start_date'),
                'end_date': usage.get('end_date')
            })

        project_data_list.append({
            'project': project,
            'resources': resources,
            'has_children': project.has_children if hasattr(project, 'has_children') else False
        })

    return {
        'user': user,
        'projects': project_data_list,
        'total_projects': len(projects)
    }


def get_resource_detail_data(
    session: Session,
    projcode: str,
    resource_name: str,
    start_date: datetime,
    end_date: datetime
) -> Optional[Dict]:
    """
    Get resource usage details for charts and summary display.

    Fetches allocation summary and daily charge breakdown for a specific
    resource on a project within a date range.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        resource_name: Resource name (e.g., 'Derecho', 'GLADE')
        start_date: Start of date range
        end_date: End of date range

    Returns:
        Dictionary with structure:
        {
            'project': Project object,
            'resource': Resource object,
            'resource_summary': {
                'resource_name': str,
                'allocated': float,
                'used': float,
                'remaining': float,
                'percent_used': float,
                'charges_by_type': Dict[str, float],
                'start_date': datetime,
                'end_date': datetime,
                'status': str
            },
            'daily_charges': [
                {
                    'date': date,
                    'comp': float,
                    'dav': float,
                    'disk': float,
                    'archive': float
                }
            ],
            'charge_totals': {
                'comp': float,
                'dav': float,
                'disk': float,
                'archive': float,
                'total': float
            }
        }
        Returns None if project or resource not found.

    Example:
        >>> from datetime import datetime, timedelta
        >>> end = datetime.now()
        >>> start = end - timedelta(days=30)
        >>> data = get_resource_detail_data(session, 'SCSG0001', 'Derecho', start, end)
        >>> if data:
        ...     print(f"Total charges: {data['charge_totals']['total']:.2f}")
        ...     print(f"Days of data: {len(data['daily_charges'])}")
    """
    # Find project
    project = find_project_by_code(session, projcode)
    if not project:
        return None

    # Find resource
    resource = session.query(Resource)\
        .filter(Resource.resource_name == resource_name)\
        .first()

    if not resource:
        return None

    # Get allocation usage for this specific resource
    all_usage = project.get_detailed_allocation_usage(
        resource_name=resource_name,
        include_adjustments=True
    )

    resource_summary = all_usage.get(resource_name)
    if not resource_summary:
        # No allocation for this resource
        resource_summary = {
            'resource_name': resource_name,
            'allocated': 0.0,
            'used': 0.0,
            'remaining': 0.0,
            'percent_used': 0.0,
            'charges_by_type': {},
            'start_date': None,
            'end_date': None,
            'status': 'No Allocation'
        }

    # Get account for this project+resource
    account = session.query(Account)\
        .filter(
            Account.project_id == project.project_id,
            Account.resource_id == resource.resource_id,
            Account.deleted == False
        )\
        .first()

    if not account:
        return {
            'project': project,
            'resource': resource,
            'resource_summary': resource_summary,
            'daily_charges': [],
            'charge_totals': {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0, 'total': 0.0}
        }

    # Determine resource type to query appropriate tables
    resource_type = resource.resource_type.resource_type if resource.resource_type else 'HPC'

    # Query daily charges by type
    daily_charges_map = {}  # date -> {comp, dav, disk, archive}
    charge_totals = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}

    # Query comp charges
    if resource_type in ['HPC', 'DAV']:
        comp_results = session.query(
            CompChargeSummary.activity_date,
            func.sum(CompChargeSummary.charges).label('charges')
        ).filter(
            CompChargeSummary.account_id == account.account_id,
            CompChargeSummary.activity_date >= start_date,
            CompChargeSummary.activity_date <= end_date
        ).group_by(CompChargeSummary.activity_date).all()

        for row in comp_results:
            date_key = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
            if date_key not in daily_charges_map:
                daily_charges_map[date_key] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_charges_map[date_key]['comp'] = float(row.charges or 0.0)
            charge_totals['comp'] += float(row.charges or 0.0)

        # Query DAV charges
        dav_results = session.query(
            DavChargeSummary.activity_date,
            func.sum(DavChargeSummary.charges).label('charges')
        ).filter(
            DavChargeSummary.account_id == account.account_id,
            DavChargeSummary.activity_date >= start_date,
            DavChargeSummary.activity_date <= end_date
        ).group_by(DavChargeSummary.activity_date).all()

        for row in dav_results:
            date_key = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
            if date_key not in daily_charges_map:
                daily_charges_map[date_key] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_charges_map[date_key]['dav'] = float(row.charges or 0.0)
            charge_totals['dav'] += float(row.charges or 0.0)

    # Query disk charges
    if resource_type == 'DISK':
        disk_results = session.query(
            DiskChargeSummary.activity_date,
            func.sum(DiskChargeSummary.charges).label('charges')
        ).filter(
            DiskChargeSummary.account_id == account.account_id,
            DiskChargeSummary.activity_date >= start_date,
            DiskChargeSummary.activity_date <= end_date
        ).group_by(DiskChargeSummary.activity_date).all()

        for row in disk_results:
            date_key = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
            if date_key not in daily_charges_map:
                daily_charges_map[date_key] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_charges_map[date_key]['disk'] = float(row.charges or 0.0)
            charge_totals['disk'] += float(row.charges or 0.0)

    # Query archive charges
    if resource_type == 'ARCHIVE':
        archive_results = session.query(
            ArchiveChargeSummary.activity_date,
            func.sum(ArchiveChargeSummary.charges).label('charges')
        ).filter(
            ArchiveChargeSummary.account_id == account.account_id,
            ArchiveChargeSummary.activity_date >= start_date,
            ArchiveChargeSummary.activity_date <= end_date
        ).group_by(ArchiveChargeSummary.activity_date).all()

        for row in archive_results:
            date_key = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
            if date_key not in daily_charges_map:
                daily_charges_map[date_key] = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}
            daily_charges_map[date_key]['archive'] = float(row.charges or 0.0)
            charge_totals['archive'] += float(row.charges or 0.0)

    # Convert map to sorted list
    daily_charges = [
        {'date': date, **charges}
        for date, charges in sorted(daily_charges_map.items())
    ]

    # Calculate total
    charge_totals['total'] = sum(charge_totals.values())

    return {
        'project': project,
        'resource': resource,
        'resource_summary': resource_summary,
        'daily_charges': daily_charges,
        'charge_totals': charge_totals
    }
