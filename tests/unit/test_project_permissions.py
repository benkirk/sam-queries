"""Tests for project-level permission utilities.

Ported from tests/unit/test_project_permissions.py. No behavioral changes.
All tests use Python mocks; there is no database dependency. The legacy
file manipulated sys.path manually — unnecessary here because new_tests
runs inside an installed package.
"""
from unittest.mock import Mock

import pytest

from webapp.utils.project_permissions import (
    _is_project_steward,
    can_change_admin,
    can_edit_allocations,
    can_edit_consumption_threshold,
    can_manage_project_members,
    can_redistribute_allocations,
    can_view_project_members,
    get_user_role_in_project,
)
from webapp.utils.rbac import Permission


pytestmark = pytest.mark.unit


def create_mock_user(user_id: int, roles: list = None, username: str = 'stubuser'):
    """Create a mock user object for testing."""
    roles = roles or ['user']
    user = Mock()
    user.user_id = user_id
    user.roles = roles
    user.username = username
    user.has_role = lambda r: r in roles
    user.has_any_role = lambda *rs: any(r in roles for r in rs)
    user.is_authenticated = True
    return user


def create_mock_project(project_lead_user_id: int, project_admin_user_id: int = None,
                        parent: Mock = None, facility_name: str = None):
    """Create a mock project object for testing.

    ``parent`` is the Project ORM ``parent`` relationship — used by the
    Phase-2 ancestor-walk tests. Default None means root.
    ``facility_name`` stubs the Phase-3 property that feeds
    facility-scoped RBAC; default ``None`` = orphan project.
    """
    project = Mock()
    project.project_lead_user_id = project_lead_user_id
    project.project_admin_user_id = project_admin_user_id
    project.parent = parent
    project.facility_name = facility_name
    return project


class TestCanManageProjectMembers:

    def test_admin_role_can_manage(self):
        user = create_mock_user(user_id=100, roles=['admin-testing-only'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_facility_manager_can_manage(self):
        user = create_mock_user(user_id=100, roles=['nusd'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_project_lead_can_manage(self):
        user = create_mock_user(user_id=1, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_project_admin_can_manage(self):
        user = create_mock_user(user_id=2, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is True

    def test_regular_member_cannot_manage(self):
        user = create_mock_user(user_id=99, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_manage_project_members(user, project) is False

    def test_project_without_admin(self):
        user = create_mock_user(user_id=1, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=None)
        assert can_manage_project_members(user, project) is True


class TestCanChangeAdmin:

    def test_admin_role_can_change(self):
        user = create_mock_user(user_id=100, roles=['admin-testing-only'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_facility_manager_can_change(self):
        user = create_mock_user(user_id=100, roles=['nusd'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_project_lead_can_change(self):
        user = create_mock_user(user_id=1, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is True

    def test_project_admin_cannot_change(self):
        """Project admin cannot change admin — only lead can."""
        user = create_mock_user(user_id=2, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is False

    def test_regular_member_cannot_change(self):
        user = create_mock_user(user_id=99, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_change_admin(user, project) is False


class TestCanViewProjectMembers:

    def test_admin_role_can_view(self):
        user = create_mock_user(user_id=100, roles=['admin-testing-only'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_project_lead_can_view(self):
        user = create_mock_user(user_id=1, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_project_admin_can_view(self):
        user = create_mock_user(user_id=2, roles=[])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert can_view_project_members(user, project) is True

    def test_regular_member_can_view(self):
        """Current implementation allows any authenticated user to view members."""
        user = create_mock_user(user_id=99, roles=[])
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


# ---------------------------------------------------------------------------
# Phase 2: _is_project_steward — central authorization primitive
# ---------------------------------------------------------------------------

class TestIsProjectSteward:
    """All ``can_*`` helpers route through ``_is_project_steward``."""

    def test_system_permission_holder_passes_without_role_check(self):
        # 'admin-testing-only' grants every permission, including EDIT_ALLOCATIONS.
        user = create_mock_user(user_id=100, roles=['admin-testing-only'])
        project = create_mock_project(project_lead_user_id=1, project_admin_user_id=2)
        assert _is_project_steward(user, project, Permission.EDIT_ALLOCATIONS)

    def test_lead_passes_for_their_project(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=42)
        assert _is_project_steward(user, project, Permission.EDIT_ALLOCATIONS)

    def test_admin_passes_for_their_project(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=42)
        assert _is_project_steward(user, project, Permission.EDIT_ALLOCATIONS)

    def test_outsider_blocked(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=998)
        assert not _is_project_steward(user, project, Permission.EDIT_ALLOCATIONS)

    def test_ancestor_lead_passes_when_include_ancestors(self):
        grandparent = create_mock_project(project_lead_user_id=42)
        parent = create_mock_project(project_lead_user_id=999, parent=grandparent)
        child = create_mock_project(project_lead_user_id=998, parent=parent)
        user = create_mock_user(user_id=42, roles=[])
        assert _is_project_steward(
            user, child, Permission.EDIT_ALLOCATIONS, include_ancestors=True
        )

    def test_ancestor_lead_blocked_without_include_ancestors(self):
        parent = create_mock_project(project_lead_user_id=42)
        child = create_mock_project(project_lead_user_id=999, parent=parent)
        user = create_mock_user(user_id=42, roles=[])
        assert not _is_project_steward(
            user, child, Permission.EDIT_ALLOCATIONS, include_ancestors=False
        )

    def test_ancestor_admin_passes_when_include_ancestors(self):
        parent = create_mock_project(project_lead_user_id=999, project_admin_user_id=42)
        child = create_mock_project(project_lead_user_id=998, parent=parent)
        user = create_mock_user(user_id=42, roles=[])
        assert _is_project_steward(
            user, child, Permission.EDIT_ALLOCATIONS, include_ancestors=True
        )

    def test_unauthenticated_user_blocked(self):
        user = Mock()
        user.user_id = None  # AnonymousUserMixin path
        user.roles = set()
        user.has_role = lambda r: False
        project = create_mock_project(project_lead_user_id=42)
        assert not _is_project_steward(user, project, Permission.EDIT_ALLOCATIONS)

    def test_facility_scoped_user_passes_for_matching_facility(self, monkeypatch):
        # A user with no system perms and no project role, but a scoped
        # EDIT_PROJECTS grant for this project's facility, should pass.
        from webapp.utils import rbac
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = create_mock_user(user_id=77, roles=[], username='mgr')
        project = create_mock_project(
            project_lead_user_id=1, project_admin_user_id=2,
            facility_name='WNA',
        )
        assert _is_project_steward(user, project, Permission.EDIT_PROJECTS)

    def test_facility_scoped_user_blocked_for_other_facility(self, monkeypatch):
        # Scoped to WNA but project is NCAR → no system perm, no role,
        # no scope match → denied.
        from webapp.utils import rbac
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = create_mock_user(user_id=77, roles=[], username='mgr')
        project = create_mock_project(
            project_lead_user_id=1, project_admin_user_id=2,
            facility_name='NCAR',
        )
        assert not _is_project_steward(user, project, Permission.EDIT_PROJECTS)

    def test_facility_scoped_user_still_wins_via_lead_role(self, monkeypatch):
        # Scope doesn't cover the facility, but user is the project lead
        # → lead short-circuit still applies. Important: facility scope
        # is additive, not replacement.
        from webapp.utils import rbac
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = create_mock_user(user_id=77, roles=[], username='mgr')
        project = create_mock_project(
            project_lead_user_id=77, project_admin_user_id=None,
            facility_name='NCAR',
        )
        assert _is_project_steward(user, project, Permission.EDIT_PROJECTS)

    def test_orphan_project_denies_scoped_user(self, monkeypatch):
        # facility_name=None means no allocation_type chain exists;
        # the facility-scoped user cannot act — only unscoped system
        # holders may.
        from webapp.utils import rbac
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = create_mock_user(user_id=77, roles=[], username='mgr')
        project = create_mock_project(
            project_lead_user_id=1, project_admin_user_id=2,
            facility_name=None,
        )
        assert not _is_project_steward(user, project, Permission.EDIT_PROJECTS)

    def test_orphan_project_still_reachable_by_system_admin(self):
        # Regression guard: orphan projects must remain manageable by
        # users with the system-wide grant.
        user = create_mock_user(user_id=100, roles=['admin-testing-only'])
        project = create_mock_project(
            project_lead_user_id=1, project_admin_user_id=2,
            facility_name=None,
        )
        assert _is_project_steward(user, project, Permission.EDIT_PROJECTS)


# ---------------------------------------------------------------------------
# Phase 2: can_edit_allocations BEHAVIOR CHANGE
# ---------------------------------------------------------------------------

class TestCanEditAllocations:
    """``can_edit_allocations`` previously short-circuited to
    ``has_permission(EDIT_ALLOCATIONS)`` only — i.e. system admins only.
    Phase 2 grants project lead/admin (and ancestor lead/admin) too,
    enabling allocation redistribution within a project subtree."""

    def test_lead_can_edit_their_allocation(self):
        # Regression target — used to return False for non-system users.
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=42)
        assert can_edit_allocations(user, project)

    def test_admin_can_edit_their_allocation(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=42)
        assert can_edit_allocations(user, project)

    def test_ancestor_lead_can_edit_descendant_allocation(self):
        # The redistribution-within-tree use case.
        parent = create_mock_project(project_lead_user_id=42)
        child = create_mock_project(project_lead_user_id=998, parent=parent)
        user = create_mock_user(user_id=42, roles=[])
        assert can_edit_allocations(user, child)

    def test_outsider_still_blocked(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=998)
        assert not can_edit_allocations(user, project)

    def test_facility_manager_grants_via_system_permission(self):
        user = create_mock_user(user_id=99, roles=['nusd'])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=998)
        assert can_edit_allocations(user, project)

    def test_redistribute_is_alias(self):
        # Same authorization, different name at the call site.
        assert can_redistribute_allocations is can_edit_allocations


# ---------------------------------------------------------------------------
# Phase 2: can_edit_consumption_threshold — should NOT walk ancestors
# ---------------------------------------------------------------------------

class TestCanEditConsumptionThreshold:
    """Threshold is a per-project tuning, not a tree-scoped operation."""

    def test_lead_can_edit_threshold(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=42)
        assert can_edit_consumption_threshold(user, project)

    def test_admin_can_edit_threshold(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=42)
        assert can_edit_consumption_threshold(user, project)

    def test_ancestor_lead_cannot_edit_descendant_threshold(self):
        parent = create_mock_project(project_lead_user_id=42)
        child = create_mock_project(project_lead_user_id=999, parent=parent)
        user = create_mock_user(user_id=42, roles=[])
        assert not can_edit_consumption_threshold(user, child)

    def test_outsider_blocked(self):
        user = create_mock_user(user_id=42, roles=[])
        project = create_mock_project(project_lead_user_id=999, project_admin_user_id=998)
        assert not can_edit_consumption_threshold(user, project)
