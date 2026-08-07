"""HTTP-tier tests for the Allocations > XRAS page.

Scope follows the house convention: the HTTP tier covers **auth, 404 and render
smoke**. Route handlers use Flask-SQLAlchemy's ``db.session`` on its own
connection and only see committed snapshot rows, so happy-path writes are
covered a layer down — replay's behaviour lives in
``tests/api/test_xras_access.py::TestReplay``.

What is genuinely worth pinning here is the **two-permission split**, because it
is the one thing a template change can silently break:

    VIEW_XRAS    the page, the table, the error lists
    MANAGE_XRAS  the raw payload (real PII) and the replay button

The payload gate is enforced twice — the route only asks the query layer for
``raw_payload`` when the viewer holds MANAGE_XRAS, so a VIEW-only response never
contains the bytes at all. A template-only gate would leave the PII sitting in
the HTML for anyone who opened view-source.
"""

import pytest

from webapp.utils.rbac import Permission


@pytest.fixture
def committed_action(app):
    """One real ``xras_action_log`` row, committed, with explicit cleanup.

    A factory-built row would be invisible here: route handlers read through
    Flask-SQLAlchemy's ``db.session`` on its own connection and only ever see
    committed rows, which is why the suite's per-test SAVEPOINT cannot help.

    Written through ``actions._record`` — the same helper the route and replay use
    — so there is exactly one insert path into this table. Deleted by primary key
    on the way out: a range predicate would take an open-ended gap lock and
    deadlock against other xdist workers inserting concurrently (see the
    ``action_log`` fixture in tests/api/test_xras_access.py).
    """
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from webapp.api.xras import actions
    from webapp.extensions import db

    payload = '{"actionType":"Extension","requestNumber":"UCUB0166"}'
    with app.app_context():
        log_id = actions._record(
            status='received', raw_payload=payload, http_status=200,
            action_type='Extension', request_number='UCUB0166',
            remote_actor='samuel',
        )

    yield log_id

    with app.app_context(), Session(db.engine) as session:
        session.execute(delete(XrasActionLog).where(
            XrasActionLog.xras_action_log_id == log_id))
        session.commit()


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS.

    `benkirk` is `[p for p in Permission]`, so there is no snapshot user who
    holds VIEW_XRAS but not MANAGE_XRAS — the split has to be simulated. Patching
    `get_user_permissions` is the narrowest way to do it: every gate in the
    request path (`require_permission`, the `has_permission` template global, the
    route's own check) reads through it.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_manage(user):
        return {p for p in real(user) if p is not Permission.MANAGE_XRAS}

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_manage)
    return auth_client


class TestXrasPageAccess:
    def test_page_renders_for_a_permitted_user(self, auth_client):
        resp = auth_client.get('/allocations/xras')
        assert resp.status_code == 200
        assert b'XRAS' in resp.data

    def test_page_is_denied_without_the_permission(self, non_admin_client):
        resp = non_admin_client.get('/allocations/xras')
        assert resp.status_code == 403

    def test_page_requires_login(self, client):
        resp = client.get('/allocations/xras')
        assert resp.status_code in (302, 401)

    @pytest.mark.parametrize('path', [
        '/allocations/xras_fragment',
        '/allocations/xras_pending_fragment',
        '/allocations/xras_action_details/1',
    ])
    def test_every_read_fragment_is_gated(self, non_admin_client, path):
        assert non_admin_client.get(path).status_code == 403

    def test_replay_is_gated_on_manage_not_view(self, view_only_client):
        """The write needs MANAGE_XRAS even though the page needs only VIEW."""
        resp = view_only_client.post('/allocations/xras_replay/1')
        assert resp.status_code == 403

    def test_view_only_user_still_gets_the_page(self, view_only_client):
        assert view_only_client.get('/allocations/xras').status_code == 200


class TestXrasFragments:
    def test_table_fragment_renders(self, auth_client):
        resp = auth_client.get('/allocations/xras_fragment')
        assert resp.status_code == 200
        # The summary strip enumerates every status, including at zero — an
        # absent bucket would read as "not measured" rather than "none".
        for state in (b'Received', b'Processed', b'Manual', b'Failed', b'Replayed'):
            assert state in resp.data

    def test_table_fragment_survives_a_junk_sort_key(self, auth_client):
        """`sort_by` is whitelisted in the route; an unknown key falls back to
        the default rather than reaching `order_by`."""
        resp = auth_client.get('/allocations/xras_fragment?sort_by=raw_payload')
        assert resp.status_code == 200

    def test_table_fragment_survives_junk_pagination(self, auth_client):
        resp = auth_client.get(
            '/allocations/xras_fragment?page=abc&per_page=99999')
        assert resp.status_code == 200

    def test_pending_fragment_renders(self, auth_client):
        resp = auth_client.get('/allocations/xras_pending_fragment')
        assert resp.status_code == 200

    def test_pending_empty_state_does_not_claim_nothing_is_pending(
            self, auth_client):
        """The card can only see projects this log knows about. While capture
        mode is on, "empty" must not be presented as "all clear"."""
        resp = auth_client.get('/allocations/xras_pending_fragment')
        if b'No XRAS projects awaiting activation' in resp.data:
            assert b'does not mean nothing is pending' in resp.data

    def test_missing_action_detail_is_a_message_not_a_404_page(self, auth_client):
        """It lands in a modal body, where a 404 error page would be worse than
        useless."""
        resp = auth_client.get('/allocations/xras_action_details/999999999')
        assert resp.status_code == 200
        assert b'Action not found' in resp.data


class TestPayloadGating:
    """The PII gate. `raw_payload` is the request body verbatim and carries
    participant names, emails, phone numbers and grant-officer contacts."""

    def test_manage_user_sees_the_payload_panel(self, auth_client,
                                                committed_action):
        resp = auth_client.get(
            f'/allocations/xras_action_details/{committed_action}')
        assert resp.status_code == 200
        assert b'Raw payload' in resp.data
        assert b'actionType' in resp.data, 'the payload bytes should be present'

    def test_view_only_user_gets_the_locked_notice_and_no_bytes(
            self, view_only_client, committed_action):
        resp = view_only_client.get(
            f'/allocations/xras_action_details/{committed_action}')
        assert resp.status_code == 200
        assert b'Requires the XRAS management permission' in resp.data
        # The bytes are absent from the RESPONSE, not merely undrawn: the route
        # never asked the query layer for them, so view-source shows nothing.
        assert b'actionType' not in resp.data
        assert b'UCUB0166' in resp.data, 'non-PII metadata still renders'


class TestNavigation:
    def test_tab_appears_on_the_sibling_allocations_pages(self, auth_client):
        resp = auth_client.get('/allocations/transactions')
        assert resp.status_code == 200
        assert b'/allocations/xras' in resp.data

    def test_tab_is_hidden_from_users_without_view_xras(self, auth_client,
                                                       monkeypatch):
        from webapp.utils import rbac

        real = rbac.get_user_permissions
        monkeypatch.setattr(
            rbac, 'get_user_permissions',
            lambda user: {p for p in real(user) if p is not Permission.VIEW_XRAS})

        resp = auth_client.get('/allocations/transactions')
        assert resp.status_code == 200
        assert b'/allocations/xras' not in resp.data
