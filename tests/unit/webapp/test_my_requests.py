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
    fakes = type('Fakes', (), {'family': [], 'available': [], 'reads': [], 'catalog': [],
                               'required': {'requiredFields': {'request': {}}},
                               'documents': []})()

    class Reader:
        def get_request_family_by_number(self, number):
            fakes.reads.append(('family', number))
            return fakes.family

        def get_resources(self):
            return fakes.catalog

        def required_fields(self, allocation_type_id, action_type):
            return fakes.required

        def get_document_types(self):
            return fakes.documents

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
            'requestType': 'New', 'opportunityId': 532220,
            'actions': [{'actionId': 1, 'actionType': 'New', 'actionStatus': 'Approved',
                         'resources': [{'resourceId': 530902, 'resourceName': 'Derecho',
                                        'resourceUnits': 'Core-hours', 'amount': 1,
                                        'type': 'Approved'}]}],
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
        assert 'Request extension' in html and 'Request supplement' in html
        assert 'data-bs-target="#xrasRequestModal"' in html
        assert 'XRAS offers no renewal' in html
        assert ('offers', 'benkirk') in armed.reads

    def test_renewal_follows_the_users_offers(self, auth_client, armed):
        armed.family = [_line('UHSS0001')]
        armed.available = [{'opportunity_id': 532221, 'request_number': 'UHSS0001',
                            'action_type': 'Renewal', 'resource_ids': [530902]}]
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'Request renewal' in html

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


# 1b: the forms

EXT = '/user/requests/UHSS0001/extension'
SUP = '/user/requests/UHSS0001/supplement'


@pytest.fixture
def on_roster(armed):
    armed.family = [_line('UHSS0001')]
    return armed


@pytest.fixture
def floor(session):
    from sam.projects.projects import Project
    from sam.xras.submission_preflight import extension_floor
    found = extension_floor(Project.get_by_projcode(session, 'UHSS0001'))
    if found is None:
        pytest.skip('UHSS0001 has no extendable allocation in this snapshot')
    return found


@pytest.fixture
def filed(monkeypatch):
    """``request_action`` recorded instead of run: no row, no XRAS write."""
    from sam.manage import xras_submission as flow
    calls = []

    def fake(factory, draft, **kw):
        calls.append((draft, kw))
        return flow.SubmissionOutcome(submission_id=1, state='submitted', step='submit',
                                      step_outcome=None, error=None)
    monkeypatch.setattr(flow, 'request_action', fake)
    return calls


def _sha():
    from webapp.register.eula import eula_sha
    return eula_sha()


def _ext(end, stage='review', **extra):
    return dict(stage=stage, end_date=end.isoformat(),
                user_comments='The runs need another quarter.', **extra)


class TestTheExtensionForm:

    def test_the_lever_off_refuses_with_a_message(self, auth_client):
        html = auth_client.get(EXT).get_data(as_text=True)
        assert 'not enabled here' in html and 'name="end_date"' not in html

    def test_the_form_shows_the_floor(self, auth_client, on_roster, floor):
        html = auth_client.get(EXT).get_data(as_text=True)
        assert 'name="end_date"' in html and floor.isoformat() in html
        assert 'xrasRequestModalLabel' in html

    def test_a_date_on_or_before_the_floor_is_refused(self, auth_client, on_roster, floor,
                                                      filed):
        html = auth_client.post(EXT, data=_ext(floor)).get_data(as_text=True)
        assert 'Choose a date after' in html and not filed

    def test_review_shows_the_preflight_and_the_terms(self, auth_client, on_roster, floor,
                                                     filed):
        from datetime import timedelta
        html = auth_client.post(EXT, data=_ext(floor + timedelta(days=365))).get_data(
            as_text=True)
        assert 'Review the Extension' in html and 'name="agree"' in html
        assert 'SAM checked this' in html and _sha() in html
        assert not filed

    def test_submit_without_the_tick_returns_to_the_review(self, auth_client, on_roster,
                                                           floor, filed):
        from datetime import timedelta
        html = auth_client.post(EXT, data=_ext(floor + timedelta(days=365), 'submit',
                                               eula_sha=_sha())).get_data(as_text=True)
        assert 'Accept the terms of use' in html and not filed

    def test_a_stale_agreement_returns_to_the_review(self, auth_client, on_roster, floor,
                                                    filed):
        from datetime import timedelta
        html = auth_client.post(EXT, data=_ext(floor + timedelta(days=365), 'submit',
                                               eula_sha='b' * 40, agree='1')).get_data(
            as_text=True)
        assert 'terms changed' in html and not filed

    def test_submit_files_through_the_pi_door(self, auth_client, on_roster, floor, filed):
        from datetime import timedelta
        end = floor + timedelta(days=365)
        html = auth_client.post(EXT, data=_ext(end, 'submit', eula_sha=_sha(),
                                               agree='1')).get_data(as_text=True)
        assert 'Extension filed' in html
        draft, kw = filed[0]
        assert (draft.action_type, draft.request_id, draft.dates) == \
            ('Extension', 1449367, (None, end))
        assert (kw['door'], kw['xa_user'], kw['actor'], kw['eula'][0]) == \
            ('pi', 'benkirk', 'benkirk', _sha())

    def test_back_returns_to_the_form_with_the_values(self, auth_client, on_roster, floor):
        from datetime import timedelta
        end = floor + timedelta(days=365)
        html = auth_client.post(EXT, data=_ext(end, 'edit')).get_data(as_text=True)
        assert f'value="{end.isoformat()}"' in html

    def test_an_impersonator_cannot_file(self, auth_client, on_roster):
        with auth_client.session_transaction() as sess:
            sess['impersonator_id'] = 1
        html = auth_client.get(EXT).get_data(as_text=True)
        assert 'viewing SAM as another user' in html

    def test_a_non_lead_is_refused(self, non_admin_client):
        assert non_admin_client.get(EXT).status_code == 403


class TestTheSupplementForm:

    def test_the_form_lists_the_lines_resources(self, auth_client, on_roster):
        html = auth_client.get(SUP).get_data(as_text=True)
        assert 'name="amount_530902"' in html and 'Derecho' in html

    def test_nothing_asked_is_refused(self, auth_client, on_roster, filed):
        html = auth_client.post(SUP, data={'amount_530902': '', 'user_comments':
                                           'GPU work for the paper.'}).get_data(as_text=True)
        assert 'at least one resource' in html

    def test_a_limit_refusal_lands_on_its_field(self, auth_client, on_roster, monkeypatch):
        import sam.xras.submission_preflight as pre
        monkeypatch.setattr(pre, 'limit_refusals', lambda s, t, a: {
            n: 'Over the 5,000 limit.' for n in a})
        on_roster.catalog = [{'resourceId': 530902, 'resourceRepositoryKey': 1}]
        monkeypatch.setattr(pre, 'sam_resources_by_xras_id', lambda s, ids, c: {
            530902: type('R', (), {'resource_name': 'Derecho'})()})
        html = auth_client.post(SUP, data={'amount_530902': '9000', 'user_comments':
                                           'GPU work for the paper.'}).get_data(as_text=True)
        assert 'Over the 5,000 limit.' in html and 'is-invalid' in html

    def test_review_carries_the_amounts(self, auth_client, on_roster, filed):
        html = auth_client.post(SUP, data={'amount_530902': '1,500', 'user_comments':
                                           'GPU work for the paper.'}).get_data(as_text=True)
        assert 'Review the Supplement' in html
        assert 'name="amount_530902" value="1500"' in html


class TestResumeAndDiscard:

    @pytest.fixture
    def a_row(self, engine):
        from datetime import datetime
        from sqlalchemy import delete
        from sqlalchemy.orm import Session
        from sam.integration.xras_submission import XrasSubmission
        with Session(engine) as s:
            row = XrasSubmission.create(s, action_type='Extension', created_by='benkirk',
                                        xa_user='benkirk', payload={}, eula_sha='a' * 40,
                                        eula_accepted_at=datetime.now())
            row.stop({'step': 'submit', 'reason': 'timeout'})
            s.commit()
            sid = row.xras_submission_id
        yield sid
        with engine.begin() as conn:
            conn.execute(delete(XrasSubmission).where(
                XrasSubmission.xras_submission_id == sid))

    def test_the_stopped_row_offers_both(self, auth_client, a_row):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'Stopped at submit' in html
        assert f'/user/requests/{a_row}/resume' in html and f'/user/requests/{a_row}/abort' in html

    def test_resume_runs_the_flow_and_refreshes(self, auth_client, a_row, monkeypatch):
        from sam.manage import xras_submission as flow
        seen = []
        monkeypatch.setattr(flow, 'resume_submission',
                            lambda f, sid, **kw: seen.append((sid, kw)))
        resp = auth_client.post(f'/user/requests/{a_row}/resume')
        assert resp.headers.get('HX-Refresh') == 'true'
        assert seen == [(a_row, {'actor': 'benkirk', 'door': 'pi'})]

    def test_abort_runs_the_flow(self, auth_client, a_row, monkeypatch):
        from sam.manage import xras_submission as flow
        seen = []
        monkeypatch.setattr(flow, 'abort_submission', lambda f, sid, **kw: seen.append(sid))
        assert auth_client.post(f'/user/requests/{a_row}/abort').status_code == 204
        assert seen == [a_row]

    def test_someone_elses_row_is_refused(self, non_admin_client, a_row):
        assert non_admin_client.post(f'/user/requests/{a_row}/abort').status_code == 403


class TestTheAgreementPage:

    def test_renders_the_terms(self, auth_client):
        html = auth_client.get('/user/requests/agreement').get_data(as_text=True)
        assert 'terms of use' in html.lower()


# 1c: the renewal

REN = '/user/requests/UHSS0001/renewal'
_LETTER = [{'documentType': 'Advisor_Letter', 'displayDocumentType': 'Advisor Letter',
            'allocationActionTypes': [{'allocationTypeId': 500847, 'actionTypes': [
                {'actionType': 'Renewal', 'isRequired': True, 'hardPageLimit': 2}]}]}]


@pytest.fixture
def renewable(on_roster):
    from sam.integration.xras_api import cache as xras_cache
    xras_cache._CACHE.reset_for_tests()
    on_roster.available = [{
        'opportunity_id': 532221, 'opportunity_name': 'Exploratory Allocation (University)',
        'allocation_type_id': 500847, 'allocation_type': 'Exploratory', 'award_months': 12,
        'eua_attribute_id': 539638, 'request_id': 1449367, 'request_number': 'UHSS0001',
        'action_type': 'Renewal', 'resource_ids': [530902],
        'resources': [{'resource_id': 530902, 'name': 'NSF NCAR Derecho', 'units': 'Core-hours'}]}]
    on_roster.documents = _LETTER
    on_roster.family[0]['abstract'] = 'Year one of the ensemble.'
    on_roster.family[0]['fos'] = [{'fosTypeId': 500003, 'fosNum': '1', 'isPrimary': True}]
    yield on_roster
    xras_cache._CACHE.reset_for_tests()


def _ren(stage='review', **extra):
    return dict(stage=stage, abstract='Year two of the ensemble runs.',
                begin_date='2027-11-01', end_date='2028-10-31', amount_530902='1000', **extra)


class TestTheRenewalForm:

    def test_the_picker_offers_the_renewal_as_a_button(self, auth_client, renewable):
        html = auth_client.get(_picker('UHSS0001')).get_data(as_text=True)
        assert 'Request renewal' in html and '/user/requests/UHSS0001/renewal' in html

    def test_no_offer_no_form(self, auth_client, on_roster):
        on_roster.available = []
        assert 'offers no renewal' in auth_client.get(REN).get_data(as_text=True)

    def test_a_rule_book_wanting_grants_waits_for_the_grant_step(self, auth_client, renewable):
        renewable.required = {'requiredFields': {'request': {'grants': {'required': True}}}}
        assert 'supporting grants' in auth_client.get(REN).get_data(as_text=True)

    def test_the_form_prefills_the_abstract_and_the_window(self, auth_client, renewable, floor):
        from datetime import timedelta
        html = auth_client.get(REN).get_data(as_text=True)
        assert 'Year one of the ensemble.' in html and 'name="amount_530902"' in html
        assert (floor + timedelta(days=1)).isoformat() in html
        assert 'Advisor Letter at the review' in html

    def test_the_review_asks_for_the_letter(self, auth_client, renewable, filed):
        html = auth_client.post(REN, data=_ren()).get_data(as_text=True)
        assert 'Review the Renewal' in html and 'name="document_Advisor_Letter"' in html
        assert 'multipart/form-data' in html and 'at most 2 pages' in html and not filed

    def test_a_submit_without_the_letter_returns_to_the_review(self, auth_client, renewable,
                                                              filed):
        html = auth_client.post(REN, data=_ren('submit', eula_sha=_sha(), agree='1')).get_data(
            as_text=True)
        assert 'Attach the Advisor Letter' in html and not filed

    def test_a_non_pdf_is_refused(self, auth_client, renewable, filed):
        from io import BytesIO
        html = auth_client.post(REN, data=_ren(
            'submit', eula_sha=_sha(), agree='1',
            document_Advisor_Letter=(BytesIO(b'plain text'), 'letter.txt')),
            content_type='multipart/form-data').get_data(as_text=True)
        assert 'not a PDF' in html and not filed

    def test_submit_files_the_renewal_with_its_letter(self, auth_client, renewable, filed):
        from datetime import date
        from io import BytesIO
        html = auth_client.post(REN, data=_ren(
            'submit', eula_sha=_sha(), agree='1',
            document_Advisor_Letter=(BytesIO(b'%PDF-1.7 letter'), 'letter.pdf')),
            content_type='multipart/form-data').get_data(as_text=True)
        assert 'Renewal filed' in html
        draft, kw = filed[0]
        assert (draft.action_type, draft.request_id, draft.opportunity_id,
                draft.eua_attribute_id) == ('Renewal', 1449367, 532221, 539638)
        assert draft.dates == (date(2027, 11, 1), date(2028, 10, 31))
        assert [(d.document_type, d.filename) for d in draft.documents] == \
            [('Advisor_Letter', 'letter.pdf')]
        assert draft.abstract == 'Year two of the ensemble runs.'
