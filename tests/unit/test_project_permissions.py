"""
Tests for project-level permission utilities.

Tests the permission functions in webapp.utils.project_permissions module
that combine project-level roles (Lead, Admin, Member) with system-wide
RBAC permissions.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'python'))

from webapp.utils.project_permissions import (
    can_manage_project_members,
    can_change_admin,
    can_view_project_members,
    get_user_role_in_project
)


def create_mock_user(user_id: int, roles: list = None):
    """Create a mock user object for testing.

    Args:
        user_id: The user's ID
        roles: List of role names (e.g., ['admin'], ['facility_manager'], ['user'])

    Returns:
        Mock user object with required attributes
    """
    roles = roles or ['user']
    user = Mock()
    user.user_id = user_id
    user.roles = roles
    user.has_role = lambda r: r in roles
    user.has_any_role = lambda *rs: any(r in roles for r in rs)
    user.is_authenticated = True
    return user


def create_mock_project(project_lead_user_id: int, project_admin_user_id: int = None):
    """Create a mock project object for testing.

    Args:
        project_lead_user_id: User ID of the project lead
        project_admin_user_id: User ID of the project admin (optional)

    Returns:
        Mock project object with required attributes
    """
    project = Mock()
    project.project_lead_user_id = project_lead_user_id
    project.project_admin_user_id = project_admin_user_id
    return project


class TestCanManageProjectMembers:
    """Tests for can_manage_project_members() function."""

    def test_admin_role_can_manage(self):
        """System admin can manage any project members."""
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_manage_project_members(user, project) is True

    def test_facility_manager_can_manage(self):
        """Facility manager can manage any project members."""
        user = create_mock_user(user_id=100, roles=['facility_manager'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_manage_project_members(user, project) is True

    def test_project_lead_can_manage(self):
        """Project lead can manage their project's members."""
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_manage_project_members(user, project) is True

    def test_project_admin_can_manage(self):
        """Project admin can manage their project's members."""
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_manage_project_members(user, project) is True

    def test_regular_member_cannot_manage(self):
        """Regular project member cannot manage members."""
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_manage_project_members(user, project) is False

    def test_project_without_admin(self):
        """Project lead can manage even when no admin is set."""
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=None)

        assert can_manage_project_members(user, project) is True


class TestCanChangeAdmin:
    """Tests for can_change_admin() function."""

    def test_admin_role_can_change(self):
        """System admin can change any project's admin."""
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_change_admin(user, project) is True

    def test_facility_manager_can_change(self):
        """Facility manager can change any project's admin."""
        user = create_mock_user(user_id=100, roles=['facility_manager'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_change_admin(user, project) is True

    def test_project_lead_can_change(self):
        """Project lead can change admin for their project."""
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_change_admin(user, project) is True

    def test_project_admin_cannot_change(self):
        """Project admin cannot change admin (only lead can)."""
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_change_admin(user, project) is False

    def test_regular_member_cannot_change(self):
        """Regular project member cannot change admin."""
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_change_admin(user, project) is False


class TestCanViewProjectMembers:
    """Tests for can_view_project_members() function."""

    def test_admin_role_can_view(self):
        """System admin can view any project's members."""
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_view_project_members(user, project) is True

    def test_project_lead_can_view(self):
        """Project lead can view their project's members."""
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_view_project_members(user, project) is True

    def test_project_admin_can_view(self):
        """Project admin can view their project's members."""
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_view_project_members(user, project) is True

    def test_regular_member_can_view(self):
        """Regular authenticated users can currently view members."""
        # Note: Current implementation allows all authenticated users to view
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert can_view_project_members(user, project) is True


class TestGetUserRoleInProject:
    """Tests for get_user_role_in_project() function."""

    def test_returns_lead_for_project_lead(self):
        """Returns 'Lead' when user is the project lead."""
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert get_user_role_in_project(1, project) == 'Lead'

    def test_returns_admin_for_project_admin(self):
        """Returns 'Admin' when user is the project admin."""
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert get_user_role_in_project(2, project) == 'Admin'

    def test_returns_member_for_regular_user(self):
        """Returns 'Member' when user is neither lead nor admin."""
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)

        assert get_user_role_in_project(99, project) == 'Member'

    def test_returns_member_when_no_admin_set(self):
        """Returns 'Member' for non-lead when project has no admin."""
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=None)

        assert get_user_role_in_project(99, project) == 'Member'

    def test_lead_takes_precedence_over_admin(self):
        """If somehow same user is both lead and admin, returns 'Lead'."""
        # Edge case: same user as lead and admin
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=1)

        # Lead is checked first, so should return 'Lead'
        assert get_user_role_in_project(1, project) == 'Lead'
