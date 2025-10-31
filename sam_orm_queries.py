"""
Example queries and operations for SAM database using SQLAlchemy ORM.

This module demonstrates common patterns for querying and managing users,
projects, allocations, and related entities.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from sqlalchemy import create_engine, and_, or_, func, desc, select
from sqlalchemy.orm import sessionmaker, Session, joinedload, selectinload
from contextlib import contextmanager

# Import ORM models (assuming they're in sam_models.py)
from sam_models import (
    Base, User, UserAlias, EmailAddress, AcademicStatus,
    Project, ProjectDirectory, Account, AccountUser,
    Allocation, AllocationTransaction, AllocationType,
    AdhocGroup, Institution, Organization, Facility, Panel,
    Resource, ResourceType, UserInstitution
)


# ============================================================================
# Database Connection Setup
# ============================================================================

def create_sam_engine(connection_string: str = None):
    """
    Create database engine and session factory.

    If connection_string is not provided, will load credentials from environment variables:
        SAM_DB_USERNAME
        SAM_DB_PASSWORD
        SAM_DB_SERVER

    Example connection_string:
        'mysql+pymysql://username:password@localhost/sam'
    """
    if connection_string is None:
        from dotenv import load_dotenv
        import os

        load_dotenv()  # Loads .env into environment variables
        username = os.environ['SAM_DB_USERNAME']
        password = os.environ['SAM_DB_PASSWORD']
        server = os.environ['SAM_DB_SERVER']
        database = 'sam'

        print(f'{username}:$SAM_DB_PASSWORD@{server}/{database}')

        # Create connection string
        connection_string = f'mysql+mysqlconnector://{username}:{password}@{server}/{database}'

    engine = create_engine(
        connection_string,
        echo=False,  # Set to True for SQL debugging
        pool_pre_ping=True,
        pool_recycle=3600
    )
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


@contextmanager
def get_session(SessionLocal):
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ============================================================================
# Helper Functions
# ============================================================================

def _get_max_allocation_subquery(resource_name: str = None):
    """
    Create a correlated subquery to find the maximum allocation_id per project.
    Optionally filter by resource name.

    Args:
        resource_name: Optional resource name to filter (e.g., 'Derecho', 'GLADE')

    Returns:
        SQLAlchemy scalar subquery for max allocation_id
    """
    Account2 = Account.__table__.alias('ac2')
    Allocation2 = Allocation.__table__.alias('a2')

    subquery = select(func.max(Allocation2.c.allocation_id))\
        .select_from(
            Allocation2.join(Account2, Allocation2.c.account_id == Account2.c.account_id)
        )\
        .where(
            and_(
                Account2.c.project_id == Account.project_id,
                Allocation2.c.deleted == False
            )
        )

    # Add resource filter if specified
    if resource_name:
        Resource2 = Resource.__table__.alias('r2')
        subquery = subquery.join(Resource2, Account2.c.resource_id == Resource2.c.resource_id)\
            .where(Resource2.c.resource_name == resource_name)

    return subquery.correlate(Account).scalar_subquery()


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
        List of dicts with keys: username, full_name, email, role
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
        'full_name': lead.full_name,
        'email': lead.primary_email,
        'role': 'Lead'
    }

    # Add project admin if exists and different from lead
    if project.admin and project.project_admin_user_id != project.project_lead_user_id:
        admin = project.admin
        users_dict[admin.user_id] = {
            'username': admin.username,
            'full_name': admin.full_name,
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
                'full_name': member.full_name,
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

def get_projects_expiring_soon(
    session: Session,
    days: int = 30,
    facility_names: List[str] = None,
    resource_name: str = None
) -> List[Tuple[Project, Allocation, str, int]]:
    """
    Get projects with allocations expiring within specified days.
    Only considers the most recent allocation per project (per resource if specified).

    Args:
        session: SQLAlchemy session
        days: Number of days in the future to check
        facility_names: Optional list of facility names to filter
        resource_name: Optional resource name to filter (e.g., 'Derecho', 'GLADE')

    Returns:
        List of (Project, Allocation, resource_name, days_remaining) tuples
    """
    cutoff_date = datetime.now() + timedelta(days=days)
    max_alloc_subquery = _get_max_allocation_subquery(resource_name)

    # Main query
    query = session.query(Project, Allocation, Resource.resource_name)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .filter(
            Project.active == True,
            Allocation.deleted == False,
            Allocation.end_date.isnot(None),
            Allocation.end_date.between(datetime.now(), cutoff_date),
            Allocation.allocation_id == max_alloc_subquery
        )

    if facility_names:
        query = query\
            .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
            .join(Panel, AllocationType.panel_id == Panel.panel_id)\
            .join(Facility, Panel.facility_id == Facility.facility_id)\
            .filter(Facility.facility_name.in_(facility_names))

    if resource_name:
        query = query.filter(Resource.resource_name == resource_name)

    # Calculate days remaining for each
    results = []
    for project, allocation, res_name in query.all():
        days_remaining = (allocation.end_date - datetime.now()).days
        results.append((project, allocation, res_name, days_remaining))

    return sorted(results, key=lambda x: x[3])



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
    from sam_models import AdhocGroupTag
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
# Example Usage
# ============================================================================

def example_usage():
    """Demonstrate usage of the query functions."""

    # Setup - will load from .env file
    engine, SessionLocal = create_sam_engine()

    with get_session(SessionLocal) as session:
        # Find a user
        user = find_user_by_username(session, 'benkirk')
        if user:
            print(f"Found user: {user.full_name}")
            print(f"Primary GID: {user.primary_gid}")
            print(f"Primary email: {user.primary_email}")
            print(f"All emails: {', '.join(user.all_emails)}")

            # Get detailed email info
            print("\nDetailed email information:")
            for email_info in user.get_emails_detailed():
                primary_marker = " (PRIMARY)" if email_info['is_primary'] else ""
                active_marker = "" if email_info['active'] else " (INACTIVE)"
                print(f"  - {email_info['email']}{primary_marker}{active_marker}")

            # Find projects
            for p in user.all_projects:
                label = "" if p.active else "** INACTIVE **"
                print(p,label)

        # Find users with multiple emails
        multi_email_users = get_users_with_multiple_emails(session, min_emails=2)
        print(f"\n--- Users with multiple emails ---: {len(multi_email_users)}")
        for user, count in multi_email_users[:5]:
            print(f"{user.username} ({user.full_name}): {count} emails")
            for email in user.all_emails:
                print(f"  - {email}")

        # Find users without primary email set
        print("\n--- Users without primary email ---")
        no_primary = get_users_without_primary_email(session)
        print(f"Found {len(no_primary)} active users without a primary email")
        for user in no_primary[:5]:
            print(f"  {user.username}: {', '.join(user.all_emails)}")

        # Get project details
        project = get_project_with_full_details(session, 'SCSG0001')
        if project:
            print(f"\n--- Project Details ---")
            print(f"Project: {project.projcode}")
            print(f"Title: {project.title}")
            print(f"Lead: {project.lead.full_name}")

            # Show allocations by resource
            print(f"\nAllocations by resource:")
            allocs_by_resource = project.get_all_allocations_by_resource()
            for resource_name, alloc in allocs_by_resource.items():
                print(f"  {resource_name}: {alloc.amount:,.2f} (expires {alloc.end_date})")

            # Show allocations for specific resource
            print(f"\nSpecific Allocation by resource:")
            resource_name = "Derecho"
            alloc = project.get_allocation_by_resource(resource_name)
            print(f"  {resource_name}: {alloc.amount:,.2f} (expires {alloc.end_date})")

            # Show users on project
            usernames = [u.username for u in project.users]
            print(f"Users ({len(usernames)}):");
            for u in usernames: print(u);

        # Show available resources
        print("\n--- Available Resources ---")
        resources = get_available_resources(session)
        for res in resources:
            status = "ACTIVE" if res['active'] else "DECOMMISSIONED"
            print(f"  {res['resource_name']:20} ({res['resource_type']:10}) {status}")

        # Get expiring projects (simple) - all resources
        print("\n--- Projects Expiring Soon (30 days, all resources) ---")
        expiring = get_projects_expiring_soon(session, days=30)
        print(f"Found {len(expiring)} allocations expiring")
        for proj, alloc, res_name, days in expiring[:5]:
            print(f"  {proj.projcode} ({res_name}): {days} days remaining")

        # Get expiring projects for specific resource
        print("\n--- Derecho Allocations Expiring Soon (30 days) ---")
        expiring_derecho = get_projects_expiring_soon(session, days=30, resource_name='Derecho')
        print(f"Found {len(expiring_derecho)} Derecho allocations expiring")
        for proj, alloc, res_name, days in expiring_derecho[:5]:
            print(f"  {proj.projcode}: {days} days remaining (expires {alloc.end_date.date()})")

        # Statistics
        print("\n--- User Statistics ---")
        stats = get_user_statistics(session)
        for key, value in stats.items():
            print(f"  {key}: {value}")

        print("\n--- Project Statistics ---")
        proj_stats = get_project_statistics(session)
        print(f"  Total: {proj_stats['total_projects']}")
        print(f"  Active: {proj_stats['active_projects']}")
        print(f"  By Facility:")
        for facility, count in proj_stats['by_facility'].items():
            print(f"    {facility}: {count}")

        # Get user's project access
        if user:
            print(f"\n--- Project Access for {user.username} ---")
            access = get_user_project_access(session, user.username)
            print(f"Total projects: {len(access)}")
            s=set()
            for item in access:
                print(f"  {item['projcode']}: {item['role']}")
                s.add(item['projcode'])
            print(len(user.projects), len(s))

        # Test get_users_on_project
        print("\n--- Users on Project SCSG0001 ---")
        project_users = get_users_on_project(session, 'SCSG0001')
        for user_info in project_users:
            print(f"  {user_info['role']:8} {user_info['username']:12} {user_info['full_name']:30} <{user_info['email']}>")


if __name__ == '__main__':
    example_usage()
