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
from sam.enums import ResourceTypeName
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

def _build_project_resources_data(project: Project,
                                   active_at: Optional[datetime] = None) -> List[Dict]:
    """
    Helper function to build resource usage data for a project.

    Extracts resource allocation and usage information from a project's
    detailed allocation usage data.

    Args:
        project: Project object
        active_at: Reference datetime for determining which allocation is "active".
                   Defaults to now.

    Returns:
        List of resource dictionaries with usage details
    """
    resources = []
    usage_data = project.get_detailed_allocation_usage(include_adjustments=True,
                                                        active_at=active_at)

    now = active_at or datetime.now()

    # Fetch rolling window usage (30d/90d) only when at least one account on
    # this project has a non-null threshold set. The dashboard template only
    # displays the rolling-usage block when threshold_pct is not None
    # (project_card.html:182-197), so for the vast majority of projects (no
    # thresholds set) we'd be fetching data that is never rendered. Skipping
    # avoids the per-project query.
    needs_rolling = any(
        (acct.first_threshold is not None or acct.second_threshold is not None)
        for acct in project.accounts
        if not acct.deleted
    )
    rolling_usage = (
        get_project_rolling_usage(project.session, project.projcode)
        if needs_rolling else {}
    )

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


def _select_query_alloc(account: Account, now: datetime):
    """
    Pick the allocation to display for an account, mirroring the logic in
    Project.get_detailed_allocation_usage(): prefer the active allocation,
    otherwise fall back to the most recent one if it expired within 90 days.

    Returns the chosen Allocation or None.
    """
    for alloc in account.allocations:
        if alloc.is_active_at(now):
            return alloc
    if account.allocations:
        most_recent = max(
            account.allocations,
            key=lambda a: a.end_date if a.end_date else datetime.max,
        )
        end = most_recent.end_date
        if end is None or (now - end).days <= 90:
            return most_recent
    return None


def _build_user_projects_resources_batched(
    session: Session,
    projects: List[Project],
    active_at: Optional[datetime] = None,
) -> Dict[int, List[Dict]]:
    """
    Batched equivalent of looping _build_project_resources_data() over a list of
    projects. Collects allocation work units across every (project, account) pair,
    issues exactly two batched charge queries (subtree + leaf) via the existing
    Project.batch_get_subtree_charges / Project.batch_get_account_charges
    primitives, then assembles per-project resource dicts.

    Used by get_user_dashboard_data() to avoid the per-project N+1 explosion
    where each call to project.get_detailed_allocation_usage() fired ~3-4
    aggregation queries per (project, account).

    Returns a dict mapping project_id → list of resource dicts in the same shape
    that _build_project_resources_data() produces, so the caller can plug each
    list into its existing project_data structure unchanged.

    Equivalence with the per-project path is enforced by
    test_query_functions.py::test_user_dashboard_batched_matches_per_project.
    """
    now = active_at or datetime.now()

    # ------------------------------------------------------------------
    # Phase 1: pre-load accounts → allocations / resource / charge_adjustments
    # for every project in one batched fetch. selectinload turns N project
    # walks into ~3 queries total.
    # ------------------------------------------------------------------
    project_ids = [p.project_id for p in projects]
    if not project_ids:
        return {}

    session.query(Account).options(
        selectinload(Account.allocations),
        joinedload(Account.resource).joinedload(Resource.resource_type),
    ).filter(
        Account.project_id.in_(project_ids),
        Account.deleted == False,  # noqa: E712 — SQLAlchemy expression
    ).all()
    # Result discarded — purpose is to populate the identity map so that the
    # subsequent project.accounts walks below are free.

    # ------------------------------------------------------------------
    # Phase 2: per (project, account), pick the allocation we'd display and
    # collect a work unit for the batch charge methods. Pure Python, no DB.
    # ------------------------------------------------------------------
    subtree_infos: List[Dict] = []
    account_infos: List[Dict] = []
    # (project_id, account_id) → (project, account, query_alloc, resource_type, end_date)
    chosen: Dict[tuple, tuple] = {}

    for project in projects:
        is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
        for account in project.accounts:
            if account.deleted:
                continue
            if not account.resource:
                continue
            query_alloc = _select_query_alloc(account, now)
            if query_alloc is None:
                continue

            resource_type = (
                account.resource.resource_type.resource_type
                if account.resource.resource_type
                else 'UNKNOWN'
            )
            start_date = query_alloc.start_date
            end_date = query_alloc.end_date or now
            key = (project.project_id, account.account_id)

            chosen[key] = (project, account, query_alloc, resource_type, end_date)

            if is_tree_valid:
                subtree_infos.append({
                    'key':           key,
                    'resource_id':   account.resource_id,
                    'resource_type': resource_type,
                    'tree_root':     project.tree_root,
                    'tree_left':     project.tree_left,
                    'tree_right':    project.tree_right,
                    'start_date':    start_date,
                    'end_date':      end_date,
                })
            else:
                account_infos.append({
                    'key':           key,
                    'account_id':    account.account_id,
                    'resource_type': resource_type,
                    'start_date':    start_date,
                    'end_date':      end_date,
                })

    # ------------------------------------------------------------------
    # Phase 3: ONE batched fetch per partition (subtree / leaf). The
    # primitives live on Project and are already used by fstree — we are
    # the second, independent consumer.
    # ------------------------------------------------------------------
    raw_charges: Dict[tuple, Dict] = {}
    if subtree_infos:
        raw_charges.update(
            Project.batch_get_subtree_charges(session, subtree_infos, include_adjustments=True)
        )
    if account_infos:
        raw_charges.update(
            Project.batch_get_account_charges(session, account_infos, include_adjustments=True)
        )

    # ------------------------------------------------------------------
    # Phase 4: rolling-usage gate. Only fetch get_project_rolling_usage()
    # for projects where at least one account has a non-null rolling
    # threshold. The vast majority of projects in production have no
    # threshold set, so this skip is the bulk of the rolling-usage win.
    # ------------------------------------------------------------------
    rolling_usage_by_projcode: Dict[str, Dict] = {}
    for project in projects:
        needs_rolling = any(
            (acct.first_threshold is not None or acct.second_threshold is not None)
            for acct in project.accounts
            if not acct.deleted
        )
        if needs_rolling:
            rolling_usage_by_projcode[project.projcode] = get_project_rolling_usage(
                session, project.projcode,
            )

    # ------------------------------------------------------------------
    # Phase 5: assemble per-project resource dicts. This mirrors the
    # shape produced by _build_project_resources_data() exactly — every
    # field, every default — so the template is unaffected and the
    # equivalence test can compare dict-by-dict.
    # ------------------------------------------------------------------
    out: Dict[int, List[Dict]] = {p.project_id: [] for p in projects}

    for key, (project, account, query_alloc, resource_type, end_date) in chosen.items():
        resource_name = account.resource.resource_name
        start_date = query_alloc.start_date

        charges_data = raw_charges.get(key, {'charges_by_type': {}, 'adjustment': 0.0})
        charges_by_type = charges_data['charges_by_type']
        adjustments = charges_data['adjustment']

        allocated = float(query_alloc.amount)
        total_charges = sum(charges_by_type.values())
        effective_used = total_charges + adjustments
        remaining = allocated - effective_used
        percent_used = (effective_used / allocated * 100) if allocated > 0 else 0.0

        days_until_expiration = None
        if query_alloc.end_date:
            days_until_expiration = (query_alloc.end_date - now).days

        start_str = start_date.strftime('%Y-%m-%d') if start_date else '0000-00-00'
        end_str = query_alloc.end_date.strftime('%Y-%m-%d') if query_alloc.end_date else 'open'
        date_group_key = f"{start_str}_{end_str}"

        if not start_date:
            elapsed_pct = 0
            bar_state = 'no-dates'
        elif not query_alloc.end_date:
            elapsed_pct = 50
            bar_state = 'open-ended'
        elif query_alloc.end_date < now:
            elapsed_pct = 100
            bar_state = 'expired'
        else:
            duration_days = (query_alloc.end_date - start_date).days
            if duration_days > 0:
                elapsed_pct = max(0.0, min(100.0, round((now - start_date).days / duration_days * 100, 1)))
                bar_state = 'active'
            else:
                elapsed_pct = 0
                bar_state = 'no-duration'

        rwin = rolling_usage_by_projcode.get(project.projcode, {}).get(resource_name, {}).get('windows', {})

        out[project.project_id].append({
            'resource_name':        resource_name,
            'allocation_id':        query_alloc.allocation_id,
            'parent_allocation_id': query_alloc.parent_allocation_id,
            'is_inheriting':        query_alloc.is_inheriting,
            'account_id':           account.account_id,
            'allocated':            allocated,
            'used':                 effective_used,
            'remaining':            remaining,
            'percent_used':         percent_used,
            'charges_by_type':      charges_by_type,
            'adjustments':          adjustments,
            'status':               'Unknown',
            'start_date':           start_date,
            'end_date':             query_alloc.end_date,
            'days_until_expiration': days_until_expiration,
            'date_group_key':       date_group_key,
            'elapsed_pct':          elapsed_pct,
            'bar_state':            bar_state,
            'resource_type':        resource_type,
            'rolling_30':           rwin.get(30),
            'rolling_90':           rwin.get(90),
        })

    # Sort each project's resources by resource_name for stable ordering
    # (matches the implicit ordering of project.get_detailed_allocation_usage()
    # which iterates accounts in load order — we sort here to be deterministic).
    for pid in out:
        out[pid].sort(key=lambda r: r['resource_name'])

    return out


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

    # Batched fetch: collect alloc work units across all projects and call
    # Project.batch_get_*_charges twice total instead of firing per-project
    # get_detailed_allocation_usage() (which would fire 3-4 queries per
    # (project, account)). See _build_user_projects_resources_batched docstring.
    project_resources_map = _build_user_projects_resources_batched(session, projects)

    project_data_list = []
    for project in projects:
        project_data_list.append({
            'project': project,
            'resources': project_resources_map.get(project.project_id, []),
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
    resource_type = resource.resource_type.resource_type if resource.resource_type else ResourceTypeName.HPC

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
        if ResourceTypeName.is_compute(resource_type):
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

        elif resource_type == ResourceTypeName.DISK:
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

        elif resource_type == ResourceTypeName.ARCHIVE:
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

        if ResourceTypeName.is_compute(resource_type):
            results = session.query(
                CompChargeSummary.activity_date,
                func.sum(CompChargeSummary.charges).label('charges')
            ).filter(
                CompChargeSummary.account_id == account.account_id,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date
            ).group_by(CompChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.DISK:
            results = session.query(
                DiskChargeSummary.activity_date,
                func.sum(DiskChargeSummary.charges).label('charges')
            ).filter(
                DiskChargeSummary.account_id == account.account_id,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date
            ).group_by(DiskChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.ARCHIVE:
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
