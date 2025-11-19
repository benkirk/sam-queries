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
from webui.schemas import UserSchema, UserListSchema, UserSummarySchema, ProjectListSchema

bp = Blueprint('api_users', __name__)


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
    from datetime import datetime

    # Get current user from SAM database
    user = db.session.query(User).filter_by(user_id=current_user.user_id).first()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    format_type = request.args.get('format', 'grouped')

    if format_type == 'dashboard':
        # Dashboard format: flat list with allocation usage details
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

        return jsonify({
            'username': user.username,
            'projects': projects_data,
            'total_projects': len(projects_data)
        })

    else:
        # Grouped format (default): projects grouped by role
        # Use ProjectListSchema for consistent serialization
        schema = ProjectListSchema()

        # Get projects where user is lead
        led_projects = [
            {**schema.dump(p), 'role': 'lead'}
            for p in user.led_projects
        ]

        # Get projects where user is admin
        admin_projects = [
            {**schema.dump(p), 'role': 'admin'}
            for p in user.admin_projects
        ]

        # Get all active projects (as member)
        member_projects = [
            {**schema.dump(p), 'role': 'member'}
            for p in user.active_projects
            if p not in user.led_projects and p not in user.admin_projects
        ]

        return jsonify({
            'username': user.username,
            'led_projects': led_projects,
            'admin_projects': admin_projects,
            'member_projects': member_projects,
            'total_projects': len(led_projects) + len(admin_projects) + len(member_projects)
        })


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
    from sam.queries import find_user_by_username

    user = find_user_by_username(db.session, username)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Serialize user using UserSchema
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
    from sam.queries import find_user_by_username

    user = find_user_by_username(db.session, username)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Use ProjectListSchema for consistent serialization
    schema = ProjectListSchema()

    # Get projects where user is lead
    led_projects = [
        {**schema.dump(p), 'role': 'lead'}
        for p in user.led_projects
    ]

    # Get projects where user is admin
    admin_projects = [
        {**schema.dump(p), 'role': 'admin'}
        for p in user.admin_projects
    ]

    # Get all active projects (as member)
    member_projects = [
        {**schema.dump(p), 'role': 'member'}
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
