"""
Role-Based Access Control (RBAC) utilities for SAM Web UI.

Defines permissions, POSIX-group-to-permission mappings, and utility
functions for checking access. Works with both Flask-Admin views and
custom API endpoints.

Authorization model
-------------------
A user's permissions are derived from two sources, unioned together:

1. **POSIX group membership** — each group the user belongs to may map
   to a bundle of permissions in ``GROUP_PERMISSIONS``. In production,
   group membership is read from ``adhoc_system_account_entry`` via
   ``get_user_group_access()``. In dev/test, ``DEV_GROUP_MAPPING``
   in app config supplies synthetic group names per username.

2. **Per-user overrides** — ``USER_PERMISSION_OVERRIDES`` grants
   additional permissions to specific usernames on top of whatever
   their groups confer. Useful for one-off privilege grants without
   touching group membership.

There is **no dependency on the SAM ``role_user`` / ``role`` tables**;
those are not consulted by the webapp's RBAC layer.

The string set returned by ``AuthUser.roles`` is the set of POSIX group
names the user belongs to that have a ``GROUP_PERMISSIONS`` bundle.
This keeps ``has_role('admin')``-style checks working as a coarse
display label, but the source of truth for authorization is the
permission set, not the role label.
"""

from enum import Enum
from typing import Set, List, Dict
from functools import wraps
from flask import abort
from flask_login import current_user


class Permission(Enum):
    """
    System-wide permissions for SAM Web UI.

    These permissions can be assigned to POSIX-group bundles (see
    ``GROUP_PERMISSIONS``) or granted to individual users (see
    ``USER_PERMISSION_OVERRIDES``), and checked in views, templates,
    and API endpoints.
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

    # Resource management (machines, queues, resource definitions)
    VIEW_RESOURCES = "view_resources"
    EDIT_RESOURCES = "edit_resources"
    CREATE_RESOURCES = "create_resources"
    DELETE_RESOURCES = "delete_resources"

    # Facility management (UNIV, WNA, ...)
    VIEW_FACILITIES = "view_facilities"
    EDIT_FACILITIES = "edit_facilities"
    CREATE_FACILITIES = "create_facilities"
    DELETE_FACILITIES = "delete_facilities"

    # Group management (adhoc/POSIX groups)
    VIEW_GROUPS = "view_groups"
    EDIT_GROUPS = "edit_groups"
    CREATE_GROUPS = "create_groups"
    DELETE_GROUPS = "delete_groups"

    # Organizational metadata: organizations, institutions, contracts,
    # NSF programs, areas of interest. Slowly-changing reference data.
    VIEW_ORG_METADATA = "view_org_metadata"
    EDIT_ORG_METADATA = "edit_org_metadata"
    CREATE_ORG_METADATA = "create_org_metadata"
    DELETE_ORG_METADATA = "delete_org_metadata"

    # Reports and analytics
    VIEW_REPORTS = "view_reports"
    VIEW_CHARGE_SUMMARIES = "view_charge_summaries"
    MANAGE_CHARGE_SUMMARIES = "manage_charge_summaries"  # Write charge summary records
    EXPORT_DATA = "export_data"

    # System administration
    MANAGE_ROLES = "manage_roles"
    IMPERSONATE_USERS = "impersonate_users"
    VIEW_SYSTEM_STATS = "view_system_stats"
    MANAGE_SYSTEM_STATUS = "manage_system_status"  # Update system status data (collector/API)
    EDIT_SYSTEM_STATUS = "edit_system_status"  # GUI create/edit/delete outages
    SYSTEM_ADMIN = "system_admin"  # Full access to everything


# POSIX-group-to-Permission mapping
#
# Keys are POSIX group names (e.g. real groups like 'nusd', 'hsg', 'csg',
# **and** synthetic dev-only bundles selected by ``DEV_GROUP_MAPPING``).
# A user receives the union of permissions across all groups they belong
# to that appear here.
#
# Groups that don't appear in this dict simply confer no permissions.
#
# TODO(rbac_refactor): the real POSIX group → permission bundles below
# (`nusd`, `hsg`, `csg`) are provisional. Confirm group names and
# permission contents with the team before the next deploy.
GROUP_PERMISSIONS: Dict[str, List[Permission]] = {
    # ---- Real POSIX group bundles (provisional) ----
    'nusd': [p for p in Permission],  # admin-equivalent: full access
    'hsg': [
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
        Permission.VIEW_FACILITIES,
        Permission.VIEW_GROUPS,
        Permission.VIEW_ORG_METADATA,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
        Permission.EDIT_SYSTEM_STATUS,
    ],
    'csg': [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_RESOURCES,
        Permission.VIEW_FACILITIES,
        Permission.VIEW_GROUPS,
        Permission.VIEW_ORG_METADATA,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
    ],

    # ---- Synthetic bundles selected by DEV_GROUP_MAPPING ----
    # These keys are not real POSIX groups; they let dev/test configs
    # assign specific permission sets to named usernames without
    # depending on adhoc_group data.
    'admin': [p for p in Permission],
    'facility_manager': [
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
        Permission.VIEW_FACILITIES,
        Permission.VIEW_GROUPS,
        Permission.VIEW_ORG_METADATA,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
        Permission.EDIT_SYSTEM_STATUS,
    ],
    'project_lead': [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_RESOURCES,
        Permission.VIEW_FACILITIES,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
    ],
    'user': [
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_CHARGE_SUMMARIES,
    ],
    'analyst': [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_PROJECT_MEMBERS,
        Permission.VIEW_ALLOCATIONS,
        Permission.VIEW_RESOURCES,
        Permission.VIEW_FACILITIES,
        Permission.VIEW_GROUPS,
        Permission.VIEW_ORG_METADATA,
        Permission.VIEW_REPORTS,
        Permission.VIEW_CHARGE_SUMMARIES,
        Permission.EXPORT_DATA,
        Permission.VIEW_SYSTEM_STATS,
    ],
}


# Per-user permission overrides
#
# Grants additional permissions to a specific username on top of
# whatever their group memberships confer. Useful for one-off privilege
# grants (e.g. a non-`hsg` user who needs EXPORT_DATA temporarily)
# without modifying group bundles or POSIX group membership.
#
# Keys: usernames. Values: set of Permission enum members to grant.
USER_PERMISSION_OVERRIDES: Dict[str, Set[Permission]] = {
    # 'someuser': {Permission.EXPORT_DATA, Permission.VIEW_REPORTS},
}


def get_user_permissions(user) -> Set[Permission]:
    """
    Get all permissions for a user.

    Composes the union of:
    - Permissions from each POSIX group the user belongs to that has a
      bundle in ``GROUP_PERMISSIONS`` (read from ``user.roles``, which
      is the set of bundle-matching group names the AuthUser exposed)
    - Per-user overrides from ``USER_PERMISSION_OVERRIDES``

    Args:
        user: AuthUser object (Flask-Login current_user)

    Returns:
        Set of Permission enum values the user has
    """
    permissions: Set[Permission] = set()

    for group_name in user.roles:
        if group_name in GROUP_PERMISSIONS:
            permissions.update(GROUP_PERMISSIONS[group_name])

    overrides = USER_PERMISSION_OVERRIDES.get(getattr(user, 'username', None))
    if overrides:
        permissions.update(overrides)

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
    return permission in get_user_permissions(user)


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
    Check if user belongs to a specific group bundle (display label).

    Note: 'role' here is the name of a ``GROUP_PERMISSIONS`` bundle
    (POSIX group name or synthetic dev bundle name). For authorization
    decisions prefer ``has_permission``; use this only for display logic
    or coarse role-name checks.
    """
    return user.has_role(role_name)


def has_any_role(user, *role_names: str) -> bool:
    """
    Check if user belongs to any of the specified group bundles.
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
    Decorator to require any of the specified group bundles.

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

        {# Project-scoped check via the conditional helper. Returns True
           when current_user has the system permission OR is project
           lead/admin (or ancestor lead/admin if include_ancestors=True). #}
        {% if can_act_on_project(Permission.EDIT_ALLOCATIONS, project, include_ancestors=True) %}
            <a href="...">Redistribute</a>
        {% endif %}
    """
    # Late import to avoid the circular path
    # rbac → project_permissions → rbac at module import time.
    from webapp.utils.project_permissions import _is_project_steward

    def _can_act_on_project(permission, project, include_ancestors=False):
        if project is None:
            return False
        if current_user is None or not current_user.is_authenticated:
            return False
        return _is_project_steward(
            current_user, project, permission, include_ancestors=include_ancestors
        )

    return {
        'Permission': Permission,
        'has_permission': lambda p: has_permission(current_user, p) if current_user.is_authenticated else False,
        'has_role': lambda r: has_role(current_user, r) if current_user.is_authenticated else False,
        'user_permissions': get_user_permissions(current_user) if current_user.is_authenticated else set(),
        'can_act_on_project': _can_act_on_project,
    }
