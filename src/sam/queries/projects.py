"""
Project search and filtering query functions for SAM.

This module provides functions for searching, filtering, and retrieving
project data with various criteria and relationship loading strategies.

Functions:
    search_projects_by_code_or_title: Search projects by code or title
    get_active_projects: Get all active projects, optionally by facility
    project_facilities: Map many projcodes to their facility in one query
    search_projects_by_title: Search projects by title only
    get_projects_by_lead: Get projects led by a specific user
    get_project_with_full_details: Get project with all relationships loaded
    get_project_members: Get all users with access to a project
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import or_

from sam.sqlcompat import ci_like
from sqlalchemy.orm import Session, joinedload

from sam.core.users import User
from sam.projects.projects import Project
from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel
from sam.accounting.accounts import Account, AccountUser


# ============================================================================
# Project Search Queries
# ============================================================================

def search_projects_by_code_or_title(
    session: Session,
    search_term: str,
    active: Optional[bool] = None,
    facility_names: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Project]:
    """Search projects by project code or title, optionally filtered by
    active status and/or a facility allowlist.

    ``facility_names`` — when supplied, results are restricted to
    projects whose ``allocation_type -> panel -> facility`` chain resolves
    to one of the listed facility names. Projects with a broken chain
    (orphans) are excluded, matching the facility-scoped RBAC rule
    that only unscoped system holders may reach orphan projects.
    """
    like_search_term = f"%{search_term}%"
    query = session.query(Project)\
        .filter(
            or_(
                ci_like(Project.projcode, like_search_term),
                ci_like(Project.title, like_search_term)
            )
        )
    if active is not None:
        query = query.filter(Project.active == active)
    if facility_names:
        query = query\
            .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
            .join(Panel, AllocationType.panel_id == Panel.panel_id)\
            .join(Facility, Panel.facility_id == Facility.facility_id)\
            .filter(Facility.facility_name.in_(facility_names))
    return query.limit(limit).all()


def search_projects_by_title(session: Session, search_term: str) -> List[Project]:
    """Search projects by title."""
    return session.query(Project)\
        .filter(ci_like(Project.title, f"%{search_term}%"))\
        .all()


def get_active_projects(session: Session, facility_name: str = None) -> List[Project]:
    """Get all active projects, optionally filtered by facility."""
    query = session.query(Project)\
        .filter(Project.is_active)

    if facility_name:
        query = query\
            .join(AllocationType)\
            .join(Panel)\
            .join(Facility)\
            .filter(Facility.facility_name == facility_name)

    return query.all()


def project_panels(session: Session,
                   projcodes: Iterable[str]) -> Dict[str, Tuple[int, str, str]]:
    """``{projcode: (facility_id, facility_name, panel_name)}``; projects with no panel are absent."""
    codes = sorted({c for c in projcodes if c})
    if not codes:
        return {}
    rows = session.query(Project.projcode, Facility.facility_id, Facility.facility_name,
                         Panel.panel_name)\
        .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
        .join(Panel, AllocationType.panel_id == Panel.panel_id)\
        .join(Facility, Panel.facility_id == Facility.facility_id)\
        .filter(Project.projcode.in_(codes))\
        .all()
    return {code: (fid, name, panel) for code, fid, name, panel in rows}


def project_facilities(session: Session,
                       projcodes: Iterable[str]) -> Dict[str, Tuple[int, str]]:
    """``{projcode: (facility_id, facility_name)}``; projects with no facility are absent."""
    return {code: (fid, name) for code, (fid, name, _) in project_panels(session, projcodes).items()}


def project_titles(session: Session, projcodes: Iterable[str]) -> Dict[str, str]:
    """``{projcode: title}`` in one query; unknown codes are absent."""
    codes = sorted({c for c in projcodes if c})
    if not codes:
        return {}
    return dict(session.query(Project.projcode, Project.title).filter(Project.projcode.in_(codes)).all())


def get_projects_by_lead(session: Session, username: str) -> List[Project]:
    """Get all projects led by a specific user."""
    return session.query(Project)\
        .join(User, Project.project_lead_user_id == User.user_id)\
        .filter(User.username == username)\
        .filter(Project.is_active)\
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
