"""
Statistics and reporting query functions for SAM.

This module provides functions for generating statistical reports and
aggregated data about users, projects, and institutions.

Functions:
    get_user_statistics: Get overall user statistics
    get_project_statistics: Get overall project statistics
    get_institution_project_count: Get project count by institution
    get_user_project_access: Get all projects a user has access to with roles
"""

from datetime import datetime
from typing import List, Dict

from sqlalchemy import or_, func, desc
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.core.organizations import Institution, UserInstitution
from sam.projects.projects import Project
from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel
from sam.accounting.accounts import Account, AccountUser


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
    from sam.queries.lookups import find_user_by_username

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
