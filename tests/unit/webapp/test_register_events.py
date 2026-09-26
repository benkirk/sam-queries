"""The /register/<code> event pages: their own blueprint, mounted by
ACCOUNT_INVITATIONS_ENABLED so production serves events, self-enroll and the
Upcoming Events card with the anonymous creation form absent."""

from datetime import datetime, timedelta

import pytest


def _html(resp):
    return resp.get_data(as_text=True)


@pytest.fixture(scope='module')
def events_only_app(test_db_url, status_db_url):
    """The production posture: events on, the creation form off, login required."""
    from webapp.run import create_app
    return create_app(config_overrides={
        'SQLALCHEMY_DATABASE_URI': test_db_url,
        'SQLALCHEMY_BINDS': {'system_status': status_db_url},
        'ACCOUNT_REGISTRATION_ENABLED': False,
        'ACCOUNT_INVITATIONS_ENABLED': True,
        'ACCOUNT_REGISTRATION_LOGIN_REQUIRED': True,
    })


@pytest.fixture(scope='module')
def invitations_off_app(test_db_url, status_db_url):
    from webapp.run import create_app
    return create_app(config_overrides={
        'SQLALCHEMY_DATABASE_URI': test_db_url,
        'SQLALCHEMY_BINDS': {'system_status': status_db_url},
        'ACCOUNT_REGISTRATION_ENABLED': False,
        'ACCOUNT_INVITATIONS_ENABLED': False,
    })


def _signed_in(app, session):
    from sam import User
    user = User.get_by_username(session, 'benkirk')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.user_id)
        sess['_fresh'] = True
    return client


class TestMounting:

    def test_events_ride_the_invitations_switch(self, events_only_app, invitations_off_app, app):
        assert 'register_events' in events_only_app.blueprints
        assert 'register' not in events_only_app.blueprints
        assert 'register_events' not in invitations_off_app.blueprints
        assert {'register', 'register_events', 'register_invite'} <= set(app.blueprints)

    def test_nothing_answers_with_invitations_off(self, invitations_off_app):
        client = invitations_off_app.test_client()
        assert client.get('/register/ANY-CODE-1').status_code == 404
        assert client.get('/register/').status_code == 404

    def test_the_creation_form_is_absent_in_the_prod_posture(self, events_only_app):
        client = events_only_app.test_client()
        assert client.get('/register/').status_code == 404
        assert client.get('/register/pending/x').status_code == 404
        assert client.get('/register/verify/x').status_code == 404
        assert client.post('/register/accept', data={'accept': '1'}).status_code == 405
        # A one-segment name falls through to the event page and is refused as
        # an unknown code, never a 500. The invitee's own terms page is unaffected.
        resp = client.get('/register/terms')
        assert resp.status_code == 200 and 'not a known event code' in _html(resp)
        assert client.get('/register/invite/terms').status_code == 200


class TestTheAnonymousVisitor:

    def test_is_sent_to_login_when_the_form_is_absent(self, events_only_app, committed_event):
        code, _ = committed_event
        resp = events_only_app.test_client().get(f'/register/{code}')
        assert resp.status_code == 302
        assert '/auth/login' in resp.headers['Location']
        assert resp.headers['Location'].endswith(f'next=/register/{code}')

    def test_even_with_the_login_gate_off(self, events_only_app, committed_event, monkeypatch):
        """An unmounted form is not a fallback, whatever LOGIN_REQUIRED says."""
        code, _ = committed_event
        monkeypatch.setitem(events_only_app.config, 'ACCOUNT_REGISTRATION_LOGIN_REQUIRED', False)
        resp = events_only_app.test_client().get(f'/register/{code}')
        assert resp.status_code == 302 and '/auth/login' in resp.headers['Location']

    def test_still_gets_the_creation_form_where_it_is_mounted(self, client, committed_event):
        """TestingConfig mounts the form with LOGIN_REQUIRED off, so the anonymous
        path stays the code-locked creation form."""
        code, _ = committed_event
        html = _html(client.get(f'/register/{code}'))
        assert 'Request an NCAR HPC account' in html and 'name="email"' in html
        assert 'Enroll me in' not in html

    def test_an_unknown_code_is_refused_at_200(self, client):
        resp = client.get('/register/NO-SUCH-EVENT-9999')
        assert resp.status_code == 200
        assert 'not a known event code' in _html(resp)

    def test_a_malformed_code_is_refused_at_200(self, client):
        resp = client.get('/register/x')
        assert resp.status_code == 200
        assert 'not a valid event code' in _html(resp)

    def test_the_refusal_offers_the_form_only_where_mounted(self, client, events_only_app, session):
        assert 'register without one' in _html(client.get('/register/NO-SUCH-EVENT-9999'))
        signed_in = _signed_in(events_only_app, session)
        html = _html(signed_in.get('/register/NO-SUCH-EVENT-9999'))
        assert 'register without one' not in html and 'your dashboard' in html


class TestAClosedEventIsRefused:
    """The card and the copied link both outlive the event they point at."""

    def _set(self, app, code, **fields):
        from sam.core.account_requests import AccountRequestEvent
        from webapp.extensions import db
        with app.app_context():
            event = db.session.query(AccountRequestEvent).filter_by(event_code=code).one()
            for key, value in fields.items():
                setattr(event, key, value)
            db.session.commit()

    def test_a_closed_code_is_refused_at_200(self, client, app, committed_event):
        code, _ = committed_event
        self._set(app, code, active=False)
        resp = client.get(f'/register/{code}')
        assert resp.status_code == 200
        assert 'not accepting registrations' in _html(resp)

    def test_a_code_past_its_window_is_refused_at_200(self, client, app, committed_event):
        code, _ = committed_event
        self._set(app, code, closes_at=datetime.now() - timedelta(hours=1))
        resp = client.get(f'/register/{code}')
        assert resp.status_code == 200
        assert 'not accepting registrations' in _html(resp)


class TestTheSignedInSelfEnrollShortcut:
    """A signed-in visitor to /register/<event> already has an account, so the
    anonymous creation form is out of context: they get a one-click self-enroll
    into the event's project instead. Registering others stays in the RBAC'd
    Invitations panel. Authorization is the open event link itself."""

    def test_a_signed_in_visitor_gets_the_self_enroll_shortcut(self, signed_in, committed_event):
        code, _ = committed_event
        client, _ = signed_in
        html = _html(client.get(f'/register/{code}'))
        assert 'Enroll me in' in html
        assert 'name="email"' not in html, 'not the anonymous creation form'
        assert f'action="/register/{code}/enroll"' in html

    def test_the_shortcut_works_with_the_creation_form_absent(self, events_only_app, session,
                                                              committed_event, monkeypatch):
        code, project_id = committed_event
        client = _signed_in(events_only_app, session)
        assert 'Enroll me in' in _html(client.get(f'/register/{code}'))
        calls = []
        monkeypatch.setattr('webapp.register.events.enroll_user_in_event',
                            lambda _s, *, event, user, source, by:
                            calls.append((event.project_id, source)))
        resp = client.post(f'/register/{code}/enroll')
        assert resp.status_code == 200 and "You're enrolled" in _html(resp)
        assert calls == [(project_id, 'self')]

    def test_enroll_records_the_signed_in_user_via_the_helper(
            self, signed_in, committed_event, monkeypatch):
        """The write itself is enroll_user_in_event (model/manage-tested); here
        we prove the route hands it the event, the session's own user, and the
        'self' source."""
        code, project_id = committed_event
        client, user = signed_in
        calls = []
        monkeypatch.setattr('webapp.register.events.enroll_user_in_event',
                            lambda _s, *, event, user, source, by:
                            calls.append((event.project_id, user.user_id, source, by)))
        resp = client.post(f'/register/{code}/enroll')
        assert resp.status_code == 200
        assert "You're enrolled" in _html(resp)
        assert calls == [(project_id, user.user_id, 'self', user.username)]

    def test_a_project_without_accounts_re_renders_the_reason(
            self, signed_in, committed_event, monkeypatch):
        code, _ = committed_event
        client, _ = signed_in
        def _boom(*_a, **_k):
            raise ValueError('Project has no accounts')
        monkeypatch.setattr('webapp.register.events.enroll_user_in_event', _boom)
        resp = client.post(f'/register/{code}/enroll')
        assert resp.status_code == 200
        assert 'Project has no accounts' in _html(resp)

    def test_enroll_requires_login(self, client):
        """The self-enroll POST is an authenticated action even with the gate
        off: an anonymous POST is bounced to login, never a silent write."""
        resp = client.post('/register/ANY-CODE-1/enroll')
        assert resp.status_code == 302 and '/auth/login' in resp.headers['Location']

    def test_enroll_refuses_an_unknown_event(self, signed_in):
        client, _ = signed_in
        resp = client.post('/register/NO-SUCH-EVENT-9999/enroll')
        assert resp.status_code == 200 and 'not a known event code' in _html(resp)


class TestTheUpcomingEventsCard:
    """Rendered from ACCOUNT_INVITATIONS_ENABLED, with the creation form absent."""

    EVENT = {'event_id': 1, 'event_code': 'ZZ-CARD-PROD', 'name': 'ZZ Card Workshop',
             'instructions': None, 'project_code': 'SCSG0001',
             'accounts_needed_by': datetime(2030, 1, 1).date(), 'closes_at': None,
             'invite_only': False}

    def test_the_card_links_the_event_page_and_asks_to_sign_in(self, events_only_app, monkeypatch):
        monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data',
                            lambda: [self.EVENT])
        html = _html(events_only_app.test_client().get('/status/events'))
        assert 'Upcoming Events' in html
        assert 'href="http://localhost/register/ZZ-CARD-PROD"' in html
        assert 'Sign in to register' in html

    def test_the_admin_card_drops_the_copy_link_with_invitations_off(self, auth_client, app,
                                                                     committed_event, monkeypatch):
        code, _ = committed_event
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', False)
        html = _html(auth_client.get('/admin/events/fragment?active_only=1'))
        assert code in html and f'/register/{code}' not in html
