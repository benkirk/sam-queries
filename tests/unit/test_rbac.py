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
    can_impersonate,
    get_user_permissions,
    has_all_permissions,
    has_any_permission,
    has_permission,
    has_permission_for_facility,
    user_facility_scope,
)


class _StubUser:
    """Minimal AuthUser-like stub: just `roles` (a set) and `username`."""

    def __init__(self, *, roles=(), username='stubuser', is_authenticated=True):
        self.roles = set(roles)
        self.username = username
        self.is_authenticated = is_authenticated

    def has_role(self, name):
        return name in self.roles


# ---------------------------------------------------------------------------
# Group bundle composition
# ---------------------------------------------------------------------------

class TestGroupBundleComposition:
    def test_single_group_resolves_to_bundle_permissions(self):
        user = _StubUser(roles=['hsg'])
        perms = get_user_permissions(user)
        assert perms == set(GROUP_PERMISSIONS['hsg'])

    def test_multiple_groups_union(self):
        user = _StubUser(roles=['hsg', 'nusd'])
        perms = get_user_permissions(user)
        expected = set(GROUP_PERMISSIONS['hsg']) | set(GROUP_PERMISSIONS['nusd'])
        assert perms == expected

    def test_unknown_group_contributes_nothing(self):
        user = _StubUser(roles=['hsg', 'no_such_group'])
        perms = get_user_permissions(user)
        assert perms == set(GROUP_PERMISSIONS['hsg'])

    def test_no_groups_yields_empty_permissions(self):
        user = _StubUser(roles=[])
        assert get_user_permissions(user) == set()

    def test_admin_testing_only_bundle_grants_every_permission(self):
        # 'admin-testing-only' is the synthetic test-session bundle
        # (registered by the autouse fixture in tests/conftest.py) that
        # carries every Permission. Real production bundles (csg/nusd/hsg)
        # may grow or shrink — tests that need 'full admin' semantics
        # should depend on this bundle, not on csg's exact contents.
        user = _StubUser(roles=['admin-testing-only'])
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
        user = _StubUser(roles=['hsg'], username='alice')
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
        user = _StubUser(roles=['hsg'], username='alice')
        perms = get_user_permissions(user)
        assert Permission.SYSTEM_ADMIN not in perms


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------

class TestPredicates:
    def test_has_permission_true_when_in_bundle(self):
        user = _StubUser(roles=['hsg'])
        assert has_permission(user, Permission.VIEW_PROJECTS)

    def test_has_permission_false_when_not_granted(self):
        user = _StubUser(roles=['hsg'])
        assert not has_permission(user, Permission.SYSTEM_ADMIN)

    def test_has_any_permission_short_circuits_on_match(self):
        user = _StubUser(roles=['hsg'])
        assert has_any_permission(
            user, Permission.SYSTEM_ADMIN, Permission.VIEW_PROJECTS
        )

    def test_has_any_permission_false_when_none_match(self):
        user = _StubUser(roles=['hsg'])
        assert not has_any_permission(
            user, Permission.SYSTEM_ADMIN, Permission.MANAGE_ROLES
        )

    def test_has_all_permissions_requires_full_intersection(self):
        user = _StubUser(roles=['hsg'])
        assert has_all_permissions(
            user, Permission.VIEW_PROJECTS, Permission.VIEW_ALLOCATIONS
        )
        assert not has_all_permissions(
            user, Permission.VIEW_PROJECTS, Permission.SYSTEM_ADMIN
        )


# ---------------------------------------------------------------------------
# Impersonation no-escalation rule
# ---------------------------------------------------------------------------

class TestCanImpersonate:
    """``can_impersonate(caller, target)`` enforces no privilege escalation:
    target's permissions must be a (non-strict) subset of caller's."""

    def test_peer_impersonation_allowed(self):
        # Two nusd users — equal permission sets — peer is fine.
        caller = _StubUser(roles=['nusd'], username='travis')
        target = _StubUser(roles=['nusd'], username='other_nusd')
        assert can_impersonate(caller, target)

    def test_caller_can_impersonate_lessor_user(self):
        # nusd impersonating a user with no bundle-conferring groups.
        caller = _StubUser(roles=['nusd'], username='travis')
        target = _StubUser(roles=[], username='regular_user')
        assert can_impersonate(caller, target)

    def test_caller_cannot_impersonate_more_privileged_user(self, monkeypatch):
        # nusd cannot impersonate a user with USER_PERMISSION_OVERRIDES
        # granting the full Permission set (escalation blocked).
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'benkirk': set(Permission)},
        )
        caller = _StubUser(roles=['nusd'], username='travis')
        target = _StubUser(roles=[], username='benkirk')
        assert not can_impersonate(caller, target)

    def test_full_admin_can_impersonate_anyone(self, monkeypatch):
        # A user with the full Permission set (via override) may
        # impersonate any user — every target's perms are a subset.
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'super': set(Permission)},
        )
        caller = _StubUser(roles=[], username='super')
        for target_roles in (['nusd'], ['hsg'], []):
            target = _StubUser(roles=target_roles, username='someone')
            assert can_impersonate(caller, target)

    def test_self_impersonation_trivially_allowed(self):
        # caller == target → identical permission sets → subset check passes.
        user = _StubUser(roles=['nusd'], username='travis')
        assert can_impersonate(user, user)

    def test_disjoint_extras_block_impersonation(self, monkeypatch):
        # If target has any permission caller lacks — even one — block.
        # Here target has EXPORT_DATA via override; caller doesn't.
        monkeypatch.setattr(
            rbac,
            'USER_PERMISSION_OVERRIDES',
            {'has_export': {Permission.EXPORT_DATA}},
        )
        caller = _StubUser(roles=['nusd'], username='travis')
        target = _StubUser(roles=['nusd'], username='has_export')
        # nusd doesn't include EXPORT_DATA — verify the premise first.
        assert Permission.EXPORT_DATA not in GROUP_PERMISSIONS['nusd']
        assert not can_impersonate(caller, target)


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

    def test_admin_testing_only_bundle_is_registered_with_full_permission_set(self):
        # The autouse session-scoped fixture in tests/conftest.py
        # registers 'admin-testing-only' as the synthetic full-access
        # bundle for the test session. Guard against the fixture
        # silently regressing or not running.
        assert set(GROUP_PERMISSIONS['admin-testing-only']) == set(Permission)


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


# ---------------------------------------------------------------------------
# Facility-scoped permissions (third RBAC tier)
# ---------------------------------------------------------------------------

class TestFacilityScopedPermissions:
    """``has_permission_for_facility`` and ``user_facility_scope`` add a
    per-user, per-facility grant layer on top of system permissions.

    Coverage matrix:
    - System-permission holder → passes for any facility, including
      orphan projects (``facility_name=None``).
    - Scoped entry → passes only for named facilities, denies others.
    - Scoped entry + orphan project → denies (system-only domain).
    - Unauthenticated user → always denies.
    - ``user_facility_scope`` returns ``None`` for system holders,
      the configured set for scoped users, empty set otherwise.
    """

    def test_has_permission_for_facility_grants_via_system_perm(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_PERMISSION_OVERRIDES',
            {'alice': set(Permission)},
        )
        user = _StubUser(roles=[], username='alice')
        # System grant applies regardless of facility.
        assert has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'NCAR')
        assert has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'WNA')
        # Even orphan projects are reachable by system holders.
        assert has_permission_for_facility(user, Permission.EDIT_PROJECTS, None)

    def test_has_permission_for_facility_grants_via_scoped_entry(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS, Permission.CREATE_PROJECTS}}},
        )
        user = _StubUser(roles=[], username='mgr')
        assert has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'WNA')
        assert has_permission_for_facility(user, Permission.CREATE_PROJECTS, 'WNA')

    def test_has_permission_for_facility_denies_other_facility(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = _StubUser(roles=[], username='mgr')
        # Scoped to WNA → must deny NCAR and UNIV.
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'NCAR')
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'UNIV')

    def test_has_permission_for_facility_denies_permission_not_in_scope(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.VIEW_PROJECTS}}},
        )
        user = _StubUser(roles=[], username='mgr')
        # WNA is in scope, but only for VIEW; EDIT must fail.
        assert has_permission_for_facility(user, Permission.VIEW_PROJECTS, 'WNA')
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'WNA')

    def test_has_permission_for_facility_denies_orphan_project_for_scoped_user(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {'mgr': {'WNA': {Permission.EDIT_PROJECTS}}},
        )
        user = _StubUser(roles=[], username='mgr')
        # facility_name=None means the target project has no
        # allocation_type → panel → facility chain. Only unscoped
        # system admins may act there.
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, None)

    def test_has_permission_for_facility_unauthenticated_returns_false(self):
        user = _StubUser(roles=[], username='nobody', is_authenticated=False)
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, 'WNA')
        assert not has_permission_for_facility(user, Permission.EDIT_PROJECTS, None)

    def test_user_facility_scope_returns_none_for_system_admin(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_PERMISSION_OVERRIDES',
            {'alice': set(Permission)},
        )
        user = _StubUser(roles=[], username='alice')
        # None signals "no facility filter needed" — unscoped.
        assert user_facility_scope(user, Permission.VIEW_PROJECTS) is None
        assert user_facility_scope(user, Permission.EDIT_PROJECTS) is None

    def test_user_facility_scope_returns_set_for_scoped_user(self, monkeypatch):
        monkeypatch.setattr(
            rbac, 'USER_FACILITY_PERMISSIONS',
            {
                'mgr': {
                    'WNA': {Permission.VIEW_PROJECTS, Permission.EDIT_PROJECTS},
                    'CISL': {Permission.VIEW_PROJECTS},
                },
            },
        )
        user = _StubUser(roles=[], username='mgr')
        assert user_facility_scope(user, Permission.VIEW_PROJECTS) == {'WNA', 'CISL'}
        assert user_facility_scope(user, Permission.EDIT_PROJECTS) == {'WNA'}
        # A permission the user does not hold anywhere → empty set.
        assert user_facility_scope(user, Permission.DELETE_PROJECTS) == set()

    def test_user_facility_scope_empty_for_unauthenticated(self):
        user = _StubUser(roles=[], username='nobody', is_authenticated=False)
        assert user_facility_scope(user, Permission.VIEW_PROJECTS) == set()

    def test_user_facility_scope_empty_for_user_without_entry(self, monkeypatch):
        monkeypatch.setattr(rbac, 'USER_FACILITY_PERMISSIONS', {})
        user = _StubUser(roles=[], username='no_entry')
        assert user_facility_scope(user, Permission.VIEW_PROJECTS) == set()
