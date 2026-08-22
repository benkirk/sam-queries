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


# ⚠️ One worker at a time for this file. The committed fixtures below use
# FIXED identifiers ('placeholder38-user-00038', NCAR4227) and real COMMITs —
# required, because the routes read committed rows through `db.session` — so
# two xdist workers running these tests concurrently either collide on the
# unique username at setup, or one worker's committed user flips another's
# `absent` classification mid-assertion. See `serial_file_lock` in
# tests/conftest.py for why this is a lock and not `--dist loadgroup`.
@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('xras_accounts_committed_fixtures'):
        yield


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


@pytest.fixture
def deactivated_worklist_user(app):
    """A committed, INACTIVE `users` row for the worklist payload's username.

    Committed for the same reason as the action row above — the route reads
    through `db.session` on its own connection.

    This is the `inactive` half of `classify_accounts`: a `users` row that
    exists and is not active, which the card badges "Reactivation". Without it
    the same username classifies `absent` ("New account"), so the two fixtures
    together are the only way to exercise both branches of the link.
    """
    from webapp.extensions import db

    from sam.core.users import User

    with app.app_context():
        user = User(username='placeholder38-user-00038',
                    unix_uid=999_000_038, active=False, locked=False)
        db.session.add(user)
        db.session.commit()
        user_id = user.user_id

    yield user_id

    with app.app_context():
        db.session.query(User).filter(User.user_id == user_id).delete()
        db.session.commit()


class TestTheUsernameLinksWhenSamHasTheAccount:
    """The username opens the shared `#userDetailsModal` — but only when there
    is something behind it.

    `classify_accounts` has already resolved every username on this card
    against `users` (an ACTIVE user never reaches the card at all), so the link
    costs no query: `absent` means no row exists, `inactive` means one does.
    Gated on the same branch as the New-account / Reactivation badge, so the
    two can never disagree.
    """

    def test_an_absent_username_is_not_a_link(self, auth_client,
                                              committed_worklist_action):
        """⚠️ The important direction. `absent` means `classify_accounts`
        found no `users` row at all, so a link would open a modal about
        nobody — an operator would read the empty body as a broken page
        rather than as 'this account does not exist', which is the very
        thing the row is telling them."""
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'placeholder38-user-00038' in body
        assert 'New account' in body
        assert 'userDetailsModal' not in body

    def test_an_inactive_username_links_to_its_user_card(
            self, auth_client, committed_worklist_action,
            deactivated_worklist_user):
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'Reactivation' in body
        assert '/admin/user/placeholder38-user-00038' in body
        assert 'data-bs-target="#userDetailsModal"' in body
        assert 'hx-target="#userDetailsModalBody"' in body

    def test_the_links_are_visibly_links(self, auth_client,
                                         committed_worklist_action,
                                         deactivated_worklist_user):
        """⚠️ Not `text-decoration-none text-reset`, the compact-table idiom
        from user_rows.html / contract_bits.html. That makes a link inherit
        its cell's colour, which is right where every name in the column is
        one. Here most rows do NOT link, so a colour-inheriting link was
        indistinguishable from the plain text beside it — measured in a
        browser, where UWIS0064 opened a modal and UAHV0010 did not and
        nothing on screen said which.

        `btn-entity` is the other half: `.btn` sets 1.25rem, so without it the
        identifier rendered 20px among 14px neighbours.
        """
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'btn btn-link btn-entity p-0' in body
        assert 'text-decoration-none text-reset' not in body


class TestTheRowStillExpands:
    """⚠️ The link forced the collapse toggle off the `<tr>`.

    Bootstrap registers its data-api with `useCapture`, so a toggle on an
    ancestor of the link fires FIRST — every click would open the modal and
    flip the row open behind it. `tests/unit/test_collapse_trigger_rows.py`
    is the static guard; these two assert the replacement actually works.
    """

    def test_the_summary_row_no_longer_carries_the_toggle(
            self, auth_client, committed_worklist_action):
        body = auth_client.get(URL).get_data(as_text=True)
        assert '<tr class="cursor-pointer" data-bs-toggle="collapse"' not in body

        # The toggle did not vanish, it multiplied: the chevron's own span
        # plus every cell except the one holding the link.
        assert body.count('data-bs-target="#xras-acct-1"') >= 5, \
            'the row lost its expand behaviour entirely' 

    def test_the_chevron_sits_in_its_own_trigger(self, auth_client,
                                                 committed_worklist_action):
        """It stays at the START of the row — parked beside the next column's
        badge it reads as that badge's icon — so it needs a trigger of its
        own, the username cell having none."""
        body = auth_client.get(URL).get_data(as_text=True)
        assert ('<span class="cursor-pointer" data-bs-toggle="collapse"'
                in body)
        span = body.split('<span class="cursor-pointer" data-bs-toggle="collapse"', 1)[1]
        assert 'collapse-icon' in span.split('</span>', 1)[0]


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
        assert 'New account' in body


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
        assert 'New account' in body and 'Reactivation' in body

    def test_the_labels_do_not_tell_an_operator_to_do_what_SAM_cannot(
            self, auth_client):
        """⚠️ The remedies are somebody else's work, and the card must not
        imply otherwise.

        There is no INSERT into ``users`` anywhere in this repo, ``User`` alone
        among the models has no ``create()``, and nothing writes ``active`` or
        ``locked`` — identities are mirrored in from the enterprise directory.
        So "Create account" as a badge was an instruction to a SAM operator who
        has no way to carry it out. The wire values are unchanged; only what a
        human reads moved.
        """
        body = auth_client.get(URL).get_data(as_text=True)
        assert 'Create account' not in body
        assert 'Reactivate account' not in body
        assert 'mirrored into SAM from the enterprise directory' in body

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


class TestTheWindowNeverHidesSilently:
    """⚠️ On the Feed-B tab an OLDER unpushed request is usually the MORE
    urgent one — a request submitted six months ago that still has no SAM
    project is the worst case there. A recency filter that quietly dropped it
    would invert the priority the tab exists to surface.

    Measured on the live snapshot: the sweep found 21 accounts needed and the
    90-day pill showed 12. The nine hidden rows were the oldest, i.e. the ones
    most worth acting on. The filter stays — one window across three tabs is
    the point — but the header must say what it hid.
    """

    URL = '/allocations/xras_pending_requests_fragment'

    def _publish(self, rows_total, shown_total):
        from sam.integration.xras_api.cache import store_pending_worklist
        from datetime import datetime
        rows = []
        for i in range(rows_total):
            # The first `shown_total` are recent; the rest are ancient.
            submitted = '2026-08-19' if i < shown_total else '2019-01-01'
            rows.append({
                'username': f'ghost-user-{i}', 'classification': 'absent',
                'remedy': 'create', 'placeholder': False, 'roles': ('PI',),
                'is_account_to_be_created': False, 'is_reconciled': None,
                'person': None, 'sources': ['reports'],
                'actions': [{'action_log_id': None,
                             'request_number': f'NCAR{i:04d}',
                             'action_type': 'New', 'status': 'Approved',
                             'received_time': None, 'submit_date': submitted,
                             'source': 'reports', 'would_succeed': None,
                             'reject_messages': []}],
            })
        store_pending_worklist({
            'generated_at': datetime(2026, 8, 20), 'window_days': 90,
            'status': 'Approved', 'requests_seen': 100,
            'requests_in_window': 50, 'budget_exhausted': False,
            'pending_push': rows_total, 'pending_push_sample': [],
            'counts': {'total': rows_total, 'absent': rows_total,
                       'inactive': 0, 'placeholder': 0, 'reconciled': 0},
            'rows': rows,
        })

    def test_it_reports_how_many_the_window_hid(self, auth_client, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        self._publish(rows_total=5, shown_total=2)
        body = auth_client.get(f'{self.URL}?days=30').get_data(as_text=True)
        assert '2 of 5' in body
        assert 'outside the date filter' in body

    def test_it_says_nothing_when_the_window_hides_nothing(self, auth_client,
                                                           monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        self._publish(rows_total=3, shown_total=3)
        body = auth_client.get(f'{self.URL}?days=30').get_data(as_text=True)
        assert 'outside the date filter' not in body


class TestBothTabsShowTheSameDetail:
    """⚠️ Feed B had already diverged: it rendered name and organization only,
    so an operator who found a row there still had to go elsewhere for the
    email and country needed to actually create the account.

    Both tabs now render `fragments/xras_person_detail.html`, whose field list
    IS `PERSON_FIELDS` — the same filter both feeds pass their person dicts
    through. There is no second list to keep in step.
    """

    PENDING_URL = '/allocations/xras_pending_requests_fragment'

    @staticmethod
    def _person():
        return {'firstName': 'Ada', 'middleName': 'Q', 'lastName': 'Invented',
                'email': 'ada@example.invalid', 'phone': '555-0100',
                'organization': 'Example University',
                'academicStatus': 'Graduate Student',
                'residenceCountry': 'Kiribati', 'orcid': '0000-0001',
                'isReconciled': False}

    def test_every_declared_field_reaches_the_accounts_tab(
            self, auth_client, monkeypatch, committed_worklist_action):
        monkeypatch.setattr('sam.integration.xras_api.people.get_person',
                            lambda u: self._person())
        body = auth_client.get(URL).get_data(as_text=True)
        for value in ('ada@example.invalid', '555-0100', 'Kiribati',
                      'Graduate Student', 'Example University', '0000-0001'):
            assert value in body, f'{value} is missing from the accounts tab'

    def test_every_declared_field_reaches_the_pending_tab(self, auth_client,
                                                          monkeypatch):
        from datetime import datetime

        from sam.integration.xras_api.cache import store_pending_worklist

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        store_pending_worklist({
            'generated_at': datetime(2026, 8, 20), 'window_days': 90,
            'status': 'Approved', 'requests_seen': 1, 'requests_in_window': 1,
            'budget_exhausted': False, 'pending_push': 1,
            'pending_push_sample': [],
            'counts': {'total': 1, 'absent': 1, 'inactive': 0,
                       'placeholder': 0, 'reconciled': 0},
            'rows': [{'username': 'ghost-user-1', 'classification': 'absent',
                      'remedy': 'create', 'placeholder': False,
                      'roles': ('PI',), 'is_account_to_be_created': False,
                      'is_reconciled': False, 'person': self._person(),
                      'sources': ['reports'],
                      'actions': [{'action_log_id': None,
                                   'request_number': 'NCAR0001',
                                   'action_type': 'New', 'status': 'Approved',
                                   'received_time': None,
                                   'submit_date': '2026-08-19',
                                   'source': 'reports', 'would_succeed': None,
                                   'reject_messages': []}]}],
        })
        body = auth_client.get(self.PENDING_URL).get_data(as_text=True)
        for value in ('ada@example.invalid', '555-0100', 'Kiribati',
                      'Graduate Student', 'Example University', '0000-0001'):
            assert value in body, f'{value} is missing from the pending tab'
        # ...and the row expands to reach it.
        assert 'data-bs-toggle="collapse"' in body

    def test_the_pending_tab_still_withholds_pii_from_view_only(
            self, view_only_client, monkeypatch):
        """The macro is a second gate; the route is the real one."""
        from datetime import datetime

        from sam.integration.xras_api.cache import store_pending_worklist

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        store_pending_worklist({
            'generated_at': datetime(2026, 8, 20), 'window_days': 90,
            'status': 'Approved', 'requests_seen': 1, 'requests_in_window': 1,
            'budget_exhausted': False, 'pending_push': 1,
            'pending_push_sample': [],
            'counts': {'total': 1, 'absent': 1, 'inactive': 0,
                       'placeholder': 0, 'reconciled': 0},
            'rows': [{'username': 'ghost-user-1', 'classification': 'absent',
                      'remedy': 'create', 'placeholder': False,
                      'roles': ('PI',), 'is_account_to_be_created': False,
                      'is_reconciled': False, 'person': self._person(),
                      'sources': ['reports'],
                      'actions': [{'action_log_id': None,
                                   'request_number': 'NCAR0001',
                                   'action_type': 'New', 'status': 'Approved',
                                   'received_time': None,
                                   'submit_date': '2026-08-19',
                                   'source': 'reports', 'would_succeed': None,
                                   'reject_messages': []}]}],
        })
        body = view_only_client.get(self.PENDING_URL).get_data(as_text=True)
        for value in ('ada@example.invalid', 'Kiribati', '555-0100'):
            assert value not in body
        assert 'ghost-user-1' in body
