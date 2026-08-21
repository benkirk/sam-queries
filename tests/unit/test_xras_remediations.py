"""The XRAS Remediations card and its modal routes.

House convention for the HTTP tier: auth, validation, 404s and render smoke.
The write happy-paths are covered at the service layer
(``test_xras_remediation_service.py``), where they belong — a route test that
exercised a real write would either mock the whole world or touch production
XRAS.

Two properties here are specific to this card and worth stating:

* it is a **card, not a fourth tab** — three panes stay three;
* every modal GET degrades with a **200**, because htmx will not swap a 4xx
  into an already-open modal, so an error status renders as an empty modal
  indistinguishable from a broken button.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.base import XrasSourceUnavailable
from sam.queries.xras_requests import request_index_entry
from webapp.utils.rbac import Permission

pytestmark = pytest.mark.unit

FRAGMENT = '/allocations/xras_remediations'


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS — the split has to be simulated.

    Same approach as the sibling card's fixture: patch `get_user_permissions`,
    which every gate in the request path reads through.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def limited(user, *args, **kwargs):
        return {p for p in real(user, *args, **kwargs)
                if p != Permission.MANAGE_XRAS}

    for module in (rbac,
                   __import__('webapp.dashboards.allocations.blueprint',
                              fromlist=['x']),
                   __import__('webapp.dashboards.allocations'
                              '.xras_remediation_routes', fromlist=['x'])):
        if hasattr(module, 'get_user_permissions'):
            monkeypatch.setattr(module, 'get_user_permissions', limited)
    monkeypatch.setattr(rbac, 'get_user_permissions', limited)
    return auth_client


@pytest.fixture(autouse=True)
def _cache(monkeypatch):
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    xras_cache._CACHE.reset_for_tests(disabled=False)
    yield
    xras_cache._CACHE.reset_for_tests(disabled=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
    monkeypatch.setenv('XRAS_API_KEY', 'k')


@pytest.fixture
def armed(configured, monkeypatch):
    monkeypatch.setenv('XRAS_WRITE_ENABLED', '1')


def _payload(number='EXAM0001', *, status='Approved', action_status='Approved',
             placeholder=True, reconciled=True, submit_date=None):
    roles = [{'person': {'username': 'pi-user', 'firstName': 'P',
                         'lastName': 'Eye', 'isReconciled': True},
              'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}]
    if placeholder:
        roles.append({'person': {'username': 'ghost-user-abcde',
                                 'firstName': 'G', 'lastName': 'Host',
                                 'isReconciled': reconciled},
                      'roles': [{'roleId': 2, 'role': 'User',
                                 'roleTypeId': 19}]})
    return {'requestId': 900001, 'requestNumber': number,
            'requestStatus': status, 'requestType': 'New',
            'opportunityId': 5, 'opportunity_name': 'Small Allocation',
            # Inside the shared window's default lookback unless a test says
            # otherwise — the window's behaviour is its own set of tests below.
            'submitDate': submit_date or datetime.now().strftime('%Y-%m-%dT00:00:00Z'),
            'roles': roles,
            'actions': [{'actionId': 7, 'actionType': 'Supplement',
                         'actionStatus': action_status}]}


def _publish(*payloads, pending=True):
    xras_cache.store_requests_index({
        'generated_at': datetime.now(),
        'statuses': ['Approved', 'Submitted', 'Under Review'],
        'extra_statuses': {},
        'rows': [request_index_entry(p, pending_push=pending) for p in payloads]})


def _reader(monkeypatch, payload=_payload(), person=None, candidates=()):
    """Swap in a scripted read client for every live lookup."""
    client = MagicMock()
    client.get_request_by_number.return_value = payload
    client.get_person.return_value = person
    client.search_people.return_value = list(candidates)
    monkeypatch.setattr(
        'sam.integration.xras_api.client.XrasApiClient.from_environment',
        classmethod(lambda cls, *a, **k: client))
    return client


# ── access control ──────────────────────────────────────────────────────

class TestAccessControl:

    def test_anonymous_is_redirected(self, client):
        assert client.get(FRAGMENT).status_code in (302, 401)

    def test_view_only_is_forbidden(self, view_only_client):
        """VIEW_XRAS is not enough — every control here writes to XRAS."""
        assert view_only_client.get(FRAGMENT).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge_form/ghost-user-abcde',
        '/allocations/xras_withdraw_form/EXAM0001/7',
        '/allocations/xras_resubmit_form/EXAM0001/7',
        '/allocations/xras_roles_form/EXAM0001',
    ])
    def test_every_modal_is_gated(self, view_only_client, path):
        assert view_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge/ghost-user-abcde',
        '/allocations/xras_withdraw/EXAM0001/7',
        '/allocations/xras_resubmit/EXAM0001/7',
        '/allocations/xras_role_add/EXAM0001',
        '/allocations/xras_role_remove/EXAM0001/2',
    ])
    def test_every_write_is_gated(self, view_only_client, path):
        assert view_only_client.post(path).status_code == 403

    def test_the_page_shell_hides_the_card_from_a_view_only_operator(
            self, view_only_client):
        """Neither the container nor its facet form may render."""
        body = view_only_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' not in body
        assert 'xras-remediation-filters' not in body

    def test_the_page_shell_carries_both_for_an_admin(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' in body
        assert 'xras-remediation-filters' in body


class TestItIsACardNotATab:

    def test_the_worklist_still_has_exactly_three_tabs(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        pane = body.split('id="xrasWorklistTabs"')[1].split('</ul>')[0]
        assert pane.count('data-bs-toggle="tab"') == 3

    def test_the_card_sits_outside_the_tab_content(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert body.index('</div>\n\n{% if' if False else 'alloc-xras-remediations') \
            > body.index('alloc-xras-pending-requests')


# ── facet / control parity ──────────────────────────────────────────────

class TestFacetParity:
    """A chip whose field has no control in the form filters nothing."""

    def test_every_facet_field_has_a_hidden_control(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        form = body.split('id="xras-remediation-filters"')[1].split('</form>')[0]
        for field in ('status', 'opportunity', 'push', 'request_number'):
            assert f'name="{field}"' in form, \
                f'{field} chips would be silently inert'


# ── the four empty states ───────────────────────────────────────────────

class TestTheFourEmptyStates:
    """Collapsing any two of these would mislead an operator."""

    def test_unconfigured(self, auth_client, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'not configured' in body

    def test_no_sweep_at_all(self, auth_client, configured):
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'No sweep has published yet' in body

    def test_a_sweep_that_predates_this_card(self, auth_client, configured):
        """The deploy window — a lie if reported as 'nothing to remediate'."""
        xras_cache.store_pending_worklist({'rows': [], 'counts': {}})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'predates this card' in body

    def test_published_and_empty(self, auth_client, configured):
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'Nothing to remediate' in body

    def test_the_four_bodies_are_distinct(self, auth_client, configured,
                                          monkeypatch):
        seen = set()
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        seen.add(auth_client.get(FRAGMENT).get_data(as_text=True))
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        seen.add(auth_client.get(FRAGMENT).get_data(as_text=True))
        xras_cache.store_pending_worklist({'rows': []})
        seen.add(auth_client.get(FRAGMENT).get_data(as_text=True))
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        seen.add(auth_client.get(FRAGMENT).get_data(as_text=True))
        assert len(seen) == 4


# ── rendering ───────────────────────────────────────────────────────────

class TestRendering:

    def test_a_published_row_renders_with_its_offers(self, auth_client, armed):
        _publish(_payload())
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'EXAM0001' in body
        assert 'Withdraw…' in body
        assert 'Resolve identity (merge in XRAS)…' in body

    def test_the_lever_off_disables_rather_than_hides(self, auth_client,
                                                      configured, monkeypatch):
        """A control that vanishes teaches nobody that a switch exists."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _publish(_payload())
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'Withdraw…' in body
        assert 'XRAS_WRITE_ENABLED' in body
        assert 'disabled' in body

    def test_a_drafted_action_offers_resubmit_instead(self, auth_client, armed):
        _publish(_payload(action_status='Incomplete'))
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'Re-submit…' in body
        assert 'Withdraw…' not in body

    def test_an_unreconciled_placeholder_offers_no_merge(self, auth_client,
                                                          armed):
        """That row means 'create the account' — the healthy path."""
        _publish(_payload(reconciled=False))
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'Resolve identity' not in body
        assert 'needs an account' in body

    def test_the_window_says_what_it_hid(self, auth_client, armed):
        """⚠️ On this card the hidden rows skew URGENT — never hide silently."""
        _publish(_payload(), _payload('EXAM0002', submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'outside the date filter' in body

    def test_a_card_emptied_by_the_window_says_so_rather_than_nothing_to_do(
            self, auth_client, armed):
        """The default lookback hides stale rows, which are the point here."""
        _publish(_payload(submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'Nothing to remediate' not in body

    def test_a_row_with_no_submit_date_survives_the_window(self, auth_client,
                                                            armed):
        """Missing information is not evidence of age."""
        payload = _payload()
        payload['submitDate'] = None
        _publish(payload)
        body = auth_client.get(
            FRAGMENT + '?start_date=2026-08-01').get_data(as_text=True)
        assert 'EXAM0001' in body

    def test_a_patched_row_is_marked_as_newer_than_the_sweep(self, auth_client,
                                                             armed):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'rows': [
                request_index_entry(_payload(), refreshed_at=datetime.now())]})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'newer than the sweep' in body

    def test_status_chips_filter(self, auth_client, armed):
        _publish(_payload('EXAM0001'), _payload('EXAM0002', status='Submitted'))
        body = auth_client.get(
            FRAGMENT + '?status=Submitted').get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body


# ── modal GETs ──────────────────────────────────────────────────────────

class TestModalGets:

    def test_the_merge_modal_renders_its_candidates(self, auth_client, armed,
                                                    monkeypatch):
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid',
                        'organization': 'Example U', 'isReconciled': True},
                candidates=[{'username': 'ghost', 'firstName': 'G',
                             'lastName': 'Host', 'email': 'g@example.invalid',
                             'organization': 'Example U'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'ghost' in body
        assert 'email matches exactly' in body

    def test_two_candidates_leave_nothing_preselected(self, auth_client, armed,
                                                      monkeypatch):
        """⚠️ Measured: two live identities for one human, differing only by
        email and organization. A default would pick one, and merge deletes
        the other."""
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'lastName': 'Host',
                        'email': 'g@example.invalid',
                        'organization': 'Example U'},
                candidates=[
                    {'username': 'ghost', 'lastName': 'Host',
                     'email': 'g@example.invalid', 'organization': 'Example U'},
                    {'username': 'ghosty', 'lastName': 'Host',
                     'email': 'other@example.invalid', 'organization': 'NCAR'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'checked' not in body
        assert '2 candidates' in body

    def test_an_already_merged_placeholder_says_so(self, auth_client, armed,
                                                   monkeypatch):
        _reader(monkeypatch, person=None)
        response = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde')
        assert response.status_code == 200
        assert 'already been merged away' in response.get_data(as_text=True)

    def test_the_withdraw_modal_names_both_identities(self, auth_client, armed,
                                                      monkeypatch):
        _reader(monkeypatch)
        _publish(_payload())
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'pi-user' in body, 'the impersonated user'
        assert 'benkirk' in body, 'the recorded operator'
        assert 'de-approves' in body

    def test_the_withdraw_modal_says_a_multi_action_request_stays_open(
            self, auth_client, armed, monkeypatch):
        payload = _payload()
        payload['actions'].append({'actionId': 8, 'actionType': 'New',
                                   'actionStatus': 'Approved'})
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'stay' in body and '2 actions' in body

    def test_the_roles_modal_renders_the_live_roster(self, auth_client, armed,
                                                     monkeypatch):
        _reader(monkeypatch)
        body = auth_client.get(
            '/allocations/xras_roles_form/EXAM0001').get_data(as_text=True)
        assert 'ghost-user-abcde' in body
        assert 'Project Lead' in body, 'XRAS display vocabulary, not "PI"'

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge_form/ghost-user-abcde',
        '/allocations/xras_withdraw_form/EXAM0001/7',
        '/allocations/xras_resubmit_form/EXAM0001/7',
        '/allocations/xras_roles_form/EXAM0001',
    ])
    def test_an_outage_degrades_with_a_200(self, auth_client, armed,
                                           monkeypatch, path):
        """⚠️ htmx will not swap a 4xx into an open modal — an error status
        renders as an empty modal indistinguishable from a broken button."""
        client = MagicMock()
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
        client.get_person.side_effect = XrasSourceUnavailable('down')
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        response = auth_client.get(path)
        assert response.status_code == 200
        assert 'not answering' in response.get_data(as_text=True)


# ── POST validation ─────────────────────────────────────────────────────

class TestPostValidation:

    def test_a_merge_with_no_target_is_rejected(self, auth_client, armed,
                                                monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde',
                                     'lastName': 'Host'})
        body = auth_client.post(
            '/allocations/xras_merge/ghost-user-abcde',
            data={}).get_data(as_text=True)
        assert 'Select the real XRAS identity' in body

    def test_a_merge_into_an_unresolvable_target_is_refused(
            self, auth_client, armed, monkeypatch):
        """⚠️ The API CREATES an unknown target rather than failing."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'typo'}
                                ).get_data(as_text=True)
        assert 'would CREATE it' in body

    def test_a_merge_into_itself_is_refused(self, auth_client, armed,
                                            monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'ghost-user-abcde'}
                                ).get_data(as_text=True)
        assert 'different account' in body

    def test_a_withdrawal_without_a_reason_is_rejected(self, auth_client, armed,
                                                       monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                data={'comment': '   '}).get_data(as_text=True)
        assert 'required' in body.lower()

    def test_adding_an_unknown_username_is_refused(self, auth_client, armed,
                                                   monkeypatch):
        """It would create a new XRAS identity, reconciled by default."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'nobody',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'can create a new XRAS identity' in body

    def test_adding_a_role_someone_already_holds_is_refused(
            self, auth_client, armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_person.return_value = {'username': 'ghost-user-abcde'}
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'ghost-user-abcde',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'already holds' in body

    def test_an_unknown_role_type_is_refused(self, auth_client, armed,
                                             monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'someone',
                                      'role_type': 'Reviewer'}
                                ).get_data(as_text=True)
        assert 'Must be one of' in body

    def test_removing_a_role_that_is_gone_says_so(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_remove/EXAM0001/999'
                                ).get_data(as_text=True)
        assert 'no longer on the roster' in body

    def test_the_lever_off_refuses_the_write_at_the_service(
            self, auth_client, configured, monkeypatch):
        """Belt and braces: the button is disabled AND the POST refuses."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_resubmit/EXAM0001/7'
                                ).get_data(as_text=True)
        assert 'switched off' in body
