"""``RBAC_SOURCE='db'``: the TTL snapshot, invalidation, last-good on error, the
per-user memo, and AuthUser.roles filtering by the catalog's group subjects."""

import pytest

from sam.security.permissions import Permission
from sam.security.rbac_catalog import GrantDef, RoleCatalog, RoleDef, build_catalog
from webapp.utils import rbac

P = Permission


class _StubUser:
    def __init__(self, *, roles=(), username='stub', is_authenticated=True):
        self.roles = set(roles)
        self.username = username
        self.is_authenticated = is_authenticated


def _catalog(**kw):
    return build_catalog(
        [RoleDef('r', frozenset({P.VIEW_PROJECTS})),
         RoleDef('sa', frozenset({P.SYSTEM_ADMIN}))],
        [GrantDef('user', 'alice', role='r'),
         GrantDef('group', 'wna-staff', role='r', facility='WNA'),
         GrantDef('user', 'root', role='sa')],
        source='db', **kw)


@pytest.fixture
def db_mode(app, monkeypatch):
    monkeypatch.setitem(app.config, 'RBAC_SOURCE', 'db')
    monkeypatch.setitem(app.config, 'RBAC_DB_TTL', 60)
    monkeypatch.setattr(rbac, '_DB_CACHE', {'at': None, 'catalog': None})
    with app.app_context():
        yield app


class TestSnapshot:
    def test_loads_once_within_ttl(self, db_mode, monkeypatch):
        calls = []
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: calls.append(1) or _catalog())
        assert rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        assert rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        assert not rbac.has_permission(_StubUser(username='bob'), P.VIEW_PROJECTS)
        assert calls == [1]

    def test_invalidate_reloads(self, db_mode, monkeypatch):
        calls = []
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: calls.append(1) or _catalog())
        rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        rbac.invalidate_catalog()
        rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        assert calls == [1, 1]

    def test_ttl_zero_reloads_every_check(self, db_mode, monkeypatch):
        monkeypatch.setitem(db_mode.config, 'RBAC_DB_TTL', 0)
        calls = []
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: calls.append(1) or _catalog())
        rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        rbac.has_permission(_StubUser(username='alice'), P.VIEW_PROJECTS)
        assert calls == [1, 1]

    def test_load_error_serves_last_good_then_empty(self, db_mode, monkeypatch):
        def boom():
            raise RuntimeError('db down')
        monkeypatch.setattr(rbac, '_load_db_catalog', boom)
        # Nothing good yet: nobody holds anything, and no exception escapes.
        assert not rbac.has_permission(_StubUser(username='root'), P.VIEW_USERS)
        monkeypatch.setattr(rbac, '_load_db_catalog', _catalog)
        rbac.invalidate_catalog()
        assert rbac.has_permission(_StubUser(username='root'), P.VIEW_USERS)
        monkeypatch.setattr(rbac, '_load_db_catalog', boom)
        rbac.invalidate_catalog()
        assert rbac.has_permission(_StubUser(username='root'), P.VIEW_USERS)

    def test_defaults_are_not_a_fallback(self, db_mode, monkeypatch):
        monkeypatch.setattr(rbac, '_load_db_catalog',
                            lambda: RoleCatalog(roles={}, unscoped={}, scoped={}, source='db'))
        assert not rbac.has_permission(_StubUser(username='benkirk'), P.VIEW_USERS)


class TestResolution:
    def test_group_facility_grant_reaches_the_three_predicates(self, db_mode, monkeypatch):
        monkeypatch.setattr(rbac, '_load_db_catalog', _catalog)
        user = _StubUser(username='carol', roles=['wna-staff'])
        assert not rbac.has_permission(user, P.VIEW_PROJECTS)
        assert rbac.has_permission_for_facility(user, P.VIEW_PROJECTS, 'WNA')
        assert not rbac.has_permission_for_facility(user, P.VIEW_PROJECTS, 'UNIV')
        assert not rbac.has_permission_for_facility(user, P.VIEW_PROJECTS, None)
        assert rbac.has_permission_any_facility(user, P.VIEW_PROJECTS)
        assert rbac.user_facility_scope(user, P.VIEW_PROJECTS) == {'WNA'}

    def test_memo_is_per_snapshot(self, db_mode, monkeypatch):
        monkeypatch.setattr(rbac, '_load_db_catalog', _catalog)
        user = _StubUser(username='alice')
        assert rbac.has_permission(user, P.VIEW_PROJECTS)
        first = user._rbac_resolved[0]
        rbac.invalidate_catalog()
        assert rbac.has_permission(user, P.VIEW_PROJECTS)
        assert user._rbac_resolved[0] is not first

    def test_anonymous_holds_nothing(self, db_mode, monkeypatch):
        monkeypatch.setattr(rbac, '_load_db_catalog', _catalog)
        anon = _StubUser(username=None, is_authenticated=False)
        assert rbac.get_user_permissions(anon) == set()


class TestAuthUserRoles:
    def test_roles_filter_by_catalog_group_subjects(self, db_mode, monkeypatch, session):
        from sam.core.users import User
        from webapp.auth.models import AuthUser
        user = User.get_by_username(session, 'benkirk')
        if user is None:
            pytest.skip('benkirk not in the snapshot')
        posix = AuthUser(user)._posix_group_names()
        if not posix:
            pytest.skip('benkirk holds no POSIX groups in the snapshot')
        some = sorted(posix)[0]
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: build_catalog(
            [RoleDef('r', frozenset({P.VIEW_USERS}))],
            [GrantDef('group', some, role='r', facility='WNA'),
             GrantDef('group', 'not-a-group-of-theirs', role='r')], source='db'))
        assert AuthUser(user).roles == {some}
