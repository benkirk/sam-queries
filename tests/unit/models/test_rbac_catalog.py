"""The pure role catalog: closure, grants, the defaults, and the gate that keeps
every Permission accounted for."""

import pytest

from sam.security.permissions import ALL_VIEW, Permission
from sam.security.rbac_catalog import (ALL_PERMISSIONS, CatalogError, GrantDef,
                                       RoleCatalog, RoleDef, build_catalog,
                                       expand_roles)
from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES, WITHHELD
from webapp.utils import rbac

P = Permission


class TestExpandRoles:
    def test_parent_permissions_fold_in(self):
        roles = expand_roles([RoleDef('base', frozenset({P.VIEW_USERS})),
                              RoleDef('child', frozenset({P.EDIT_USERS}), extends='base')])
        assert roles['child'] == {P.VIEW_USERS, P.EDIT_USERS}
        assert roles['base'] == {P.VIEW_USERS}

    def test_system_admin_implies_everything(self):
        roles = expand_roles([RoleDef('sa', frozenset({P.SYSTEM_ADMIN}))])
        assert roles['sa'] == ALL_PERMISSIONS

    def test_cycle_is_refused(self):
        with pytest.raises(CatalogError, match='cycle'):
            expand_roles([RoleDef('a', frozenset(), extends='b'),
                          RoleDef('b', frozenset(), extends='a')])

    def test_unknown_parent_is_refused(self):
        with pytest.raises(CatalogError, match='unknown role'):
            expand_roles([RoleDef('a', frozenset(), extends='nope')])

    def test_long_chain_resolves_in_any_order(self):
        chain = [RoleDef(f'r{i}', frozenset({P.VIEW_USERS} if i == 0 else ()),
                         extends=f'r{i - 1}' if i else None) for i in range(12)]
        assert expand_roles(reversed(chain))['r11'] == {P.VIEW_USERS}


class TestGrants:
    def test_grant_needs_exactly_one_of_role_and_permission(self):
        with pytest.raises(CatalogError):
            GrantDef('user', 'x')
        with pytest.raises(CatalogError):
            GrantDef('user', 'x', role='r', permission=P.VIEW_USERS)
        with pytest.raises(CatalogError, match='subject type'):
            GrantDef('robot', 'x', role='r')

    def test_scoped_and_unscoped_land_in_their_own_maps(self):
        cat = build_catalog(
            [RoleDef('r', frozenset({P.VIEW_PROJECTS}))],
            [GrantDef('user', 'u', role='r', facility='WNA'),
             GrantDef('group', 'g', permission=P.VIEW_USERS),
             GrantDef('group', 'h', role='r', facility='UNIV')])
        assert cat.permissions('user', 'u') == frozenset()
        assert cat.facility_permissions('user', 'u') == {'WNA': {P.VIEW_PROJECTS}}
        assert cat.permissions('group', 'g') == {P.VIEW_USERS}
        assert cat.group_subjects == {'g', 'h'}

    def test_single_permission_grant_of_system_admin_closes(self):
        cat = build_catalog([], [GrantDef('apikey', 'k', permission=P.SYSTEM_ADMIN)])
        assert cat.permissions('apikey', 'k') == ALL_PERMISSIONS

    def test_unknown_role_in_grant_is_refused(self):
        with pytest.raises(CatalogError, match='unknown role'):
            build_catalog([], [GrantDef('user', 'u', role='ghost')])

    def test_dict_round_trip(self):
        cat = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS)
        again = RoleCatalog.from_dicts(*cat.as_dicts())
        for key, perms in cat.unscoped.items():
            if key[0] != 'apikey':
                assert again.unscoped[key] == perms
        assert again.scoped == cat.scoped


class TestDefaults:
    """The defaults reproduce the bundles the module dicts always held."""

    def test_module_dicts_are_the_defaults(self):
        groups, users, facility_users = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS).as_dicts()
        assert {k: v for k, v in rbac.GROUP_PERMISSIONS.items()
                if k in groups} == groups
        assert rbac.USER_PERMISSION_OVERRIDES == users
        assert rbac.USER_FACILITY_PERMISSIONS == facility_users

    def test_every_permission_is_granted_or_withheld(self):
        granted = set().union(*(r.permissions for r in DEFAULT_ROLES))
        unaccounted = ALL_PERMISSIONS - granted - WITHHELD
        assert not unaccounted, sorted(p.name for p in unaccounted)
        assert not (granted & WITHHELD), sorted(p.name for p in granted & WITHHELD)

    def test_manage_roles_is_held_unscoped(self):
        cat = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS)
        assert any(P.MANAGE_ROLES in perms and key[0] != 'apikey'
                   for key, perms in cat.unscoped.items())

    def test_viewer_is_all_view(self):
        cat = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS)
        assert cat.roles['viewer'] == ALL_VIEW | {P.ACCESS_ADMIN_DASHBOARD}
