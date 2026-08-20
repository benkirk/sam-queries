"""HTTP tier for the XRAS account-creation worklist card.

Scope follows the house convention — auth, render smoke, and the one thing a
template change can silently break: the **permission split**.

    VIEW_XRAS    the card, the worklist, the filters, `isReconciled`
    MANAGE_XRAS  person detail (name, email, organization, academic status,
                 residence country) — real PII about researchers who are not
                 SAM users

The PII gate is enforced in the ROUTE: `row['person']` is set to None before
render for a VIEW-only viewer, so the response never carries the values and a
view-source cannot leak what the card chose not to draw. A template-only gate
would leave them sitting in the HTML.

`isReconciled` is deliberately on the VIEW_XRAS side. It is account *state*,
not a personal detail, and it is the signal that an item is closing itself.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from webapp.utils.rbac import Permission

pytestmark = pytest.mark.unit

URL = '/allocations/xras_accounts_fragment'


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS.

    `benkirk` holds every Permission, so there is no snapshot user with
    VIEW_XRAS and not MANAGE_XRAS — the split has to be simulated. Patching
    `get_user_permissions` is the narrowest way: every gate in the request
    path reads through it.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def limited(user, *args, **kwargs):
        return {p for p in real(user, *args, **kwargs)
                if p != Permission.MANAGE_XRAS}

    for module in (rbac, __import__(
            'webapp.dashboards.allocations.blueprint', fromlist=['x'])):
        if hasattr(module, 'get_user_permissions'):
            monkeypatch.setattr(module, 'get_user_permissions', limited)
    monkeypatch.setattr(rbac, 'get_user_permissions', limited)
    return auth_client


@pytest.fixture
def committed_worklist_action(app):
    """One committed `xras_action_log` row naming an unknown placeholder.

    Committed, not factory-built: route handlers read through
    Flask-SQLAlchemy's `db.session` on its own connection and only ever see
    committed rows. Deleted by primary key on the way out — a range predicate
    would take an open-ended gap lock and deadlock against other xdist workers.
    """
    from pathlib import Path

    from webapp.extensions import db

    from sam.integration.xras import XrasActionLog

    payload = json.loads(
        (Path(__file__).resolve().parents[1] / 'fixtures' / 'xras' / 'actions'
         / 'new_ncar4227_failed.json').read_text())

    with app.app_context():
        row = XrasActionLog(received_time=datetime.now(), remote_actor='XRAS',
                            raw_payload=json.dumps(payload), status='received',
                            action_type='New', request_number='NCAR4227')
        db.session.add(row)
        db.session.commit()
        action_id = row.xras_action_log_id

    yield action_id

    with app.app_context():
        db.session.query(XrasActionLog).filter(
            XrasActionLog.xras_action_log_id == action_id).delete()
        db.session.commit()


class TestAccess:

    def test_it_requires_login(self, client):
        response = client.get(URL)
        assert response.status_code in (302, 401)

    def test_view_xras_is_enough_to_see_it(self, view_only_client):
        assert view_only_client.get(URL).status_code == 200

    def test_a_user_without_view_xras_is_refused(self, non_admin_client):
        assert non_admin_client.get(URL).status_code == 403

    def test_it_is_embedded_in_the_xras_page(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-accounts' in body
        assert 'xras_accounts_fragment' in body

    def test_the_filter_form_lives_outside_the_fragment(self, auth_client):
        """Controls inside the fragment vanish with an empty state, and the
        container refetches a bare hx-get on refreshXrasTab."""
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        form = body.index('id="xras-accounts-filters"')
        container = body.index('id="alloc-xras-accounts"')
        assert form < container


class TestRenderStates:
    """Both are designed states, and both are what a reviewer sees first."""

    def test_the_empty_state_is_designed_not_broken(self, auth_client):
        """Production is legitimately zero rows until ACCESS repoints."""
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'Accounts Needed for XRAS Handoffs' in body
        assert 'No accounts are waiting' in body or 'xras-acct-' in body

    def test_it_renders_with_the_api_unconfigured(self, auth_client, monkeypatch):
        """The shipped state, and what staging shows. The worklist is complete
        from the action log; only XRAS-sourced detail is missing."""
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        response = auth_client.get(URL)
        assert response.status_code == 200

    def test_an_api_outage_degrades_rather_than_500ing(self, auth_client,
                                                       monkeypatch):
        from sam.integration.xras_api.base import XrasSourceUnavailable

        def boom(_username):
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr('sam.integration.xras_api.people.get_person', boom)
        assert auth_client.get(URL).status_code == 200

    def test_the_worklist_lists_an_unknown_placeholder(
            self, auth_client, committed_worklist_action):
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'placeholder38-user-00038' in body
        assert 'Create' in body


class TestPiiGating:
    """The gate is in the route; the template checks are a second layer."""

    def _stub_person(self, monkeypatch):
        monkeypatch.setattr(
            'sam.integration.xras_api.people.get_person',
            lambda username: {
                'firstName': 'Ada', 'lastName': 'Invented',
                'email': 'ada@example.invalid',
                'organization': 'Example University',
                'academicStatus': 'Graduate Student',
                'residenceCountry': 'Kiribati',
                'isReconciled': False})

    def test_manage_xras_sees_person_detail(self, auth_client, monkeypatch,
                                            committed_worklist_action):
        self._stub_person(monkeypatch)
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'ada@example.invalid' in body
        assert 'Kiribati' in body

    def test_view_only_never_receives_the_person_bytes(
            self, view_only_client, monkeypatch, committed_worklist_action):
        """Not merely undrawn — absent from the response."""
        self._stub_person(monkeypatch)
        body = view_only_client.get(URL).get_data(as_text=True)
        assert 'ada@example.invalid' not in body
        assert 'Kiribati' not in body
        assert 'Example University' not in body
        assert 'Invented' not in body
        # ...while the row itself is still there to work from.
        assert 'placeholder38-user-00038' in body

    def test_identity_state_is_not_pii(self, view_only_client, monkeypatch,
                                       committed_worklist_action):
        """XRAS-side identity state is account context, not a personal detail,
        so it survives the VIEW_XRAS gate that strips the person dict.

        (It is emphatically *not* a closure signal — see
        `TestTheHeaderDoesNotConflateTwoFacts` and `enrich_worklist`.)"""
        self._stub_person(monkeypatch)     # isReconciled: False
        body = view_only_client.get(URL).get_data(as_text=True)
        assert 'unidentified' in body


class TestFacets:

    def test_both_classifications_render_even_at_zero(self, auth_client):
        """An absent chip reads as 'not measured' — a different claim."""
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'Create account' in body and 'Reactivate account' in body

    def test_facets_are_self_excluding(self):
        """Scope a dimension by itself and every unselected value reads 0 the
        moment one is picked, which turns the chips into a dead end."""
        from webapp.dashboards.allocations.blueprint import _account_facets

        rows = [{'classification': 'absent', 'roles': ('PI',)},
                {'classification': 'inactive', 'roles': ('User',)}]
        # Filtering on classification must NOT collapse the classification
        # rollup — it is the dimension being chosen.
        facets = _account_facets(rows, 'classification',
                                 classifications=['absent'])
        assert facets == {'absent': 1, 'inactive': 1}
        # But it does scope the other dimension.
        assert _account_facets(rows, 'role', classifications=['absent']) == {'PI': 1}

    def test_an_unknown_dimension_raises(self):
        from webapp.dashboards.allocations.blueprint import _account_facets

        with pytest.raises(ValueError):
            _account_facets([], 'nonsense')

    def test_filters_are_anded_across_dimensions(self):
        from webapp.dashboards.allocations.blueprint import _filter_accounts

        rows = [{'classification': 'absent', 'roles': ('PI',)},
                {'classification': 'absent', 'roles': ('User',)}]
        assert len(_filter_accounts(rows, classifications=['absent'])) == 2
        assert len(_filter_accounts(rows, classifications=['absent'],
                                    roles=['PI'])) == 1

    def test_a_classification_filter_reaches_the_route(self, auth_client):
        assert auth_client.get(f'{URL}?classification=absent').status_code == 200
        assert auth_client.get(f'{URL}?role=PI').status_code == 200


class TestTheHeaderDoesNotConflateTwoFacts:
    """⚠️ Caught by the local smoke, and only visible in a browser.

    A *placeholder* is a username shape (`<name>-user-<token>`) XRAS mints for
    someone with no site account. *Reconciliation* is whether XRAS has since
    linked that username to a confirmed identity. They are independent: all
    three placeholders on the local corpus were reconciled, so a header badge
    reading "3 unreconciled" contradicted every row's own "identified".
    """

    def test_the_badge_counts_placeholders_by_that_name(self, auth_client,
                                                        committed_worklist_action):
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'placeholder' in body
        assert 'unreconciled' not in body.split('<tbody>')[0], (
            'the header badge must not call a placeholder count "unreconciled"')
