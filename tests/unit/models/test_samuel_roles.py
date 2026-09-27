"""The samuel_role_* models under the per-test savepoint: writes, the
invariants, the seed, and the seed -> load_catalog round trip.

The seed writes fixed role names into a unique index, so the file runs
under ``serial_file_lock`` (see tests/conftest.py).
"""

import pytest

from sam.security.permissions import Permission
from sam.security.rbac_catalog import build_catalog
from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES
from sam.security.samuel_roles import (RbacInvariantError, SamuelRole, SamuelRoleGrant,
                                       SamuelRolePermission, load_catalog, seed_defaults)
from tests.factories import make_samuel_grant, make_samuel_role, next_seq

P = Permission


@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('samuel_roles_fixed_names'):
        yield


@pytest.fixture
def anchor(session):
    """A guard holder, so the last-holder invariant does not bite unrelated tests."""
    role = make_samuel_role(session, permissions=[P.SYSTEM_ADMIN])
    return make_samuel_grant(session, role=role)


class TestRoleWrites:
    def test_create_writes_permission_rows_and_stamps(self, session, anchor):
        role = SamuelRole.create(session, name=next_seq('ops'), permissions=[P.VIEW_USERS, 'view_projects'],
                                 by='tester', description='  ops  ')
        assert role.samuel_role_id
        assert [r.permission for r in role.permission_rows()] == ['view_projects', 'view_users']
        assert role.description == 'ops'
        assert role.created_by == 'tester' and role.creation_time is not None
        assert role.active is True

    def test_bad_names_and_duplicates_are_refused(self, session, anchor):
        with pytest.raises(ValueError):
            SamuelRole.create(session, name='has space', permissions=[], by='t')
        dup = next_seq('dup')
        SamuelRole.create(session, name=dup, permissions=[], by='t')
        with pytest.raises(ValueError, match='exists'):
            SamuelRole.create(session, name=dup, permissions=[], by='t')

    def test_set_permissions_replaces_rows(self, session, anchor):
        role = make_samuel_role(session, permissions=[P.VIEW_USERS])
        role.set_permissions([P.EDIT_USERS], by='t')
        assert role.direct_permissions() == [P.EDIT_USERS]
        assert (session.query(SamuelRolePermission)
                .filter_by(samuel_role_id=role.samuel_role_id).count() == 1)

    def test_extends_cycle_and_self_are_refused(self, session, anchor):
        a = make_samuel_role(session)
        b = make_samuel_role(session, extends=a)
        with pytest.raises(RbacInvariantError, match='cycle'):
            a.update(by='t', extends=b)
        with pytest.raises(RbacInvariantError, match='itself'):
            a.update(by='t', extends=a)

    def test_retired_permission_value_is_skipped(self, session, anchor):
        role = make_samuel_role(session, permissions=[P.VIEW_USERS])
        session.add(SamuelRolePermission(samuel_role_id=role.samuel_role_id,
                                         permission='no_such_permission'))
        session.flush()
        assert role.direct_permissions() == [P.VIEW_USERS]
        assert load_catalog(session).roles[role.name] == {P.VIEW_USERS}


class TestGrantWrites:
    def test_create_and_find_active(self, session, anchor):
        role = make_samuel_role(session)
        g = SamuelRoleGrant.create(session, subject_type='group', subject_name=' wna ',
                                   by='t', role=role, facility_name='WNA', note='x')
        assert g.subject_name == 'wna' and g.is_active
        assert SamuelRoleGrant.find_active(session, subject_type='group', subject_name='wna',
                                           role=role, facility_name='WNA') is g
        with pytest.raises(ValueError, match='already'):
            SamuelRoleGrant.create(session, subject_type='group', subject_name='wna',
                                   by='t', role=role, facility_name='WNA')

    def test_exactly_one_of_role_permission(self, session, anchor):
        role = make_samuel_role(session)
        with pytest.raises(ValueError):
            SamuelRoleGrant.create(session, subject_type='user', subject_name='u', by='t')
        with pytest.raises(ValueError):
            SamuelRoleGrant.create(session, subject_type='user', subject_name='u', by='t',
                                   role=role, permission=P.VIEW_USERS)
        with pytest.raises(ValueError, match='subject_type'):
            SamuelRoleGrant.create(session, subject_type='robot', subject_name='u', by='t',
                                   role=role)

    def test_an_api_key_grant_is_never_facility_scoped(self, session, anchor):
        role = make_samuel_role(session)
        with pytest.raises(ValueError, match='facility'):
            SamuelRoleGrant.create(session, subject_type='apikey', subject_name='k', by='t',
                                   role=role, facility_name='WNA')

    def test_revoke_stamps_and_keeps_the_row(self, session, anchor):
        g = make_samuel_grant(session, permission=P.VIEW_USERS)
        g.revoke(by='t')
        assert not g.is_active and g.revoked_by == 't'
        assert session.get(SamuelRoleGrant, g.samuel_role_grant_id) is g
        assert session.query(SamuelRoleGrant).filter(SamuelRoleGrant.is_active,
                                                     SamuelRoleGrant.subject_name == g.subject_name).count() == 0


class TestLastHolder:
    def test_revoking_the_only_guard_grant_is_refused(self, session):
        role = make_samuel_role(session, permissions=[P.MANAGE_ROLES])
        only = make_samuel_grant(session, role=role)
        with pytest.raises(RbacInvariantError, match='nobody'):
            only.revoke(by='t')
        assert only.is_active

    def test_dropping_manage_roles_from_the_only_role_is_refused(self, session):
        role = make_samuel_role(session, permissions=[P.MANAGE_ROLES, P.VIEW_USERS])
        make_samuel_grant(session, role=role)
        with pytest.raises(RbacInvariantError):
            role.set_permissions([P.VIEW_USERS], by='t')

    def test_deactivating_the_only_guard_role_is_refused(self, session):
        role = make_samuel_role(session, permissions=[P.SYSTEM_ADMIN])
        make_samuel_grant(session, role=role)
        with pytest.raises(RbacInvariantError):
            role.update(by='t', active=False)

    def test_a_scoped_or_apikey_holder_does_not_count(self, session):
        role = make_samuel_role(session, permissions=[P.SYSTEM_ADMIN])
        make_samuel_grant(session, role=role, facility_name='WNA')
        make_samuel_grant(session, subject_type='apikey', role=role)
        unscoped = make_samuel_grant(session, role=role)
        with pytest.raises(RbacInvariantError):
            unscoped.revoke(by='t')


class TestSeed:
    def test_seed_then_load_reproduces_the_defaults(self, session):
        assert session.query(SamuelRole).count() == 0, 'the snapshot ships the table empty'
        n = seed_defaults(session)
        assert n == len(DEFAULT_ROLES)
        loaded = load_catalog(session)
        expected = build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS)
        assert loaded.roles == expected.roles
        assert loaded.unscoped == expected.unscoped
        assert loaded.scoped == expected.scoped
        assert loaded.source == 'db'

    def test_seed_is_a_no_op_on_a_populated_table(self, session):
        seed_defaults(session)
        csg = SamuelRole.get_by_name(session, 'csg')
        csg.update(by='t', description='edited')
        assert seed_defaults(session) == 0
        assert SamuelRole.get_by_name(session, 'csg').description == 'edited'

    def test_inactive_role_drops_out_of_the_catalog(self, session):
        seed_defaults(session)
        SamuelRole.get_by_name(session, 'viewer').update(by='t', active=False)
        cat = load_catalog(session)
        assert 'viewer' not in cat.roles
        assert cat.permissions('user', 'mcjones') == frozenset()
