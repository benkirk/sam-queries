"""
User API endpoints (v1).

Provides RESTful API for user management with RBAC.

Example usage:
    GET /api/v1/users?page=1&per_page=50
    GET /api/v1/users/johndoe
    GET /api/v1/users/johndoe/projects
"""

from datetime import datetime
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from webapp.utils.rbac import require_permission, Permission
from webapp.extensions import db
from sam.schemas import UserSchema, UserListSchema, UserSummarySchema, ProjectListSchema
from webapp.api.helpers import register_error_handlers, get_user_or_404, serialize_projects_by_role

bp = Blueprint('api_users', __name__)
register_error_handlers(bp)


# ============================================================================
# Helper Functions
# ============================================================================

def _serialize_dashboard_format(user):
    """
    Serialize user's projects in dashboard format with allocation usage details.

    Args:
        user: User object with active_projects relationship

    Returns:
        dict with username, projects list (with usage details), and total_projects
    """
    projects = user.active_projects
    projects_data = []

    for project in projects:
        # Get allocation usage for this project
        usage_data = project.get_detailed_allocation_usage(include_adjustments=True)

        resources_list = []
        for resource_name, details in usage_data.items():
            # Get adjustments
            adjustments_total = details.get('adjustments', 0.0)

            # Determine status based on end_date
            status = 'Active'
            end_date = details.get('end_date')
            if end_date and end_date < datetime.now():
                status = 'Expired'

            # Format dates for JSON serialization
            start_date = details.get('start_date')
            start_date_str = start_date.isoformat() if start_date else None
            end_date_str = end_date.isoformat() if end_date else None

            resources_list.append({
                'resource_name': resource_name,
                'allocated': details.get('allocated', 0.0),
                'used': details.get('used', 0.0),
                'remaining': details.get('remaining', 0.0),
                'percent_used': details.get('percent_used', 0.0),
                'charges_by_type': details.get('charges_by_type', {}),
                'adjustments': adjustments_total,
                'status': status,
                'start_date': start_date_str,
                'end_date': end_date_str,
            })

        projects_data.append({
            'projcode': project.projcode,
            'title': project.title,
            'active': project.active,
            'lead_username': project.lead.username if project.lead else None,
            'lead_name': project.lead.full_name if project.lead else None,
            'resources': resources_list,
        })

    return {
        'username': user.username,
        'projects': projects_data,
        'total_projects': len(projects_data)
    }


def _serialize_grouped_format(user, schema):
    """
    Serialize user's projects in grouped format (led/admin/member).

    Args:
        user: User object with led_projects, admin_projects, active_projects
        schema: Marshmallow schema instance for project serialization

    Returns:
        dict with username and role-grouped projects
    """
    data = serialize_projects_by_role(user, schema)
    return {'username': user.username, **data}


# ============================================================================
# Routes
# ============================================================================


@bp.route('/me', methods=['GET'])
@login_required
def get_current_user():
    """
    GET /api/v1/users/me - Get current user's details.

    Returns:
        JSON with current user's full details
    """
    from sam.core.users import User

    # Get current user's details from SAM database
    user = db.session.query(User).filter_by(user_id=current_user.user_id).first()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Serialize using UserSchema
    return jsonify(UserSchema().dump(user))


@bp.route('/me/projects', methods=['GET'])
@login_required
def get_current_user_projects():
    """
    GET /api/v1/users/me/projects - Get current user's projects.

    Query parameters:
        format (str): 'grouped' (default) or 'dashboard'
            - grouped: Returns projects grouped by role (led/admin/member)
            - dashboard: Returns flat list with allocation usage for dashboard UI

    Returns:
        JSON with list of projects where user is lead, admin, or member
    """
    from sam.core.users import User

    # Get current user from SAM database
    user = db.session.query(User).filter_by(user_id=current_user.user_id).first()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    format_type = request.args.get('format', 'grouped')

    if format_type == 'dashboard':
        return jsonify(_serialize_dashboard_format(user))
    else:
        return jsonify(_serialize_grouped_format(user, ProjectListSchema()))


@bp.route('/search', methods=['GET'])
@login_required
def search_users():
    """
    GET /api/v1/users/search - Search users for autocomplete functionality.

    Query parameters:
        q (str): Search query (minimum 2 characters, required)
        projcode (str): Optional project code to exclude existing members
        limit (int): Maximum results to return (default: 20, max: 50)
        active_only (bool): If true, only return active users (default: false)

    Returns:
        JSON array of matching users with username, display_name, email
    """
    from sam.queries import search_users_by_pattern, get_project_member_user_ids
    from sam.projects.projects import Project

    query = request.args.get('q', '').strip()

    if len(query) < 2:
        return jsonify([])

    limit = min(request.args.get('limit', 20, type=int), 50)
    active_only = request.args.get('active_only', 'false').lower() == 'true'

    # Get existing members to exclude if projcode provided
    exclude_ids = None
    projcode = request.args.get('projcode')
    if projcode:
        project = db.session.query(Project).filter_by(projcode=projcode).first()
        if project:
            exclude_ids = get_project_member_user_ids(db.session, project.project_id)

    users = search_users_by_pattern(
        db.session,
        query,
        limit=limit,
        exclude_user_ids=exclude_ids,
        active_only=active_only
    )

    return jsonify([
        {
            'username': u.username,
            'display_name': u.display_name,
            'email': u.primary_email or ''
        }
        for u in users
    ])


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

    # Serialize users using UserListSchema
    users_data = UserListSchema(many=True).dump(users)

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
    user, error = get_user_or_404(db.session, username)
    if error:
        return error

    return jsonify(UserSchema().dump(user))


@bp.route('/<username>/projects', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def get_user_projects(username):
    """
    GET /api/v1/users/<username>/projects - Get user's projects.

    Returns:
        JSON with list of projects where user is lead, admin, or member
    """
    user, error = get_user_or_404(db.session, username)
    if error:
        return error

    schema = ProjectListSchema()
    data = serialize_projects_by_role(user, schema)
    return jsonify({'username': username, **data})
