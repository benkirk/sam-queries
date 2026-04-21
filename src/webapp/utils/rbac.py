"""
Role-Based Access Control (RBAC) utilities for SAM Web UI.

Defines permissions, POSIX-group-to-permission mappings, and utility
functions for checking access. Works with both Flask-Admin views and
custom API endpoints.

Authorization model
-------------------
A user's permissions are derived from two sources, unioned together:

1. **POSIX group membership** — each group the user belongs to may map
   to a bundle of permissions in ``GROUP_PERMISSIONS``. Group membership
   is read from ``adhoc_system_account_entry`` via
   ``get_user_group_access()`` in dev, test, and production alike.

2. **Per-user overrides** — ``USER_PERMISSION_OVERRIDES`` grants
   additional permissions to specific usernames on top of whatever
   their groups confer. Useful for one-off privilege grants without
   touching group membership.

There is **no dependency on the SAM ``role_user`` / ``role`` tables**;
those are not consulted by the webapp's RBAC layer.

The string set returned by ``AuthUser.roles`` is the set of POSIX group
names the user belongs to that have a ``GROUP_PERMISSIONS`` bundle.
This keeps ``has_role('csg')``-style checks working as a coarse display
label, but the source of truth for authorization is the permission set,
not the role label.
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
    ACCESS_ADMIN_DASHBOARD = "access_admin_dashboard"  # Land on /admin/ and see the navbar tab
    MANAGE_ROLES = "manage_roles"
    IMPERSONATE_USERS = "impersonate_users"  # Actually log in as another user
    VIEW_SYSTEM_STATS = "view_system_stats"
    MANAGE_SYSTEM_STATUS = "manage_system_status"  # Update system status data (collector/API)
    EDIT_SYSTEM_STATUS = "edit_system_status"  # GUI create/edit/delete outages
    SYSTEM_ADMIN = "system_admin"  # Full access to everything


# Building blocks for group bundles
# ----------------------------------
# ``_perms_with_action`` returns every Permission whose value starts
# with one of the given action prefixes — e.g. all ``VIEW_*`` or all
# ``EDIT_*``. The four ``ALL_*`` constants below pre-compute the common
# slices so bundles can use plain set arithmetic:
#
#     'foo': ALL_VIEW | ALL_EDIT | {Permission.EXPORT_DATA}
#     'bar': ALL_VIEW - {Permission.VIEW_GROUPS}
#
# When a new entity domain (e.g. CONTRACTS) gets a full CRUD set in the
# Permission enum, every bundle expressed via these constants picks up
# the new permissions automatically — no need to edit each bundle.
def _perms_with_action(*action_prefixes: str) -> Set[Permission]:
    """All Permission members whose value starts with one of the given
    action prefixes (``'view'``, ``'edit'``, ``'create'``, ``'delete'``)."""
    return {
        p for p in Permission
        if any(p.value.startswith(f'{a}_') for a in action_prefixes)
    }


ALL_VIEW   = _perms_with_action('view')
ALL_EDIT   = _perms_with_action('edit')
ALL_CREATE = _perms_with_action('create')
ALL_DELETE = _perms_with_action('delete')


# POSIX-group-to-Permission mapping
#
# Keys are POSIX group names (e.g. real groups like 'csg', 'nusd', 'hsg'.
# A user receives the union of permissions across all groups they belong
# to that appear here.
#
# Groups that don't appear in this dict simply confer no permissions.
GROUP_PERMISSIONS: Dict[str, Set[Permission]] = {
    # ---- Real POSIX group bundles (provisional) ----

    # csg: full access (admin-equivalent).
    #'csg': set(Permission),

    # nusd: read, edit,everything + write to projects, allocations
    # and system status. Does NOT confer write on users/groups/facilities/
    # org_metadata (those remain admin-only). May impersonate any user
    # whose permission set is a subset of nusd's (the can_impersonate
    # rule blocks escalation).
    'nusd': ALL_VIEW | ALL_EDIT | {
        Permission.ACCESS_ADMIN_DASHBOARD,
        Permission.CREATE_PROJECTS,
        Permission.CREATE_ALLOCATIONS,
        Permission.IMPERSONATE_USERS,
    },

    # hsg: read-only across the board,
    # resources permissions, and edit system status (for outages etc...)
    'hsg': ALL_VIEW | {
        Permission.ACCESS_ADMIN_DASHBOARD,
        Permission.EDIT_RESOURCES, Permission.CREATE_RESOURCES,
        Permission.EDIT_SYSTEM_STATUS
    },
}

# csg - same as nusd
GROUP_PERMISSIONS['csg'] = GROUP_PERMISSIONS['nusd']

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
    'benkirk' : [p for p in Permission],  # admin-equivalent: full access
}


# Per-user, per-facility permission grants — the third RBAC tier.
#
# A user is granted ``permission`` here only when the target project's
# facility is in the configured set. Permissions held here are ADDITIVE
# to whatever ``USER_PERMISSION_OVERRIDES`` / ``GROUP_PERMISSIONS``
# confer (which apply unconditionally).
#
# Example — a WNA-scoped manager who may CRUD WNA projects/allocations
# but has no authority anywhere else:
#
#     'sureshm': {
#         'WNA': {
#             Permission.CREATE_PROJECTS, Permission.EDIT_PROJECTS,
#             Permission.CREATE_ALLOCATIONS, Permission.EDIT_ALLOCATIONS,
#             ...
#         },
#     }
#
# Multi-facility entries are supported — the outer dict's value may
# name any number of facilities, each mapping to its own permission set.
#
# Format: {username: {facility_name: {Permission, ...}}}
USER_FACILITY_PERMISSIONS: Dict[str, Dict[str, Set[Permission]]] = {
    # WNA-scoped admin — provisions and manages WNA projects and
    # allocations. Holds no authority over NCAR/UNIV/CISL/CSL/XSEDE/ASD.
    'sureshm': {
        'WNA': {
            Permission.ACCESS_ADMIN_DASHBOARD,
            Permission.VIEW_PROJECTS,
            Permission.EDIT_PROJECTS,
            Permission.CREATE_PROJECTS,
            Permission.VIEW_PROJECT_MEMBERS,
            Permission.EDIT_PROJECT_MEMBERS,
            Permission.VIEW_ALLOCATIONS,
            Permission.EDIT_ALLOCATIONS,
            Permission.CREATE_ALLOCATIONS,
            Permission.VIEW_ORG_METADATA,
        },
    },
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


def has_permission_for_facility(user, permission: Permission,
                                facility_name) -> bool:
    """
    Check if ``user`` holds ``permission`` for the given facility.

    True iff either:
    - The user has ``permission`` unconditionally (system grant via
      groups or ``USER_PERMISSION_OVERRIDES``) — applies to any facility.
    - ``USER_FACILITY_PERMISSIONS[user.username][facility_name]``
      contains ``permission``.

    Args:
        user: AuthUser object (Flask-Login current_user). Unauthenticated
            users always fail.
        permission: Permission enum member to check.
        facility_name: ``Facility.facility_name`` string, or ``None``
            for orphan projects (no allocation_type chain). Orphans can
            only be acted on by unscoped system-permission holders.

    Returns:
        True if the permission applies to this facility, else False.
    """
    # System grant — applies to every facility, including unknown ones.
    if has_permission(user, permission):
        return True
    if facility_name is None:
        # Orphan projects: only unscoped system-permission holders can act.
        return False
    if not getattr(user, 'is_authenticated', False):
        return False
    username = getattr(user, 'username', None)
    if username is None:
        return False
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return permission in scoped.get(facility_name, set())


def has_permission_any_facility(user, permission: Permission) -> bool:
    """True if ``user`` can exercise ``permission`` **somewhere** —
    either unconditionally (system grant) or in at least one facility
    via ``USER_FACILITY_PERMISSIONS``.

    Use this for route-level gates that admit scoped users: they reach
    the route, and the body intersects their scope against whatever
    the request targeted (listing filter, create-target facility, …).

    Contrast with ``has_permission``: that one answers "does the user
    hold this unconditionally?" — the right question for routes that
    must remain pure system-admin domain (impersonation, system
    status, etc.)."""
    if has_permission(user, permission):
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    username = getattr(user, 'username', None)
    if username is None:
        return False
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return any(permission in perms for perms in scoped.values())


def user_facility_scope(user, permission: Permission):
    """
    Return the set of facility names where ``user`` may exercise
    ``permission``, or ``None`` for "unscoped" (any facility, including
    orphan projects).

    Use at listing-filter call sites:
      - ``None`` → skip the facility filter entirely (system-permission
        holder; sees everything).
      - ``set`` → constrain results to those facilities.
      - empty ``set`` → user has no way to exercise this permission.

    Args:
        user: AuthUser object.
        permission: Permission enum member.
    """
    if has_permission(user, permission):
        return None
    if not getattr(user, 'is_authenticated', False):
        return set()
    username = getattr(user, 'username', None)
    if username is None:
        return set()
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return {f for f, perms in scoped.items() if permission in perms}


def apply_facility_scope(requested, permission: Permission, default=None):
    """
    Combine a user-submitted ``facilities`` list with the caller's
    facility-scoped RBAC grants for ``permission``, returning the
    effective facility-name list to pass to downstream queries.

    Semantics:
    - **Unscoped users** (system-permission holders): ``requested``
      wins; if empty, ``default`` applies; ``None`` means
      "no restriction".
    - **Scoped users**: returns the intersection of ``requested`` with
      their allowed set. Falls back to the full allowed set when the
      request is empty or the intersection is empty (clamp, don't
      error — the user just asked for nothing they can see).
    - **Users with an empty scope** (no entry at all): returns ``[]``.
      Caller should treat as "no rows".

    Used as the single source of truth for "what facility names do I
    actually filter on, given this user + this request?" at both the
    admin expirations/search routes and the allocations dashboard.
    """
    allowed = user_facility_scope(current_user, permission)
    if allowed is None:
        return list(requested) if requested else (list(default) if default else None)
    if not allowed:
        return []
    if not requested:
        return sorted(allowed)
    intersected = [f for f in requested if f in allowed]
    return intersected or sorted(allowed)


def filter_rows_by_facility(rows, allowed):
    """Drop rows whose ``'facility'`` key isn't in ``allowed``.

    Pass ``None`` for ``allowed`` to skip filtering (unscoped / global
    view). Used by the allocations dashboard's post-fetch scope filter
    — every row returned by the summary / usage / transactions
    queries carries a ``'facility'`` field."""
    if allowed is None:
        return rows
    if not allowed:
        return []
    allowed_set = allowed if isinstance(allowed, (set, frozenset)) else set(allowed)
    return [r for r in rows if r.get('facility') in allowed_set]


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


def can_impersonate(caller, target) -> bool:
    """
    Decide whether ``caller`` is permitted to impersonate ``target``.

    No-escalation rule: ``target``'s permission set must be a subset of
    ``caller``'s. Equal sets (peer impersonation) are allowed; strictly
    smaller sets ("lessor" users — regular users, project leads with no
    system permissions, etc.) are allowed; any permission ``target``
    holds that ``caller`` does not blocks the impersonation.

    Note: this does NOT check whether ``caller`` has
    ``Permission.IMPERSONATE_USERS`` — the route decorator should still
    gate that. ``can_impersonate`` only enforces the no-escalation
    invariant once impersonation is otherwise allowed.

    Args:
        caller: AuthUser doing the impersonation.
        target: AuthUser being impersonated.

    Returns:
        True if ``target``'s permissions are a (non-strict) subset of
        ``caller``'s permissions; False otherwise.
    """
    return get_user_permissions(target) <= get_user_permissions(caller)


def has_role(user, role_name: str) -> bool:
    """
    Check if user belongs to a specific group bundle (display label).

    Note: 'role' here is the name of a ``GROUP_PERMISSIONS`` bundle
    (POSIX group name). For authorization decisions prefer
    ``has_permission``; use this only for display logic or coarse
    role-name checks.
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


def require_permission_any_facility(permission: Permission):
    """
    Decorator that admits callers who hold ``permission`` either
    unconditionally (system grant) **or** in at least one facility via
    ``USER_FACILITY_PERMISSIONS``.

    The route body is then responsible for intersecting the user's
    facility scope against whatever the request targets. Use for admin
    routes that a facility-scoped manager must be able to reach (e.g.
    the admin dashboard, project search, expirations fragment,
    project-create form) even though they don't hold the permission
    globally.

    For routes that must remain pure system-admin domain (impersonation,
    global system administration), use ``require_permission`` instead.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not has_permission_any_facility(current_user, permission):
                abort(403)
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
        'has_permission_any_facility': lambda p: (
            has_permission_any_facility(current_user, p)
            if current_user.is_authenticated else False
        ),
        'has_role': lambda r: has_role(current_user, r) if current_user.is_authenticated else False,
        'user_permissions': get_user_permissions(current_user) if current_user.is_authenticated else set(),
        'can_act_on_project': _can_act_on_project,
    }
