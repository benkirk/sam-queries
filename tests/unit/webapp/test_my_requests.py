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
                               'documents': [], 'opportunities': [], 'people': set()})()

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

        def new_request_opportunities(self, username):
            fakes.reads.append(('opportunities', username))
            return fakes.opportunities

        def get_person(self, username):
            return {'username': username} if username in fakes.people else None

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

    @pytest.fixture
    def lettered_row(self, engine):
        import hashlib
        from datetime import datetime
        from sqlalchemy import delete
        from sqlalchemy.orm import Session
        from sam.integration.xras_submission import XrasSubmission
        letter = {'document_type': 'Advisor_Letter', 'filename': 'letter.pdf',
                  'title': 'Advisor Letter', 'bytes': 13,
                  'sha256': hashlib.sha256(b'%PDF-1.7 body').hexdigest()}
        with Session(engine) as s:
            row = XrasSubmission.create(
                s, action_type='New', created_by='benkirk', xa_user='benkirk',
                payload={'documents': [letter]}, eula_sha='a' * 40,
                eula_accepted_at=datetime.now())
            row.stop({'step': 'attributes', 'reason': 'unverified'})
            s.commit()
            sid = row.xras_submission_id
        yield sid
        with engine.begin() as conn:
            conn.execute(delete(XrasSubmission).where(XrasSubmission.xras_submission_id == sid))

    @pytest.fixture
    def refused_letter_row(self, engine):
        """A New whose letter XRAS refused (400): the step exists, its event is rejected."""
        from datetime import datetime
        from sqlalchemy import delete
        from sqlalchemy.orm import Session
        from sam.integration.xras import XrasRemediationEvent
        from sam.integration.xras_submission import XrasSubmission
        letter = {'document_type': 'Advisor_Letter', 'filename': 'letter.pdf',
                  'title': 'Advisor Letter', 'bytes': 13, 'sha256': None}
        with Session(engine) as s:
            event = XrasRemediationEvent.create(s, operation='upload_document',
                                                created_by='benkirk', xa_user='benkirk',
                                                status='rejected')
            row = XrasSubmission.create(
                s, action_type='New', created_by='benkirk', xa_user='benkirk',
                payload={'documents': [letter]}, eula_sha='a' * 40,
                eula_accepted_at=datetime.now())
            row.advance('created')
            row.record_step('document:letter.pdf', event.xras_remediation_event_id)
            row.stop({'step': 'document:letter.pdf', 'reason': 'rejected'})
            s.commit()
            sid, eid = row.xras_submission_id, event.xras_remediation_event_id
        yield sid
        with engine.begin() as conn:
            conn.execute(delete(XrasSubmission).where(XrasSubmission.xras_submission_id == sid))
            conn.execute(delete(XrasRemediationEvent).where(
                XrasRemediationEvent.xras_remediation_event_id == eid))

    def test_a_refused_letter_is_asked_for_again(self, auth_client, refused_letter_row,
                                                 resumed):
        from io import BytesIO
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert f'hx-get="/user/requests/{refused_letter_row}/resume"' in html
        modal = auth_client.get(f'/user/requests/{refused_letter_row}/resume').get_data(
            as_text=True)
        assert 'name="document_Advisor_Letter"' in modal
        auth_client.post(f'/user/requests/{refused_letter_row}/resume', data={
            'document_Advisor_Letter': (BytesIO(b'%PDF-1.7 fixed'), 'letter.pdf')},
            content_type='multipart/form-data')
        (sid, kw), = resumed
        assert [d.content for d in kw['documents']] == [b'%PDF-1.7 fixed']

    @pytest.fixture
    def resumed(self, monkeypatch):
        from sam.manage import xras_submission as flow
        seen = []
        monkeypatch.setattr(flow, 'resume_submission',
                            lambda f, sid, **kw: seen.append((sid, kw)))
        return seen

    def test_a_row_lacking_its_document_opens_the_resume_modal(self, auth_client,
                                                               lettered_row):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert f'hx-get="/user/requests/{lettered_row}/resume"' in html
        assert 'the request it started' in html
        modal = auth_client.get(f'/user/requests/{lettered_row}/resume').get_data(as_text=True)
        assert 'name="document_Advisor_Letter"' in modal and 'first attached as letter.pdf' in modal

    def test_resume_hands_the_attached_letter_to_the_flow(self, auth_client, lettered_row,
                                                          resumed):
        from io import BytesIO
        resp = auth_client.post(f'/user/requests/{lettered_row}/resume', data={
            'document_Advisor_Letter': (BytesIO(b'%PDF-1.7 body'), 'renamed.pdf')},
            content_type='multipart/form-data')
        assert resp.headers.get('HX-Refresh') == 'true'
        (sid, kw), = resumed
        (doc,) = kw['documents']
        assert (doc.document_type, doc.filename, doc.content) == \
            ('Advisor_Letter', 'letter.pdf', b'%PDF-1.7 body')

    def test_a_different_file_is_taken_and_said_so(self, auth_client, lettered_row, resumed):
        from io import BytesIO
        auth_client.post(f'/user/requests/{lettered_row}/resume', data={
            'document_Advisor_Letter': (BytesIO(b'%PDF-1.7 revised'), 'letter.pdf')},
            content_type='multipart/form-data')
        assert resumed
        with auth_client.session_transaction() as sess:
            flashes = [m for _, m in sess.get('_flashes', [])]
        assert any('different file' in m for m in flashes)

    def test_a_missing_or_non_pdf_stays_in_the_modal(self, auth_client, lettered_row, resumed):
        from io import BytesIO
        html = auth_client.post(f'/user/requests/{lettered_row}/resume', data={},
                                content_type='multipart/form-data').get_data(as_text=True)
        assert 'Attach the Advisor Letter' in html
        html = auth_client.post(f'/user/requests/{lettered_row}/resume', data={
            'document_Advisor_Letter': (BytesIO(b'plain'), 'letter.txt')},
            content_type='multipart/form-data').get_data(as_text=True)
        assert 'not a PDF' in html and not resumed

    def test_resume_runs_the_flow_and_refreshes(self, auth_client, a_row, monkeypatch):
        from sam.manage import xras_submission as flow
        seen = []
        monkeypatch.setattr(flow, 'resume_submission',
                            lambda f, sid, **kw: seen.append((sid, kw)))
        resp = auth_client.post(f'/user/requests/{a_row}/resume')
        assert resp.headers.get('HX-Refresh') == 'true'
        assert seen == [(a_row, {'actor': 'benkirk', 'door': 'pi', 'documents': ()})]

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
    # disabled=False: the default would leave every XRAS bucket off for the worker's later tests.
    xras_cache._CACHE.reset_for_tests(disabled=False)


_GRANT = dict(grant_number='2345678', grant_title='Ensembles', grant_pi_name='B. Kirk',
              grant_begin_date='2026-01-01', grant_end_date='2028-12-31',
              grant_officer_name='P. Officer', grant_officer_email='po@nsf.gov')


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

    def test_a_rule_book_wanting_grants_asks_for_one(self, auth_client, renewable, filed):
        renewable.required = {'requiredFields': {'request': {'grants': {'required': True}}}}
        assert 'name="grant_number"' in auth_client.get(REN).get_data(as_text=True)
        html = auth_client.post(REN, data=_ren()).get_data(as_text=True)
        assert 'needs a supporting grant' in html and not filed

    def test_the_grant_rides_the_renewal_with_nsfs_agency_id(self, auth_client, renewable,
                                                            filed):
        from io import BytesIO
        renewable.required = {'requiredFields': {'request': {'grants': {'required': True}}}}
        auth_client.post(REN, data=_ren('submit', eula_sha=_sha(), agree='1', **_GRANT,
                                        document_Advisor_Letter=(BytesIO(b'%PDF-1.7 x'), 'l.pdf')),
                         content_type='multipart/form-data')
        draft, _ = filed[0]
        assert draft.grants[0]['grantNumber'] == '2345678'
        assert draft.grants[0]['fundingAgencyId'] == 1
        assert draft.grants[0]['programOfficerEmail'] == 'po@nsf.gov'

    def test_only_the_resources_the_line_holds_are_prefilled(self, auth_client, renewable,
                                                             monkeypatch):
        import sam.xras.submission_preflight as pre
        renewable.available[0]['resources'].append(
            {'resource_id': 530275, 'name': 'Casper', 'units': 'Core-hours'})
        limit = type('L', (), {'default_amount': 5000.0, 'max_amount': 10000.0})()
        monkeypatch.setattr(pre, 'sam_resources_by_xras_id', lambda s, ids, k: {
            rid: type('R', (), {'resource_name': name})()
            for rid, name in ((530902, 'Derecho'), (530275, 'Casper'))})
        monkeypatch.setattr('sam.accounting.allocation_type_limits.limits_for_allocation_type',
                            lambda s, t: {'Derecho': limit, 'Casper': limit})
        import re
        html = auth_client.get(REN).get_data(as_text=True)
        assert re.search(r'name="amount_530902"\s+value="5000"', html)
        casper = html.split('name="amount_530275"')[1][:120]
        assert 'value=""' in casper

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


# 2a: the New

NEW = '/user/requests/new'
_FOS = [{'fosTypeId': 500003, 'fosNum': '1', 'fosName': 'Atmospheric Sciences',
         'isSelectable': True},
        {'fosTypeId': 500004, 'fosNum': '2', 'fosName': 'Not offered', 'isSelectable': False}]


def _opportunity(opportunity_id=532221, allocation_type='Exploratory', type_id=500847, **kw):
    return {'opportunity_id': opportunity_id, 'opportunity_name': f'{allocation_type} (University)',
            'allocation_type_id': type_id, 'allocation_type': allocation_type,
            'award_months': 12, 'eua_attribute_id': 539638, 'can_submit': True, 'reason': None,
            'resource_ids': [530902],
            'resources': [{'resource_id': 530902, 'name': 'NSF NCAR Derecho',
                           'units': 'Core-hours'}], **kw}


@pytest.fixture
def newable(armed, monkeypatch):
    from sam.integration.xras_api import cache as xras_cache
    xras_cache._CACHE.reset_for_tests()
    monkeypatch.setattr('sam.integration.xras_api.lookups.get_fos_types', lambda: _FOS)
    armed.opportunities = [_opportunity(),
                           _opportunity(532220, 'Small', 500024),
                           _opportunity(532223, 'Educational', 500026, can_submit=False,
                                        reason='You already have one')]
    armed.documents = [{'documentType': 'Advisor_Letter', 'displayDocumentType': 'Advisor Letter',
                        'allocationActionTypes': [{'allocationTypeId': 500847, 'actionTypes': [
                            {'actionType': 'New', 'isRequired': True}]}]}]
    yield armed
    xras_cache._CACHE.reset_for_tests(disabled=False)


@pytest.fixture
def lettered(monkeypatch):
    """The ladder's mnemonic for the PI, whatever this snapshot's affiliation rows say."""
    import sam.xras.submission_preflight as pre
    from sam.core.organizations import MnemonicCode
    real = pre.new_request_sam_state

    def state(session, opportunity, pi):
        out = real(session, opportunity, pi)
        if out['mnemonic'] is None:
            out['mnemonic'] = session.query(MnemonicCode).filter(MnemonicCode.is_active).first()
        return out
    monkeypatch.setattr(pre, 'new_request_sam_state', state)


def _new(stage='request', **extra):
    return {**dict(stage=stage, opportunity_id='532221', title='Ensemble downscaling',
                   abstract='Regional runs for the next three summers.', fos_type_id='500003'),
            **extra}


def _new_review(**extra):
    return _new(**{'stage': 'review', 'begin_date': '2026-12-01', 'end_date': '2027-11-30',
                   'amount_530902': '1000', **extra})


class TestTheNewForm:

    def test_the_lever_off_refuses_with_a_message(self, auth_client):
        assert 'not enabled here' in auth_client.get(NEW).get_data(as_text=True)

    def test_the_opener_follows_the_lever(self, auth_client, armed):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'id="xrasNewRequestOpen"' in html and '/user/requests/new' in html

    def test_page_one_lists_the_opportunities_and_the_selectable_fields(self, auth_client,
                                                                       newable):
        html = auth_client.get(NEW).get_data(as_text=True)
        assert 'Exploratory (University)' in html and 'Educational (University) (not open to you)' in html
        assert 'Atmospheric Sciences' in html and 'Not offered' not in html
        assert ('opportunities', 'benkirk') in newable.reads

    def test_the_reveal_names_the_letter_and_the_code(self, auth_client, newable, lettered):
        html = auth_client.get(NEW + '/opportunity?opportunity_id=532221').get_data(as_text=True)
        assert 'Advisor Letter at the review' in html
        assert 'project code starts with' in html and 'name="grant_number"' not in html

    def test_a_grant_wanting_type_reveals_the_grant(self, auth_client, newable, lettered):
        newable.required = {'requiredFields': {'request': {'grants': {'required': True}}}}
        html = auth_client.get(NEW + '/opportunity?opportunity_id=532220').get_data(as_text=True)
        assert 'name="grant_number"' in html and 'Look up' in html and 'hx-swap-oob' in html

    def test_a_missing_title_stays_on_page_one(self, auth_client, newable, lettered):
        html = auth_client.post(NEW, data=_new(title='')).get_data(as_text=True)
        assert 'needs a title' in html and 'name="abstract"' in html

    def test_a_closed_opportunity_gives_xrass_reason(self, auth_client, newable, lettered):
        html = auth_client.post(NEW, data=_new(opportunity_id='532223')).get_data(as_text=True)
        assert 'You already have one' in html

    def test_an_unknown_admin_is_refused(self, auth_client, newable, lettered):
        html = auth_client.post(NEW, data=_new(admin_username='nosuchuser9')).get_data(
            as_text=True)
        assert 'SAM has no active user nosuchuser9' in html

    def test_the_grant_is_required_where_the_rule_book_wants_one(self, auth_client, newable,
                                                                lettered):
        newable.required = {'requiredFields': {'request': {'grants': {'required': True}}}}
        html = auth_client.post(NEW, data=_new(opportunity_id='532220')).get_data(as_text=True)
        assert 'needs a supporting grant' in html
        html = auth_client.post(NEW, data=_new(opportunity_id='532220', grant_number='2345678')
                                ).get_data(as_text=True)
        assert 'Required for the grant.' in html

    def test_where_the_ladder_misses_the_pi_picks_the_letters(self, auth_client, newable,
                                                              session):
        from sam.core.organizations import MnemonicCode
        html = auth_client.get(NEW + '/opportunity?opportunity_id=532221').get_data(as_text=True)
        if 'project code starts with' in html:
            pytest.skip("the ladder resolves benkirk's code in this snapshot")
        assert 'name="mnemonic_code_id"' in html
        html = auth_client.post(NEW, data=_new()).get_data(as_text=True)
        assert 'Choose the letters' in html
        code = session.query(MnemonicCode).filter(MnemonicCode.is_active).first()
        html = auth_client.post(NEW, data=_new(mnemonic_code_id=str(code.mnemonic_code_id))
                                ).get_data(as_text=True)
        assert 'name="amount_530902"' in html

    def test_page_two_carries_page_one_and_offers_the_resources(self, auth_client, newable,
                                                               lettered):
        html = auth_client.post(NEW, data=_new()).get_data(as_text=True)
        assert 'name="amount_530902"' in html and 'name="begin_date"' in html
        assert 'type="hidden" name="title" value="Ensemble downscaling"' in html
        assert 'value="describe"' in html          # Back

    def test_a_yes_no_resource_is_a_checkbox_that_asks_for_one(self, auth_client, newable,
                                                              lettered, filed):
        newable.opportunities[0]['resources'].append(
            {'resource_id': 530999, 'name': 'NCAR Data Access', 'units': 'Yes / No'})
        html = auth_client.post(NEW, data=_new()).get_data(as_text=True)
        box = html.split('name="amount_530999"')[0].rsplit('<input', 1)[1]
        assert 'type="checkbox"' in box and 'value="1"' in box
        assert 'yes or no' in html
        html = auth_client.post(NEW, data=_new_review(amount_530999='1')).get_data(as_text=True)
        assert 'NCAR Data Access' in html and '>Yes<' in html
        assert 'name="amount_530999" value="1"' in html
        unticked = auth_client.post(NEW, data=_new_review()).get_data(as_text=True)
        assert 'NCAR Data Access</dt>' not in unticked

    def test_the_review_asks_for_the_letter_and_the_terms(self, auth_client, newable, lettered,
                                                         filed):
        html = auth_client.post(NEW, data=_new_review()).get_data(as_text=True)
        assert 'Review the New request' in html and 'name="document_Advisor_Letter"' in html
        assert 'name="agree"' in html and 'value="request"' in html and not filed

    def test_submit_files_the_new_through_the_pi_door(self, auth_client, newable, lettered,
                                                     filed):
        from datetime import date
        from io import BytesIO
        html = auth_client.post(NEW, data=_new_review(
            stage='submit', eula_sha=_sha(), agree='1',
            document_Advisor_Letter=(BytesIO(b'%PDF-1.7 letter'), 'letter.pdf')),
            content_type='multipart/form-data').get_data(as_text=True)
        assert 'New request filed' in html
        draft, kw = filed[0]
        assert (draft.action_type, draft.opportunity_id, draft.fos_type_id,
                draft.eua_attribute_id) == ('New', 532221, 500003, 539638)
        assert draft.dates == (date(2026, 12, 1), date(2027, 11, 30))
        assert [(r.resource_id, r.amount) for r in draft.resources] == [(530902, 1000.0)]
        assert draft.sam.mnemonic_code_id is not None and draft.request_id is None
        assert (kw['door'], kw['xa_user']) == ('pi', 'benkirk')

    def test_an_impersonator_cannot_file(self, auth_client, newable):
        with auth_client.session_transaction() as sess:
            sess['impersonator_id'] = 1
        assert 'viewing SAM as another user' in auth_client.get(NEW).get_data(as_text=True)


class TestThePickers:

    def test_the_award_lookup_fills_the_grant_from_nsf(self, auth_client, monkeypatch):
        from datetime import date
        from sam.integration.awards.base import AwardRecord, PersonRef
        record = AwardRecord('nsf', contract_number='AGS-2345678', title='Ensembles',
                             start_date=date(2026, 1, 1), end_date=date(2028, 12, 31),
                             pi=PersonRef('B. Kirk'), monitor=PersonRef('P. Officer', 'po@nsf.gov'))
        monkeypatch.setattr('sam.integration.awards.registry.resolve_award',
                            lambda source, number: record if source == 'NSF' else None)
        html = auth_client.get('/user/requests/htmx/award-lookup?grant_number=2345678'
                               '&grant_required=title,programOfficerEmail').get_data(as_text=True)
        assert 'value="Ensembles"' in html and 'value="po@nsf.gov"' in html
        assert 'value="2345678"' in html and 'AGS-2345678' not in html  # the number typed
        assert 'value="2026-01-01"' in html and 'Filled from NSF' in html

    def test_an_unknown_award_says_so(self, auth_client, monkeypatch):
        monkeypatch.setattr('sam.integration.awards.registry.resolve_award', lambda s, n: None)
        html = auth_client.get('/user/requests/htmx/award-lookup?grant_number=9').get_data(
            as_text=True)
        assert 'NSF has no award 9' in html

    def test_nsf_down_is_not_no_such_award(self, auth_client, monkeypatch):
        from sam.integration.awards.base import AwardSourceUnavailable

        def down(source, number):
            raise AwardSourceUnavailable('timeout')
        monkeypatch.setattr('sam.integration.awards.registry.resolve_award', down)
        html = auth_client.get('/user/requests/htmx/award-lookup?grant_number=9').get_data(
            as_text=True)
        assert 'NSF is not answering' in html

    def test_the_mnemonic_suggest_needs_two_letters(self, auth_client, session):
        from sam.core.organizations import MnemonicCode
        code = session.query(MnemonicCode).filter(MnemonicCode.is_active).first()
        assert auth_client.get('/user/requests/htmx/mnemonic-suggest?q=a').get_data() == b''
        html = auth_client.get(f'/user/requests/htmx/mnemonic-suggest?q={code.code}').get_data(
            as_text=True)
        assert f'data-fk-label="{code.code}"' in html
