"""
API helper utilities for common patterns.

This module provides reusable helpers for:
- Error handler registration
- Date range parsing
- Project lookup with 404 handling
- Project serialization by role
"""

from datetime import datetime, timedelta
from flask import jsonify, request
from typing import Optional, Tuple, Any, Dict, List


def register_error_handlers(blueprint):
    """
    Register standard API error handlers on a blueprint.

    Provides consistent JSON error responses for common HTTP error codes.

    Usage:
        from webapp.api.helpers import register_error_handlers
        bp = Blueprint('api_name', __name__)
        register_error_handlers(bp)
    """

    @blueprint.errorhandler(400)
    def bad_request(e):
        return jsonify({'error': str(e.description) if hasattr(e, 'description') else 'Bad request'}), 400

    @blueprint.errorhandler(401)
    def unauthorized(e):
        return jsonify({'error': 'Unauthorized - authentication required'}), 401

    @blueprint.errorhandler(403)
    def forbidden(e):
        return jsonify({'error': 'Forbidden - insufficient permissions'}), 403

    @blueprint.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Resource not found'}), 404


def parse_date_range(
    days_back: int = 90,
    start_param: str = 'start_date',
    end_param: str = 'end_date'
) -> Tuple[Optional[datetime], Optional[datetime], Optional[Tuple[Any, int]]]:
    """
    Parse start_date and end_date from request query parameters.

    Args:
        days_back: Default number of days before end_date for start_date
        start_param: Query parameter name for start date
        end_param: Query parameter name for end date

    Returns:
        tuple: (start_date, end_date, error_response)
        If error_response is not None, return it immediately from your endpoint.

    Usage:
        start_date, end_date, error = parse_date_range()
        if error:
            return error
    """
    try:
        end_str = request.args.get(end_param)
        start_str = request.args.get(start_param)

        end_date = datetime.strptime(end_str, '%Y-%m-%d') if end_str else datetime.now()
        start_date = datetime.strptime(start_str, '%Y-%m-%d') if start_str else end_date - timedelta(days=days_back)

        return start_date, end_date, None
    except ValueError:
        return None, None, (jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400)


def get_project_or_404(session, projcode: str) -> Tuple[Optional[Any], Optional[Tuple[Any, int]]]:
    """
    Look up a project by projcode, returning 404 error if not found.

    Args:
        session: SQLAlchemy database session
        projcode: Project code to look up

    Returns:
        tuple: (project, error_response)
        If error_response is not None, return it immediately from your endpoint.

    Usage:
        project, error = get_project_or_404(db.session, projcode)
        if error:
            return error
    """
    from sam.queries import find_project_by_code

    project = find_project_by_code(session, projcode)
    if not project:
        return None, (jsonify({'error': f'Project {projcode} not found'}), 404)
    return project, None


def get_user_or_404(session, username: str) -> Tuple[Optional[Any], Optional[Tuple[Any, int]]]:
    """
    Look up a user by username, returning 404 error if not found.

    Args:
        session: SQLAlchemy database session
        username: Username to look up

    Returns:
        tuple: (user, error_response)
        If error_response is not None, return it immediately from your endpoint.

    Usage:
        user, error = get_user_or_404(db.session, username)
        if error:
            return error
    """
    from sam.queries import find_user_by_username

    user = find_user_by_username(session, username)
    if not user:
        return None, (jsonify({'error': f'User {username} not found'}), 404)
    return user, None


def serialize_projects_by_role(user, schema) -> Dict[str, Any]:
    """
    Serialize a user's projects grouped by their role.

    Args:
        user: User object with led_projects, admin_projects, active_projects
        schema: Marshmallow schema instance for project serialization

    Returns:
        dict with keys: led_projects, admin_projects, member_projects, total_projects

    Usage:
        from sam.schemas import ProjectListSchema
        schema = ProjectListSchema()
        data = serialize_projects_by_role(user, schema)
        return jsonify({'username': user.username, **data})
    """
    # Use sets for efficient membership checking
    led_set = set(user.led_projects)
    admin_set = set(user.admin_projects) - led_set

    led_projects = [
        {**schema.dump(p), 'role': 'lead'}
        for p in user.led_projects
    ]

    admin_projects = [
        {**schema.dump(p), 'role': 'admin'}
        for p in admin_set
    ]

    member_projects = [
        {**schema.dump(p), 'role': 'member'}
        for p in user.active_projects
        if p not in led_set and p not in admin_set
    ]

    return {
        'led_projects': led_projects,
        'admin_projects': admin_projects,
        'member_projects': member_projects,
        'total_projects': len(led_projects) + len(admin_projects) + len(member_projects)
    }


# ============================================================================
# Standard Response Helpers
# ============================================================================

def success_response(data: Any, message: Optional[str] = None) -> Tuple[Any, int]:
    """
    Create a standard success response wrapper.

    Args:
        data: The response data payload
        message: Optional success message

    Returns:
        tuple: (JSON response, 200 status code)

    Usage:
        return success_response({'user': user_data}, 'User created successfully')
        # Returns: {'success': True, 'data': {...}, 'message': '...'}
    """
    response = {'success': True, 'data': data}
    if message:
        response['message'] = message
    return jsonify(response), 200


def error_response(
    message: str,
    status_code: int = 400,
    code: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> Tuple[Any, int]:
    """
    Create a standard error response wrapper.

    Args:
        message: Human-readable error message
        status_code: HTTP status code (default: 400)
        code: Machine-readable error code (e.g., 'NOT_FOUND', 'INVALID_DATE')
        details: Additional error details dict

    Returns:
        tuple: (JSON response, status code)

    Usage:
        return error_response('User not found', 404, code='USER_NOT_FOUND')
        # Returns: {'error': 'User not found', 'code': 'USER_NOT_FOUND'}

        return error_response('Validation failed', 400, details={'field': 'email', 'reason': 'invalid format'})
        # Returns: {'error': 'Validation failed', 'details': {...}}
    """
    response = {'error': message}
    if code:
        response['code'] = code
    if details:
        response['details'] = details
    return jsonify(response), status_code
