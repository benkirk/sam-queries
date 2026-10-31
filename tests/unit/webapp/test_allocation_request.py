"""``/request``: a visitor's New, steps 1-4 (XRAS_SUBMISSION_SPRINT2.md steps 1a, 1b).

The overview, the person, the confirmation. The POST creates a committed row through
``db.session``, so tests clean up by address; the mailer records instead of sending.
"""

import re
import secrets
from dataclasses import replace

import pytest

from sam.integration.xras_api.config import XrasApiConfig

PERSON = {'first_name': 'Vis', 'last_name': 'Itor', 'organization': 'University of Example',
          'academic_status': 'Faculty', 'residence_country': 'US', 'phone': '+1 303 555 0100'}


@pytest.fixture
def type_id():
    return 10_000_000 + secrets.randbelow(10_000_000) * 10


@pytest.fixture
def guides(app, type_id):
    """Committed guide rows: one public open type, one staff-only, one with no opportunity."""
    from sam.accounting.allocation_type_guide import AllocationTypeGuide
    from webapp.extensions import db
    with app.app_context():
        ids = [AllocationTypeGuide.create(db.session, xras_allocation_type_id=t, created_by='t', **kw)
               .allocation_type_guide_id for t, kw in (
                   (type_id, dict(title='Probeopen', summary='For probing', cadence='Continuous',
                                  allocation_type='Small')),
                   (type_id + 1, dict(title='Probestaff', audience='ncar')),
                   (type_id + 2, dict(title='Probeclosed')))]
        db.session.commit()
    yield type_id
    with app.app_context():
        db.session.query(AllocationTypeGuide).filter(
            AllocationTypeGuide.allocation_type_guide_id.in_(ids)).delete()
        db.session.commit()


@pytest.fixture
def visitor(monkeypatch, guides):
    """The visitor lever on; XRAS answers with the probe opportunities."""
    config = replace(XrasApiConfig(enabled=True, write_enabled=True, api_key='k'),
                     submit_enabled=True, submit_anonymous_enabled=True,
                     submit_action_types=frozenset({'New'}))
    monkeypatch.setattr('webapp.register.allocation_request.interactive_config', lambda: config)
    monkeypatch.setattr('webapp.dashboards.user.requests_routes._config', lambda: config)
    fakes = type('Fakes', (), {'outage': False, 'reads': []})()

    def _opp(offset, opp_id):
        return {'opportunity_id': opp_id, 'opportunity_name': f'Probe {offset}',
                'allocation_type_id': guides + offset, 'allocation_type': 'Probe',
                'description': None, 'award_months': 12, 'can_submit': True, 'reason': None,
                'eua_attribute_id': None, 'resource_ids': [530902],
                'resources': [{'resource_id': 530902, 'name': 'NSF NCAR Derecho',
                               'units': 'Core-hours'}]}

    class Admin:
        def new_request_opportunities(self, username):
            from sam.integration.xras_api import XrasSourceUnavailable
            fakes.reads.append(username)
            if fakes.outage:
                raise XrasSourceUnavailable('down')
            return [_opp(0, 777001), _opp(1, 777002)]

        def get_person(self, username):
            return {'username': username}

    class Reader:
        def get_resources(self):
            return []

        def required_fields(self, allocation_type_id, action_type):
            return {'requiredFields': {'request': {'grants': {'required': True}}}}

        def get_document_types(self):
            return []

    monkeypatch.setattr('sam.integration.xras_api.XrasApiClient.from_environment',
                        classmethod(lambda cls, *a, **k: Reader()))
    monkeypatch.setattr('sam.integration.xras_api.admin_client.XrasAdminClient.from_environment',
                        classmethod(lambda cls, *a, **k: Admin()))
    return fakes


@pytest.fixture
def mail(monkeypatch):
    """The verify mailer and NUSD's ticket mailer, both recording."""
    from sam.notify.base import DeliveryResult

    class _Recorder:
        def __init__(self):
            self.messages = []

        def send(self, message, **_):
            self.messages.append(message)
            return DeliveryResult(ok=True, status='sent', message=message)
    verify, ticket = _Recorder(), _Recorder()
    monkeypatch.setattr('webapp.register.allocation_request.get_notifier', lambda **_: verify)
    monkeypatch.setattr('webapp.register.handoff_mail.get_notifier', lambda **_: ticket)
    return type('Mail', (), {'verify': verify, 'ticket': ticket})()


@pytest.fixture
def cleanup(app):
    """A per-test address (xdist workers share the DB), and its rows deleted after."""
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    address = f'zz.request.{secrets.token_hex(4)}@example.invalid'
    yield address
    with app.app_context():
        db.session.query(AccountRequest).filter(AccountRequest.email == address).delete()
        db.session.commit()


def _submit(client, mail, address):
    resp = client.post('/request/start/777001', data=dict(PERSON, email=address))
    assert resp.status_code == 302 and '/request/pending/' in resp.location
    return resp.location, mail.verify.messages[-1].context


class TestTheLever:

    def test_off_every_route_is_a_404(self, client):
        assert client.get('/request/').status_code == 404
        assert client.get('/request/start/777001').status_code == 404


class TestTheOverview:

    def test_it_lays_out_the_public_types_before_anything_starts(self, client, visitor):
        html = client.get('/request/').get_data(as_text=True)
        assert 'Probeopen' in html and 'For probing' in html and 'Continuous' in html
        assert 'href="/request/start/777001"' in html
        assert 'Probestaff' not in html, 'NCAR-staff types stay off the public page'
        assert visitor.reads == [None], 'read in the report context'

    def test_a_type_with_no_opportunity_shows_without_start(self, client, visitor):
        html = client.get('/request/').get_data(as_text=True)
        closed = html.split('Probeclosed')[1].split('</section>')[0]
        assert 'Not open for requests now.' in closed and '/request/start/' not in closed

    def test_the_rule_book_says_an_award_is_needed(self, client, visitor):
        assert 'Your award number' in client.get('/request/').get_data(as_text=True)

    def test_an_outage_says_so_and_offers_no_start(self, client, visitor):
        visitor.outage = True
        html = client.get('/request/').get_data(as_text=True)
        assert 'not answering right now' in html and '/request/start/' not in html


class TestThePerson:

    def test_a_type_off_the_public_list_is_refused(self, client, visitor):
        assert 'not open for requests' in client.get('/request/start/777002').get_data(
            as_text=True)

    def test_start_asks_for_the_person_without_a_purpose_note(self, client, visitor):
        html = client.get('/request/start/777001').get_data(as_text=True)
        assert 'About you' in html and 'Requesting: <strong>Probe 0' in html
        assert 'name="purpose_note"' not in html and 'name="event_code"' not in html

    def test_submit_files_an_unverified_submission_and_mails_the_link(self, client, app, visitor,
                                                                       mail, cleanup):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        _, context = _submit(client, mail, cleanup)
        assert '/request/verify/' in context['verify_url']
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=cleanup).one()
            assert (row.purpose, row.xras_username, row.verified_at) == ('submission', None, None)
            assert row.purpose_note == 'New request: Probe 0'


class TestTheConfirmation:

    def test_the_code_confirms_and_opens_the_request_without_a_ticket(self, client, visitor,
                                                                       mail, cleanup):
        pending, context = _submit(client, mail, cleanup)
        resp = client.post(pending, data={'code': context['code']})
        assert resp.status_code == 302 and '/request/draft/' in resp.location
        assert 'Your email address is confirmed' in client.get(resp.location).get_data(
            as_text=True)
        assert mail.ticket.messages == [], 'the account waits for an approved request'

    def test_the_mailed_link_confirms_too(self, client, visitor, mail, cleanup):
        _, context = _submit(client, mail, cleanup)
        link = re.sub(r'^https?://[^/]+', '', context['verify_url'])
        assert '/request/draft/' in client.get(link).location

    def test_a_confirmed_pending_page_goes_on_to_the_draft(self, client, visitor, mail, cleanup):
        pending, context = _submit(client, mail, cleanup)
        client.post(pending, data={'code': context['code']})
        assert '/request/draft/' in client.get(pending).location

    def test_a_wrong_code_is_refused(self, client, visitor, mail, cleanup):
        pending, _ = _submit(client, mail, cleanup)
        assert 'wrong or has expired' in client.post(pending, data={'code': '000000'}).get_data(
            as_text=True)

    def test_a_register_token_does_not_open_a_draft(self, client, visitor):
        from webapp.register import tokens
        with client.application.test_request_context():
            token = tokens.page_token(1)
        assert 'no longer valid' in client.get(f'/request/draft/{token}').get_data(as_text=True)


_FOS = [{'fosTypeId': 500003, 'fosName': 'Atmospheric Sciences', 'isSelectable': True}]


@pytest.fixture
def confirmed(client, app, visitor, mail, cleanup, monkeypatch):
    """A visitor through step 4: ``(draft path, token, row id)``."""
    from sam.core.account_requests import AccountRequest
    from sam.integration.xras_api import cache as xras_cache
    from webapp.extensions import db
    xras_cache._CACHE.reset_for_tests()
    monkeypatch.setattr('sam.integration.xras_api.lookups.get_fos_types', lambda: _FOS)
    monkeypatch.setattr('webapp.dashboards.user.requests_routes.funding_agency_choices',
                        lambda: [(1, 'NSF')])
    pending, context = _submit(client, mail, cleanup)
    draft = client.post(pending, data={'code': context['code']}).location
    with app.app_context():
        row = db.session.query(AccountRequest).filter_by(email=cleanup).one()
        row_id, placeholder = row.account_request_id, row.xras_username
    yield draft, draft.rsplit('/', 1)[1], row_id, placeholder
    xras_cache._CACHE.reset_for_tests(disabled=False)


#: The probe opportunities' rule book wants a grant: the six fields validate demands.
_GRANT = {'grant_agency_id': '1', 'grant_number': '2441755', 'grant_title': 'Ensembles',
          'grant_pi_name': 'Vis Itor', 'grant_begin_date': '2025-09-01',
          'grant_end_date': '2028-08-31', 'grant_officer_name': 'P. Officer',
          'grant_officer_email': 'po@example.invalid'}


def _describe(**extra):
    return {'stage': 'request', 'opportunity_id': '777001', 'title': 'Ensemble downscaling',
            'abstract': 'Regional runs for the next three summers.', 'fos_type_id': '500003',
            'mnemonic_code_id': '', **_GRANT, **extra}


class TestTheVisitorsRequest:

    def test_confirming_names_the_placeholder_in_sams_shape(self, confirmed):
        from sam.queries.xras_accounts import is_placeholder
        _, _, row_id, placeholder = confirmed
        assert placeholder.startswith(f'samr{row_id}-user-') and is_placeholder(placeholder)

    def test_an_operators_verify_still_names_the_placeholder(self, client, app, visitor, mail,
                                                             cleanup):
        """Mail off (samuel-dev): Verify on the Accounts card, then the visitor's own page."""
        from sam.core.account_requests import AccountRequest
        from sam.manage import management_transaction
        from webapp.extensions import db
        pending, _ = _submit(client, mail, cleanup)
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=cleanup).one()
            with management_transaction(db.session):
                row.mark_verified('benkirk')
        assert '/request/draft/' in client.get(pending).location
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=cleanup).one()
            assert row.xras_username.startswith(f'samr{row.account_request_id}-user-')

    def test_the_page_loads_the_form_into_its_panel(self, client, confirmed):
        draft, token, _, _ = confirmed
        html = client.get(draft).get_data(as_text=True)
        assert f'hx-get="/request/draft/{token}/form"' in html and 'X-CSRFToken' in html

    def test_page_one_fixes_the_opportunity_and_names_no_admin(self, client, confirmed):
        _, token, _, _ = confirmed
        html = client.get(f'/request/draft/{token}/form').get_data(as_text=True)
        assert 'name="opportunity_id" value="777001"' in html and 'xrasNewOpportunitySelect' not in html
        assert 'admin_user_id' not in html
        assert f'/request/draft/{token}/mnemonic-suggest' in html, 'the letters picker is its own'

    def test_the_review_does_not_count_the_pi_xras_cannot_know_yet(self, client, confirmed,
                                                                    monkeypatch):
        _, token, _, placeholder = confirmed
        verdict = _verdict('failed', (f'PI {placeholder} is not in database',))
        monkeypatch.setattr('sam.xras.submission_preflight.preflight_draft',
                            lambda *a, **k: verdict)
        code = _a_code(client.application)
        resp = client.post(f'/request/draft/{token}', data=_describe(
            stage='review', mnemonic_code_id=str(code), begin_date='2026-12-01',
            end_date='2027-11-30', amount_530902='1000'))
        html = resp.get_data(as_text=True)
        assert 'data-gate-blocked disabled' not in html, 'only the PI message failed it'

    def test_submit_files_through_the_visitor_door(self, client, confirmed, monkeypatch):
        from sam.manage import xras_submission as flow
        from sam.manage.xras_submission import SubmissionOutcome
        from webapp.register.eula import eula_sha
        _, token, row_id, placeholder = confirmed
        monkeypatch.setattr('sam.xras.submission_preflight.preflight_draft',
                            lambda *a, **k: _verdict('rechecked', ()))
        calls = []
        monkeypatch.setattr(flow, 'request_action',
                            lambda *a, **k: calls.append(k) or SubmissionOutcome(1, 'submitted'))
        code = _a_code(client.application)
        with client.application.test_request_context():
            sha = eula_sha()
        html = client.post(f'/request/draft/{token}', data=_describe(
            stage='submit', mnemonic_code_id=str(code), begin_date='2026-12-01',
            end_date='2027-11-30', amount_530902='1000', agree='1',
            eula_sha=sha)).get_data(as_text=True)
        [call] = calls
        assert (call['door'], call['actor'], call['xa_user']) == (
            'visitor', f'registration:{row_id}', placeholder)
        assert f'/request/status/{token}' in html, 'Done goes to the status page'

    def test_the_review_never_says_whom_an_address_belongs_to(self, client, confirmed,
                                                              monkeypatch):
        from sam.core.users import User
        from webapp.extensions import db
        _, token, _, _ = confirmed
        monkeypatch.setattr('sam.xras.submission_preflight.preflight_draft',
                            lambda *a, **k: _verdict('rechecked', ()))
        with client.application.app_context():
            known = db.session.query(User).filter(User.is_active, User.username != 'benkirk'
                                                  ).order_by(User.user_id).first()
            username, email = known.username, known.primary_email
        roster = f'Known Person <{email}>\nMe Again <{_visitor_email(client, token)}>'
        html = client.post(f'/request/draft/{token}', data=_describe(
            stage='review', mnemonic_code_id=str(_a_code(client.application)),
            begin_date='2026-12-01', end_date='2027-11-30', amount_530902='1000',
            roster=roster)).get_data(as_text=True)
        facts = html.split('</dl>')[0]
        assert 'Known Person' in facts and 'Me Again' not in facts
        assert 'in XRAS as' not in html and f'>{username}<' not in html

    def test_the_pickers_need_a_live_token(self, client, visitor):
        assert client.get('/request/draft/nope/mnemonic-suggest?q=cub').status_code == 404
        assert client.get('/request/draft/nope/award-lookup?grant_number=1').status_code == 404

    def test_the_status_page_says_approved_once_released(self, client, confirmed):
        from datetime import datetime
        from sam.core.account_requests import AccountRequest
        from sam.integration.xras_submission import XrasSubmission
        from sam.manage import management_transaction
        from webapp.extensions import db
        _, token, row_id, placeholder = confirmed
        with client.application.app_context():
            with management_transaction(db.session):
                sub = XrasSubmission.create(
                    db.session, action_type='New', created_by=f'registration:{row_id}',
                    xa_user=placeholder, eula_sha='a' * 40, eula_accepted_at=datetime.now(),
                    payload={}, registration_id=row_id)
                sub.advance('created').advance('submitted')
            sid = sub.xras_submission_id
        try:
            assert 'CISL has your request' in client.get(f'/request/status/{token}').get_data(
                as_text=True)
            with client.application.app_context():
                with management_transaction(db.session):
                    db.session.get(AccountRequest, row_id).mark_requested()
            html = client.get(f'/request/status/{token}').get_data(as_text=True)
            assert 'the NCAR account team is creating your account' in html
        finally:
            with client.application.app_context():
                db.session.query(XrasSubmission).filter_by(xras_submission_id=sid).delete()
                db.session.commit()

    def test_the_status_page_offers_the_unsent_request(self, client, confirmed):
        _, token, _, _ = confirmed
        assert 'Continue the request' in client.get(f'/request/status/{token}').get_data(
            as_text=True)


def _visitor_email(client, token):
    from webapp.register.allocation_request import draft_row
    with client.application.test_request_context():
        return draft_row(token)[0].email


def _verdict(status, messages):
    from datetime import datetime
    from sam.xras.preflight import Verdict
    return Verdict(status=status, would_succeed=not messages, messages=messages, gaps=(),
                   service=None, warnings=(), action_id=None, action_type='New',
                   action_status=None, request_status=None, stage='Requested',
                   push_state='unknown', push_detail=None, checked_at=datetime.now(),
                   resolved=None)


def _a_code(app):
    from sam.core.organizations import MnemonicCode
    from webapp.extensions import db
    with app.app_context():
        return db.session.query(MnemonicCode.mnemonic_code_id).filter(
            MnemonicCode.is_active).order_by(MnemonicCode.mnemonic_code_id).first()[0]
