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
from datetime import datetime, timedelta

bp = Blueprint('api_projects', __name__)


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

    # Serialize projects
    projects_data = []
    for project in projects:
        projects_data.append({
            'project_id': project.project_id,
            'projcode': project.projcode,
            'title': project.title,
            'lead_username': project.lead.username if project.lead else None,
            'admin_username': project.admin.username if project.admin else None,
            'active': project.active,
            'charging_exempt': project.charging_exempt,
            'area_of_interest': project.area_of_interest.area_of_interest if project.area_of_interest else None,
        })

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
    GET /api/v1/projects/<projcode> - Get project details.

    Returns:
        JSON with project details
    """
    from sam.queries import find_project_by_code

    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    return jsonify({
        'project_id': project.project_id,
        'projcode': project.projcode,
        'title': project.title,
        'description': project.description,
        'lead': {
            'username': project.lead.username,
            'name': project.lead.full_name,
            'email': project.lead.primary_email
        } if project.lead else None,
        'admin': {
            'username': project.admin.username,
            'name': project.admin.full_name,
            'email': project.admin.primary_email
        } if project.admin else None,
        'active': project.active,
        'charging_exempt': project.charging_exempt,
        'area_of_interest': project.area_of_interest.area_of_interest if project.area_of_interest else None,
        'creation_time': project.creation_time.isoformat() if project.creation_time else None,
        'modified_time': project.modified_time.isoformat() if project.modified_time else None,
    })


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
    lead_user, admin_user, members = get_users_on_project(db.session, projcode)

    return jsonify({
        'projcode': projcode,
        'lead': {
            'username': lead_user.username,
            'name': lead_user.full_name,
            'email': lead_user.primary_email
        } if lead_user else None,
        'admin': {
            'username': admin_user.username,
            'name': admin_user.full_name,
            'email': admin_user.primary_email
        } if admin_user else None,
        'members': [
            {
                'username': m.username,
                'name': m.full_name,
                'email': m.primary_email
            }
            for m in members
        ],
        'total_members': len(members)
    })


@bp.route('/<projcode>/allocations', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_allocations(projcode):
    """
    GET /api/v1/projects/<projcode>/allocations - Get project allocations.

    Query parameters:
        resource (str): Filter by resource name

    Returns:
        JSON with allocations for the project
    """
    from sam.queries import find_project_by_code, get_project_allocations

    project = find_project_by_code(db.session, projcode)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    resource_name = request.args.get('resource')

    allocations = get_project_allocations(db.session, projcode, resource_name)

    allocations_data = []
    for account, alloc, resource in allocations:
        allocations_data.append({
            'allocation_id': alloc.allocation_id,
            'resource_name': resource.resource_name,
            'resource_type': resource.resource_type.resource_type if resource.resource_type else None,
            'amount': float(alloc.amount) if alloc.amount else None,
            'start_date': alloc.start_date.isoformat() if alloc.start_date else None,
            'end_date': alloc.end_date.isoformat() if alloc.end_date else None,
            'is_active': alloc.is_active,
            'deleted': alloc.deleted,
        })

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
        facility (str): Filter by facility name

    Returns:
        JSON with expiring projects
    """
    days = request.args.get('days', 30, type=int)
    facility = request.args.get('facility', '')

    from sam.queries import get_projects_expiring_soon

    if facility:
        expiring = get_projects_expiring_soon(db.session, days=days)
        # Filter by facility if needed
        # (get_projects_expiring_soon doesn't have facility param yet)
    else:
        expiring = get_projects_expiring_soon(db.session, days=days)

    expiring_data = []
    for project in expiring:
        expiring_data.append({
            'projcode': project.projcode,
            'title': project.title,
            'lead_username': project.lead.username if project.lead else None,
            'active': project.active,
        })

    return jsonify({
        'expiring_projects': expiring_data,
        'days': days,
        'total': len(expiring_data)
    })


@bp.errorhandler(403)
def forbidden(e):
    """Handle forbidden access."""
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403


@bp.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access."""
    return jsonify({'error': 'Unauthorized - authentication required'}), 401
