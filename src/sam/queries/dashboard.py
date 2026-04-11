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
from sam.queries.charges import get_adjustment_totals_by_date
from sam.queries.rolling_usage import get_project_rolling_usage


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

    now = datetime.now()

    # Fetch rolling window usage (30d/90d) for all HPC/DAV resources in one call
    rolling_usage = get_project_rolling_usage(project.session, project.projcode)

    for resource_name, usage in usage_data.items():
        start_date = usage.get('start_date')
        end_date = usage.get('end_date')

        # Calculate days until expiration
        days_until_expiration = None
        if end_date:
            days_until_expiration = (end_date - now).days

        # Sortable group key for grouping resources with identical date bounds
        start_str = start_date.strftime('%Y-%m-%d') if start_date else '0000-00-00'
        end_str   = end_date.strftime('%Y-%m-%d')   if end_date   else 'open'
        date_group_key = f"{start_str}_{end_str}"

        # Timeline progress (mirrors allocations dashboard project_table.html logic)
        if not start_date:
            elapsed_pct = 0
            bar_state   = 'no-dates'
        elif not end_date:
            elapsed_pct = 50
            bar_state   = 'open-ended'
        elif end_date < now:
            elapsed_pct = 100
            bar_state   = 'expired'
        else:
            duration_days = (end_date - start_date).days
            if duration_days > 0:
                elapsed_pct = max(0.0, min(100.0, round((now - start_date).days / duration_days * 100, 1)))
                bar_state   = 'active'
            else:
                elapsed_pct = 0
                bar_state   = 'no-duration'

        rwin = rolling_usage.get(resource_name, {}).get('windows', {})
        resources.append({
            'resource_name': resource_name,
            'allocation_id': usage.get('allocation_id'),  # Required for edit functionality
            'parent_allocation_id': usage.get('parent_allocation_id'),
            'is_inheriting': usage.get('is_inheriting', False),
            'account_id': usage.get('account_id'),  # Required for permission checks
            'allocated': usage.get('allocated', 0.0),
            'used': usage.get('used', 0.0),
            'remaining': usage.get('remaining', 0.0),
            'percent_used': usage.get('percent_used', 0.0),
            'charges_by_type': usage.get('charges_by_type', {}),
            'adjustments': usage.get('adjustments', 0.0),
            'status': usage.get('status', 'Unknown'),
            'start_date': start_date,
            'end_date': end_date,
            'days_until_expiration': days_until_expiration,
            'date_group_key': date_group_key,
            'elapsed_pct': elapsed_pct,
            'bar_state': bar_state,
            'resource_type': usage.get('resource_type', 'HPC'),
            'rolling_30': rwin.get(30),
            'rolling_90': rwin.get(90),
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

    # Get active projects, sorted by project code for consistent display order
    projects = sorted(user.active_projects(), key=lambda p: p.projcode)

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
    end_date: datetime,
    include_adjustments: bool = True,
    scope_projcode: Optional[str] = None,
) -> Optional[Dict]:
    """
    Get resource usage details for charts and summary display.

    Fetches allocation summary and daily charge breakdown for a specific
    resource on a project within a date range.

    Args:
        session: SQLAlchemy session
        projcode: Root project code (determines allocation / Resource Summary card)
        resource_name: Resource name (e.g., 'Derecho', 'GLADE')
        start_date: Start of date range
        end_date: End of date range
        include_adjustments: If True (default), include manual charge adjustments
                             in both the resource_summary and daily_charges data.
        scope_projcode: Optional project code for scoping daily charges. When
                        provided (and different from projcode), the daily charge
                        trend uses this project's MPTT subtree. When the scope
                        project has children, subtree aggregation is used; when
                        it is a leaf, only its direct charges are included.
                        Defaults to projcode (root — include all descendants).

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
        include_adjustments=include_adjustments
    )

    resource_summary = all_usage.get(resource_name)
    if not resource_summary:
        # No allocation for this resource
        resource_summary = {
            'resource_name': resource_name,
            'allocation_id': None,  # No allocation exists
            'account_id': None,
            'allocated': 0.0,
            'used': 0.0,
            'remaining': 0.0,
            'percent_used': 0.0,
            'charges_by_type': {},
            'start_date': None,
            'end_date': None,
            'status': 'No Allocation'
        }

    # Determine resource type to query appropriate tables
    resource_type = resource.resource_type.resource_type if resource.resource_type else 'HPC'

    # Resolve the scope project (controls daily charge aggregation)
    if scope_projcode and scope_projcode != projcode:
        scope_proj = Project.get_by_projcode(session, scope_projcode)
        if not scope_proj:
            scope_proj = project
    else:
        scope_proj = project  # default: root project = include all descendants

    # Use subtree MPPT when the scope project has children and valid tree coords
    use_subtree = bool(
        scope_proj.has_children
        and scope_proj.tree_root
        and scope_proj.tree_left
        and scope_proj.tree_right
    )

    if use_subtree:
        # Use MPPT join pattern (same as Project.get_subtree_charges) to aggregate
        # daily charges across this project and all descendants.
        results = None
        if resource_type in ('HPC', 'DAV'):
            results = session.query(
                CompChargeSummary.activity_date,
                func.sum(CompChargeSummary.charges).label('charges')
            ).join(Account, CompChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date,
            ).group_by(CompChargeSummary.activity_date).all()

        elif resource_type == 'DISK':
            results = session.query(
                DiskChargeSummary.activity_date,
                func.sum(DiskChargeSummary.charges).label('charges')
            ).join(Account, DiskChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date,
            ).group_by(DiskChargeSummary.activity_date).all()

        elif resource_type == 'ARCHIVE':
            results = session.query(
                ArchiveChargeSummary.activity_date,
                func.sum(ArchiveChargeSummary.charges).label('charges')
            ).join(Account, ArchiveChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                ArchiveChargeSummary.activity_date >= start_date,
                ArchiveChargeSummary.activity_date <= end_date,
            ).group_by(ArchiveChargeSummary.activity_date).all()

        daily_map = {}
        if results:
            for row in results:
                d = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
                daily_map[d] = daily_map.get(d, 0.0) + float(row.charges or 0.0)

        if include_adjustments:
            # Collect all subtree account IDs for adjustment lookup
            subtree_account_ids = [
                row.account_id for row in
                session.query(Account.account_id)
                .join(Project, Account.project_id == Project.project_id)
                .filter(
                    Project.tree_root == scope_proj.tree_root,
                    Project.tree_left  >= scope_proj.tree_left,
                    Project.tree_right <= scope_proj.tree_right,
                    Account.resource_id == resource.resource_id,
                    Account.deleted == False,
                ).all()
            ]
            if subtree_account_ids:
                for d, amount in get_adjustment_totals_by_date(
                    session, subtree_account_ids, start_date, end_date
                ).items():
                    daily_map[d] = daily_map.get(d, 0.0) + amount

    else:
        # Single-account path: use the scope project's account (may differ from root)
        account = Account.get_by_project_and_resource(
            session,
            scope_proj.project_id,
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

        results = None

        if resource_type in ('HPC', 'DAV'):
            results = session.query(
                CompChargeSummary.activity_date,
                func.sum(CompChargeSummary.charges).label('charges')
            ).filter(
                CompChargeSummary.account_id == account.account_id,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date
            ).group_by(CompChargeSummary.activity_date).all()

        elif resource_type == 'DISK':
            results = session.query(
                DiskChargeSummary.activity_date,
                func.sum(DiskChargeSummary.charges).label('charges')
            ).filter(
                DiskChargeSummary.account_id == account.account_id,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date
            ).group_by(DiskChargeSummary.activity_date).all()

        elif resource_type == 'ARCHIVE':
            results = session.query(
                ArchiveChargeSummary.activity_date,
                func.sum(ArchiveChargeSummary.charges).label('charges')
            ).filter(
                ArchiveChargeSummary.account_id == account.account_id,
                ArchiveChargeSummary.activity_date >= start_date,
                ArchiveChargeSummary.activity_date <= end_date
            ).group_by(ArchiveChargeSummary.activity_date).all()

        daily_map = {}
        if results:
            for row in results:
                d = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
                daily_map[d] = daily_map.get(d, 0.0) + float(row.charges or 0.0)

        if include_adjustments:
            for d, amount in get_adjustment_totals_by_date(
                session, [account.account_id], start_date, end_date
            ).items():
                daily_map[d] = daily_map.get(d, 0.0) + amount

    sorted_dates = sorted(daily_map.keys())
    daily_charges = { 'dates': sorted_dates, 'values': [daily_map[d] for d in sorted_dates] }

    return {
        'project': project,
        'resource': resource,
        'resource_summary': resource_summary,
        'daily_charges': daily_charges,
    }
