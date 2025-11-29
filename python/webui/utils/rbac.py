"""
Role-Based Access Control (RBAC) utilities for SAM Web UI.

Defines permissions, role mappings, and utility functions for checking access.
Works with both Flask-Admin views and custom API endpoints.
"""

from enum import Enum
from typing import Set, List
from functools import wraps
from flask import abort
from flask_login import current_user


class Permission(Enum):
    """
    System-wide permissions for SAM Web UI.

    These permissions can be assigned to roles and checked
    in views, templates, and API endpoints.
    """

    # User management
    VIEW_USERS = "view_users"
    EDIT_USERS = "edit_users"
    CREATE_USERS = "create_users"
    DELETE_USERS = "delete_users"

    # Project management
    VIEW_PROJECTS = "view_projects"
    EDIT_PROJECTS = "edit_projects"
    CREATE_PROJECTS = "create_projects"
    DELETE_PROJECTS = "delete_projects"
    VIEW_PROJECT_MEMBERS = "view_project_members"
    EDIT_PROJECT_MEMBERS = "edit_project_members"

    # Allocation management
    VIEW_ALLOCATIONS = "view_allocations"
    EDIT_ALLOCATIONS = "edit_allocations"
    CREATE_ALLOCATIONS = "create_allocations"
    DELETE_ALLOCATIONS = "delete_allocations"

    # Resource management
    VIEW_RESOURCES = "view_resources"
    EDIT_RESOURCES = "edit_resources"
    CREATE_RESOURCES = "create_resources"

    # Reports and analytics
    VIEW_REPORTS = "view_reports"
    VIEW_CHARGE_SUMMARIES = "view_charge_summaries"
    EXPORT_DATA = "export_data"

    # System administration
    MANAGE_ROLES = "manage_roles"
    IMPERSONATE_USERS = "impersonate_users"
    VIEW_SYSTEM_STATS = "view_system_stats"
    MANAGE_SYSTEM_STATUS = "manage_system_status"  # Update system status data
    SYSTEM_ADMIN = "system_admin"  # Full access to everything


# Role-to-Permission mapping
# This can later be moved to database for dynamic role management
ROLE_PERMISSIONS = {
    # System administrator - full access
    "admin": [p for p in Permission],

    # Facility manager - can manage projects, allocations, resources
    "facility_manager": [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.EDIT_PROJECTS,
        Permission.CREATE_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.EDIT_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.EDIT_ALLOCATIONS,
        Permission.CREATE_ALLOCATIONS,
        Permission.VIEW_RESOURCES,
        Permission.EDIT_RESOURCES,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
    ],

    # Project lead - can view their projects and allocations
    "project_lead": [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
    ],

    # Regular user - read-only access to their own data
    "user": [
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_CHARGE_SUMMARIES,
    ],

    # Read-only analyst - can view everything but not modify
    "analyst": [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_RESOURCES,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
    ],
}


def get_user_permissions(user) -> Set[Permission]:
    """
    Get all permissions for a user based on their roles.

    Args:
        user: AuthUser object with roles

    Returns:
        Set of Permission enum values the user has
    """
    permissions = set()

    # Get permissions from all user's roles
    for role_name in user.roles:
        if role_name in ROLE_PERMISSIONS:
            permissions.update(ROLE_PERMISSIONS[role_name])

    return permissions


def has_permission(user, permission: Permission) -> bool:
    """
    Check if user has a specific permission.

    Args:
        user: AuthUser object
        permission: Permission to check

    Returns:
        True if user has the permission, False otherwise
    """
    # System admins always have all permissions
    if user.has_role('admin'):
        return True

    user_perms = get_user_permissions(user)
    return permission in user_perms


def has_any_permission(user, *permissions: Permission) -> bool:
    """
    Check if user has any of the specified permissions.

    Args:
        user: AuthUser object
        *permissions: Permission values to check

    Returns:
        True if user has at least one permission, False otherwise
    """
    user_perms = get_user_permissions(user)
    return bool(user_perms.intersection(permissions))


def has_all_permissions(user, *permissions: Permission) -> bool:
    """
    Check if user has all of the specified permissions.

    Args:
        user: AuthUser object
        *permissions: Permission values to check

    Returns:
        True if user has all permissions, False otherwise
    """
    user_perms = get_user_permissions(user)
    return set(permissions).issubset(user_perms)


def has_role(user, role_name: str) -> bool:
    """
    Check if user has a specific role.

    Args:
        user: AuthUser object
        role_name: Name of role to check

    Returns:
        True if user has the role, False otherwise
    """
    return user.has_role(role_name)


def has_any_role(user, *role_names: str) -> bool:
    """
    Check if user has any of the specified roles.

    Args:
        user: AuthUser object
        *role_names: Role names to check

    Returns:
        True if user has at least one role, False otherwise
    """
    return user.has_any_role(*role_names)


# Decorator for requiring permissions in views
def require_permission(permission: Permission):
    """
    Decorator to require a specific permission for a view.

    Usage:
        @app.route('/admin/users')
        @login_required
        @require_permission(Permission.VIEW_USERS)
        def list_users():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)  # Unauthorized
            if not has_permission(current_user, permission):
                abort(403)  # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_any_permission(*permissions: Permission):
    """
    Decorator to require any of the specified permissions.

    Usage:
        @app.route('/reports')
        @login_required
        @require_any_permission(Permission.VIEW_REPORTS, Permission.SYSTEM_ADMIN)
        def view_report():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not has_any_permission(current_user, *permissions):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_role(*role_names: str):
    """
    Decorator to require any of the specified roles.

    Usage:
        @app.route('/admin')
        @login_required
        @require_role('admin', 'facility_manager')
        def admin_panel():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not has_any_role(current_user, *role_names):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Context processor for templates
def rbac_context_processor():
    """
    Add RBAC utilities to template context.

    Register this in your app:
        app.context_processor(rbac_context_processor)

    Then use in templates:
        {% if has_permission(Permission.EDIT_USERS) %}
            <a href="/users/edit">Edit Users</a>
        {% endif %}
    """
    return {
        'Permission': Permission,
        'has_permission': lambda p: has_permission(current_user, p) if current_user.is_authenticated else False,
        'has_role': lambda r: has_role(current_user, r) if current_user.is_authenticated else False,
        'user_permissions': get_user_permissions(current_user) if current_user.is_authenticated else set(),
    }
