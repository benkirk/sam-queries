"""Tests for project-level permission utilities.

Ported from tests/unit/test_project_permissions.py. No behavioral changes.
All tests use Python mocks; there is no database dependency. The legacy
file manipulated sys.path manually — unnecessary here because new_tests
runs inside an installed package.
"""
from unittest.mock import Mock

import pytest

from webapp.utils.project_permissions import (
    can_change_admin,
    can_manage_project_members,
    can_view_project_members,
    get_user_role_in_project,
)


pytestmark = pytest.mark.unit


def create_mock_user(user_id: int, roles: list = None):
    """Create a mock user object for testing."""
    roles = roles or ['user']
    user = Mock()
    user.user_id = user_id
    user.roles = roles
    user.has_role = lambda r: r in roles
    user.has_any_role = lambda *rs: any(r in roles for r in rs)
    user.is_authenticated = True
    return user


def create_mock_project(project_lead_user_id: int, project_admin_user_id: int = None):
    """Create a mock project object for testing."""
    project = Mock()
    project.project_lead_user_id = project_lead_user_id
    project.project_admin_user_id = project_admin_user_id
    return project


class TestCanManageProjectMembers:

    def test_admin_role_can_manage(self):
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_facility_manager_can_manage(self):
        user = create_mock_user(user_id=100, roles=['facility_manager'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_project_lead_can_manage(self):
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_project_admin_can_manage(self):
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_regular_member_cannot_manage(self):
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is False

    def test_project_without_admin(self):
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=None)
        assert can_manage_project_members(user, project) is True


class TestCanChangeAdmin:

    def test_admin_role_can_change(self):
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_facility_manager_can_change(self):
        user = create_mock_user(user_id=100, roles=['facility_manager'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_project_lead_can_change(self):
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_project_admin_cannot_change(self):
        """Project admin cannot change admin — only lead can."""
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is False

    def test_regular_member_cannot_change(self):
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is False


class TestCanViewProjectMembers:

    def test_admin_role_can_view(self):
        user = create_mock_user(user_id=100, roles=['admin'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_project_lead_can_view(self):
        user = create_mock_user(user_id=1, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_project_admin_can_view(self):
        user = create_mock_user(user_id=2, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_regular_member_can_view(self):
        """Current implementation allows any authenticated user to view members."""
        user = create_mock_user(user_id=99, roles=['user'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True


class TestGetUserRoleInProject:

    def test_returns_lead_for_project_lead(self):
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert get_user_role_in_project(1, project) == 'Lead'

    def test_returns_admin_for_project_admin(self):
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert get_user_role_in_project(2, project) == 'Admin'

    def test_returns_member_for_regular_user(self):
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert get_user_role_in_project(99, project) == 'Member'

    def test_returns_member_when_no_admin_set(self):
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=None)
        assert get_user_role_in_project(99, project) == 'Member'

    def test_lead_takes_precedence_over_admin(self):
        """If the same user is both lead and admin, returns 'Lead' (lead is checked first)."""
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=1)
        assert get_user_role_in_project(1, project) == 'Lead'
