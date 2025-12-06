"""
Project search and filtering query functions for SAM.

This module provides functions for searching, filtering, and retrieving
project data with various criteria and relationship loading strategies.

Functions:
    search_projects_by_code_or_title: Search projects by code or title
    get_active_projects: Get all active projects, optionally by facility
    search_projects_by_title: Search projects by title only
    get_projects_by_lead: Get projects led by a specific user
    get_project_with_full_details: Get project with all relationships loaded
    get_project_members: Get all users with access to a project
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from sam.core.users import User
from sam.projects.projects import Project
from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel
from sam.accounting.accounts import Account, AccountUser


# ============================================================================
# Project Search Queries
# ============================================================================

def search_projects_by_code_or_title(session: Session, search_term: str, active: Optional[bool] = None) -> List[Project]:
    """Search projects by project code or title, optionally filtered by active status."""
    # Ensure search_term is case-insensitive for projcode and title
    like_search_term = f"%{search_term}%"
    query = session.query(Project)\
        .filter(
            or_(
                Project.projcode.ilike(like_search_term),
                Project.title.ilike(like_search_term)
            )
        )
    if active is not None:
        query = query.filter(Project.active == active)
    return query.all()


def search_projects_by_title(session: Session, search_term: str) -> List[Project]:
    """Search projects by title."""
    return session.query(Project)\
        .filter(Project.title.ilike(f"%{search_term}%"))\
        .all()


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


# ============================================================================
# Project Detail Queries
# ============================================================================

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
