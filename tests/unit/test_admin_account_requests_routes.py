"""Admin -> Accounts: the account-request queue routes.

House rule: HTTP tests cover auth, validation, 404 and render smoke; the
writes (claim, dismiss, reconcile) are covered at the model and manage
layers. Route handlers use Flask-SQLAlchemy's own session, so a factory row
built on the test session is invisible to them anyway.
"""

import pytest

from webapp.utils.rbac import Permission

pytestmark = pytest.mark.unit

PAGE = '/admin/account-requests'
FRAGMENT = '/admin/account-requests/fragment'
MISSING = 99_999_999


@pytest.fixture
def no_queue_client(auth_client, monkeypatch):
    """`benkirk` without MANAGE_ACCOUNT_REQUESTS (and without SYSTEM_ADMIN,
    which would grant everything): an admin-dashboard user who must be
    403'd off this page."""
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without(user):
        return {p for p in real(user)
                if p not in (Permission.MANAGE_ACCOUNT_REQUESTS, Permission.SYSTEM_ADMIN)}

    monkeypatch.setattr(rbac, 'get_user_permissions', _without)
    return auth_client


class TestThePermissionBoundary:

    @pytest.mark.parametrize('path', [PAGE, FRAGMENT,
                                      f'/admin/account-requests/{MISSING}/dismiss-form'])
    def test_anonymous_is_refused(self, client, path):
        assert client.get(path).status_code in (302, 401, 403)

    @pytest.mark.parametrize('path', [PAGE, FRAGMENT,
                                      f'/admin/account-requests/{MISSING}/dismiss-form'])
    def test_without_the_permission_is_403(self, no_queue_client, path):
        assert no_queue_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [
        f'/admin/account-requests/{MISSING}/claim',
        f'/admin/account-requests/{MISSING}/dismiss',
        '/admin/account-requests/reconcile',
        '/admin/account-requests/digest',
    ])
    def test_without_the_permission_cannot_post(self, no_queue_client, path):
        assert no_queue_client.post(path, data={'reason': 'x'}).status_code == 403

    # Two tests, not one: `no_queue_client` monkeypatches the permission
    # reader and returns the SAME client, so requesting both fixtures in one
    # test would strip the permission from `auth_client` too.

    def test_the_tab_is_drawn_for_the_holder(self, auth_client):
        assert b'href="/admin/account-requests"' in auth_client.get('/admin/contracts').data

    def test_the_tab_is_hidden_without_the_permission(self, no_queue_client):
        """Cosmetic gate on top of the route gate: the nav entry is hidden
        from a user who would only be 403'd by it."""
        page = no_queue_client.get('/admin/contracts')
        assert page.status_code == 200
        assert b'href="/admin/account-requests"' not in page.data


class TestRenderSmoke:

    def test_the_page_renders_with_its_filter_form_and_modal_shell(self, auth_client):
        resp = auth_client.get(PAGE)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Account requests' in html
        assert 'id="account-requests-filters"' in html
        assert 'id="auditDetailsModal"' in html
        for dim in ('state', 'purpose', 'origin', 'readiness', 'event'):
            assert f'name="{dim}" multiple hidden' in html, dim

    def test_the_fragment_renders(self, auth_client):
        resp = auth_client.get(FRAGMENT)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Reconcile' in html and 'Send to NUSD' in html

    def test_the_fragment_honors_the_view_switch_and_sort(self, auth_client):
        resp = auth_client.get(FRAGMENT + '?show_all=1&sort_by=waiting&sort_dir=desc'
                               '&state=submitted&search=zz_nobody')
        assert resp.status_code == 200
        assert 'checked' in resp.get_data(as_text=True)


class TestNotFound:

    def test_a_missing_request_404s_the_one_click_posts(self, auth_client):
        for verb in ('claim', 'unclaim', 'verify', 'reopen'):
            resp = auth_client.post(f'/admin/account-requests/{MISSING}/{verb}')
            assert resp.status_code == 404, verb
            assert 'HX-Trigger' not in resp.headers

    def test_a_missing_request_in_a_modal_answers_200_not_found(self, auth_client):
        resp = auth_client.get(f'/admin/account-requests/{MISSING}/dismiss-form')
        assert resp.status_code == 200
        assert 'not found' in resp.get_data(as_text=True).lower()

    def test_a_missing_request_404s_the_reason_posts(self, auth_client):
        for verb in ('dismiss', 'reject'):
            resp = auth_client.post(f'/admin/account-requests/{MISSING}/{verb}',
                                    data={'reason': 'dup'})
            assert resp.status_code == 404, verb


class TestGuards:

    def test_the_digest_refuses_without_a_recipient(self, auth_client, app, monkeypatch):
        monkeypatch.delenv('NOTIFY_ACCOUNT_QUEUE_TO', raising=False)
        monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_QUEUE_TO', '')
        resp = auth_client.post('/admin/account-requests/digest')
        assert resp.status_code == 200
        assert 'No digest recipient' in resp.get_data(as_text=True)
        assert 'refreshAccountQueue' not in resp.headers.get('HX-Trigger', '')

    def test_reconcile_answers_with_the_reload_trigger(self, auth_client):
        resp = auth_client.post('/admin/account-requests/reconcile')
        assert resp.status_code == 200
        assert 'refreshAccountQueue' in resp.headers.get('HX-Trigger', '')
