"""
Dashboard data aggregation queries for SAM.

This module provides functions for building dashboard views that aggregate
project and user data with allocation usage details. These queries are
optimized for server-side rendering with minimal database round-trips.

Functions:
    get_user_dashboard_data: Get all dashboard data for a user
    get_project_dashboard_data: Get dashboard data for a single project
    get_resource_detail_data: Get resource usage details for charts
"""

from datetime import datetime
from typing import List, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from sam.core.users import User
from sam.core.organizations import Organization, ProjectOrganization
from sam.projects.projects import Project
from sam.projects.contracts import Contract, ContractSource, ProjectContract
from sam.accounting.accounts import Account, AccountUser
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource
from sam.resources.facilities import Facility, Panel
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


# ============================================================================
# Dashboard Query Helpers
# ============================================================================

def _build_project_resources_data(project: Project) -> List[Dict]:
    """
    Helper function to build resource usage data for a project.

    Extracts resource allocation and usage information from a project's
    detailed allocation usage data.

    Args:
        project: Project object

    Returns:
        List of resource dictionaries with usage details
    """
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

    return resources


def get_project_dashboard_data(session: Session, projcode: str) -> Optional[Dict]:
    """
    Get dashboard data for a single project.

    Used for admin project search functionality and as a helper for
    get_user_dashboard_data().

    Args:
        session: SQLAlchemy session
        projcode: Project code to fetch data for

    Returns:
        Dictionary with structure:
        {
            'project': Project object,
            'resources': List[Dict],  # From get_detailed_allocation_usage()
            'has_children': bool
        }
        Returns None if project not found.

    Example:
        >>> data = get_project_dashboard_data(session, 'SCSG0001')
        >>> if data:
        ...     proj = data['project']
        ...     print(f"{proj.projcode}: {len(data['resources'])} resources")
    """
    # Get project with relationships eagerly loaded
    project = session.query(Project)\
        .options(
            joinedload(Project.lead),
            joinedload(Project.admin),
            joinedload(Project.allocation_type).joinedload(AllocationType.panel).joinedload(Panel.facility),
            joinedload(Project.area_of_interest),
            selectinload(Project.contracts).joinedload(ProjectContract.contract).joinedload(Contract.contract_source),
            selectinload(Project.organizations).joinedload(ProjectOrganization.organization),
            selectinload(Project.directories)
        )\
        .filter(Project.projcode == projcode)\
        .first()

    if not project:
        return None

    return {
        'project': project,
        'resources': _build_project_resources_data(project),
        'has_children': project.has_children if hasattr(project, 'has_children') else False
    }


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
            joinedload(User.led_projects).joinedload(Project.allocation_type).joinedload(AllocationType.panel).joinedload(Panel.facility),
            joinedload(User.led_projects).joinedload(Project.area_of_interest),
            joinedload(User.led_projects).selectinload(Project.contracts).joinedload(ProjectContract.contract).joinedload(Contract.contract_source),
            joinedload(User.led_projects).selectinload(Project.organizations).joinedload(ProjectOrganization.organization),
            joinedload(User.led_projects).selectinload(Project.directories),

            joinedload(User.admin_projects).joinedload(Project.admin),
            joinedload(User.admin_projects).joinedload(Project.allocation_type).joinedload(AllocationType.panel).joinedload(Panel.facility),
            joinedload(User.admin_projects).joinedload(Project.area_of_interest),
            joinedload(User.admin_projects).selectinload(Project.contracts).joinedload(ProjectContract.contract).joinedload(Contract.contract_source),
            joinedload(User.admin_projects).selectinload(Project.organizations).joinedload(ProjectOrganization.organization),
            joinedload(User.admin_projects).selectinload(Project.directories),

            # Optimize the active_projects path (via accounts)
            selectinload(User.accounts)
                .joinedload(AccountUser.account)
                .joinedload(Account.project)
                .joinedload(Project.allocation_type)
                .joinedload(AllocationType.panel)
                .joinedload(Panel.facility),
            selectinload(User.accounts)
                .joinedload(AccountUser.account)
                .joinedload(Account.project)
                .joinedload(Project.area_of_interest),
            selectinload(User.accounts)
                .joinedload(AccountUser.account)
                .joinedload(Account.project)
                .selectinload(Project.contracts)
                .joinedload(ProjectContract.contract)
                .joinedload(Contract.contract_source),
            selectinload(User.accounts)
                .joinedload(AccountUser.account)
                .joinedload(Account.project)
                .selectinload(Project.organizations)
                .joinedload(ProjectOrganization.organization),
            selectinload(User.accounts)
                .joinedload(AccountUser.account)
                .joinedload(Account.project)
                .selectinload(Project.directories)
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

    # Build project data using helper function to avoid code duplication
    project_data_list = []
    for project in projects:
        project_data_list.append({
            'project': project,
            'resources': _build_project_resources_data(project),
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
            'daily_charges': {
                  'dates': [],
                  'values': [],
            },
        }
        Returns None if project or resource not found.
    """
    # Find project
    project = Project.get_by_projcode(session, projcode)
    if not project:
        return None

    # Find resource
    resource = Resource.get_by_name(session, resource_name)
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
    account = Account.get_by_project_and_resource(
        session,
        project.project_id,
        resource.resource_id,
        exclude_deleted=True
    )

    if not account:
        return {
            'project': project,
            'resource': resource,
            'resource_summary': resource_summary,
            'daily_charges': { 'dates': None, 'values': None }
        }

    # Determine resource type to query appropriate tables
    resource_type = resource.resource_type.resource_type if resource.resource_type else 'HPC'

    results = None

    # Query appropriate charges
    if resource_type in  [ 'HPC', 'DAV' ]:
        results = session.query(
            CompChargeSummary.activity_date,
            func.sum(CompChargeSummary.charges).label('charges')
        ).filter(
            CompChargeSummary.account_id == account.account_id,
            CompChargeSummary.activity_date >= start_date,
            CompChargeSummary.activity_date <= end_date
        ).group_by(CompChargeSummary.activity_date).all()

    # Query disk charges
    if resource_type == 'DISK':
        results = session.query(
            DiskChargeSummary.activity_date,
            func.sum(DiskChargeSummary.charges).label('charges')
        ).filter(
            DiskChargeSummary.account_id == account.account_id,
            DiskChargeSummary.activity_date >= start_date,
            DiskChargeSummary.activity_date <= end_date
        ).group_by(DiskChargeSummary.activity_date).all()

    # Query archive charges
    if resource_type == 'ARCHIVE':
        results = session.query(
            ArchiveChargeSummary.activity_date,
            func.sum(ArchiveChargeSummary.charges).label('charges')
        ).filter(
            ArchiveChargeSummary.account_id == account.account_id,
            ArchiveChargeSummary.activity_date >= start_date,
            ArchiveChargeSummary.activity_date <= end_date
        ).group_by(ArchiveChargeSummary.activity_date).all()

    dates = []
    values = []
    for row in results:
        dates.append( row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date )
        values.append( float(row.charges or 0.0) )

    daily_charges = { 'dates': dates, 'values': values }

    return {
        'project': project,
        'resource': resource,
        'resource_summary': resource_summary,
        'daily_charges': daily_charges,
    }
