"""
Project API endpoints (v1).

Provides RESTful API for project management with RBAC.

Example usage:
    GET /api/v1/projects?page=1&per_page=50
    GET /api/v1/projects/ABC123
    GET /api/v1/projects/ABC123/allocations
    GET /api/v1/projects/expiring?days=30
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from webui.utils.rbac import require_permission, Permission
from webui.extensions import db
from webui.schemas import (
    ProjectSchema, ProjectListSchema, ProjectSummarySchema,
    AllocationWithUsageSchema
)
from datetime import datetime, timedelta

bp = Blueprint('api_projects', __name__)


# ============================================================================
# Helper Functions
# ============================================================================

def _parse_project_filter_params():
    """
    Parse common filter parameters for project queries.

    Returns:
        dict with parsed filter parameters:
            - facility_names: List of facility names (or None for all)
            - resource_name: Optional resource name filter
            - days: Number of days (context-dependent)
    """
    # Parse facility names (can be comma-separated or multiple params)
    facility_names = request.args.getlist('facility_names')
    if not facility_names:
        # Check for single 'facility' param for backwards compatibility
        facility = request.args.get('facility', '').strip()
        if facility:
            facility_names = [facility]
        else:
            facility_names = None

    # Parse resource name
    resource_name = request.args.get('resource', '').strip()
    resource_name = resource_name if resource_name else None

    # Parse days parameter (meaning depends on endpoint context)
    days = request.args.get('days', type=int)

    return {
        'facility_names': facility_names,
        'resource_name': resource_name,
        'days': days
    }


def _format_project_allocation_tuple(project, alloc, res_name, days_value, days_label_key):
    """
    Format a (Project, Allocation, resource_name, days) tuple for JSON response.

    Args:
        project: Project object
        alloc: Allocation object
        res_name: Resource name string
        days_value: Number of days (remaining or since expiration)
        days_label_key: JSON key for days value ('days_remaining' or 'days_since_expiration')

    Returns:
        dict with formatted project allocation data
    """
    return {
        'projcode': project.projcode,
        'title': project.title,
        'lead_username': project.lead.username if project.lead else None,
        'lead_name': project.lead.full_name if project.lead else None,
        'admin_username': project.admin.username if project.admin else None,
        'active': project.active,
        'resource_name': res_name,
        days_label_key: days_value,
        'allocation_end_date': alloc.end_date.isoformat() if alloc.end_date else None,
        'allocation_start_date': alloc.start_date.isoformat() if alloc.start_date else None,
    }


# ============================================================================
# Routes
# ============================================================================

@bp.route('/', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def list_projects():
    """
    GET /api/v1/projects - List projects with pagination and filtering.

    Query parameters:
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 50, max: 100)
        search (str): Search term for projcode/title
        active (bool): Filter by active status
        facility (str): Filter by facility name

    Returns:
        JSON with projects list, pagination info
    """
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    search = request.args.get('search', '')
    active = request.args.get('active', type=lambda v: v.lower() == 'true')
    facility = request.args.get('facility', '')

    from sam.queries import search_projects_by_title, get_active_projects
    from sam.projects.projects import Project

    # Build query based on filters
    if search:
        projects = search_projects_by_title(db.session, search)
    elif facility:
        projects = get_active_projects(db.session, facility_name=facility)
    else:
        query = db.session.query(Project)
        if active is not None:
            query = query.filter(Project.active == active)
        projects = query.limit(per_page).offset((page - 1) * per_page).all()

    # Serialize projects using ProjectListSchema
    projects_data = ProjectListSchema(many=True).dump(projects)

    return jsonify({
        'projects': projects_data,
        'page': page,
        'per_page': per_page,
        'total': len(projects_data)
    })


@bp.route('/<projcode>', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def get_project(projcode):
    """
    GET /api/v1/projects/<projcode> - Get project details with tree structure.

    Query parameters:
        max_depth (int): Maximum depth for tree traversal (default: 4)
                        Controls how deep the project tree is expanded.
                        Set to 0 to disable tree expansion.

    Returns:
        JSON with project details including:
        - breadcrumb_path: List of projcodes from root to current project
        - tree_depth: Depth of current project in tree (0 = root)
        - tree: Full tree structure from root with nested children
    """
    from sam.queries import find_project_by_code

    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Get max_depth parameter (default: 4)
    max_depth = request.args.get('max_depth', 4, type=int)

    # Serialize project using ProjectSchema with tree context
    schema = ProjectSchema()
    schema.context = {
        'max_depth': max_depth,
        'session': db.session
    }
    return jsonify(schema.dump(project))


@bp.route('/<projcode>/members', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_PROJECT_MEMBERS)
def get_project_members(projcode):
    """
    GET /api/v1/projects/<projcode>/members - Get project members.

    Returns:
        JSON with lead, admin, and all members
    """
    from sam.queries import find_project_by_code, get_users_on_project

    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Get all users on project
    users = get_users_on_project(db.session, projcode)

    # Separate users by role
    lead = next((u for u in users if u['role'] == 'Lead'), None)
    admin = next((u for u in users if u['role'] == 'Admin'), None)
    members = [u for u in users if u['role'] == 'Member']

    return jsonify({
        'projcode': projcode,
        'lead': {
            'username': lead['username'],
            'name': lead['display_name'],
            'email': lead['email']
        } if lead else None,
        'admin': {
            'username': admin['username'],
            'name': admin['display_name'],
            'email': admin['email']
        } if admin else None,
        'members': [
            {
                'username': m['username'],
                'name': m['display_name'],
                'email': m['email']
            }
            for m in members
        ],
        'total_members': len(users)
    })


@bp.route('/<projcode>/allocations', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_allocations(projcode):
    """
    GET /api/v1/projects/<projcode>/allocations - Get project allocations with usage.

    **ENHANCED**: Now includes usage data (used, remaining, percent_used) like sam_search.py output.

    Query parameters:
        resource (str): Filter by resource name
        include_adjustments (bool): Include manual adjustments (default: true)

    Returns:
        JSON with allocations including usage details for the project
    """
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account

    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    resource_name = request.args.get('resource')
    include_adjustments = request.args.get('include_adjustments', 'true').lower() == 'true'

    # Get accounts with allocations
    query = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.deleted == False
    )

    if resource_name:
        from sam.resources.resources import Resource
        query = query.join(Resource).filter(Resource.resource_name == resource_name)

    allocations_data = []
    now = datetime.now()

    for account in query.all():
        # Find active allocation
        for alloc in account.allocations:
            if alloc.is_active_at(now) and not alloc.deleted:
                # Serialize with AllocationWithUsageSchema for full usage details
                schema = AllocationWithUsageSchema()
                schema.context = {
                    'account': account,
                    'session': db.session,
                    'include_adjustments': include_adjustments
                }
                alloc_data = schema.dump(alloc)
                allocations_data.append(alloc_data)

    return jsonify({
        'projcode': projcode,
        'allocations': allocations_data,
        'total': len(allocations_data)
    })


@bp.route('/expiring', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_expiring_projects():
    """
    GET /api/v1/projects/expiring - Get projects with expiring allocations.

    Query parameters:
        days (int): Days in future to check (default: 30)
        facility_names (list): Filter by facility names (can specify multiple)
        facility (str): Single facility filter (backwards compatibility)
        resource (str): Filter by resource name

    Returns:
        JSON with expiring projects including days_remaining and allocation dates
    """
    from sam.queries import get_projects_expiring_soon

    # Parse filter parameters using common helper
    filters = _parse_project_filter_params()
    days = filters['days'] if filters['days'] is not None else 30

    # Query for expiring projects
    expiring = get_projects_expiring_soon(
        db.session,
        days=days,
        facility_names=filters['facility_names'],
        resource_name=filters['resource_name']
    )

    # Format results using common helper
    expiring_data = [
        _format_project_allocation_tuple(project, alloc, res_name, days_value, 'days_remaining')
        for project, alloc, res_name, days_value in expiring
    ]

    return jsonify({
        'expiring_projects': expiring_data,
        'days': days,
        'facility_names': filters['facility_names'],
        'resource_name': filters['resource_name'],
        'total': len(expiring_data)
    })


@bp.route('/recently_expired', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_recently_expired_projects():
    """
    GET /api/v1/projects/recently_expired - Get projects with recently expired allocations.

    Query parameters:
        min_days (int): Minimum days since expiration (default: 90)
        max_days (int): Maximum days since expiration (default: 365)
        facility_names (list): Filter by facility names (can specify multiple)
        facility (str): Single facility filter (backwards compatibility)
        resource (str): Filter by resource name

    Returns:
        JSON with expired projects including days_since_expiration and allocation dates

    Example:
        GET /api/v1/projects/recently_expired?min_days=90&max_days=180&facility_names=UNIV
        Returns projects with allocations that expired 90-180 days ago for UNIV facility
    """
    from sam.queries import get_projects_with_expired_allocations

    # Parse filter parameters using common helper
    filters = _parse_project_filter_params()

    # Parse min/max days for expired range
    min_days = request.args.get('min_days', 90, type=int)
    max_days = request.args.get('max_days', 365, type=int)

    # Query for recently expired projects
    expired = get_projects_with_expired_allocations(
        session=db.session,
        min_days_expired=min_days,
        max_days_expired=max_days,
        facility_names=filters['facility_names'],
        resource_name=filters['resource_name'],
        include_inactive_projects=False
    )

    # Format results using common helper
    expired_data = [
        _format_project_allocation_tuple(project, alloc, res_name, days_value, 'days_since_expiration')
        for project, alloc, res_name, days_value in expired
    ]

    return jsonify({
        'expired_projects': expired_data,
        'min_days': min_days,
        'max_days': max_days,
        'facility_names': filters['facility_names'],
        'resource_name': filters['resource_name'],
        'total': len(expired_data)
    })


@bp.errorhandler(403)
def forbidden(e):
    """Handle forbidden access."""
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403


@bp.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access."""
    return jsonify({'error': 'Unauthorized - authentication required'}), 401
