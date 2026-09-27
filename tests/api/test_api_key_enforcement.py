"""The token path honors the route's Permission in ``RBAC_SOURCE=db`` mode and
nothing else changes: ``defaults`` mode passes every valid key as it always has."""

import base64

import bcrypt
import pytest
from flask import g

from sam.security.permissions import Permission
from sam.security.rbac_catalog import GrantDef, RoleDef, build_catalog
from webapp.utils import api_auth, rbac


def _basic(username, password):
    return 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')


@pytest.fixture(autouse=True)
def _reset_caches():
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    api_auth._VERIFY_CACHE.clear()
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    api_auth._VERIFY_CACHE.clear()


@pytest.fixture
def keys(app, monkeypatch):
    """Two config keys: `viewer-key` and `admin-key`, password `pw`."""
    original = app.config.get('API_KEYS')
    h = bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()
    app.config['API_KEYS'] = {'viewer-key': h, 'admin-key': h, 'orphan-key': h}
    try:
        yield app
    finally:
        app.config['API_KEYS'] = original


def _catalog():
    return build_catalog(
        [RoleDef('api_legacy', frozenset({P.VIEW_PROJECTS, P.VIEW_USERS})),
         RoleDef('api_admin', frozenset({P.SYSTEM_ADMIN}))],
        [GrantDef('apikey', 'viewer-key', role='api_legacy'),
         GrantDef('apikey', 'admin-key', role='api_admin')], source='db')


P = Permission


@pytest.fixture
def db_mode(keys, monkeypatch):
    monkeypatch.setitem(keys.config, 'RBAC_SOURCE', 'db')
    monkeypatch.setitem(keys.config, 'RBAC_DB_TTL', 60)
    monkeypatch.setattr(rbac, '_DB_CACHE', {'at': None, 'catalog': None})
    monkeypatch.setattr(rbac, '_load_db_catalog', _catalog)
    return keys


def _view(permission=None, **kw):
    @api_auth.login_or_token_required(permission, **kw)
    def view():
        return 'ok', 200
    return view


def _call(app, view, username, method='GET'):
    with app.test_request_context('/', method=method,
                                  headers={'Authorization': _basic(username, 'pw')}):
        result = view()
    return result if isinstance(result, tuple) else (result, 200)


class TestDefaultsMode:
    @pytest.mark.parametrize('username', ['viewer-key', 'admin-key', 'orphan-key'])
    def test_every_valid_key_passes(self, keys, username):
        assert _call(keys, _view(P.EDIT_RESOURCES), username)[1] == 200

    def test_bare_and_gated_api_key_required_both_pass(self, keys):
        @api_auth.api_key_required
        def bare():
            return 'ok', 200

        @api_auth.api_key_required(permission=P.MANAGE_SYSTEM_STATUS)
        def gated():
            return 'ok', 200
        assert _call(keys, bare, 'orphan-key', 'POST')[1] == 200
        assert _call(keys, gated, 'orphan-key', 'POST')[1] == 200


class TestDbMode:
    def test_a_key_with_the_permission_passes(self, db_mode):
        assert _call(db_mode, _view(P.VIEW_PROJECTS), 'viewer-key')[1] == 200

    def test_a_key_without_it_is_403(self, db_mode, caplog):
        resp, status = _call(db_mode, _view(P.EDIT_RESOURCES), 'viewer-key')
        assert status == 403
        assert resp.get_json()['error'] == 'Forbidden - insufficient permissions'
        assert 'permission=edit_resources' in caplog.text and 'pw' not in caplog.text

    def test_a_key_with_no_grant_is_denied_everywhere(self, db_mode):
        assert _call(db_mode, _view(P.VIEW_PROJECTS), 'orphan-key')[1] == 403

    def test_system_admin_passes_everything(self, db_mode):
        assert _call(db_mode, _view(P.EDIT_RESOURCES), 'admin-key')[1] == 200

    def test_no_permission_means_just_authenticated(self, db_mode):
        assert _call(db_mode, _view(None), 'orphan-key')[1] == 200

    def test_the_identity_is_set_before_the_denial(self, db_mode):
        seen = {}

        @api_auth.login_or_token_required(P.EDIT_RESOURCES)
        def view():
            return 'ok', 200
        with db_mode.test_request_context('/', headers={'Authorization': _basic('viewer-key', 'pw')}):
            view()
            seen['user'] = g.api_key_user
        assert seen['user'] == 'viewer-key'

    def test_deny_hook_owns_the_403_body(self, db_mode):
        calls = []

        def deny(status, message):
            calls.append(status)
            return 'custom', status
        assert _call(db_mode, _view(P.EDIT_RESOURCES, deny=deny), 'viewer-key') == ('custom', 403)
        assert calls == [403]

    def test_legacy_roles_gate_is_still_independent(self, db_mode, monkeypatch):
        h = bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(api_auth, '_get_db_api_keys',
                            lambda: {'xraskey': {'hash': h, 'roles': ['ROLE_XRAS']}})
        # roles= without a permission: the role check alone decides.
        assert _call(db_mode, _view(None, roles=('ROLE_XRAS',)), 'xraskey')[1] == 200
        assert _call(db_mode, _view(None, roles=('ROLE_OTHER',)), 'xraskey')[1] == 403

    def test_gated_api_key_required(self, db_mode):
        @api_auth.api_key_required(permission=P.MANAGE_SYSTEM_STATUS)
        def gated():
            return 'ok', 200
        assert _call(db_mode, gated, 'viewer-key', 'POST')[1] == 403
        assert _call(db_mode, gated, 'admin-key', 'POST')[1] == 200

    def test_bare_api_key_required_never_checks(self, db_mode):
        @api_auth.api_key_required
        def bare():
            return 'ok', 200
        assert _call(db_mode, bare, 'orphan-key', 'POST')[1] == 200
