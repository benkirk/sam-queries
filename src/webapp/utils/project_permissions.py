"""
Project-level permission utilities for SAM Web UI.

Provides project-scoped authorization checks that combine:
1. Project-level roles (Lead, Admin, Member) — scoped per-project
2. System-wide RBAC permissions (admin, facility_manager, …) — global override

This allows both project owners AND system administrators to manage
project-scoped operations (member changes, allocation edits, threshold
tuning, etc.).

The shared shape — "system permission OR project lead/admin (optionally
walking ancestors)" — lives in ``_is_project_steward``. All public
``can_*`` helpers delegate to it so the rule set stays consistent.
"""

from webapp.utils.rbac import has_permission, Permission


def _is_project_steward(
    user,
    project,
    system_permission: Permission,
    *,
    include_ancestors: bool = False,
) -> bool:
    """
    Authorization primitive used by every project-scoped ``can_*`` check.

    Returns True if **any** of:
    - The user has ``system_permission`` (e.g. EDIT_PROJECT_MEMBERS,
      EDIT_ALLOCATIONS) — system-wide override.
    - The user is the project lead.
    - The user is the project admin.
    - When ``include_ancestors`` is True: the user is lead or admin of
      any ancestor in the project tree (walks ``project.parent`` up to
      the root).

    ``include_ancestors=True`` is the right choice for operations that
    apply to a project's subtree — e.g. allocation redistribution,
    where the lead of a parent project should be able to act on
    allocations of its children.

    Args:
        user: AuthUser object (Flask-Login current_user)
        project: Project ORM object
        system_permission: System-wide permission that grants access
            without consulting project-level roles.
        include_ancestors: If True, lead/admin of any ancestor counts.
    """
    if has_permission(user, system_permission):
        return True

    user_id = getattr(user, 'user_id', None)
    if user_id is None:
        return False

    candidates = [project]
    if include_ancestors:
        current = project.parent
        while current is not None:
            candidates.append(current)
            current = current.parent

    for p in candidates:
        if p.project_lead_user_id == user_id:
            return True
        if p.project_admin_user_id and p.project_admin_user_id == user_id:
            return True

    return False


def can_access_edit_project_page(user, project) -> bool:
    """
    Enter the admin Edit Project page (/admin/project/<projcode>/edit).

    Granted to: system EDIT_PROJECTS holders, project lead, project admin.
    The page itself opens all three tabs (Details, Allocations, Members);
    per-field / per-action gates on each tab constrain what a non-admin
    steward can actually change.
    """
    return _is_project_steward(user, project, Permission.EDIT_PROJECTS)


def can_edit_project_governance(user, project) -> bool:
    """
    Edit the governance fields on the Details tab — facility, panel,
    allocation type, lead, admin, active, charging_exempt, ext_alias.

    These fields shape the project's financial / organizational
    identity. System EDIT_PROJECTS holders only — no steward override.
    Project leads can still reassign the admin role via the Members
    tab's dedicated change-admin flow (``can_change_admin``).
    """
    return has_permission(user, Permission.EDIT_PROJECTS)


def can_manage_project_members(user, project) -> bool:
    """
    Add/remove members from a project.

    Granted to: system EDIT_PROJECT_MEMBERS holders, project lead,
    project admin.
    """
    return _is_project_steward(user, project, Permission.EDIT_PROJECT_MEMBERS)


def can_change_admin(user, project) -> bool:
    """
    Change the project admin role.

    Granted to: system EDIT_PROJECT_MEMBERS holders or the project lead.
    The current admin can NOT reassign the admin role to someone else
    (lead-only invariant).
    """
    if has_permission(user, Permission.EDIT_PROJECT_MEMBERS):
        return True

    user_id = getattr(user, 'user_id', None)
    if user_id is None:
        return False
    return project.project_lead_user_id == user_id


def can_view_project_members(user, project) -> bool:
    """
    View a project's member list.

    Permissive: system VIEW_PROJECT_MEMBERS holders, project lead/admin,
    and any authenticated user (members of a project are non-sensitive).
    Tighten if requirements change.
    """
    if has_permission(user, Permission.VIEW_PROJECT_MEMBERS):
        return True

    user_id = getattr(user, 'user_id', None)
    if user_id is None:
        return False
    if project.project_lead_user_id == user_id:
        return True
    if project.project_admin_user_id and project.project_admin_user_id == user_id:
        return True

    # Permissive default — see docstring.
    return True


def can_edit_allocations(user, project) -> bool:
    """
    Edit allocation values (amount, dates, description).

    Granted to: system EDIT_ALLOCATIONS holders, OR project lead/admin
    of this project OR any ancestor in the project tree. The
    ancestor walk supports redistribution within a subtree the user
    leads — e.g. a lead of project A can edit allocations on its
    children A1, A2.
    """
    return _is_project_steward(
        user, project, Permission.EDIT_ALLOCATIONS, include_ancestors=True
    )


# Allocation redistribution is a specialization of allocation editing
# (same authorization, distinct UI/API surface). The alias exists so
# callers can use the name that reads best at the call site.
can_redistribute_allocations = can_edit_allocations


def can_edit_consumption_threshold(user, project) -> bool:
    """
    Set/change rolling consumption-rate thresholds for a project.

    Granted to: system EDIT_PROJECT_MEMBERS holders, project lead,
    project admin. Does NOT walk ancestors — thresholds are scoped to
    the specific project.
    """
    return _is_project_steward(user, project, Permission.EDIT_PROJECT_MEMBERS)


def get_user_role_in_project(user_id: int, project) -> str:
    """
    Get the role of a user in a project.

    Returns: 'Lead', 'Admin', or 'Member'.
    """
    if project.project_lead_user_id == user_id:
        return 'Lead'
    if project.project_admin_user_id and project.project_admin_user_id == user_id:
        return 'Admin'
    return 'Member'
