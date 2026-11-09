"""The Remediations card's identity work: placeholder merges, the identity strip, the blocker
facet, make PI, and the Type and recommender popovers.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import cache as xras_cache
from sam.queries.xras_requests import request_index_entry

# The shared fixtures and payload builders; `_cache` is the autouse cache reset.
from _remediations_support import (  # noqa: F401
    _cache,
    FRAGMENT,
    view_only_client,
    configured,
    armed,
    _payload,
    _publish,
    _detail_payload,
    _reader,
    _publish_with_verdict,
    _person,
)

def _sam_holds(monkeypatch, email, username, active=True):
    """Stub the one derivation: the route's db.session cannot see SAVEPOINT rows."""
    monkeypatch.setattr(
        'sam.queries.xras_accounts.sam_merge_targets',
        lambda session, emails: ({email.lower(): {'username': username,
                                                  'active': active}}
                                 if email.lower() in {e.lower() for e in emails}
                                 else {}))


class TestUnidentifiedPlaceholdersCanMerge:
    """Every active SAM account resolves in XRAS, so the SAM account holding a
    placeholder's email is a merge target — reconciled or not."""

    GHOST = 'ghost-user-abcde'
    EMAIL = 'g@example.invalid'

    def _ghost(self, **over):
        return _person(username=self.GHOST, firstName='G', lastName='Host',
                       email=self.EMAIL, isReconciled=False, **over)

    def test_the_user_modal_offers_the_merge_when_sam_holds_the_email(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=self._ghost())
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        body = auth_client.get(
            f'/allocations/xras_user_detail/{self.GHOST}').get_data(as_text=True)
        assert f'/allocations/xras_merge_form/{self.GHOST}' in body
        assert 'SAM holds this email on' in body and 'ghost' in body

    def test_the_user_modal_offers_nothing_without_a_target(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=self._ghost())
        _sam_holds(monkeypatch, 'someone.else@example.invalid', 'other')
        body = auth_client.get(
            f'/allocations/xras_user_detail/{self.GHOST}').get_data(as_text=True)
        assert 'xras_merge_form' not in body
        assert 'No SAM account' in body

    def test_an_inactive_holder_is_shown_but_not_offered(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=self._ghost())
        _sam_holds(monkeypatch, self.EMAIL, 'ghost', active=False)
        body = auth_client.get(
            f'/allocations/xras_user_detail/{self.GHOST}').get_data(as_text=True)
        assert 'xras_merge_form' not in body
        assert 'inactive' in body

    def test_the_request_roster_offers_the_merge_when_sam_holds_the_email(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_payload(reconciled=False, email=self.EMAIL))
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Resolve identity' in body
        assert 'needs an account' not in body

    def test_the_finder_resolves_the_sam_account_directly(
            self, auth_client, armed, monkeypatch):
        """search/people never matches an email and caps at 20, so the SAM
        account holding the email is asked for by name."""
        real = {'username': 'ghost', 'firstName': 'G', 'lastName': 'Host',
                'email': self.EMAIL, 'organization': 'Example U'}
        client = _reader(monkeypatch, person=self._ghost(), candidates=[],
                         people={'ghost': real})
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        body = auth_client.get(
            f'/allocations/xras_merge_form/{self.GHOST}').get_data(as_text=True)
        assert 'email matches exactly' in body
        assert 'checked' not in body, 'no preselect, ever'
        assert 'g@example.invalid' not in [c[0][0] for c in client.search_people.call_args_list], \
            'the dead email query is gone'

    def test_the_finder_skips_the_name_search_when_sam_resolves_the_email(
            self, auth_client, armed, monkeypatch):
        """Every active SAM user resolves in XRAS, so a direct email->SAM->XRAS
        hit IS the merge target. The name-only search (capped at 20) is then not
        run at all — keeping its rank-2 arbitrary matches out of a destructive
        merge decision."""
        real = {'username': 'ghost', 'firstName': 'G', 'lastName': 'Host',
                'email': self.EMAIL, 'organization': 'Example U'}
        noise = {'username': 'ghosty', 'firstName': 'G', 'lastName': 'Host',
                 'email': 'other@example.invalid', 'organization': 'NCAR'}
        client = _reader(monkeypatch, person=self._ghost(), candidates=[noise],
                         people={'ghost': real})
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        body = auth_client.get(
            f'/allocations/xras_merge_form/{self.GHOST}').get_data(as_text=True)
        assert 'email matches exactly' in body
        assert 'ghosty' not in body, 'name-only rows suppressed once SAM resolves'
        assert client.search_people.call_count == 0

    def test_the_finder_asks_for_the_full_name_as_well_as_the_surname(
            self, auth_client, armed, monkeypatch):
        hit = {'username': 'ghosth', 'firstName': 'G', 'lastName': 'Host',
               'email': 'other@example.invalid', 'organization': 'Example U'}
        client = _reader(monkeypatch, person=self._ghost(organization='Example U'))
        client.search_people.side_effect = lambda q: [hit] if q == 'G Host' else []
        _sam_holds(monkeypatch, 'nobody@example.invalid', 'x')
        body = auth_client.get(
            f'/allocations/xras_merge_form/{self.GHOST}').get_data(as_text=True)
        assert 'ghosth' in body and 'organization matches' in body

    def test_an_unreconciled_placeholder_with_no_target_still_says_needs_an_account(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_payload(reconciled=False, email=self.EMAIL))
        _sam_holds(monkeypatch, 'nobody@example.invalid', 'x')
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'needs an account' in body


class TestIdentityUnblockStrip:
    """The identity strip — placeholders SAM can merge now, opening the merge modal."""

    EMAIL = 'ghost@example.invalid'

    def _publish(self, push_state='pending'):
        payload = _payload('EXAM0001', reconciled=False, email=self.EMAIL)
        _publish_with_verdict(payload, push_state=push_state, status='failed')
        xras_cache.store_pending_worklist({
            'generated_at': datetime.now(), 'window_days': 120,
            'rows': [{'username': 'ghost-user-abcde', 'classification': 'absent',
                      'remedy': 'create', 'placeholder': True,
                      'is_reconciled': False, 'roles': ('User',),
                      'sources': ['reports'], 'waiting_since': None,
                      'person': {'email': self.EMAIL, 'firstName': 'G',
                                 'lastName': 'Host'},
                      'actions': [{'action_log_id': None,
                                   'request_number': 'EXAM0001',
                                   'action_type': 'Supplement',
                                   'status': 'Approved', 'received_time': None,
                                   'submit_date': '2026-08-20',
                                   'source': 'reports', 'would_succeed': None,
                                   'preflight_status': None,
                                   'reject_messages': []}]}]})

    def test_the_strip_reads_pushes_from_the_sweeps_window(self, auth_client, armed,
                                                           monkeypatch):
        """Like the task and the sweep: Feed A is bounded where the sweep's lookback began."""
        from types import SimpleNamespace
        self._publish()
        seen = {}
        monkeypatch.setattr('sam.queries.xras_accounts.assemble_worklist',
                            lambda *a, **k: seen.update(k) or SimpleNamespace(rows=[]))
        assert auth_client.get(FRAGMENT).status_code == 200
        built = xras_cache.load_pending_worklist()['generated_at']
        assert seen['since'] == built - timedelta(days=120)

    def test_a_ready_placeholder_opens_the_merge_modal(self, auth_client, armed,
                                                       monkeypatch):
        self._publish()
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        strip = body.split('id="xras-identity-strip"')[1][:4000]
        assert 'ready to merge' in strip
        assert '/allocations/xras_merge_form/ghost-user-abcde' in strip
        assert 'data-bs-target="#xrasModal"' in strip
        assert 'ghost' in strip
        assert self.EMAIL not in strip, 'the strip names accounts, never emails'

    def test_the_strip_explains_itself_in_a_popover(self, auth_client, armed,
                                                    monkeypatch):
        self._publish()
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        strip = auth_client.get(FRAGMENT).get_data(as_text=True).split(
            'id="xras-identity-strip"')[1][:4000]
        assert 'data-bs-toggle="popover"' in strip and 'Placeholders to merge' in strip

    def test_the_lever_off_disables_the_button(self, auth_client, configured,
                                               monkeypatch):
        self._publish()
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        strip = auth_client.get(FRAGMENT).get_data(as_text=True).split(
            'id="xras-identity-strip"')[1][:4000]
        assert 'disabled' in strip and 'XRAS_WRITE_ENABLED' in strip

    def test_the_strip_follows_the_rows_in_view(self, auth_client, armed, monkeypatch):
        self._publish(push_state='seen_in_log')
        _sam_holds(monkeypatch, self.EMAIL, 'ghost')
        assert 'xras-identity-strip' not in auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'xras-identity-strip' in auth_client.get(
            FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)

    def test_no_sam_account_says_so_rather_than_offering(self, auth_client, armed,
                                                         monkeypatch):
        self._publish()
        _sam_holds(monkeypatch, 'nobody@example.invalid', 'x')
        strip = auth_client.get(FRAGMENT).get_data(as_text=True).split(
            'id="xras-identity-strip"')[1][:4000]
        assert 'need' in strip and 'SAM account' in strip
        assert 'xras_merge_form' not in strip

    def test_view_only_is_still_forbidden(self, view_only_client, armed, monkeypatch):
        self._publish()
        assert view_only_client.get(FRAGMENT).status_code == 403


class TestAllocationDateWireKeys:
    """The third fixture-agrees-with-the-bug occurrence (after resourceRepositoryKey):
    the modal read `type` while the wire says `allocationDateType`, and the
    fixture spelled it the modal's way, so the stage label rendered blank in
    production with every test green."""

    WIRE = {'allocationDateId', 'allocationDateType', 'beginDate', 'endDate'}

    def test_the_fixture_speaks_the_wire(self):
        for entry in _detail_payload()['actions'][0]['allocationDates']:
            assert set(entry) <= self.WIRE, set(entry) - self.WIRE

    def test_the_modal_reads_only_wire_keys(self):
        import ast
        import inspect

        from webapp.dashboards.allocations.xras import modals
        tree = ast.parse(inspect.getsource(modals))
        keys = {node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get' and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).startswith('allocationDate')}
        # `allocationDates` itself is the array key on the action.
        assert keys and keys <= self.WIRE | {'allocationDates'}, keys

    def test_the_stage_label_renders(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert '(Requested)' in body


class TestTheMergeScreenShowsIdentityState:
    """The client returns isReconciled; the irreversible screen must show it."""

    def test_candidates_carry_the_identity_badge(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid'},
                candidates=[{'username': 'ghost', 'firstName': 'G', 'lastName': 'Host',
                             'email': 'g@example.invalid', 'isReconciled': True},
                            {'username': 'ghost2', 'firstName': 'G', 'lastName': 'Host',
                             'email': 'g2@example.invalid', 'isReconciled': False}])
        _sam_holds(monkeypatch, 'nobody@example.invalid', 'x')
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert '>identified<' in body and '>unidentified<' in body


class TestTheAddRoleCopyKnowsSamResolves:

    def test_the_field_says_a_sam_account_is_safe(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Add username (SAM or XRAS)' in body
        assert 'any active SAM account' in body


class TestBlockerFacet:
    """The Blocker facet: a per-row category derived from the same signals the
    three summary strips pivot on, filterable so an operator can trace an
    aggregate blocker down to the rows it blocks."""

    @staticmethod
    def _publish_blockers():
        from sam.xras import errors as e
        from sam.xras.errors import MNEMONIC_INTERNAL_PREFIX

        mnem = _payload('MNEM0001', placeholder=False)
        pers = _payload('PERS0001', placeholder=False)
        ctract = _payload('CTRL0001', placeholder=False)
        acct = _payload('ACCT0001')  # placeholder+reconciled -> account blocker
        preflights = {
            'MNEM0001': {7: {'status': 'failed', 'push_state': None,
                             'messages': [MNEMONIC_INTERNAL_PREFIX + ': ACME Org'],
                             'resolved': {}}},
            'CTRL0001': {7: {'status': 'failed', 'push_state': None,
                             'messages': ['grant not found'],
                             'resolved': {'unresolved_grants': [{'number': 'X-1'}]}}},
            # A PI SAM does not hold also reports "no affiliation data": person, not mnemonic.
            'PERS0001': {7: {'status': 'failed', 'push_state': None,
                             'messages': [e.pi_not_in_database('p-user-1'),
                                          e.no_affiliation_for_pi('p-user-1')],
                             'resolved': {}}},
        }
        xras_cache.store_requests_index({
            'generated_at': datetime.now(),
            'statuses': ['Approved', 'Submitted', 'Under Review'],
            'extra_statuses': {},
            'rows': [request_index_entry(mnem, pending_push=True,
                                         preflights=preflights['MNEM0001']),
                     request_index_entry(ctract, pending_push=True,
                                         preflights=preflights['CTRL0001']),
                     request_index_entry(pers, pending_push=True,
                                         preflights=preflights['PERS0001']),
                     request_index_entry(acct, pending_push=True)]})

    def test_the_chip_row_counts_each_category(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(FRAGMENT + '?show_all=1').get_data(as_text=True)
        assert 'Blocker' in body
        # Each category is present exactly once across the three rows.
        for field in ('Person', 'Mnemonic', 'Contract', 'Account'):
            assert field in body

    @staticmethod
    def _table_rows(body):
        # The row's request cell links via xras_request_detail/<number>; the
        # summary strips reference numbers too, so assert on the table marker.
        import re
        return set(re.findall(r'xras_request_detail/(\w+)', body))

    def test_it_filters_to_the_contract_rows(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(
            FRAGMENT + '?show_all=1&blockers=contract').get_data(as_text=True)
        assert self._table_rows(body) == {'CTRL0001'}

    def test_it_filters_to_the_mnemonic_rows(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(
            FRAGMENT + '?show_all=1&blockers=mnemonic').get_data(as_text=True)
        assert self._table_rows(body) == {'MNEM0001'}

    def test_a_missing_pi_filters_as_person_not_mnemonic(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(
            FRAGMENT + '?show_all=1&blockers=person').get_data(as_text=True)
        assert self._table_rows(body) == {'PERS0001'}

    def test_unfiltered_shows_all_four(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(FRAGMENT + '?show_all=1').get_data(as_text=True)
        assert self._table_rows(body) == {'MNEM0001', 'CTRL0001', 'PERS0001', 'ACCT0001'}

    def test_each_row_carries_its_blocker_icon(self, auth_client, configured):
        self._publish_blockers()
        body = auth_client.get(FRAGMENT + '?show_all=1').get_data(as_text=True)
        assert 'fa-link-slash' in body      # mnemonic
        assert 'fa-file-contract' in body   # contract
        assert 'fa-user-xmark' in body      # person
        # The account icon replaces the old verbose "placeholder roster" badge.
        assert 'placeholder roster' not in body


class TestProjcodePreview:
    """The readiness partial previews the projcode a would-land New request would
    mint — series + a placeholder for the number, which is only assigned at
    creation (a race). Shared by the request-detail and readiness modals."""

    _T = 'dashboards/allocations/partials/_xras_readiness_why.html'

    def test_a_would_land_new_shows_the_projcode_shape(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template(self._T, action={'preflight': {
                'status': 'rechecked', 'would_succeed': True,
                'resolved': {'series': 'NRAL', 'allocation_type': 'University'}}})
        assert 'Would be created as' in html
        assert 'NRAL####' in html
        assert '· University' in html          # keeps the allocation-type suffix

    def test_a_would_fail_action_shows_no_projcode(self, app):
        # No series without a resolved mint -> no preview (also the New-only gate).
        with app.test_request_context():
            from flask import render_template
            html = render_template(self._T, action={'preflight': {
                'status': 'failed', 'would_succeed': False,
                'messages': ['x'], 'resolved': {}}})
        assert 'created as' not in html


class TestTheTicketKey:
    """A refused request's NUSD ticket shows on its row (XRAS_REQUEST_TICKETS.md § 6).

    Each test files under its own request id: the ticket row is committed, so on the
    shared 900001 another worker's row could show it (the flake this replaced).
    """

    @staticmethod
    def _own_request():
        from factories import next_seq
        payload = _payload()
        payload['requestId'] = int(next_seq('91'))
        _publish(payload)
        return payload['requestId']

    def _body_with_ticket(self, app, auth_client, provider, key):
        from sqlalchemy.orm import Session

        from sam import ExternalTicket
        from webapp.extensions import db
        rid = self._own_request()
        with app.app_context():
            engine = db.engine
        with Session(engine) as s:
            ExternalTicket.create(s, provider=provider, ticket_key=key.format(rid=rid),
                                  entity_type='xras_request', entity_id=rid,
                                  origin='created', requested_by='task:xras_request_tickets')
            s.commit()
        try:
            return rid, auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(
                as_text=True)
        finally:
            with Session(engine) as s:
                s.query(ExternalTicket).filter_by(entity_type='xras_request',
                                                  entity_id=rid).delete()
                s.commit()

    def test_the_row_names_its_ticket(self, app, auth_client, configured):
        rid, body = self._body_with_ticket(app, auth_client, 'dry-run', 'DRY-SAM-XR-{rid}')
        assert f'DRY-SAM-XR-{rid}' in body
        # A provider SAM never reads has no page to open: the badge stays plain.
        assert '/browse/DRY-SAM-XR-' not in body

    def test_a_jira_ticket_opens_in_a_new_tab(self, app, auth_client, configured,
                                              monkeypatch):
        monkeypatch.setenv('JIRA_BASE_URL', 'https://tickets.example.org')
        rid, body = self._body_with_ticket(app, auth_client, 'jira-servicedesk',
                                           'RC-{rid}')
        link = re.search(rf'<a class="badge state-tag[^"]*" href="([^"]+)" '
                         rf'target="_blank" rel="noopener"[^>]*>RC-{rid}<', body)
        assert link and link.group(1) == f'https://tickets.example.org/browse/RC-{rid}'

    def test_a_row_without_one_shows_none(self, auth_client, configured):
        self._own_request()
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'NUSD ticket for this refused post' not in body


# make PI: the one-click swap for a student who filed with their advisor as PI

def _advisor_and_student():
    payload = _detail_payload()
    payload['roles'] = [
        {'person': {'username': 'pi-user', 'firstName': 'P', 'lastName': 'Eye',
                    'isReconciled': True},
         'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]},
        {'person': {'username': 'ghost-user-abcde', 'firstName': 'G', 'lastName': 'Host',
                    'isReconciled': True},
         'roles': [{'roleId': 2, 'role': 'User', 'roleTypeId': 19}]},
        {'person': {'username': 'student', 'firstName': 'S', 'lastName': 'Tudent',
                    'isReconciled': True},
         'roles': [{'roleId': 3, 'role': 'Allocation Manager', 'roleTypeId': 14}]}]
    return payload


def _outcome(*steps, patched=True):
    from sam.manage.xras_remediation import MakePiOutcome, RemediationOutcome
    return MakePiOutcome(tuple((verb, what, RemediationOutcome(1, status=status, error=error))
                               for verb, what, status, error in steps), patched)


class TestMakePi:
    MAKE = '/allocations/xras_role_make_pi/EXAM0001/'

    def _body(self, auth_client):
        return auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)

    def test_the_button_is_on_a_real_non_pi_row_only(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_advisor_and_student())
        body = self._body(auth_client)
        assert self.MAKE + '3"' in body
        assert self.MAKE + '1"' not in body, 'not on the PI'
        assert self.MAKE + '2"' not in body, 'not on a placeholder'
        assert 'fa-crown text-muted' in body, 'the PI row carries the crown'
        assert 'student: Allocation Manager → PI; pi-user: PI → User.' in body
        assert 'data-confirm-variant="warning"' in body, 'consequential, not destructive'

    def test_it_is_disabled_with_writes_off(self, auth_client, configured, monkeypatch):
        _reader(monkeypatch, payload=_advisor_and_student())
        body = self._body(auth_client)
        button = body[body.index(self.MAKE + '3"') - 400:body.index(self.MAKE + '3"')]
        assert 'disabled' in button

    def test_the_pi_and_a_placeholder_are_refused(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_advisor_and_student())
        called = []
        monkeypatch.setattr('sam.manage.xras_remediation.make_pi',
                            lambda *a, **k: called.append(k))
        pi = auth_client.post(self.MAKE + '1')
        assert 'already the PI' in pi.get_data(as_text=True)
        assert 'HX-Trigger' not in pi.headers
        ghost = auth_client.post(self.MAKE + '2').get_data(as_text=True)
        assert 'placeholder' in ghost and called == []

    def test_a_completed_swap_flashes_every_step(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_advisor_and_student())
        seen = []

        def fake(*a, **k):
            seen.append(k)
            return _outcome(('add', 'student as PI', 'verified', None),
                            ('keep', 'pi-user as User', 'verified', None))
        monkeypatch.setattr('sam.manage.xras_remediation.make_pi', fake)
        response = auth_client.post(self.MAKE + '3')
        body = response.get_data(as_text=True)
        assert 'student is now the PI: added student as PI; kept pi-user as User.' in body
        assert response.headers.get('HX-Trigger') == 'refreshXrasTab'
        assert (seen[0]['target_role_id'], seen[0]['xa_user']) == (3, 'pi-user')

    def test_a_stopped_swap_says_what_landed_and_what_failed(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_advisor_and_student())
        monkeypatch.setattr(
            'sam.manage.xras_remediation.make_pi',
            lambda *a, **k: _outcome(('add', 'student as PI', 'verified', None),
                                     ('remove', 'pi-user as PI', 'rejected', 'nope')))
        response = auth_client.post(self.MAKE + '3')
        body = response.get_data(as_text=True)
        assert 'Make PI stopped: removing pi-user as PI failed (nope).' in body
        assert 'Done so far: added student as PI.' in body
        assert response.headers.get('HX-Trigger') == 'refreshXrasTab', 'one step landed'


class TestRefusalsLeaveTheCardsAlone:

    def test_removing_the_acting_pis_only_role_says_what_works(
            self, auth_client, armed, monkeypatch):
        from sam.manage.xras_remediation import RemediationOutcome
        _reader(monkeypatch, payload=_advisor_and_student())
        monkeypatch.setattr(
            'sam.manage.xras_remediation.change_role',
            lambda *a, **k: RemediationOutcome(1, status='rejected',
                                               error="can't delete your only role"))
        response = auth_client.post('/allocations/xras_role_remove/EXAM0001/1')
        assert "use Make PI on the new PI&#39;s row" in response.get_data(as_text=True)
        assert 'HX-Trigger' not in response.headers

    def test_a_refused_add_does_not_reload_the_cards(self, auth_client, armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        response = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                    data={'username': 'nobody', 'role_type': 'User'})
        assert 'HX-Trigger' not in response.headers


class TestARosterWriteReadsTheRequestTwice:
    """Once before the write and once to verify it: route, verify-before, card patch and
    dialog re-render share the first read, and the write forces the second."""

    def test_an_add(self, auth_client, armed, monkeypatch):
        from sam.integration.xras_api import XrasApiClient
        from sam.integration.xras_api.admin_client import XrasAdminClient
        from sam.manage import xras_remediation

        state = {'added': False}
        seen = []

        def route(method, url, **_kw):
            path = url.split('api.xras.org', 1)[-1]
            seen.append((method, path))
            payload = _payload(placeholder=False)
            if state['added']:
                payload['roles'].append(
                    {'person': {'username': 'student', 'isReconciled': True},
                     'roles': [{'roleId': 77, 'role': 'User', 'roleTypeId': 19}]})
            if method == 'POST':
                state['added'] = True
                result = {'roleId': 77}
            elif '/reports/request_numbers/' in path:
                result = [payload]
            elif path.startswith('/v1/people/'):
                result = {'username': 'student'}
            else:
                result = []
            resp = MagicMock(status_code=200, text='')
            resp.json.return_value = {'result': result}
            return resp

        def reader(cls, *a, **k):
            client = XrasApiClient(XrasApiConfig.from_environment())
            client.session.request = route
            return client

        def admin(cls, *a, **k):
            client = XrasAdminClient(XrasApiConfig.from_environment(),
                                     reader=reader(XrasApiClient))
            client.session.request = route
            return client
        from sam.integration.xras_api import XrasApiConfig
        monkeypatch.setattr(XrasApiClient, 'from_environment', classmethod(reader))
        monkeypatch.setattr(XrasAdminClient, 'from_environment', classmethod(admin))
        monkeypatch.setattr(xras_remediation, '_open_event', lambda *a, **k: None)
        monkeypatch.setattr(xras_remediation, '_close_event', lambda *a, **k: None)

        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'student', 'role_type': 'User'}
                                ).get_data(as_text=True)
        assert 'Added student as User' in body
        family = [p for m, p in seen if m == 'GET' and '/request_numbers/' in p]
        assert len(family) == 2, seen
        assert [m for m, _ in seen].count('POST') == 1


class TestTheTypePopover:

    def test_the_type_cell_carries_the_gist(self, auth_client, configured):
        payload = _payload()
        payload['title'] = 'Turbulence at scale'
        payload['actions'][0].update(resources=[
            {'displayResourceName': 'Derecho', 'amount': '1000.0',
             'resourceUnits': 'Core-hours', 'type': 'Requested'}])
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-trigger="hover focus"' in body
        assert 'data-bs-title="Turbulence at scale"' in body
        # The popover body is HTML in an attribute: escaped here, parsed by Bootstrap.
        assert 'Supplement requesting&lt;/p&gt;' in body
        assert '&lt;li&gt;Derecho 1,000 Core-hours&lt;/li&gt;' in body

    def test_a_row_swept_before_the_lines_shows_its_sentence(self, auth_client, configured):
        _publish(_payload())
        entry = xras_cache.load_requests_index()
        for row in entry['rows']:
            row['gist'] = {'title': 'T', 'ask': 'Supplement requesting Derecho 1,000 Core-hours.'}
        xras_cache.store_requests_index(entry)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Supplement requesting Derecho 1,000 Core-hours.' in body

    def test_a_row_swept_before_the_gist_shows_the_plain_label(self, auth_client, configured):
        _publish(_payload())
        entry = xras_cache.load_requests_index()
        for row in entry['rows']:
            row.pop('gist', None)
        xras_cache.store_requests_index(entry)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-trigger="hover focus"' not in body
        assert '>Supplement</span>' in body



class TestTheRecommenderNote:
    """A tool's note in the request dialog and as a marker on the card (ARC_RECOMMENDER_NOTES.md § 4).

    Route reads go through ``db.session``, which cannot see the test session's rows, so the
    lookups are patched to hand back a note built in the test session.
    """

    MODALS = 'webapp.dashboards.allocations.xras.modals.lookup_request_notes'
    CARD = 'webapp.dashboards.allocations.xras.remediation.request_notes_by_number'

    def _note(self, session, **kw):
        from factories import make_request_note, next_seq
        kw.setdefault('admin_comment', 'Approved as requested for one year.')
        kw.setdefault('action_id', 7)
        # The lookups are patched, so the row's own number is free: a fixed one deadlocks
        # two xdist workers inserting the same uncommitted PK (InnoDB 1213).
        return make_request_note(session, request_number=next_seq('NOTEUI'), rationale='Inside every rule.',
                                 values={'hours_by_resource': {'Derecho': 1000000},
                                         'requested_hours': {'Derecho': 1200000}}, **kw)

    def _dialog(self, auth_client, monkeypatch, notes):
        monkeypatch.setattr(self.MODALS, lambda session, number: notes)
        _reader(monkeypatch, payload=_detail_payload())
        return auth_client.get('/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)

    def test_the_dialog_shows_the_note_with_copy_and_sources(self, auth_client, armed,
                                                             monkeypatch, session):
        body = self._dialog(auth_client, monkeypatch, [self._note(session)])
        assert 'Needs a person to review' in body
        assert 'run 2026-11-05T06:00' in body
        assert 'data-copy="Approved as requested for one year."' in body
        assert 'Copy admin comment' in body
        assert 'Process action 7 in XRAS' in body
        assert 'Needs your judgment' in body and 'Already checked' in body
        assert 'NSF award 2320667' in body and 'Inside every rule.' in body
        # raw: the operator types these into the XRAS Process page
        assert 'Hours: Derecho 1,000,000' in body and 'Requested: Derecho 1,200,000' in body

    def test_every_note_link_opens_without_a_referrer(self, auth_client, armed,
                                                      monkeypatch, session):
        body = self._dialog(auth_client, monkeypatch, [self._note(session)])
        start = body.index('Recommender</h6>')
        section = body[start:body.index('</section>', start)]
        links = re.findall(r'<a [^>]*href="https?://[^"]*"[^>]*>', section)
        note_links = [a for a in links if 'nsf.gov' in a]
        assert note_links and all('rel="noopener noreferrer"' in a for a in note_links)
        assert 'data-bs-toggle="modal"' not in section

    def test_no_note_no_section(self, auth_client, armed, monkeypatch):
        body = self._dialog(auth_client, monkeypatch, [])
        assert 'Recommender</h6>' not in body
        assert 'Copy admin comment' not in body

    def test_approve_reads_as_a_tag_not_attention(self, auth_client, armed, monkeypatch, session):
        body = self._dialog(auth_client, monkeypatch, [self._note(session, decision='approve')])
        assert '<span class="badge state-tag">Ready to approve</span>' in body

    def test_the_card_marks_a_request_with_a_note(self, auth_client, configured,
                                                  monkeypatch, session):
        note = self._note(session)
        calls = []

        def notes_by_number(s, numbers):
            numbers = list(numbers)
            calls.append(numbers)
            return {'EXAM0001': [note]} if 'EXAM0001' in numbers else {}

        monkeypatch.setattr(self.CARD, notes_by_number)
        _publish(_payload('EXAM0001'), _payload('EXAM0002'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert len(calls) == 1, 'one lookup per render, not one per row'
        assert set(calls[0]) == {'EXAM0001', 'EXAM0002'}
        assert body.count('fa-wand-magic-sparkles') == 1
        assert 'title="Recommender: Needs a person to review · 2026-11-05"' in body

    def test_the_card_has_no_marker_without_a_note(self, auth_client, configured, monkeypatch):
        monkeypatch.setattr(self.CARD, lambda s, numbers: {})
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'fa-wand-magic-sparkles' not in body


class TestTheSweptStamp:
    """The header names the full sweep, then the light sweep that last merged rows."""

    @staticmethod
    def _publish(**extra):
        swept = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
        xras_cache.store_requests_index({
            'generated_at': swept, 'statuses': ['Approved'], 'extra_statuses': {},
            'rows': [request_index_entry(_payload(), pending_push=True)], **extra})
        return swept

    def test_a_merge_after_the_full_sweep_shows_its_time(self, auth_client, configured):
        swept = self._publish()
        xras_cache.store_requests_index({**xras_cache.load_requests_index(),
                                         'merged_at': swept.replace(hour=10, minute=15)})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert ' · updated 10:15' in body

    def test_no_merge_says_only_swept(self, auth_client, configured):
        self._publish()
        assert ' · updated ' not in auth_client.get(FRAGMENT).get_data(as_text=True)


SWEEP_NOW = '/allocations/xras_sweep_now'


class TestSweepNow:
    """The card's Sweep now: the light pass through the real runner, or a reason why not."""

    @pytest.fixture
    def ledger(self, app):
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from scheduling.ledger import TaskLedger
        from system_status.models import TaskRun
        from webapp.extensions import db
        with app.app_context():
            engine = db.engines['system_status']

        def clear():
            with Session(engine) as s:
                s.execute(delete(TaskRun).where(TaskRun.task_name == 'xras_sweep'))
                s.commit()
        clear()
        yield TaskLedger(lambda: Session(engine))
        clear()

    @pytest.fixture
    def body(self, monkeypatch):
        """Swap the registered task body for one that reports three merged rows."""
        import dataclasses

        import scheduling.tasks  # noqa: F401
        from scheduling.registry import TASKS, TaskResult
        ran = []

        def fake(ctx):
            ran.append(ctx)
            return TaskResult(detail={'index_merge': {'rebuilt': 5, 'written': 3},
                                      'contracts': {'created': ['AGS-1']}})
        monkeypatch.setitem(TASKS, 'xras_sweep', dataclasses.replace(TASKS['xras_sweep'], fn=fake))
        return ran

    def test_it_runs_the_task_as_a_manual_run(self, auth_client, configured, ledger, body):
        _publish(_payload())
        response = auth_client.post(SWEEP_NOW)
        text = response.get_data(as_text=True)
        assert '5 card row(s) re-checked, 3 changed, 1 contract(s) created' in text
        assert 'refreshXrasTab' in response.headers.get('HX-Trigger', '')
        row = ledger.latest('xras_sweep')
        assert row['trigger'] == 'manual' and row['state'] == 'succeeded'
        assert row['runner_id'].startswith('webapp:') and row['occurrence_key'].startswith('M')

    def test_unconfigured_refuses(self, auth_client, ledger, body):
        text = auth_client.post(SWEEP_NOW).get_data(as_text=True)
        assert 'not configured' in text and body == []

    def test_no_published_index_refuses(self, auth_client, configured, ledger, body,
                                        monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: None)
        text = auth_client.post(SWEEP_NOW).get_data(as_text=True)
        assert 'No sweep has published this list yet' in text and body == []

    def test_a_running_sweep_refuses(self, auth_client, configured, ledger, body):
        from system_status.timeutil import utcnow_naive
        _publish(_payload())
        ledger.claim('xras_sweep', 'TEST-RUNNING', now=utcnow_naive(), trigger='schedule')
        text = auth_client.post(SWEEP_NOW).get_data(as_text=True)
        assert 'is still running' in text and body == []

    def test_a_sweep_that_just_finished_refuses(self, auth_client, configured, ledger, body):
        from system_status.timeutil import utcnow_naive
        _publish(_payload())
        now = utcnow_naive()
        run_id = ledger.claim('xras_sweep', 'TEST-DONE', now=now, trigger='schedule')
        ledger.finish(run_id, state='succeeded', now=now, duration_ms=1, detail=None)
        text = auth_client.post(SWEEP_NOW).get_data(as_text=True)
        assert 'wait a couple of minutes' in text and body == []

    def test_the_full_slot_minute_refuses(self, auth_client, configured, ledger, body,
                                          monkeypatch):
        from datetime import timezone
        from zoneinfo import ZoneInfo
        _publish(_payload())
        full = datetime(2033, 11, 16, 4, 0, 30, tzinfo=ZoneInfo('America/Denver'))
        monkeypatch.setattr('system_status.timeutil.utcnow_naive',
                            lambda: full.astimezone(timezone.utc).replace(tzinfo=None))
        monkeypatch.setenv('SAM_TASKS_XRAS_SWEEP_FULL_HOUR', '4')
        text = auth_client.post(SWEEP_NOW).get_data(as_text=True)
        assert 'daily full sweep runs this minute' in text and body == []

    def test_the_card_offers_it(self, auth_client, configured):
        _publish(_payload())
        assert 'xras_sweep_now' in auth_client.get(FRAGMENT).get_data(as_text=True)
