"""
Centralized access control helpers for API endpoints.

This module provides decorators and utilities for:
- System-level permission checks
- Project-specific access control
- User membership verification

Usage:
    from webapp.api.access_control import require_project_access, require_permission_decorator

    @bp.route('/<projcode>/data')
    @login_required
    @require_project_access
    def get_project_data(project):  # Note: receives project object, not projcode
        return jsonify(project.data)
"""

from functools import wraps
from flask import abort
from flask_login import current_user
from typing import Callable, Any

from webapp.extensions import db
from webapp.utils.rbac import has_permission, Permission
from webapp.api.helpers import get_project_or_404


def _get_sam_user():
    """Get SAM user record for current_user."""
    from sam.core.users import User
    return db.session.query(User).filter_by(user_id=current_user.user_id).first()


def _user_can_access_project(user, project) -> bool:
    """
    Check if a SAM user can access project data.

    Users can access project data if they are:
    - A member of the project (active membership)
    - The project lead
    - A project admin

    Args:
        user: SAM User object
        project: Project object to check access for

    Returns:
        bool: True if user can access, False otherwise
    """
    if not user:
        return False

    # Build set of all user's project IDs
    user_projects = {p.project_id for p in user.active_projects}
    user_projects.update({p.project_id for p in user.led_projects})
    user_projects.update({p.project_id for p in user.admin_projects})

    return project.project_id in user_projects


def require_permission_decorator(permission: Permission) -> Callable:
    """
    Decorator to require a system-level permission.

    Args:
        permission: Permission enum value to check

    Usage:
        @bp.route('/admin/users')
        @login_required
        @require_permission_decorator(Permission.MANAGE_USERS)
        def admin_users():
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_permission(current_user, permission):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_project_access(f: Callable) -> Callable:
    """
    Decorator to require access to project specified by projcode URL parameter.

    The decorated function receives the project object instead of projcode.
    Access is granted if user has VIEW_PROJECTS permission OR is a project member.

    Usage:
        @bp.route('/<projcode>/data')
        @login_required
        @require_project_access
        def get_project_data(project):  # Receives project object
            return jsonify({'projcode': project.projcode})
    """
    @wraps(f)
    def decorated_function(projcode: str, *args, **kwargs):
        # Look up project
        project, error = get_project_or_404(db.session, projcode)
        if error:
            return error

        # Check access: admin permission OR user is project member
        if has_permission(current_user, Permission.VIEW_PROJECTS):
            return f(project, *args, **kwargs)

        sam_user = _get_sam_user()
        if _user_can_access_project(sam_user, project):
            return f(project, *args, **kwargs)

        abort(403)

    return decorated_function


def require_project_member_access(permission: Permission) -> Callable:
    """
    Decorator factory for project endpoints requiring specific permission OR membership.

    Args:
        permission: Permission that grants access without membership check

    Usage:
        @bp.route('/<projcode>/members')
        @login_required
        @require_project_member_access(Permission.VIEW_PROJECT_MEMBERS)
        def get_members(project):
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(projcode: str, *args, **kwargs):
            # Look up project
            project, error = get_project_or_404(db.session, projcode)
            if error:
                return error

            # Check access: specific permission OR user is project member
            if has_permission(current_user, permission):
                return f(project, *args, **kwargs)

            sam_user = _get_sam_user()
            if _user_can_access_project(sam_user, project):
                return f(project, *args, **kwargs)

            abort(403)

        return decorated_function
    return decorator
