"""HTTP contract of the LDAP sync API: auth, the error envelope, the no-404 rule, reads.

Happy-path writes are covered at the model layer (CLAUDE.md § Testing): a route-level
write commits on ``db.session``'s own connection, outside the per-test SAVEPOINT.
"""

import json

import pytest

from ldapsync_helpers import (  # noqa: F401  — pytest resolves fixtures by name
    PREFIX,
    admin_auth,
    ldapsync_client,
    ldapsync_keys,
    reset_db_key_cache,
)
from webapp.limiter import limiter as facade

COLLECTIONS = ('institution', 'organization', 'user', 'group', 'gidAllocation',
               'projectGroup', 'groupTag')


def _get(client, rule, user='admin', **kw):
    return client.get(f'{PREFIX}/{rule}', headers=admin_auth(user), **kw)


# --- auth ------------------------------------------------------------------

class TestAuth:

    @pytest.mark.parametrize('rule', ['ldapsync/status', 'ldapsync/user/'])
    def test_unauthenticated_is_a_realm_challenge(self, ldapsync_client, rule):
        """LWP sends the password only after a 401 naming exactly this realm."""
        resp = ldapsync_client.get(f'{PREFIX}/{rule}')
        assert resp.status_code == 401
        assert resp.headers['WWW-Authenticate'] == 'Basic realm="Realm"'

    def test_key_without_the_role_is_forbidden(self, ldapsync_client):
        resp = _get(ldapsync_client, 'ldapsync/status', user='nobody')
        assert resp.status_code == 403
        assert 'errorMessage' in resp.get_json()

    def test_bad_password_is_a_challenge_too(self, ldapsync_client):
        resp = ldapsync_client.get(f'{PREFIX}/ldapsync/status',
                                   headers={'Authorization': 'Basic YWRtaW46bm9wZQ=='})
        assert resp.status_code == 401
        assert resp.headers['WWW-Authenticate'] == 'Basic realm="Realm"'

    def test_config_key_fails_closed(self, client, app, monkeypatch):
        """Config ``API_KEYS`` carry no roles, so they cannot reach a ROLE_API_ADMIN route."""
        import bcrypt
        from webapp.utils import api_auth
        hashed = bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setitem(app.config, 'API_KEYS', {'admin': hashed})
        monkeypatch.setattr(api_auth, '_get_db_api_keys', lambda: {})
        resp = client.get(f'{PREFIX}/ldapsync/status',
                          headers={'Authorization': 'Basic YWRtaW46cHc='})
        assert resp.status_code == 403

    def test_browser_session_cannot_reach_it(self, auth_client):
        resp = auth_client.get(f'{PREFIX}/ldapsync/status')
        assert resp.status_code == 401


# --- reads -----------------------------------------------------------------

class TestCollections:

    @pytest.mark.parametrize('rule', COLLECTIONS)
    def test_each_collection_is_a_json_list(self, ldapsync_client, rule):
        resp = _get(ldapsync_client, f'ldapsync/{rule}')
        assert resp.status_code == 200
        assert resp.mimetype == 'application/json'
        assert isinstance(json.loads(resp.data), list)

    def test_trailing_slash_serves_the_list(self, ldapsync_client):
        """An older daemon image asked for ``ldapsync/user/``; legacy served the list."""
        resp = _get(ldapsync_client, 'ldapsync/groupTag/')
        assert resp.status_code == 200

    def test_user_keys_are_legacy_order(self, ldapsync_client, multi_project_user):
        resp = _get(ldapsync_client, f'ldapsync/user/{multi_project_user.unix_uid}')
        assert resp.status_code == 200
        assert list(resp.get_json()) == [
            'academicStatus', 'active', 'chargingExempt', 'collaborations',
            'contactPersonUpid', 'emails', 'firstname', 'institutionIds', 'lastname',
            'locked', 'middlename', 'nameSuffix', 'nickname', 'orgIds', 'phones',
            'positions', 'title', 'tokenType', 'typeOfLogin', 'unixUid', 'upid',
            'userId', 'userName', 'deleted', 'preferredName']

    def test_unknown_user_is_200_empty_not_404(self, ldapsync_client):
        """The daemon retries a 404 forever; legacy answered a null entity with an empty 200."""
        resp = _get(ldapsync_client, 'ldapsync/user/987654321')
        assert resp.status_code == 200
        assert resp.data == b''

    def test_non_numeric_uid_is_a_400_envelope(self, ldapsync_client):
        resp = _get(ldapsync_client, 'ldapsync/user/abc')
        assert resp.status_code == 400
        assert resp.get_json()['errorMessage'].startswith('ValidationException:\n ')

    def test_group_tags(self, ldapsync_client):
        tags = _get(ldapsync_client, 'ldapsync/groupTag').get_json()
        assert tags[-2:] == [{'name': 'exclude-from-google', 'accessBranch': False},
                             {'name': 'auto-renewed-project', 'accessBranch': False}]
        assert all(t['accessBranch'] for t in tags[:-2])

    def test_status_keys(self, ldapsync_client):
        body = _get(ldapsync_client, 'ldapsync/status').get_json()
        assert list(body) == ['institutionUpdateTime', 'organizationUpdateTime',
                              'userUpdateTime', 'groupUpdateTime',
                              'gidAllocationUpdateTime', 'accessBranches']

    def test_project_group_since_filters_only_when_named(self, ldapsync_client):
        far_future_ms = 32503680000000
        everything = _get(ldapsync_client, 'ldapsync/projectGroup').get_json()
        named = _get(ldapsync_client, f'ldapsync/projectGroup?since={far_future_ms}').get_json()
        bare = ldapsync_client.get(f'{PREFIX}/ldapsync/projectGroup?{far_future_ms}',
                                   headers=admin_auth()).get_json()
        assert named == []
        assert len(bare) == len(everything)


# --- limiter ---------------------------------------------------------------

@pytest.fixture
def tight_limiter(app):
    """Limiter on with a 2/min default, restored afterwards."""
    with app.app_context():
        facade.limiter.enabled = True
        app.config['RATELIMIT_ENABLED'] = True
        old = app.config['RATELIMIT_AUTHED']
        app.config['RATELIMIT_AUTHED'] = '2 per minute'
        try:
            yield
        finally:
            facade.limiter.enabled = False
            app.config['RATELIMIT_ENABLED'] = False
            app.config['RATELIMIT_AUTHED'] = old


def test_blueprint_is_exempt_from_the_rate_limit(ldapsync_client, tight_limiter):
    """An add-file replay runs at ~350 PUTs/min; a limit would turn it into lost records."""
    for _ in range(5):
        assert _get(ldapsync_client, 'ldapsync/groupTag').status_code == 200


def test_legacy_prefix_belongs_to_one_blueprint(app):
    """The daemon's paths are baked into its image; nothing else may claim the prefix."""
    owners = {rule.endpoint.split('.')[0] for rule in app.url_map.iter_rules()
              if rule.rule.startswith(PREFIX + '/')}
    assert owners == {'api_ldapsync'}
