"""
Simple lookup query functions for SAM database.

This module provides basic lookup and search functions for users, projects,
groups, and resources. These are simple queries that return single objects
or small filtered lists.

Functions:
    find_user_by_username: Find a user by username
    find_users_by_name: Find users whose name contains a search string
    find_project_by_code: Find a project by its project code
    get_group_by_name: Find an ad-hoc group by name
    get_available_resources: Get list of all available resources with types
    get_resources_by_type: Get resource names for a specific type
"""

from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy.orm import Session, joinedload

from sam.core.users import User
from sam.core.groups import AdhocGroup, AdhocSystemAccountEntry
from sam.projects.projects import Project
from sam.resources.resources import Resource, ResourceType


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
    return User.get_by_username(session, username)


def find_users_by_name(session: Session, name_part: str) -> List[User]:
    """Find users whose first or last name contains the given string."""
    from sqlalchemy import or_
    search = f"%{name_part}%"
    return session.query(User).filter(
        or_(
            User.first_name.like(search),
            User.last_name.like(search)
        )
    ).all()


# ============================================================================
# Project Queries
# ============================================================================

def find_project_by_code(session: Session, projcode: str) -> Optional[Project]:
    """Find a project by its code."""
    return Project.get_by_projcode(session, projcode)


# ============================================================================
# Group Queries
# ============================================================================

def get_group_by_name(session: Session, group_name: str) -> Optional[AdhocGroup]:
    """Find a group by name."""
    return AdhocGroup.get_by_name(session, group_name)


def get_user_group_access(
    session: Session,
    username: Optional[str] = None,
    access_branch: Optional[str] = None,
    active_only: bool = True,
) -> Dict[str, List[Dict]]:
    """
    Efficient user -> adhoc group/gid mapping via AdhocSystemAccountEntry.

    Runs a single JOIN of adhoc_system_account_entry against adhoc_group, so
    cost is independent of whether the caller filters by username.

    Args:
        session: SQLAlchemy session.
        username: If provided, restrict to this username. If None, return all
            users with adhoc group memberships.
        access_branch: If provided, restrict to this access branch (e.g. 'hpc',
            'hpc-data', 'hpc-dev'). If None, all branches are included.
        active_only: If True (default), only include active AdhocGroups.

    Returns:
        Dict keyed by username, each value a list of dicts with keys
        'group_name', 'unix_gid', 'access_branch_name', sorted by
        (access_branch_name, group_name). An unknown username yields {}.
    """
    q = session.query(
        AdhocSystemAccountEntry.username,
        AdhocGroup.group_name,
        AdhocGroup.unix_gid,
        AdhocSystemAccountEntry.access_branch_name,
    ).join(AdhocGroup, AdhocSystemAccountEntry.group_id == AdhocGroup.group_id)

    if active_only:
        q = q.filter(AdhocGroup.is_active)
    if username is not None:
        q = q.filter(AdhocSystemAccountEntry.username == username)
    if access_branch is not None:
        q = q.filter(AdhocSystemAccountEntry.access_branch_name == access_branch)

    q = q.order_by(
        AdhocSystemAccountEntry.username,
        AdhocSystemAccountEntry.access_branch_name,
        AdhocGroup.group_name,
    )

    result: Dict[str, List[Dict]] = {}
    for uname, group_name, unix_gid, branch_name in q:
        result.setdefault(uname, []).append({
            'group_name': group_name,
            'unix_gid': unix_gid,
            'access_branch_name': branch_name,
        })
    return result


def get_group_members(
    session: Session,
    group_name: str,
    access_branch: str,
    active_only: bool = True,
) -> Optional[Dict]:
    """
    Fetch the members of an adhoc group within a single access branch.

    Joins AdhocSystemAccountEntry -> User by username (LEFT OUTER — the FK is
    a string and some adhoc usernames may not resolve to a `users` row).
    Eagerly loads User.email_addresses to avoid N+1 on primary_email.

    Args:
        session: SQLAlchemy session.
        group_name: AdhocGroup.group_name.
        access_branch: AdhocSystemAccountEntry.access_branch_name ('hpc',
            'hpc-data', 'hpc-dev').
        active_only: If True (default), only return active groups.

    Returns:
        {
            'group_name': str,
            'unix_gid': int,
            'access_branch_name': str,
            'members': [
                {'username': str, 'display_name': str, 'primary_email': Optional[str]},
                ...
            ],
        }
        or None if no matching group exists.
    """
    gq = session.query(AdhocGroup).filter(AdhocGroup.group_name == group_name)
    if active_only:
        gq = gq.filter(AdhocGroup.is_active)
    group = gq.first()
    if group is None:
        return None

    rows = (
        session.query(AdhocSystemAccountEntry.username, User)
        .outerjoin(User, User.username == AdhocSystemAccountEntry.username)
        .options(joinedload(User.email_addresses))
        .filter(AdhocSystemAccountEntry.group_id == group.group_id)
        .filter(AdhocSystemAccountEntry.access_branch_name == access_branch)
        .order_by(AdhocSystemAccountEntry.username)
        .all()
    )

    members: List[Dict] = []
    for uname, user in rows:
        if user is not None:
            members.append({
                'username': user.username,
                'display_name': user.display_name,
                'primary_email': user.primary_email,
            })
        else:
            members.append({
                'username': uname,
                'display_name': uname,
                'primary_email': None,
            })

    return {
        'group_name': group.group_name,
        'unix_gid': group.unix_gid,
        'access_branch_name': access_branch,
        'members': members,
    }
