"""The anonymous /register form: the flag, the form, the write, verification.

The flag mirrors COMPONENT_GALLERY_ENABLED (on outside production, off in
production; the URL 404s when off). The POST creates a committed row through
`db.session`, so the tests that exercise it clean up by address; the mailer
is replaced with a null transport and no ledger, so no notification_log row
leaks into the shared test database.
"""

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from webapp.limiter import limiter as facade


GOOD = {
    'email': 'zz.register.test@example.invalid', 'first_name': 'Reg', 'last_name': 'Tester',
    'organization': 'University of Example', 'academic_status': 'Graduate Student',
    'residence_country': 'US', 'phone': '+1 303 555 0100',
    'purpose_note': 'Running the class exercises.',
}


@pytest.fixture(scope='module')
def registration_disabled_app(test_db_url, status_db_url):
    from webapp.run import create_app
    return create_app(config_overrides={
        'SQLALCHEMY_DATABASE_URI': test_db_url,
        'SQLALCHEMY_BINDS': {'system_status': status_db_url},
        'ACCOUNT_REGISTRATION_ENABLED': False,
    })


@pytest.fixture
def null_notifier(monkeypatch):
    """A mailer that records nothing: TestingConfig has NOTIFY off, and a real
    Notifier would still write a `suppressed` ledger row per POST."""
    from sam.notify import NotifyConfig, Notifier, NullTransport
    monkeypatch.setattr('webapp.register.blueprint.get_notifier',
                        lambda **_: Notifier(config=NotifyConfig(enabled=False),
                                             transport=NullTransport(), ledger=None))


@pytest.fixture
def ticket_mailer(app, monkeypatch):
    """NUSD's ticket address configured, and the handoff mailer recording."""
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', 'help@example.invalid')
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_FROM', 'person@ucar.edu')
    from sam.notify.base import DeliveryResult

    class _Recorder:
        messages = []

        def send(self, message, **_):
            self.messages.append(message)
            return DeliveryResult(ok=True, status='sent', message=message)
    recorder = _Recorder()
    monkeypatch.setattr('webapp.register.handoff_mail.get_notifier', lambda **_: recorder)
    return recorder


@pytest.fixture
def cleanup_address(app):
    """Delete whatever a POST created for the test address."""
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    yield GOOD['email']
    with app.app_context():
        db.session.query(AccountRequest).filter(
            AccountRequest.email == GOOD['email']).delete()
        db.session.commit()


@pytest.fixture
def committed_registration(app):
    """A committed, unverified self-registration with a known code."""
    from sam.core.account_requests import AccountRequest, CREATED_BY_SELF
    from webapp.extensions import db
    from webapp.register import tokens

    code = '246810'
    with app.app_context():
        row = AccountRequest.create(
            db.session, email='zz.pending.test@example.invalid', first_name='Pen',
            last_name='Ding', purpose='standalone', created_by=CREATED_BY_SELF)
        row.set_verification(tokens.code_hash(row.account_request_id, code),
                             datetime.now() + timedelta(hours=1))
        db.session.commit()
        row_id = row.account_request_id

    yield row_id, code

    with app.app_context():
        db.session.query(AccountRequest).filter(
            AccountRequest.account_request_id == row_id).delete()
        db.session.commit()


def _row(app, row_id):
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    with app.app_context():
        row = db.session.get(AccountRequest, row_id)
        db.session.refresh(row)
        return {'verified_at': row.verified_at, 'verified_by': row.verified_by,
                'hash': row.verify_code_hash}


class TestTheFlag:

    def test_on_by_default_outside_production(self, app):
        assert app.config['ACCOUNT_REGISTRATION_ENABLED'] is True
        assert 'register' in app.blueprints

    def test_off_means_404_and_no_blueprint(self, registration_disabled_app):
        client = registration_disabled_app.test_client()
        assert client.get('/register/').status_code == 404
        assert client.get('/register/verify/x').status_code == 404
        assert 'register' not in registration_disabled_app.blueprints
        assert client.get('/auth/login').status_code == 200

    def test_loaded_class_defaults(self):
        import os
        from webapp.config import DevelopmentConfig, ProductionConfig, TestingConfig
        if 'ACCOUNT_REGISTRATION_ENABLED' not in os.environ:
            assert ProductionConfig.ACCOUNT_REGISTRATION_ENABLED is False
            assert DevelopmentConfig.ACCOUNT_REGISTRATION_ENABLED is True
            assert TestingConfig.ACCOUNT_REGISTRATION_ENABLED is True


class TestTheFormShell:
    def test_country_is_a_datalist_of_display_cased_names(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'list="residence_country-list"' in html
        assert '<option value="United States">' in html
        assert '<option value="Korea, Republic of">' in html
        assert 'UNITED STATES' not in html and '\u00c3' not in html, 'raw or mojibake name'

    def test_the_shell_loads_htmx_for_the_institution_search(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'hx-get="/register/institutions"' in html
        assert re.search(r'<script[^>]+htmx', html), 'the Institution datalist is dead without htmx'


class TestTheEventPicker:
    """The open form offers the publicly listed events as an optional select;
    an unlisted event is reachable by its link only."""

    LISTED = [{'event_code': 'ZZ-LISTED', 'name': 'ZZ Listed Workshop'}]

    @pytest.fixture
    def listed(self, monkeypatch):
        monkeypatch.setattr('webapp.register.blueprint.upcoming_events_data',
                            lambda: self.LISTED)

    def test_listed_events_are_the_options(self, client, listed):
        html = client.get('/register/').get_data(as_text=True)
        assert '<select' in html and 'name="event_code"' in html
        assert 'value="ZZ-LISTED"' in html and 'ZZ Listed Workshop (ZZ-LISTED)' in html

    def test_no_listed_events_means_no_picker(self, client, monkeypatch):
        monkeypatch.setattr('webapp.register.blueprint.upcoming_events_data', lambda: [])
        assert 'name="event_code"' not in client.get('/register/').get_data(as_text=True)

    def test_an_unlisted_locked_code_survives_an_error_re_render(self, client, listed,
                                                                 committed_event):
        code, _ = committed_event
        resp = client.post('/register/', data={**GOOD, 'email': 'not-an-address',
                                               'event_code': code, 'event_locked': '1'})
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert f'name="event_code" value="{code}"' in html, 'the locked code was dropped'
        assert 'value="ZZ-LISTED"' not in html


class TestTheAcceptGate:
    """ACCOUNT_REGISTRATION_GATE_ENABLED: a terms acceptance must pass,
    server-side, before the open form is reachable. Off in
    TestingConfig so the form tests above stay direct; this class builds an app
    with it on."""

    @pytest.fixture(scope='class')
    def gate_app(self, test_db_url, status_db_url):
        from webapp.run import create_app
        return create_app(config_overrides={
            'SQLALCHEMY_DATABASE_URI': test_db_url,
            'SQLALCHEMY_BINDS': {'system_status': status_db_url},
            'ACCOUNT_REGISTRATION_GATE_ENABLED': True,
        })

    def _accept(self, client):
        """Pass the gate on `client`; returns the accept response."""
        return client.post('/register/accept', data={'accept': '1'})

    def test_default_is_on_outside_testing(self):
        import os
        from webapp.config import DevelopmentConfig, ProductionConfig, TestingConfig
        if 'ACCOUNT_REGISTRATION_GATE_ENABLED' not in os.environ:
            assert ProductionConfig.ACCOUNT_REGISTRATION_GATE_ENABLED is True
            assert DevelopmentConfig.ACCOUNT_REGISTRATION_GATE_ENABLED is True
        assert TestingConfig.ACCOUNT_REGISTRATION_GATE_ENABLED is False

    def test_get_shows_the_gate_not_the_form(self, gate_app):
        html = gate_app.test_client().get('/register/').get_data(as_text=True)
        assert 'Accept and continue' in html
        assert 'name="email"' not in html, 'the open form must be gated'

    def test_the_gate_links_the_full_terms_in_a_new_tab(self, gate_app):
        html = gate_app.test_client().get('/register/').get_data(as_text=True)
        assert 'href="/register/terms" target="_blank" rel="noopener"' in html

    def test_the_full_terms_page_is_readable_without_accepting(self, gate_app):
        from webapp.register.eula import eula_html
        resp = gate_app.test_client().get('/register/terms')
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200 and str(eula_html()) in html
        assert 'name="accept"' not in html, 'read-only: acceptance stays on the gate'

    def test_the_gate_carries_the_scroll_to_end_hooks(self, gate_app):
        """register.js holds the accept box until the end sentinel is seen."""
        html = gate_app.test_client().get('/register/').get_data(as_text=True)
        assert 'data-eula-scroll' in html and 'data-eula-end' in html
        assert 'data-eula-hint' in html

    def test_post_without_accepting_is_bounced_and_writes_nothing(self, gate_app, app,
                                                                  null_notifier):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        email = 'zz.gate.bounce@example.invalid'
        resp = gate_app.test_client().post('/register/', data={**GOOD, 'email': email})
        assert resp.status_code == 200
        assert 'Accept and continue' in resp.get_data(as_text=True), 'the open POST is re-gated'
        with app.app_context():
            assert db.session.query(AccountRequest).filter_by(email=email).count() == 0

    def test_missing_acceptance_re_renders_with_an_error(self, gate_app):
        resp = gate_app.test_client().post('/register/accept', data={})
        assert resp.status_code == 200
        assert 'accept the terms' in resp.get_data(as_text=True)

    def test_accepting_opens_the_form_then_submit_succeeds(self, gate_app, app,
                                                           null_notifier):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        # Its own address: the shared GOOD one races other workers' committed rows.
        email = f'zz.gate.{uuid4().hex[:8]}@example.invalid'
        client = gate_app.test_client()
        accepted = self._accept(client)
        assert accepted.status_code == 302 and accepted.headers['Location'].endswith('/register/')
        # The session now carries the marker, so the open form renders...
        form_html = client.get('/register/').get_data(as_text=True)
        assert 'name="email"' in form_html and 'Send me the confirmation' in form_html
        # ...and the write goes through.
        try:
            resp = client.post('/register/', data={**GOOD, 'email': email})
            assert resp.status_code == 302 and '/register/pending/' in resp.headers['Location']
            with app.app_context():
                assert db.session.query(AccountRequest).filter_by(email=email).count() == 1
        finally:
            with app.app_context():
                db.session.query(AccountRequest).filter_by(email=email).delete()
                db.session.commit()
        # The marker is cleared after a submission: a second one re-gates.
        assert 'Accept and continue' in client.post(
            '/register/', data={**GOOD, 'email': 'zz.gate.second@example.invalid'}
        ).get_data(as_text=True)

    def test_a_stale_accept_is_bounced(self, gate_app, monkeypatch):
        from datetime import timedelta
        from webapp.register import blueprint
        client = gate_app.test_client()
        self._accept(client)
        monkeypatch.setattr(blueprint, '_GATE_TTL', timedelta(seconds=-1))
        assert 'Accept and continue' in client.get('/register/').get_data(as_text=True)

    def test_an_event_locked_accept_returns_to_the_event_form(self, gate_app,
                                                              committed_event):
        open_event, _ = committed_event
        client = gate_app.test_client()
        html = client.get(f'/register/{open_event}').get_data(as_text=True)
        assert f'name="event_code" value="{open_event}"' in html
        resp = client.post('/register/accept', data={'accept': '1', 'event_code': open_event})
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith(f'/register/{open_event}')


class TestTheLoginGate:
    """ACCOUNT_REGISTRATION_LOGIN_REQUIRED: the preview posture. Off in
    TestingConfig so the anonymous-form tests above stay the public path;
    this class builds an app with it on."""

    @pytest.fixture(scope='class')
    def gated_app(self, test_db_url, status_db_url):
        from webapp.run import create_app
        return create_app(config_overrides={
            'SQLALCHEMY_DATABASE_URI': test_db_url,
            'SQLALCHEMY_BINDS': {'system_status': status_db_url},
            'ACCOUNT_REGISTRATION_LOGIN_REQUIRED': True,
        })

    def test_on_by_default_outside_testing(self):
        import os
        from webapp.config import DevelopmentConfig, ProductionConfig, TestingConfig
        if 'ACCOUNT_REGISTRATION_LOGIN_REQUIRED' not in os.environ:
            assert ProductionConfig.ACCOUNT_REGISTRATION_LOGIN_REQUIRED is True
            assert DevelopmentConfig.ACCOUNT_REGISTRATION_LOGIN_REQUIRED is True
        assert TestingConfig.ACCOUNT_REGISTRATION_LOGIN_REQUIRED is False

    def test_anonymous_is_sent_to_login_on_every_route(self, gated_app):
        client = gated_app.test_client()
        for path in ('/register/', '/register/institutions?organization=uni',
                     '/register/pending/x', '/register/verify/x', '/register/verified'):
            resp = client.get(path)
            assert resp.status_code == 302, path
            assert '/auth/login' in resp.headers['Location'], path
        from urllib.parse import unquote
        assert 'next=/register/verify/x' in unquote(
            client.get('/register/verify/x').headers['Location']), \
            'the mailed link survives the round trip through login'
        assert client.post('/register/', data={}).status_code == 302

    def test_a_signed_in_user_sees_the_form_and_the_banner(self, gated_app, session):
        from sam import User
        user = User.get_by_username(session, 'benkirk')
        client = gated_app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
            sess['_fresh'] = True
        resp = client.get('/register/')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Request an NCAR HPC account' in html
        assert 'limits the form to signed-in users' in html, 'the preview banner'
        assert 'name="csrf_token"' in html

    def test_off_means_no_banner(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'limits the form to signed-in users' not in html


class TestTheForm:

    def test_it_is_anonymous_and_csrf_protected(self, client):
        resp = client.get('/register/')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'name="csrf_token"' in html
        assert 'name="website"' in html, 'the honeypot'
        assert 'name="purpose_note"' in html

    def test_the_institution_field_is_free_text_with_suggestions(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'list="organization-list"' in html and '<datalist id="organization-list">' in html
        assert 'hx-get="/register/institutions"' in html
        assert 'Institution' in html and 'Organization' not in html, \
            "SAM's word for the affiliation; Organization is a UCAR unit"

    def test_institution_suggestions_are_anonymous_names_only(self, client):
        resp = client.get('/register/institutions?organization=universit')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 0 < body.count('<option value="') <= 10
        assert client.get('/register/institutions?organization=u').get_data(as_text=True).strip() == '', \
            'below two characters nothing is suggested'


class TestSubmit:

    def test_missing_fields_re_render(self, client, null_notifier):
        resp = client.post('/register/', data={'email': GOOD['email']})
        assert resp.status_code == 200
        assert 'first_name' in resp.get_data(as_text=True)

    def test_a_phone_number_is_required(self, client, null_notifier):
        """The account team needs it for two-factor enrollment."""
        data = dict(GOOD)
        data.pop('phone')
        resp = client.post('/register/', data=data)
        assert resp.status_code == 200
        assert 'name="phone"' in resp.get_data(as_text=True)
        assert 'Missing data for required field' in resp.get_data(as_text=True)

    def test_no_reason_without_an_event_is_refused(self, client, null_notifier):
        data = dict(GOOD)
        data.pop('purpose_note')
        resp = client.post('/register/', data=data)
        assert resp.status_code == 200
        assert 'what you need the account for' in resp.get_data(as_text=True)

    def test_the_reason_label_is_not_marked_optional(self, client):
        html = client.get('/register/').get_data(as_text=True)
        label = html[html.index('for="purpose_note"'):]
        assert '(optional)' not in label[:label.index('</label>')]
        assert 'new collaborator to be added on project' in html

    def test_an_event_code_needs_no_reason(self, client, app, null_notifier, committed_event):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        code, _ = committed_event
        email = f'zz.evt.{uuid4().hex[:8]}@example.invalid'
        data = {**GOOD, 'email': email, 'event_code': code}
        data.pop('purpose_note')
        try:
            resp = client.post('/register/', data=data)
            assert resp.status_code == 302 and '/register/pending/' in resp.headers['Location']
        finally:
            with app.app_context():
                db.session.query(AccountRequest).filter_by(email=email).delete()
                db.session.commit()

    def test_the_honeypot_pretends_and_writes_nothing(self, client, app, null_notifier):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        # A distinct address, not GOOD['email']: a committed row from the
        # good-submission test (route COMMIT, outside the per-test SAVEPOINT) is
        # visible to every xdist worker, so sharing the address would make this
        # "wrote nothing" assertion order-dependent under parallel runs.
        honeypot_email = 'zz.honeypot.test@example.invalid'
        resp = client.post('/register/',
                           data={**GOOD, 'email': honeypot_email, 'website': 'http://spam'})
        assert resp.status_code == 302 and '/register/pending/' in resp.headers['Location']
        with app.app_context():
            assert db.session.query(AccountRequest).filter_by(
                email=honeypot_email).count() == 0

    def test_a_good_submission_creates_an_unverified_row_and_lands_on_pending(
            self, client, app, null_notifier, cleanup_address):
        from sam.core.account_requests import AccountRequest, CREATED_BY_SELF
        from webapp.extensions import db
        resp = client.post('/register/', data=GOOD)
        assert resp.status_code == 302
        location = resp.headers['Location']
        assert '/register/pending/' in location and 'sent=0' in location, \
            'mail is suppressed in the test config, and the page must say so'
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=GOOD['email']).one()
            assert row.created_by == CREATED_BY_SELF and row.verified_at is None
            assert row.purpose == 'standalone' and row.purpose_note
            assert row.verify_code_hash and row.verify_expires_at > datetime.now()
            assert row.verify_sent_count == 1 and row.source_ip == '127.0.0.1'
            assert not row.is_open, 'invisible to the queue until verified'
        page = client.get(location)
        assert page.status_code == 200
        assert 'Mail delivery is switched off' in page.get_data(as_text=True)


class TestVerification:

    def test_the_link_verifies_once_and_clears_the_code(self, client, app,
                                                        committed_registration):
        from webapp.register import tokens
        row_id, _ = committed_registration
        with app.app_context():
            token = tokens.link_token(row_id)
        resp = client.get(f'/register/verify/{token}')
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/register/verified')
        state = _row(app, row_id)
        assert state['verified_at'] is not None and state['verified_by'] == 'self'
        assert state['hash'] is None

    def test_the_link_files_one_ticket_for_nusd(self, client, app, committed_registration,
                                                ticket_mailer):
        from webapp.register import tokens
        row_id, _ = committed_registration
        with app.app_context():
            token = tokens.link_token(row_id)
        client.get(f'/register/verify/{token}')
        client.get(f'/register/verify/{token}')
        assert len(ticket_mailer.messages) == 1, 'the second visit is not a second ticket'
        ticket = ticket_mailer.messages[0]
        assert ticket.kind == 'account_ticket'
        assert ticket.recipient.address == 'help@example.invalid'
        assert ticket.sender == 'person@ucar.edu'
        assert ticket.subject == "New HPC User Request 'Pen Ding'"
        assert ticket.dedup_key == f'account_ticket:{row_id}'
        assert ticket.context['requested_via'].startswith('self-registration')

    def test_the_code_files_the_ticket_too(self, client, app, committed_registration,
                                           ticket_mailer):
        from webapp.register import tokens
        row_id, code = committed_registration
        with app.app_context():
            page = tokens.page_token(row_id)
        client.post(f'/register/pending/{page}', data={'code': '000000'})
        assert ticket_mailer.messages == []
        client.post(f'/register/pending/{page}', data={'code': code})
        assert [m.kind for m in ticket_mailer.messages] == ['account_ticket']

    def test_no_ticket_address_means_no_ticket(self, client, app, committed_registration,
                                               ticket_mailer, monkeypatch):
        from webapp.register import tokens
        monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', '')
        row_id, _ = committed_registration
        with app.app_context():
            token = tokens.link_token(row_id)
        client.get(f'/register/verify/{token}')
        assert ticket_mailer.messages == []
        assert _row(app, row_id)['verified_at'] is not None

    def test_a_page_token_cannot_verify(self, client, app, committed_registration):
        from webapp.register import tokens
        row_id, _ = committed_registration
        with app.app_context():
            token = tokens.page_token(row_id)
        resp = client.get(f'/register/verify/{token}')
        assert resp.status_code == 200
        assert 'no longer valid' in resp.get_data(as_text=True)
        assert _row(app, row_id)['verified_at'] is None

    def test_a_tampered_or_expired_link_is_refused_at_200(self, client, app,
                                                          committed_registration, monkeypatch):
        from webapp.register import tokens
        row_id, _ = committed_registration
        assert client.get('/register/verify/not-a-token').status_code == 200
        with app.app_context():
            token = tokens.link_token(row_id)
        # itsdangerous expires on `age > max_age`, so a TTL of 0 still admits a
        # token minted this second; a negative TTL is what forces expiry.
        monkeypatch.setitem(app.config, 'ACCOUNT_VERIFY_TTL_HOURS', -1)
        resp = client.get(f'/register/verify/{token}')
        assert 'no longer valid' in resp.get_data(as_text=True)
        assert _row(app, row_id)['verified_at'] is None

    def test_the_code_path(self, client, app, committed_registration):
        from webapp.register import tokens
        row_id, code = committed_registration
        with app.app_context():
            page = tokens.page_token(row_id)
        wrong = client.post(f'/register/pending/{page}', data={'code': '000000'})
        assert wrong.status_code == 200
        assert 'wrong or has expired' in wrong.get_data(as_text=True)
        assert _row(app, row_id)['verified_at'] is None
        right = client.post(f'/register/pending/{page}', data={'code': f' {code} '})
        assert right.status_code == 302 and right.headers['Location'].endswith('/register/verified')
        state = _row(app, row_id)
        assert state['verified_by'] == 'self' and state['hash'] is None

    def test_a_verified_row_skips_the_pending_page(self, client, app, committed_registration):
        from webapp.register import tokens
        row_id, _ = committed_registration
        with app.app_context():
            link, page = tokens.link_token(row_id), tokens.page_token(row_id)
        client.get(f'/register/verify/{link}')
        resp = client.get(f'/register/pending/{page}')
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/register/verified')


@pytest.fixture
def enabled_limiter(app):
    """Flip the limiter on for one test (tests/integration/test_rate_limit_flow.py)."""
    def _clear():
        try:
            storage = facade.limiter.storage
        except (AssertionError, AttributeError):
            return
        inner = getattr(storage, 'storage', None)
        if isinstance(inner, dict):
            inner.clear()
    with app.app_context():
        facade.limiter.enabled = True
        app.config['RATELIMIT_ENABLED'] = True
        _clear()
        try:
            yield
        finally:
            facade.limiter.enabled = False
            app.config['RATELIMIT_ENABLED'] = False
            _clear()


class TestTheHumanCheck:
    """webapp.utils.human_check on the form POST. Provider switched on per test
    with Cloudflare's published dummy keys; `_siteverify` is the one outbound
    call and is always replaced (tests block real HTTP)."""

    SECRET = '1x0000000000000000000000000000000AA'
    SITE = '1x00000000000000000000AA'

    @pytest.fixture
    def turnstile(self, app, monkeypatch):
        """Turn the provider on; yields the list of siteverify calls, whose
        reply is `calls.reply` (a dict, or an exception to raise)."""
        from webapp.utils import human_check
        monkeypatch.setitem(app.config, 'HUMAN_CHECK_PROVIDER', 'turnstile')
        monkeypatch.setitem(app.config, 'HUMAN_CHECK_SITE_KEY', self.SITE)
        monkeypatch.setitem(app.config, 'HUMAN_CHECK_SECRET_KEY', self.SECRET)

        class Calls(list):
            reply = {'success': True}

        calls = Calls()

        def fake(url, data):
            calls.append((url, data))
            if isinstance(calls.reply, Exception):
                raise calls.reply
            return calls.reply

        monkeypatch.setattr(human_check, '_siteverify', fake)
        return calls

    @pytest.fixture
    def form(self, app):
        """GOOD under its own address: committed rows are visible to every
        xdist worker, so the shared one races the other submit tests."""
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        data = {**GOOD, 'email': f'zz.hc.{uuid4().hex[:8]}@example.invalid'}
        yield data
        with app.app_context():
            db.session.query(AccountRequest).filter_by(email=data['email']).delete()
            db.session.commit()

    @staticmethod
    def _count(app, email):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        with app.app_context():
            return db.session.query(AccountRequest).filter_by(email=email).count()

    def test_the_form_renders_the_widget_but_never_the_secret(self, client, turnstile):
        html = client.get('/register/').get_data(as_text=True)
        assert 'class="cf-turnstile"' in html
        assert f'data-sitekey="{self.SITE}"' in html
        assert 'https://challenges.cloudflare.com/turnstile/v0/api.js' in html
        assert 'data-human-check-submit' in html
        assert self.SECRET not in html

    def test_off_renders_no_widget(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'cf-turnstile' not in html and 'challenges.cloudflare.com' not in html

    def test_a_missing_token_is_refused_and_writes_nothing(self, client, app, turnstile,
                                                           null_notifier, form):
        resp = client.post('/register/', data=form)
        assert resp.status_code == 200
        assert 'complete the verification' in resp.get_data(as_text=True)
        assert turnstile == [], 'no token, no outbound call'
        assert self._count(app, form['email']) == 0

    def test_a_passed_check_writes_the_row(self, client, app, turnstile,
                                           null_notifier, form):
        resp = client.post('/register/', data={**form, 'cf-turnstile-response': 'tok'})
        assert resp.status_code == 302 and '/register/pending/' in resp.headers['Location']
        assert self._count(app, form['email']) == 1
        (url, sent), = turnstile
        assert url.endswith('/turnstile/v0/siteverify')
        assert sent == {'secret': self.SECRET, 'response': 'tok'}

    def test_a_failed_check_keeps_the_typed_values_and_writes_nothing(
            self, client, app, turnstile, null_notifier, form):
        turnstile.reply = {'success': False, 'error-codes': ['invalid-input-response']}
        resp = client.post('/register/', data={**form, 'cf-turnstile-response': 'bad'})
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200 and 'Verification failed' in html
        assert GOOD['organization'] in html, 'the typed form survives the re-render'
        assert self._count(app, form['email']) == 0

    def test_an_unreachable_provider_fails_closed(self, client, app, turnstile,
                                                  null_notifier, form):
        import requests
        turnstile.reply = requests.ConnectionError('boom')
        resp = client.post('/register/', data={**form, 'cf-turnstile-response': 'tok'})
        assert resp.status_code == 200
        assert 'verification service is unavailable' in resp.get_data(as_text=True)
        assert self._count(app, form['email']) == 0

    def test_an_invalid_form_never_spends_the_token(self, client, turnstile, null_notifier, form):
        data = {**form, 'cf-turnstile-response': 'tok'}
        data.pop('phone')
        assert client.post('/register/', data=data).status_code == 200
        assert turnstile == []

    def test_the_real_transport_is_a_bounded_post(self, monkeypatch):
        from webapp.utils import human_check

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {'success': True}

        seen = {}
        monkeypatch.setattr(human_check.requests, 'post',
                            lambda url, **kw: seen.update(url=url, **kw) or Resp())
        assert human_check._siteverify('https://x.invalid/v', {'a': 1}) == {'success': True}
        assert seen['timeout'] == human_check.SITEVERIFY_TIMEOUT and seen['data'] == {'a': 1}


class TestTheHumanCheckConfig:

    def _cfg(self, monkeypatch, **attrs):
        from webapp.config import SAMWebappConfig
        for k, v in attrs.items():
            monkeypatch.setattr(SAMWebappConfig, k, v)
        return SAMWebappConfig

    def test_an_unknown_provider_refuses_to_start(self, monkeypatch):
        cfg = self._cfg(monkeypatch, HUMAN_CHECK_PROVIDER='recaptcha-v9')
        with pytest.raises(EnvironmentError, match='HUMAN_CHECK_PROVIDER'):
            cfg.validate_human_check()

    def test_a_provider_without_keys_refuses_to_start(self, monkeypatch):
        cfg = self._cfg(monkeypatch, HUMAN_CHECK_PROVIDER='turnstile',
                        HUMAN_CHECK_SITE_KEY='site', HUMAN_CHECK_SECRET_KEY='')
        with pytest.raises(EnvironmentError, match='HUMAN_CHECK_SECRET_KEY'):
            cfg.validate_human_check()

    def test_none_and_a_keyed_provider_validate(self, monkeypatch):
        self._cfg(monkeypatch, HUMAN_CHECK_PROVIDER='none').validate_human_check()
        self._cfg(monkeypatch, HUMAN_CHECK_PROVIDER='Turnstile',
                  HUMAN_CHECK_SITE_KEY='s', HUMAN_CHECK_SECRET_KEY='k').validate_human_check()


class TestTheEula:
    """webapp.register.eula: the vendored NWSC agreement rendered from markdown.
    The .md is vendored verbatim from NCAR/HPC-Docs; the render maps its
    relative doc links onto the published site and emits no raw script."""

    def test_it_renders_the_agreement(self):
        from markupsafe import Markup
        from webapp.register.eula import eula_html
        html = eula_html()
        assert isinstance(html, Markup)
        assert '<h1>NWSC End User Agreement</h1>' in html
        assert '<li>' in html and '<strong>' in html

    def test_relative_doc_links_are_absolutised_and_no_script(self):
        from webapp.register.eula import eula_html
        html = str(eula_html())
        assert 'href="acknowledging-ncar-and-cisl.md"' not in html
        assert ('https://ncar-hpc-docs.readthedocs.io/en/latest/getting-started/'
                'acknowledging-ncar-and-cisl/') in html
        assert 'https://rchelp.ucar.edu/' in html, 'an absolute link is left alone'
        assert '<script' not in html.lower()

    def test_the_plain_text_rendering_keeps_no_markdown(self):
        """The mail appendix's text part: the same source, links kept as
        'text (url)', wrapped, with the heading and the bullets readable."""
        from webapp.register.eula import eula_text
        text = eula_text()
        assert text.startswith('NWSC END USER AGREEMENT\n')
        assert '**' not in text and '](' not in text and '`' not in text
        assert ('https://ncar-hpc-docs.readthedocs.io/en/latest/getting-started/'
                'acknowledging-ncar-and-cisl/') in text
        assert 'rchelp.ucar.edu (https://rchelp.ucar.edu/)' in text
        assert '\n- You will use NWSC computer' in text
        assert max(len(line) for line in text.splitlines()) <= 100, 'wrapped; URLs intact'

    def test_the_gate_embeds_the_agreement(self, app):
        """The gate route passes the rendered agreement into the panel."""
        from webapp.register import blueprint
        with app.test_request_context():
            html = blueprint._render_gate()
        assert 'NWSC End User Agreement' in html
        assert 'eula-panel' in html


class TestRateLimits:

    def test_the_post_carries_the_login_tier_per_ip(self, client, app, enabled_limiter,
                                                    null_notifier):
        """5 per minute: the sixth empty POST in a minute is a 429 -- the
        brute-force surface, keyed by IP whatever the address typed."""
        for i in range(5):
            resp = client.post('/register/', data={'email': f'x{i}@example.invalid'})
            assert resp.status_code != 429, i
        assert client.post('/register/', data={'email': 'x9@example.invalid'}).status_code == 429

    def test_the_post_is_also_capped_per_address(self, client, app, enabled_limiter,
                                                 null_notifier, monkeypatch):
        monkeypatch.setitem(app.config, 'RATELIMIT_AUTH_LOGIN', '100 per minute')
        monkeypatch.setitem(app.config, 'RATELIMIT_REGISTER_EMAIL', '2 per hour')
        for _ in range(2):
            assert client.post('/register/', data={'email': GOOD['email']}).status_code != 429
        assert client.post('/register/', data={'email': GOOD['email']}).status_code == 429
        assert client.post('/register/', data={'email': 'other@example.invalid'}).status_code != 429

    def test_a_global_ceiling_caps_all_addresses_together(self, client, app,
                                                          enabled_limiter, null_notifier,
                                                          monkeypatch):
        """The fixed-key tier is a site-wide ceiling: with the per-IP and
        per-address tiers slackened, a fourth *never-seen* address still 429s,
        so enabling the form cannot open an unbounded mailer (relay blast-radius)."""
        monkeypatch.setitem(app.config, 'RATELIMIT_AUTH_LOGIN', '100 per minute')
        monkeypatch.setitem(app.config, 'RATELIMIT_REGISTER_EMAIL', '100 per hour')
        monkeypatch.setitem(app.config, 'RATELIMIT_REGISTER_GLOBAL', '3 per hour')
        for i in range(3):
            assert client.post('/register/',
                               data={'email': f'g{i}@example.invalid'}).status_code != 429, i
        assert client.post('/register/',
                           data={'email': 'fresh@example.invalid'}).status_code == 429

    def test_the_global_ceiling_default_is_low(self):
        """A safety default: the code ceiling ships low so prod (which uses the
        code default, not a helm override) is throttled until deliberately raised."""
        import os
        from webapp.config import ProductionConfig
        if 'RATELIMIT_REGISTER_GLOBAL' not in os.environ:
            assert ProductionConfig.RATELIMIT_REGISTER_GLOBAL == '10 per hour; 30 per day'
