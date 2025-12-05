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

from sqlalchemy.orm import Session

from sam.core.users import User
from sam.core.groups import AdhocGroup
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
