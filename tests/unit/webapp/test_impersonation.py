"""Session impersonation (``webapp.auth.impersonation``): the key's lifecycle and who acts."""

import pytest

from sam.core.users import User


@pytest.fixture
def target(session):
    """An active user benkirk may impersonate: no bundle groups, no overrides."""
    user = (session.query(User).filter(User.is_active)
            .filter(User.username != 'benkirk').order_by(User.user_id).first())
    assert user is not None
    return user


def _impersonate(client, username):
    return client.post('/admin/impersonate', data={'username': username})


class TestTheKeyLifecycle:

    def test_impersonate_sets_the_key_and_stop_clears_it(self, auth_client, target):
        assert _impersonate(auth_client, target.username).status_code == 302
        with auth_client.session_transaction() as sess:
            assert sess['_user_id'] == str(target.user_id) and sess['impersonator_id']
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'You are currently impersonating' in html
        assert auth_client.get('/admin/stop-impersonating').status_code == 302
        with auth_client.session_transaction() as sess:
            assert 'impersonator_id' not in sess and sess['_user_id'] != str(target.user_id)

    def test_logout_forgets_the_impersonation(self, auth_client, target):
        """logout_user() clears only Flask-Login's keys; a stale impersonator id would let
        the next login on this browser "stop impersonating" into the admin's account."""
        _impersonate(auth_client, target.username)
        auth_client.get('/auth/logout')
        with auth_client.session_transaction() as sess:
            assert 'impersonator_id' not in sess
        # The next person logs in and asks to stop impersonating: nothing to stop.
        auth_client.post('/auth/login', data={'username': target.username, 'password': 'x'})
        resp = auth_client.get('/admin/stop-impersonating', follow_redirects=False)
        assert resp.status_code == 302
        with auth_client.session_transaction() as sess:
            assert sess['_user_id'] == str(target.user_id)

    def test_a_login_never_inherits_the_key(self, auth_client, target):
        with auth_client.session_transaction() as sess:
            sess.pop('_user_id', None)
            sess['impersonator_id'] = 1           # left behind by some earlier session
        auth_client.post('/auth/login', data={'username': target.username, 'password': 'x'})
        with auth_client.session_transaction() as sess:
            assert 'impersonator_id' not in sess and sess['_user_id'] == str(target.user_id)


class TestWhoActs:

    def test_writes_name_the_admin_and_the_audit_says_as_whom(self, app, session, target):
        from flask_login import login_user
        from webapp.audit.events import responsible_user
        from webapp.auth import impersonation
        from webapp.auth.impersonation import acting_username
        from webapp.auth.models import AuthUser
        admin = User.get_by_username(session, 'benkirk')
        with app.test_request_context('/'):
            login_user(AuthUser(target))
            assert acting_username() == target.username
            assert responsible_user() == target.username
            impersonation.begin(admin.user_id)
            assert acting_username() == 'benkirk'
            assert responsible_user() == f'benkirk as={target.username}'
            impersonation.end()
            assert responsible_user() == target.username
