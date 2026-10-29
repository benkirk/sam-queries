"""User dashboard -> My Requests: the page, the tab, the card link and the per-project picker.

HTTP-layer only; the XRAS clients are fakes (no outbound call), and the readouts are
covered against real rows in ``tests/unit/queries/test_xras_my_requests.py``.
"""

from dataclasses import replace

import pytest

from sam.integration.xras_api.config import XrasApiConfig
from webapp.dashboards.user import requests_routes

PAGE = '/user/requests'


def _picker(projcode):
    return f'/user/requests/{projcode}/actions'


@pytest.fixture
def armed(monkeypatch):
    """The submit lever on, every type armed; returns the fakes the picker builds."""
    config = replace(XrasApiConfig(enabled=True, write_enabled=True, api_key='k'),
                     submit_enabled=True,
                     submit_action_types=('Extension', 'Supplement', 'Renewal', 'New'))
    monkeypatch.setattr(requests_routes, '_config', lambda: config)
    fakes = type('Fakes', (), {'family': [], 'available': [], 'reads': []})()

    class Reader:
        def get_request_family_by_number(self, number):
            fakes.reads.append(('family', number))
            return fakes.family

    class Admin:
        def available_action_requests(self, username):
            fakes.reads.append(('offers', username))
            return fakes.available

    monkeypatch.setattr('sam.integration.xras_api.XrasApiClient.from_environment',
                        classmethod(lambda cls, *a, **k: Reader()))
    monkeypatch.setattr('sam.integration.xras_api.admin_client.XrasAdminClient.from_environment',
                        classmethod(lambda cls, *a, **k: Admin()))
    return fakes


def _line(number, username='benkirk'):
    return {'requestNumber': number, 'requestId': 1449367, 'requestStatus': 'Approved',
            'actions': [{'actionId': 1, 'actionType': 'New', 'actionStatus': 'Approved'}],
            'roles': [{'person': {'username': username},
                       'roles': [{'roleId': 1, 'roleTypeId': 13, 'role': 'PI'}]}]}


class TestThePage:

    def test_renders_both_cards_and_the_led_projects(self, auth_client):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'Your requests' in html and 'Your projects' in html
        assert 'UHSS0001' in html and 'req-UHSS0001-row' in html

    def test_the_lever_off_says_so(self, auth_client):
        assert 'not enabled here yet' in auth_client.get(PAGE).get_data(as_text=True)

    def test_a_deep_link_opens_that_projects_picker(self, auth_client):
        html = auth_client.get(PAGE + '?projcode=uhss0001').get_data(as_text=True)
        assert 'id="req-UHSS0001-row"' in html
        row = html.split('id="req-UHSS0001-row"')[0].rsplit('<tr', 1)[1]
        assert 'collapse show' in row

    def test_the_tab_sits_on_every_user_page(self, auth_client):
        html = auth_client.get('/user/info').get_data(as_text=True)
        assert 'My Requests' in html and 'href="/user/requests"' in html

    def test_anonymous_is_refused(self, client):
        assert client.get(PAGE).status_code in (302, 401)


class TestThePicker:

    def test_the_lever_off_disables_everything_without_reading_xras(self, auth_client,
                                                                   monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.XrasApiClient.from_environment',
                            classmethod(lambda *a, **k: pytest.fail('read XRAS')))
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'not enabled here' in html and 'available' not in html

    def test_a_staff_project_names_its_route(self, auth_client, armed):
        html = auth_client.get(_picker('SCSG0001')).get_data(as_text=True)
        assert 'handled by CISL staff' in html and armed.reads == []

    def test_no_family_is_a_reason(self, auth_client, armed):
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'no live request' in html

    def test_off_the_roster_is_a_reason(self, auth_client, armed):
        armed.family = [_line('UHSS0001', username='someone-else')]
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'You hold no role' in html

    def test_on_the_roster_offers_extension_and_supplement(self, auth_client, armed):
        armed.family = [_line('UHSS0001')]
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'Extension available' in html and 'Supplement available' in html
        assert 'XRAS offers no renewal' in html
        assert ('offers', 'benkirk') in armed.reads

    def test_renewal_follows_the_users_offers(self, auth_client, armed):
        armed.family = [_line('UHSS0001')]
        armed.available = [{'opportunity_id': 532221, 'request_number': 'UHSS0001',
                            'action_type': 'Renewal', 'resource_ids': [530902]}]
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'Renewal available' in html

    def test_an_outage_degrades_in_place(self, auth_client, armed, monkeypatch):
        from sam.integration.xras_api import XrasSourceUnavailable

        def boom(self, number):
            raise XrasSourceUnavailable('timeout')
        monkeypatch.setattr('sam.integration.xras_api.XrasApiClient.from_environment',
                            classmethod(lambda *a, **k: type('R', (), {
                                'get_request_family_by_number': boom})()))
        resp = auth_client.get(_picker('UHSS0001'))
        assert resp.status_code == 200 and 'not answering' in resp.get_data(as_text=True)

    def test_a_non_lead_without_the_permission_is_refused(self, non_admin_client):
        assert non_admin_client.get(_picker('UHSS0001')).status_code == 403


class TestTheCardLink:

    def test_a_lead_sees_request_time(self, auth_client):
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'data-request-time' in html
        assert '/user/requests?projcode=' in html
