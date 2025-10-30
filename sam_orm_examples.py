"""
Example queries and operations for SAM database using SQLAlchemy ORM.

This module demonstrates common patterns for querying and managing users,
projects, allocations, and related entities.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from sqlalchemy import create_engine, and_, or_, func, desc
from sqlalchemy.orm import sessionmaker, Session, joinedload, selectinload
from contextlib import contextmanager

# Import ORM models (assuming they're in sam_models.py)
from sam_models import (
    Base, User, UserAlias, EmailAddress, AcademicStatus,
    Project, ProjectDirectory, Account, AccountUser,
    Allocation, AllocationTransaction, AllocationType,
    AdhocGroup, Institution, Organization, Facility, Panel
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


def get_projects_expiring_soon(
    session: Session,
    days: int = 30,
    facility_names: List[str] = None
) -> List[Tuple[Project, Allocation, int]]:
    """
    Get projects with allocations expiring within specified days.
    Only considers the most recent allocation per project.
    Returns list of (Project, Allocation, days_remaining) tuples.
    """
    from sqlalchemy import select

    cutoff_date = datetime.now() + timedelta(days=days)

    # Create correlated subquery to find max allocation_id per project
    # This is equivalent to the SQL subquery in your WHERE clause
    Account2 = Account.__table__.alias('ac2')
    Allocation2 = Allocation.__table__.alias('a2')

    max_alloc_subquery = select(func.max(Allocation2.c.allocation_id))\
        .select_from(
            Allocation2.join(Account2, Allocation2.c.account_id == Account2.c.account_id)
        )\
        .where(
            and_(
                Account2.c.project_id == Account.project_id,
                Allocation2.c.deleted == False
            )
        )\
        .correlate(Account)\
        .scalar_subquery()

    # Main query
    query = session.query(Project, Allocation)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .filter(
            Project.active == True,
            Allocation.deleted == False,
            Allocation.end_date.isnot(None),
            Allocation.end_date.between(datetime.now(), cutoff_date),
            Allocation.allocation_id == max_alloc_subquery  # Only most recent allocation
        )

    if facility_names:
        query = query\
            .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
            .join(Panel, AllocationType.panel_id == Panel.panel_id)\
            .join(Facility, Panel.facility_id == Facility.facility_id)\
            .filter(Facility.facility_name.in_(facility_names))

    # Calculate days remaining for each
    results = []
    for project, allocation in query.all():
        days_remaining = (allocation.end_date - datetime.now()).days
        results.append((project, allocation, days_remaining))

    return sorted(results, key=lambda x: x[2])


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


def search_projects_by_title(session: Session, search_term: str) -> List[Project]:
    """Search projects by title."""
    return session.query(Project)\
        .filter(Project.title.like(f"%{search_term}%"))\
        .filter(Project.active == True)\
        .all()


# ============================================================================
# Allocation Queries
# ============================================================================

def get_project_allocations(session: Session, projcode: str) -> List[Allocation]:
    """Get all allocations for a project."""
    return session.query(Allocation)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .filter(Project.projcode == projcode)\
        .filter(Allocation.deleted == False)\
        .order_by(Allocation.start_date.desc())\
        .all()


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


def find_projects_needing_renewal(
    session: Session,
    days_before_expiry: int = 60
) -> List[Dict]:
    """
    Find projects that should be considered for renewal.
    Returns projects with most recent allocation expiring soon.
    """
    from sqlalchemy import select

    cutoff = datetime.now() + timedelta(days=days_before_expiry)

    # Correlated subquery for max allocation_id
    Account2 = Account.__table__.alias('ac2')
    Allocation2 = Allocation.__table__.alias('a2')

    max_alloc_subquery = select(func.max(Allocation2.c.allocation_id))\
        .select_from(
            Allocation2.join(Account2, Allocation2.c.account_id == Account2.c.account_id)
        )\
        .where(
            and_(
                Account2.c.project_id == Account.project_id,
                Allocation2.c.deleted == False
            )
        )\
        .correlate(Account)\
        .scalar_subquery()

    results = session.query(Project, Allocation, User)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(User, Project.project_lead_user_id == User.user_id)\
        .options(
            joinedload(Project.allocation_type).joinedload(AllocationType.panel),
            selectinload(User.email_addresses)
        )\
        .filter(
            Project.active == True,
            Allocation.deleted == False,
            Allocation.end_date.isnot(None),
            Allocation.end_date <= cutoff,
            Allocation.end_date >= datetime.now(),
            Allocation.allocation_id == max_alloc_subquery
        )\
        .order_by(Allocation.end_date)\
        .all()

    renewal_list = []
    for proj, alloc, lead in results:
        renewal_list.append({
            'projcode': proj.projcode,
            'title': proj.title,
            'lead_name': lead.full_name,
            'lead_email': lead.primary_email,
            'allocation_amount': alloc.amount,
            'start_date': alloc.start_date,
            'end_date': alloc.end_date,
            'days_remaining': (alloc.end_date - datetime.now()).days,
            'allocation_type': proj.allocation_type.allocation_type if proj.allocation_type else None
        })

    return renewal_list


# ============================================================================
# Example Usage
# ============================================================================

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


def get_projects_expiring_soon_alternative(
    session: Session,
    days: int = 30,
    facility_names: List[str] = None
) -> List[Tuple[Project, Allocation, int]]:
    """
    Alternative approach using window functions (MySQL 8.0+).
    More efficient for large datasets.
    """
    from sqlalchemy import select, literal_column

    cutoff_date = datetime.now() + timedelta(days=days)

    # Use a CTE with ROW_NUMBER() to get latest allocation per project
    # This requires MySQL 8.0+ for window function support
    latest_allocs = select(
        Allocation.allocation_id,
        Allocation.account_id,
        Allocation.amount,
        Allocation.start_date,
        Allocation.end_date,
        Account.project_id,
        func.row_number().over(
            partition_by=Account.project_id,
            order_by=Allocation.allocation_id.desc()
        ).label('rn')
    ).select_from(
        Allocation.join(Account, Allocation.account_id == Account.account_id)
    ).where(
        Allocation.deleted == False
    ).cte('latest_allocs')

    # Main query using the CTE
    query = session.query(Project, Allocation)\
        .join(latest_allocs, Project.project_id == latest_allocs.c.project_id)\
        .join(Allocation, Allocation.allocation_id == latest_allocs.c.allocation_id)\
        .filter(
            Project.active == True,
            latest_allocs.c.rn == 1,  # Only the most recent
            latest_allocs.c.end_date.isnot(None),
            latest_allocs.c.end_date.between(datetime.now(), cutoff_date)
        )

    if facility_names:
        query = query\
            .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
            .join(Panel, AllocationType.panel_id == Panel.panel_id)\
            .join(Facility, Panel.facility_id == Facility.facility_id)\
            .filter(Facility.facility_name.in_(facility_names))

    results = []
    for project, allocation in query.all():
        days_remaining = (allocation.end_date - datetime.now()).days
        results.append((project, allocation, days_remaining))

    return sorted(results, key=lambda x: x[2])


def example_usage():
    """Demonstrate usage of the query functions."""

    # Setup
    engine, SessionLocal = create_sam_engine()

    with get_session(SessionLocal) as session:
        # Find a user
        user = find_user_by_username(session, 'benkirk')
        if user:
            print(f"Found user: {user.full_name}")
            print(f"Primary email: {user.primary_email}")

        # Get project details
        project = get_project_with_full_details(session, 'UCUB0001')
        if project:
            print(f"\nProject: {project.projcode}")
            print(f"Title: {project.title}")
            print(f"Lead: {project.lead.full_name}")

            alloc = project.current_allocation
            if alloc:
                print(f"Current allocation: {alloc.amount}")
                print(f"Expires: {alloc.end_date}")

        # Get expiring projects
        expiring = get_projects_expiring_soon(session, days=30)
        print(f"\nProjects expiring in 30 days: {len(expiring)}")
        for proj, alloc, days in expiring[:5]:
            print(f"  {proj.projcode}: {days} days remaining")

        # Statistics
        stats = get_user_statistics(session)
        print(f"\nUser Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

        # Get user's project access
        access = get_user_project_access(session, 'benkirk')
        print(f"\nProject access for benkirk:")
        for item in access:
            print(f"  {item['projcode']}: {item['role']}")


if __name__ == '__main__':
    example_usage()
