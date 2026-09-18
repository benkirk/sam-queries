"""The anonymous /register form: the flag, the form, the write, verification.

The flag mirrors COMPONENT_GALLERY_ENABLED (on outside production, off in
production; the URL 404s when off). The POST creates a committed row through
`db.session`, so the tests that exercise it clean up by address; the mailer
is replaced with a null transport and no ledger, so no notification_log row
leaks into the shared test database.
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from webapp.limiter import limiter as facade

pytestmark = pytest.mark.unit

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
        assert 'temporarily limited to signed-in' in html, 'the preview banner'
        assert 'name="csrf_token"' in html

    def test_off_means_no_banner(self, client):
        html = client.get('/register/').get_data(as_text=True)
        assert 'temporarily limited to signed-in' not in html


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

    def test_an_unknown_code_is_refused_at_200(self, client):
        resp = client.get('/register/NO-SUCH-EVENT-9999')
        assert resp.status_code == 200
        assert 'not a known event code' in resp.get_data(as_text=True)

    def test_a_malformed_code_is_refused_at_200(self, client):
        resp = client.get('/register/x')
        assert resp.status_code == 200
        assert 'not a valid event code' in resp.get_data(as_text=True)


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
def committed_event(app):
    """A committed, open event on an existing project, visible to db.session."""
    from sam.core.account_requests import AccountRequestEvent
    from sam.projects.projects import Project
    from webapp.extensions import db
    with app.app_context():
        project = db.session.query(Project).filter(Project.is_active).first()
        event = AccountRequestEvent.create(
            db.session, event_code=f'ZZ-EVT-{uuid4().hex[:8].upper()}',
            name='ZZ Self-Enroll Test', project_id=project.project_id,
            accounts_needed_by=date.today() + timedelta(days=30), created_by='benkirk')
        db.session.commit()
        code, project_id, event_id = (event.event_code, project.project_id,
                                      event.account_request_event_id)
    yield code, project_id
    with app.app_context():
        db.session.query(AccountRequestEvent).filter(
            AccountRequestEvent.account_request_event_id == event_id).delete()
        db.session.commit()


@pytest.fixture
def signed_in(app, session):
    """A test client with benkirk's session cookie, and his user row."""
    from sam import User
    user = User.get_by_username(session, 'benkirk')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.user_id)
        sess['_fresh'] = True
    return client, user


class TestTheSignedInSelfEnrollShortcut:
    """A signed-in visitor to /register/<event> already has an account, so the
    anonymous creation form is out of context: they get a one-click self-enroll
    into the event's project instead. Registering others stays in the RBAC'd
    Invitations panel. Authorization is the open event link itself."""

    def test_a_signed_in_visitor_gets_the_self_enroll_shortcut(self, signed_in, committed_event):
        code, _ = committed_event
        client, _ = signed_in
        html = client.get(f'/register/{code}').get_data(as_text=True)
        assert 'Enroll me in' in html
        assert 'name="email"' not in html, 'not the anonymous creation form'

    def test_an_anonymous_visitor_still_gets_the_creation_form(self, client, committed_event):
        """TestingConfig leaves LOGIN_REQUIRED off, so the anonymous path stays
        the code-locked creation form -- the signed-in branch must not hijack it."""
        code, _ = committed_event
        html = client.get(f'/register/{code}').get_data(as_text=True)
        assert 'Request an NCAR HPC account' in html and 'name="email"' in html
        assert 'Enroll me in' not in html

    def test_enroll_records_the_signed_in_user_via_the_helper(
            self, signed_in, committed_event, monkeypatch):
        """The write itself is enroll_user_in_event (model/manage-tested); here
        we prove the route hands it the event, the session's own user, and the
        'self' source."""
        code, project_id = committed_event
        client, user = signed_in
        calls = []
        monkeypatch.setattr('webapp.register.blueprint.enroll_user_in_event',
                            lambda _s, *, event, user, source, by:
                            calls.append((event.project_id, user.user_id, source, by)))
        resp = client.post(f'/register/{code}/enroll')
        assert resp.status_code == 200
        assert "You're enrolled" in resp.get_data(as_text=True)
        assert calls == [(project_id, user.user_id, 'self', user.username)]

    def test_a_project_without_accounts_re_renders_the_reason(
            self, signed_in, committed_event, monkeypatch):
        code, _ = committed_event
        client, _ = signed_in
        def _boom(*_a, **_k):
            raise ValueError('Project has no accounts')
        monkeypatch.setattr('webapp.register.blueprint.enroll_user_in_event', _boom)
        resp = client.post(f'/register/{code}/enroll')
        assert resp.status_code == 200
        assert 'Project has no accounts' in resp.get_data(as_text=True)

    def test_enroll_requires_login(self, client):
        """The self-enroll POST is an authenticated action even with the gate
        off: an anonymous POST is bounced to login, never a silent write."""
        resp = client.post('/register/ANY-CODE-1/enroll')
        assert resp.status_code == 302 and '/auth/login' in resp.headers['Location']

    def test_enroll_refuses_an_unknown_event(self, signed_in):
        client, _ = signed_in
        resp = client.post('/register/NO-SUCH-EVENT-9999/enroll')
        assert resp.status_code == 200 and 'not a known event code' in resp.get_data(as_text=True)


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
