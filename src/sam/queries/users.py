"""
User query functions for SAM.

This module provides functions for searching, filtering, and retrieving
user data including email addresses, institutional affiliations, and
project membership information.

Functions:
    get_users_on_project: Get all users on a project with roles
    search_users_by_pattern: Search users by various criteria
    get_project_member_user_ids: Get user IDs of project members
    get_active_users: Get active, non-deleted users
    get_user_with_details: Get user with all relationships loaded
    get_users_by_institution: Get users from a specific institution
    get_users_by_organization: Get users in a specific organization
    search_users_by_email: Search users by email address
    get_user_emails: Get all email addresses for a user
    get_user_emails_detailed: Get detailed email information
    get_users_with_multiple_emails: Find users with multiple emails
    get_users_without_primary_email: Find users missing primary email
"""

from datetime import datetime
from typing import List, Optional, Dict, Tuple

from sqlalchemy import or_, func, desc, select, exists
from sqlalchemy.orm import Session, joinedload, selectinload

from sam.core.users import User, EmailAddress
from sam.core.organizations import Organization, Institution
from sam.projects.projects import Project
from sam.accounting.accounts import Account, AccountUser


# ============================================================================
# Project Membership Queries
# ============================================================================

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


def get_project_member_user_ids(session: Session, project_id: int) -> List[int]:
    """
    Get list of user IDs who are currently active members of a project.

    Includes lead, admin, and all users with active memberships in any account.
    Active means start_date <= now and (end_date is NULL or end_date >= now).

    Args:
        session: SQLAlchemy session
        project_id: Project ID

    Returns:
        List of user IDs
    """
    project = session.get(Project, project_id)
    if not project:
        return []

    user_ids = set()

    # Add lead and admin
    if project.project_lead_user_id:
        user_ids.add(project.project_lead_user_id)
    if project.project_admin_user_id:
        user_ids.add(project.project_admin_user_id)

    # Add all currently active account users
    account_ids = session.query(Account.account_id).filter(
        Account.project_id == project_id
    ).subquery()

    account_users = session.query(AccountUser.user_id).filter(
        AccountUser.account_id.in_(select(account_ids)),
        AccountUser.is_currently_active  # Filter by active date range
    ).distinct().all()

    for (uid,) in account_users:
        user_ids.add(uid)

    return list(user_ids)


# ============================================================================
# User Search Queries
# ============================================================================

def search_users_by_pattern(
    session: Session,
    pattern: str,
    limit: int = 50,
    exclude_user_ids: Optional[List[int]] = None,
    active_only: bool = False
) -> List[User]:
    """
    Search users by username, first name, last name, or email for autocomplete.

    Args:
        session: SQLAlchemy session
        pattern: Search pattern (will be wrapped with % for LIKE)
        limit: Maximum results to return (default 50)
        exclude_user_ids: Optional list of user IDs to exclude from results
        active_only: If True, only return active users

    Returns:
        List of User objects matching the pattern
    """
    like_pattern = f"%{pattern}%"

    # Search by username, first name, last name, or email
    # Join with email addresses to search by email too
    query = session.query(User).outerjoin(
        EmailAddress, User.user_id == EmailAddress.user_id
    ).filter(
        or_(
            User.username.ilike(like_pattern),
            User.first_name.ilike(like_pattern),
            User.last_name.ilike(like_pattern),
            EmailAddress.email_address.ilike(like_pattern)
        )
    ).distinct()

    if exclude_user_ids:
        query = query.filter(~User.user_id.in_(exclude_user_ids))

    if active_only:
        query = query.filter(User.active == True)

    return query.order_by(User.last_name, User.first_name, User.username).limit(limit).all()


def search_users_by_email(session: Session, email_part: str) -> List[User]:
    """Find users by partial email address match."""
    return session.query(User)\
        .join(User.email_addresses)\
        .filter(EmailAddress.email_address.like(f"%{email_part}%"))\
        .distinct()\
        .all()


# ============================================================================
# User Detail Queries
# ============================================================================

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


# ============================================================================
# Email Queries
# ============================================================================

def get_user_emails(session: Session, username: str) -> List[str]:
    """
    Get all email addresses for a user.
    Simple convenience function that returns just the email strings.
    """
    from sam.queries.lookups import find_user_by_username
    user = find_user_by_username(session, username)
    if user:
        return user.all_emails
    return []


def get_user_emails_detailed(session: Session, username: str) -> List[dict]:
    """
    Get detailed email information for a user.
    Returns list of dicts with email, is_primary, active, and created fields.
    """
    from sam.queries.lookups import find_user_by_username
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
