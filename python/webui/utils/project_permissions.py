"""
Project-level permission utilities for SAM Web UI.

Provides project-specific authorization checks that combine:
1. Project-level roles (Lead, Admin, Member) - scoped per-project
2. System-wide RBAC permissions (admin, facility_manager) - global override

This allows both project owners AND system administrators to manage project membership.
"""

from webui.utils.rbac import has_permission, Permission


def can_manage_project_members(user, project) -> bool:
    """
    Check if user can add/remove members from a project.

    Authorization is granted if ANY of these conditions are true:
    - User has system-wide EDIT_PROJECT_MEMBERS permission (admin, facility_manager)
    - User is the project lead
    - User is the project admin

    Args:
        user: AuthUser object (Flask-Login current_user)
        project: Project ORM object

    Returns:
        True if user can manage members, False otherwise
    """
    # System-wide permission (admin, facility_manager)
    if has_permission(user, Permission.EDIT_PROJECT_MEMBERS):
        return True

    # Project lead
    if project.project_lead_user_id == user.user_id:
        return True

    # Project admin
    if project.project_admin_user_id and project.project_admin_user_id == user.user_id:
        return True

    return False


def can_change_admin(user, project) -> bool:
    """
    Check if user can change the project admin role.

    Only the project lead or system admin can change who the admin is.
    The current admin cannot reassign the admin role to someone else.

    Args:
        user: AuthUser object (Flask-Login current_user)
        project: Project ORM object

    Returns:
        True if user can change admin, False otherwise
    """
    # System-wide permission (admin, facility_manager)
    if has_permission(user, Permission.EDIT_PROJECT_MEMBERS):
        return True

    # Only project lead (not admin) can change admin
    if project.project_lead_user_id == user.user_id:
        return True

    return False


def can_view_project_members(user, project) -> bool:
    """
    Check if user can view project members.

    Currently all authenticated users who are members of a project can view
    its members. System users with VIEW_PROJECT_MEMBERS can view any project.

    Args:
        user: AuthUser object (Flask-Login current_user)
        project: Project ORM object

    Returns:
        True if user can view members, False otherwise
    """
    # System-wide permission
    if has_permission(user, Permission.VIEW_PROJECT_MEMBERS):
        return True

    # Project lead or admin
    if project.project_lead_user_id == user.user_id:
        return True
    if project.project_admin_user_id and project.project_admin_user_id == user.user_id:
        return True

    # For now, any authenticated user can view members of projects they're on
    # This could be restricted further if needed
    return True


def get_user_role_in_project(user_id: int, project) -> str:
    """
    Get the role of a user in a project.

    Args:
        user_id: The user's ID to check
        project: Project ORM object

    Returns:
        Role string: 'Lead', 'Admin', or 'Member'
    """
    if project.project_lead_user_id == user_id:
        return 'Lead'
    if project.project_admin_user_id and project.project_admin_user_id == user_id:
        return 'Admin'
    return 'Member'
