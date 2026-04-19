"""
Unit tests for the RBAC permission composition machinery.

Covers ``get_user_permissions``, group-bundle resolution, and
``USER_PERMISSION_OVERRIDES`` layering. Uses a stub user object so the
tests don't depend on POSIX-group data in the snapshot DB.
"""

import pytest

from webapp.utils import rbac
from webapp.utils.rbac import (
    GROUP_PERMISSIONS,
    Permission,
    get_user_permissions,
    has_all_permissions,
    has_any_permission,
    has_permission,
)


class _StubUser:
    """Minimal AuthUser-like stub: just `roles` (a set) and `username`."""

    def __init__(self, *, roles=(), username='stubuser'):
        self.roles = set(roles)
        self.username = username

    def has_role(self, name):
        return name in self.roles


# ---------------------------------------------------------------------------
# Group bundle composition
# ---------------------------------------------------------------------------

class TestGroupBundleComposition:
    def test_single_group_resolves_to_bundle_permissions(self):
        user = _StubUser(roles=['user'])
        perms = get_user_permissions(user)
        assert perms == set(GROUP_PERMISSIONS['user'])

    def test_multiple_groups_union(self):
        user = _StubUser(roles=['user', 'analyst'])
        perms = get_user_permissions(user)
        expected = set(GROUP_PERMISSIONS['user']) | set(GROUP_PERMISSIONS['analyst'])
        assert perms == expected

    def test_unknown_group_contributes_nothing(self):
        user = _StubUser(roles=['user', 'no_such_group'])
        perms = get_user_permissions(user)
        assert perms == set(GROUP_PERMISSIONS['user'])

    def test_no_groups_yields_empty_permissions(self):
        user = _StubUser(roles=[])
        assert get_user_permissions(user) == set()

    def test_admin_bundle_grants_every_permission(self):
        user = _StubUser(roles=['admin'])
        assert get_user_permissions(user) == set(Permission)


# ---------------------------------------------------------------------------
# USER_PERMISSION_OVERRIDES layering
# ---------------------------------------------------------------------------

class TestUserPermissionOverrides:
    def test_override_adds_permissions_to_group_baseline(self, monkeypatch):
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'alice': {Permission.EXPORT_DATA}},
        )
        user = _StubUser(roles=['user'], username='alice')
        perms = get_user_permissions(user)
        assert Permission.EXPORT_DATA in perms
        # Baseline still present
        assert Permission.VIEW_PROJECTS in perms

    def test_override_works_with_no_groups(self, monkeypatch):
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'bob': {Permission.VIEW_REPORTS, Permission.EXPORT_DATA}},
        )
        user = _StubUser(roles=[], username='bob')
        perms = get_user_permissions(user)
        assert perms == {Permission.VIEW_REPORTS, Permission.EXPORT_DATA}

    def test_no_override_for_username_is_noop(self, monkeypatch):
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'someone_else': {Permission.SYSTEM_ADMIN}},
        )
        user = _StubUser(roles=['user'], username='alice')
        perms = get_user_permissions(user)
        assert Permission.SYSTEM_ADMIN not in perms


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------

class TestPredicates:
    def test_has_permission_true_when_in_bundle(self):
        user = _StubUser(roles=['user'])
        assert has_permission(user, Permission.VIEW_PROJECTS)

    def test_has_permission_false_when_not_granted(self):
        user = _StubUser(roles=['user'])
        assert not has_permission(user, Permission.SYSTEM_ADMIN)

    def test_has_any_permission_short_circuits_on_match(self):
        user = _StubUser(roles=['user'])
        assert has_any_permission(
            user, Permission.SYSTEM_ADMIN, Permission.VIEW_PROJECTS
        )

    def test_has_any_permission_false_when_none_match(self):
        user = _StubUser(roles=['user'])
        assert not has_any_permission(
            user, Permission.SYSTEM_ADMIN, Permission.MANAGE_ROLES
        )

    def test_has_all_permissions_requires_full_intersection(self):
        user = _StubUser(roles=['user'])
        assert has_all_permissions(
            user, Permission.VIEW_PROJECTS, Permission.VIEW_ALLOCATIONS
        )
        assert not has_all_permissions(
            user, Permission.VIEW_PROJECTS, Permission.SYSTEM_ADMIN
        )


# ---------------------------------------------------------------------------
# Permission enum surface area (regression guard for newly-added members)
# ---------------------------------------------------------------------------

class TestPermissionEnumSurface:
    @pytest.mark.parametrize('perm_name', [
        'VIEW_FACILITIES', 'EDIT_FACILITIES', 'CREATE_FACILITIES', 'DELETE_FACILITIES',
        'VIEW_GROUPS', 'EDIT_GROUPS', 'CREATE_GROUPS', 'DELETE_GROUPS',
        'VIEW_ORG_METADATA', 'EDIT_ORG_METADATA', 'CREATE_ORG_METADATA', 'DELETE_ORG_METADATA',
    ])
    def test_new_permission_member_exists(self, perm_name):
        assert hasattr(Permission, perm_name)

    def test_admin_bundle_covers_every_permission(self):
        # Sanity check: the 'admin' bundle must list every Permission so
        # has_permission(admin, anything) returns True without a special case.
        assert set(GROUP_PERMISSIONS['admin']) == set(Permission)


# ---------------------------------------------------------------------------
# Template context processor — can_act_on_project closure
# ---------------------------------------------------------------------------

class TestRbacContextProcessor:
    """Phase 3 added a ``can_act_on_project(permission, project, ...)``
    helper to the template context. Verify it delegates to
    ``_is_project_steward`` and handles the unauthenticated /
    no-project edge cases."""

    def _ctx(self, app):
        from webapp.utils.rbac import rbac_context_processor
        with app.test_request_context('/'):
            return rbac_context_processor()

    def test_exposes_can_act_on_project(self, app):
        ctx = self._ctx(app)
        assert 'can_act_on_project' in ctx
        assert callable(ctx['can_act_on_project'])

    def test_returns_false_when_project_is_none(self, app):
        ctx = self._ctx(app)
        assert ctx['can_act_on_project'](Permission.EDIT_ALLOCATIONS, None) is False
