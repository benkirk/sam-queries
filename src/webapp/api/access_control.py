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
from webapp.utils.rbac import has_permission, has_permission_for_facility, Permission
from webapp.utils.project_permissions import _is_project_steward
from webapp.api.helpers import get_project_or_404


def _get_sam_user():
    """Get SAM user record for current_user."""
    from sam.core.users import User
    return db.session.query(User).filter_by(user_id=current_user.user_id).first()


def _user_can_access_project(user, project, *, include_ancestors: bool = False) -> bool:
    """
    Check if a SAM user can access project data.

    Users can access project data if they are:
    - A member of the project (active membership)
    - The project lead
    - A project admin

    With ``include_ancestors=True``, also grant access when the user
    is the lead or admin of any ancestor in the project tree. This
    models tree-governance: a user who leads a parent can view (or
    act on, depending on the caller) projects within the subtree they
    govern. Plain ``AccountUser`` membership is intentionally NOT
    propagated up the tree — being a member of a parent does not
    imply visibility into child projects.

    Args:
        user: SAM User object
        project: Project object to check access for
        include_ancestors: If True, lead/admin of any ancestor counts.

    Returns:
        bool: True if user can access, False otherwise
    """
    if not user:
        return False

    # Direct affiliation on THIS project.
    direct_projects = {p.project_id for p in user.active_projects()}
    direct_projects.update({p.project_id for p in user.led_projects})
    direct_projects.update({p.project_id for p in user.admin_projects})
    if project.project_id in direct_projects:
        return True

    if include_ancestors:
        led_or_admin = {p.project_id for p in user.led_projects}
        led_or_admin.update({p.project_id for p in user.admin_projects})
        current = project.parent
        while current is not None:
            if current.project_id in led_or_admin:
                return True
            current = current.parent

    return False


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


def require_project_access(f: Callable = None, *, include_ancestors: bool = False):
    """
    Decorator to require access to project specified by projcode URL parameter.

    The decorated function receives the project object instead of projcode.
    Access is granted if user has VIEW_PROJECTS permission OR is a project
    member. With ``include_ancestors=True``, also admits the lead/admin of
    any ancestor in the project tree (tree-governance grant).

    Supports both bare and factory usage:

        @bp.route('/<projcode>/data')
        @login_required
        @require_project_access                 # bare — no ancestor walk
        def get_project_data(project):
            ...

        @bp.route('/<projcode>/modal')
        @login_required
        @require_project_access(include_ancestors=True)
        def project_modal(project):
            ...
    """
    # Factory-form: @require_project_access(include_ancestors=True)
    if f is None:
        return lambda fn: require_project_access(fn, include_ancestors=include_ancestors)

    @wraps(f)
    def decorated_function(projcode: str, *args, **kwargs):
        project, error = get_project_or_404(db.session, projcode)
        if error:
            return error

        if has_permission_for_facility(
            current_user, Permission.VIEW_PROJECTS, project.facility_name
        ):
            return f(project, *args, **kwargs)

        sam_user = _get_sam_user()
        if _user_can_access_project(sam_user, project, include_ancestors=include_ancestors):
            return f(project, *args, **kwargs)

        abort(403)

    return decorated_function


def require_project_member_access(
    permission: Permission, *, include_ancestors: bool = False
) -> Callable:
    """
    Decorator factory for project endpoints requiring specific permission OR membership.

    Args:
        permission: Permission that grants access without membership check.
        include_ancestors: If True, also admits the lead/admin of any
            ancestor in the project tree (tree-governance grant).

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

            # Check access: specific permission (incl. facility scope) OR
            # user is project member.
            if has_permission_for_facility(
                current_user, permission, project.facility_name
            ):
                return f(project, *args, **kwargs)

            sam_user = _get_sam_user()
            if _user_can_access_project(sam_user, project, include_ancestors=include_ancestors):
                return f(project, *args, **kwargs)

            abort(403)

        return decorated_function
    return decorator


def require_project_permission(
    permission: Permission, *, include_ancestors: bool = False
) -> Callable:
    """
    Decorator factory for project-mutating routes.

    Grants access if the user has ``permission`` system-wide OR is the
    project's lead/admin (optionally walking the project tree).
    Resolves ``<projcode>`` from the URL and passes the ``project``
    object to the decorated function.

    This is the right decorator for project-scoped mutations (member
    add/remove, threshold edits, allocation redistribution within a
    subtree). For pure read access where any project member should
    be allowed, use ``require_project_member_access`` instead.

    Args:
        permission: System-wide permission that grants access without
            consulting project-level roles.
        include_ancestors: If True, lead/admin of any ancestor project
            counts. Use this for operations that act on a subtree
            (e.g. allocation redistribution).

    Usage:
        @bp.route('/<projcode>/members', methods=['POST'])
        @login_required
        @require_project_permission(Permission.EDIT_PROJECT_MEMBERS)
        def add_member(project):
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(projcode: str, *args, **kwargs):
            project, error = get_project_or_404(db.session, projcode)
            if error:
                return error

            if not _is_project_steward(
                current_user, project, permission, include_ancestors=include_ancestors
            ):
                abort(403)

            return f(project, *args, **kwargs)

        return decorated_function
    return decorator


def require_allocation_permission(permission: Permission) -> Callable:
    """
    Decorator factory for allocation-mutating routes.

    Resolves ``<allocation_id>`` (or ``<int:allocation_id>``) from the
    URL to an Allocation, walks ``allocation.account.project``, and
    grants access if the user has ``permission`` system-wide OR is
    lead/admin of the allocation's project or any ancestor (covers
    redistribution within a subtree the user owns).

    Passes the ``allocation`` object to the decorated function in
    place of ``allocation_id``.

    Usage:
        @bp.route('/<int:allocation_id>', methods=['PUT'])
        @login_required
        @require_allocation_permission(Permission.EDIT_ALLOCATIONS)
        def update_allocation(allocation):
            ...
    """
    # Late import: Allocation lives in sam.accounting and importing it
    # at module load triggers the SAM ORM init chain.
    from sam.accounting.allocations import Allocation

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(allocation_id: int, *args, **kwargs):
            allocation = db.session.get(Allocation, int(allocation_id))
            if allocation is None:
                abort(404)

            account = allocation.account
            project = account.project if account is not None else None
            if project is None:
                # Orphaned allocation — no project to authorize against.
                abort(403)

            if not _is_project_steward(
                current_user, project, permission, include_ancestors=True
            ):
                abort(403)

            return f(allocation, *args, **kwargs)

        return decorated_function
    return decorator
