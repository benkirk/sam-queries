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
    fakes = type('Fakes', (), {'outage': False, 'reads': []})()

    def _opp(offset, opp_id):
        return {'opportunity_id': opp_id, 'opportunity_name': f'Probe {offset}',
                'allocation_type_id': guides + offset, 'allocation_type': 'Probe',
                'description': None, 'award_months': 12, 'can_submit': True, 'reason': None,
                'resource_ids': [], 'resources': []}

    class Admin:
        def new_request_opportunities(self, username):
            from sam.integration.xras_api import XrasSourceUnavailable
            fakes.reads.append(username)
            if fakes.outage:
                raise XrasSourceUnavailable('down')
            return [_opp(0, 777001), _opp(1, 777002)]

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
