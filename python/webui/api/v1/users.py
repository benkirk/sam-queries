"""
User API endpoints (v1).

Provides RESTful API for user management with RBAC.

Example usage:
    GET /api/v1/users?page=1&per_page=50
    GET /api/v1/users/johndoe
    GET /api/v1/users/johndoe/projects
"""

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from webui.utils.rbac import require_permission, Permission
from webui.extensions import db

bp = Blueprint('api_users', __name__)


@bp.route('/', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)
def list_users():
    """
    GET /api/v1/users - List users with pagination and filtering.

    Query parameters:
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 50, max: 100)
        search (str): Search term for username/name
        active (bool): Filter by active status
        locked (bool): Filter by locked status

    Returns:
        JSON with users list, pagination info
    """
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    search = request.args.get('search', '')
    active = request.args.get('active', type=lambda v: v.lower() == 'true')
    locked = request.args.get('locked', type=lambda v: v.lower() == 'true')

    from sam.queries import find_users_by_name, get_active_users
    from sam.core.users import User

    # Build query
    query = db.session.query(User)

    # Apply filters
    if search:
        users = find_users_by_name(db.session, search)
    else:
        if active is not None:
            query = query.filter(User.active == active)
        if locked is not None:
            query = query.filter(User.locked == locked)

        users = query.limit(per_page).offset((page - 1) * per_page).all()

    # Serialize users
    users_data = []
    for user in users:
        users_data.append({
            'user_id': user.user_id,
            'username': user.username,
            'full_name': user.full_name,
            'display_name': user.display_name,
            'email': user.primary_email,
            'active': user.active,
            'locked': user.locked,
            'charging_exempt': user.charging_exempt,
        })

    return jsonify({
        'users': users_data,
        'page': page,
        'per_page': per_page,
        'total': len(users_data)
    })


@bp.route('/<username>', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)
def get_user(username):
    """
    GET /api/v1/users/<username> - Get user details.

    Returns:
        JSON with user details
    """
    from sam.queries import find_user_by_username

    user = find_user_by_username(db.session, username)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Get user's institutions
    institutions = [
        {
            'institution_name': ui.institution.institution_name,
            'is_primary': ui.is_primary
        }
        for ui in user.institutions
    ]

    # Get user's organizations
    organizations = [
        {
            'organization_acronym': uo.organization.acronym,
            'organization_name': uo.organization.name
        }
        for uo in user.organizations
    ]

    # Get user's roles
    roles = [ra.role.name for ra in user.role_assignments]

    return jsonify({
        'user_id': user.user_id,
        'username': user.username,
        'first_name': user.first_name,
        'middle_name': user.middle_name,
        'last_name': user.last_name,
        'full_name': user.full_name,
        'display_name': user.display_name,
        'email': user.primary_email,
        'active': user.active,
        'locked': user.locked,
        'charging_exempt': user.charging_exempt,
        'institutions': institutions,
        'organizations': organizations,
        'roles': roles,
        'creation_time': user.creation_time.isoformat() if user.creation_time else None,
        'modified_time': user.modified_time.isoformat() if user.modified_time else None,
    })


@bp.route('/<username>/projects', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def get_user_projects(username):
    """
    GET /api/v1/users/<username>/projects - Get user's projects.

    Returns:
        JSON with list of projects where user is lead, admin, or member
    """
    from sam.queries import find_user_by_username

    user = find_user_by_username(db.session, username)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Get projects where user is lead
    led_projects = [
        {
            'projcode': p.projcode,
            'title': p.title,
            'role': 'lead',
            'active': p.active
        }
        for p in user.led_projects
    ]

    # Get projects where user is admin
    admin_projects = [
        {
            'projcode': p.projcode,
            'title': p.title,
            'role': 'admin',
            'active': p.active
        }
        for p in user.admin_projects
    ]

    # Get all active projects (as member)
    member_projects = [
        {
            'projcode': p.projcode,
            'title': p.title,
            'role': 'member',
            'active': p.active
        }
        for p in user.active_projects
        if p not in user.led_projects and p not in user.admin_projects
    ]

    return jsonify({
        'username': username,
        'led_projects': led_projects,
        'admin_projects': admin_projects,
        'member_projects': member_projects,
        'total_projects': len(led_projects) + len(admin_projects) + len(member_projects)
    })


@bp.errorhandler(403)
def forbidden(e):
    """Handle forbidden access."""
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403


@bp.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access."""
    return jsonify({'error': 'Unauthorized - authentication required'}), 401
