"""Invitation links: the sponsor mails a signed link, the invitee finishes the row.

Route handlers read and commit through Flask-SQLAlchemy's own session, so the
rows here are really committed and each test removes what it made (xdist
workers share one database: every address and code is unique per test). The
mailer is a recording fake with no ledger, so no notification_log row leaks.
Design: docs/plans/implemented/ACCOUNT_INVITE_LINKS.md.
"""

import uuid
from datetime import date, datetime, timedelta

import pytest

from sam.notify.base import DeliveryResult

SENT = datetime(2026, 9, 24, 9, 0, 0)
PERSON = {'first_name': 'Ada', 'middle_name': '', 'last_name': 'Lovelace',
          'organization': 'University of Example', 'academic_status': 'Faculty',
          'residence_country': 'United Kingdom', 'phone': '+44 20 7946 0000',
          'orcid': '0000-0002-1825-0097'}


def _address():
    return f'zz.invite.{uuid.uuid4().hex[:10]}@example.invalid'


class FakeNotifier:
    """Records every message; answers each with ``status``."""

    def __init__(self, status='sent'):
        self.status, self.messages = status, []

    def send_many(self, messages, **_kwargs):
        messages = list(messages)
        self.messages.extend(messages)
        return [DeliveryResult(ok=self.status != 'failed', status=self.status, message=m,
                               detail='relay down' if self.status == 'failed' else None)
                for m in messages]

    def send(self, message, **kwargs):
        return self.send_many([message], **kwargs)[0]


@pytest.fixture
def mailer(app, monkeypatch):
    """The invite-link mailer; ``.handoff`` records receipts and NUSD tickets,
    with the ticket address configured."""
    fake = FakeNotifier()
    fake.handoff = FakeNotifier()
    monkeypatch.setattr('webapp.register.invite_mail.get_notifier', lambda **_: fake)
    monkeypatch.setattr('webapp.register.handoff_mail.get_notifier', lambda **_: fake.handoff)
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', 'help@example.invalid')
    return fake


def _tickets(fake):
    return [m for m in fake.handoff.messages if m.kind == 'account_ticket']


@pytest.fixture
def no_mail(monkeypatch):
    """The invitee side sends only through handoff_mail (recorded here); any
    other mailer call fails the test."""
    def _boom(**_):
        raise AssertionError('the invitee side must not send through this mailer')
    monkeypatch.setattr('webapp.register.invite_mail.get_notifier', _boom)
    monkeypatch.setattr('webapp.register.blueprint.get_notifier', _boom)
    fake = FakeNotifier()
    monkeypatch.setattr('webapp.register.handoff_mail.get_notifier', lambda **_: fake)
    return fake


@pytest.fixture
def ticket_address(app, monkeypatch):
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', 'help@example.invalid')


@pytest.fixture
def committed(app):
    """A registry of committed rows, all removed at teardown."""
    from sam.core.account_requests import AccountRequest, AccountRequestEvent, EventEnrollment
    from webapp.extensions import db
    made = {'emails': [], 'events': []}
    yield made
    with app.app_context():
        if made['emails']:
            db.session.query(AccountRequest).filter(
                AccountRequest.email.in_(made['emails'])).delete(synchronize_session=False)
        for event_id in made['events']:
            db.session.query(AccountRequest).filter(
                AccountRequest.event_id == event_id).delete(synchronize_session=False)
            db.session.query(EventEnrollment).filter(
                EventEnrollment.event_id == event_id).delete(synchronize_session=False)
            db.session.query(AccountRequestEvent).filter(
                AccountRequestEvent.account_request_event_id == event_id).delete(
                    synchronize_session=False)
        db.session.commit()


@pytest.fixture
def led_project(session):
    """``(project_id, projcode)`` of an active project benkirk leads (so he is a member)."""
    from sam.core.users import User
    from sam.projects.projects import Project
    me = User.get_by_username(session, 'benkirk')
    project = (session.query(Project)
               .filter(Project.project_lead_user_id == me.user_id, Project.is_active)
               .order_by(Project.project_id).first())
    assert project is not None, 'benkirk leads no active snapshot project'
    return project.project_id, project.projcode


@pytest.fixture
def make_event(app, committed, led_project):
    def _make(**fields):
        from sam.core.account_requests import AccountRequestEvent
        from webapp.extensions import db
        with app.app_context():
            event = AccountRequestEvent.create(
                db.session, event_code=f'ZZ-INV-{uuid.uuid4().hex[:8].upper()}',
                name='ZZ Invite Workshop', project_id=led_project[0],
                accounts_needed_by=date.today() + timedelta(days=20),
                instructions='Bring a laptop.', created_by='benkirk', **fields)
            db.session.commit()
            committed['events'].append(event.account_request_event_id)
            return event.event_code, event.account_request_event_id
    return _make


@pytest.fixture
def make_invite(app, committed, led_project):
    """A committed sponsor row with a link out; returns ``(row_id, token)``."""
    def _make(*, event_id=None, sent=SENT, **fields):
        from sam.core.account_requests import AccountRequest
        from sam.core.users import User
        from webapp.extensions import db
        from webapp.register import tokens
        email = _address()
        with app.app_context():
            sponsor = User.get_by_username(db.session, 'benkirk')
            row = AccountRequest.create(
                db.session, email=email, first_name='Ada', last_name='Lovelace',
                purpose='enrollment', project_id=led_project[0], event_id=event_id,
                sponsor_user_id=sponsor.user_id, created_by='benkirk',
                verified_by='benkirk', comment='visiting scholar', **fields)
            if sent is not None:
                row.mark_invite_sent(sent)
            db.session.commit()
            committed['emails'].append(email)
            token = tokens.invite_token(row.account_request_id, sent) if sent else None
            return row.account_request_id, token
    return _make


def _row(app, row_id):
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    with app.app_context():
        row = db.session.get(AccountRequest, row_id)
        db.session.refresh(row)
        db.session.expunge(row)
        return row


def _set(app, row_id, **fields):
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    with app.app_context():
        row = db.session.get(AccountRequest, row_id)
        for key, value in fields.items():
            setattr(row, key, value)
        db.session.commit()


def _html(resp):
    return resp.get_data(as_text=True)


# -- tokens -------------------------------------------------------------------

class TestTokens:

    def test_round_trip(self, app):
        from webapp.register import tokens
        with app.app_context():
            assert tokens.read_invite_token(tokens.invite_token(41, SENT)) == (
                41, '2026-09-24T09:00:00', '')

    def test_a_token_of_another_salt_is_invalid_both_ways(self, app):
        from webapp.register import tokens
        with app.app_context():
            assert tokens.read_invite_token(tokens.link_token(41))[2] == 'invalid'
            assert tokens.read_link_token(tokens.invite_token(41, SENT)) is None

    def test_expiry(self, app, monkeypatch):
        from webapp.register import tokens
        with app.app_context():
            token = tokens.invite_token(41, SENT)
            monkeypatch.setitem(app.config, 'ACCOUNT_INVITE_TTL_DAYS', -1)
            assert tokens.read_invite_token(token) == (None, None, 'expired')

    def test_the_stamp_is_seconds_only(self):
        from webapp.register.tokens import invite_stamp
        assert invite_stamp(datetime(2026, 9, 24, 9, 0, 0, 999)) == '2026-09-24T09:00:00'


# -- the invitee's page -------------------------------------------------------

class TestRefusals:

    def test_a_bad_token_is_refused_at_200(self, client):
        resp = client.get('/register/invite/not-a-token')
        assert resp.status_code == 200
        assert 'not a valid invitation link' in _html(resp)

    def test_an_expired_token_is_refused(self, client, app, make_invite, monkeypatch):
        _, token = make_invite()
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITE_TTL_DAYS', -1)
        assert 'has expired' in _html(client.get(f'/register/invite/{token}'))

    def test_a_superseded_link_is_refused(self, client, app, make_invite):
        row_id, token = make_invite()
        _set(app, row_id, invite_sent_at=SENT + timedelta(hours=1))
        assert 'A newer invitation was sent' in _html(client.get(f'/register/invite/{token}'))

    def test_a_closed_row_is_refused(self, client, app, make_invite):
        row_id, token = make_invite()
        _set(app, row_id, state='dismissed')
        assert 'closed by the account team' in _html(client.get(f'/register/invite/{token}'))

    def test_a_fulfilled_row_is_refused(self, client, app, make_invite, session):
        from sam.core.users import User
        row_id, token = make_invite()
        _set(app, row_id, user_id=User.get_by_username(session, 'benkirk').user_id)
        assert 'already exists' in _html(client.get(f'/register/invite/{token}'))


class TestCompleting:

    def _accept(self, client, token):
        resp = client.post(f'/register/invite/{token}/accept', data={'accept': '1'})
        assert resp.status_code == 302 and resp.headers['Location'].endswith(token)

    def test_the_gate_comes_first(self, client, make_invite):
        _, token = make_invite()
        html = _html(client.get(f'/register/invite/{token}'))
        assert 'Accept and continue' in html
        assert f'action="/register/invite/{token}/accept"' in html
        assert 'href="/register/invite/terms"' in html
        assert 'name="phone"' not in html

    def test_then_a_prefilled_form_with_the_email_read_only(self, client, app, make_invite):
        row_id, token = make_invite()
        self._accept(client, token)
        html = _html(client.get(f'/register/invite/{token}'))
        email = _row(app, row_id).email
        assert 'value="Ada"' in html and 'value="Lovelace"' in html
        assert email in html and 'name="email"' not in html
        for absent in ('name="website"', 'name="event_code"', 'name="purpose_note"',
                       'data-human-check-submit', 'limits the form to'):
            assert absent not in html, absent
        assert 'visiting scholar' not in html, "the sponsor's note is for NUSD only"
        assert f'action="/register/invite/{token}"' in html

    def test_an_event_invite_shows_the_event(self, client, make_event, make_invite):
        code, event_id = make_event()
        _, token = make_invite(event_id=event_id)
        self._accept(client, token)
        html = _html(client.get(f'/register/invite/{token}'))
        assert 'ZZ Invite Workshop' in html and 'Bring a laptop.' in html

    def test_a_submit_updates_the_row_and_stamps_it(self, client, app, make_invite, no_mail):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        from webapp.register.eula import eula_sha
        row_id, token = make_invite()
        email = _row(app, row_id).email
        self._accept(client, token)
        resp = client.post(f'/register/invite/{token}',
                           data={**PERSON, 'email': 'other@example.invalid',
                                 'event_code': 'NOPE'})
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/complete')
        row = _row(app, row_id)
        assert row.email == email, 'the vouched address is not a form field'
        assert (row.phone, row.residence_country, row.academic_status) == (
            PERSON['phone'], 'United Kingdom', 'Faculty')
        assert row.orcid == PERSON['orcid'] and row.desired_username is None
        assert row.completed_at is not None
        assert row.eula_sha == eula_sha() and row.eula_accepted_at is not None
        assert row.event_id is None and row.comment == 'visiting scholar'
        with app.app_context():
            assert db.session.query(AccountRequest).filter_by(email=email).count() == 1
        assert 'We already have your details' in _html(client.get(f'/register/invite/{token}'))
        assert 'request is complete' in _html(client.get('/register/invite/complete'))

    def test_a_submit_mails_the_invitee_a_receipt_with_the_agreement(self, client, app,
                                                                     make_invite, no_mail):
        from webapp.register.eula import eula_text
        row_id, token = make_invite()
        email = _row(app, row_id).email
        self._accept(client, token)
        client.post(f'/register/invite/{token}', data=PERSON)
        receipt, = [m for m in no_mail.messages if m.kind == 'account_request_received']
        assert receipt.recipient.address == email
        assert receipt.context['name'] == 'Ada Lovelace'
        assert receipt.context['eula_text'] == eula_text()
        assert 'NWSC End User Agreement' in receipt.context['eula_html']
        assert receipt.context['eula_accepted_on']
        assert receipt.dedup_key.startswith(f'account_request_received:{row_id}:2')

    def test_a_submit_files_the_ticket_after_the_receipt(self, client, app, make_invite,
                                                         no_mail, ticket_address):
        row_id, token = make_invite()
        self._accept(client, token)
        client.post(f'/register/invite/{token}', data=PERSON)
        assert [m.kind for m in no_mail.messages] == ['account_request_received',
                                                      'account_ticket']
        ticket = no_mail.messages[1]
        assert ticket.recipient.address == 'help@example.invalid'
        assert ticket.subject == f"New HPC User Request 'Ada Lovelace' for {_row(app, row_id).project_id and self._projcode(app, row_id)}"
        assert ticket.context['phone'] == PERSON['phone']
        assert ticket.context['requested_via'].startswith('invitation by')
        assert ticket.dedup_key == f'account_ticket:{row_id}'

    def _projcode(self, app, row_id):
        from sam.projects.projects import Project
        from webapp.extensions import db
        with app.app_context():
            return db.session.get(Project, _row(app, row_id).project_id).projcode

    def test_a_failed_submit_mails_nothing(self, client, app, make_invite, no_mail):
        row_id, token = make_invite()
        self._accept(client, token)
        data = dict(PERSON)
        data.pop('phone')
        client.post(f'/register/invite/{token}', data=data)
        assert no_mail.messages == []

    def test_a_submit_without_the_gate_is_regated(self, client, app, make_invite, no_mail):
        row_id, token = make_invite()
        html = _html(client.post(f'/register/invite/{token}', data=PERSON))
        assert 'Accept and continue' in html
        assert _row(app, row_id).completed_at is None

    def test_an_accept_is_bound_to_its_link(self, client, make_invite):
        _, first = make_invite()
        _, second = make_invite()
        self._accept(client, first)
        assert 'Accept and continue' in _html(client.get(f'/register/invite/{second}'))

    def test_a_missing_phone_re_renders(self, client, app, make_invite, no_mail):
        row_id, token = make_invite()
        self._accept(client, token)
        data = dict(PERSON)
        data.pop('phone')
        html = _html(client.post(f'/register/invite/{token}', data=data))
        assert 'name="phone"' in html and 'Missing data for required field' in html
        assert _row(app, row_id).completed_at is None


class TestMounting:
    """The link belongs to the invitations switch, not the public form's."""

    @pytest.fixture(scope='class')
    def dark_registration_app(self, test_db_url, status_db_url):
        from webapp.run import create_app
        return create_app(config_overrides={
            'SQLALCHEMY_DATABASE_URI': test_db_url,
            'SQLALCHEMY_BINDS': {'system_status': status_db_url},
            'ACCOUNT_REGISTRATION_ENABLED': False,
            'ACCOUNT_REGISTRATION_LOGIN_REQUIRED': True,
        })

    @pytest.fixture(scope='class')
    def no_invitations_app(self, test_db_url, status_db_url):
        from webapp.run import create_app
        return create_app(config_overrides={
            'SQLALCHEMY_DATABASE_URI': test_db_url,
            'SQLALCHEMY_BINDS': {'system_status': status_db_url},
            'ACCOUNT_INVITATIONS_ENABLED': False,
        })

    def test_reachable_with_registration_off_and_login_required(
            self, dark_registration_app, make_invite, no_mail):
        _, token = make_invite()
        client = dark_registration_app.test_client()
        assert client.get('/register/').status_code == 404
        resp = client.get(f'/register/invite/{token}')
        assert resp.status_code == 200 and 'Accept and continue' in _html(resp)
        accept = client.post(f'/register/invite/{token}/accept', data={'accept': '1'})
        assert accept.status_code == 302 and '/auth/login' not in accept.headers['Location']
        html = _html(client.get(f'/register/invite/{token}'))
        assert 'name="phone"' in html and 'limits the form to' not in html
        assert 'hx-get="/register/invite/institutions"' in html

    def test_404_when_invitations_are_off(self, no_invitations_app, make_invite):
        _, token = make_invite()
        client = no_invitations_app.test_client()
        assert client.get(f'/register/invite/{token}').status_code == 404
        assert 'register_invite' not in no_invitations_app.blueprints


# -- the sponsor side ---------------------------------------------------------

class TestInviteForm:

    def _post(self, auth_client, projcode, email, **extra):
        return auth_client.post(f'/project-invitations/{projcode}/invite', data={
            'email': email, 'first_name': 'Grace', 'last_name': 'Hopper',
            'note': 'NUSD: needs Casper', **extra})

    def _row_for(self, app, email):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=email).one()
            db.session.expunge(row)
            return row

    def test_the_form_offers_the_box_ticked(self, auth_client, led_project):
        html = _html(auth_client.get(f'/project-invitations/{led_project[1]}/invite-form'))
        assert 'name="send_invite"' in html
        box = html[html.index('name="send_invite"'):][:120]
        assert 'checked' in box

    def test_ticked_sends_one_message_and_stamps_the_row(self, auth_client, app, mailer,
                                                         committed, led_project):
        email = _address()
        committed['emails'].append(email)
        resp = self._post(auth_client, led_project[1], email, send_invite='1')
        assert resp.status_code == 200
        assert f'Invitation link sent to {email}' in _html(resp)
        message, = mailer.messages
        assert message.kind == 'account_invite' and message.recipient.address == email
        assert 'NUSD' not in ' '.join(str(v) for v in message.context.values())
        assert '/register/invite/' in message.context['invite_url']
        row = self._row_for(app, email)
        assert row.invite_sent_at is not None
        assert message.dedup_key.endswith(row.invite_sent_at.isoformat(timespec='seconds'))
        assert _tickets(mailer) == [], 'with a link out, NUSD waits for the invitee'

    def test_unticked_sends_no_link_but_files_the_ticket(self, auth_client, app, mailer,
                                                         committed, led_project):
        email = _address()
        committed['emails'].append(email)
        assert self._post(auth_client, led_project[1], email).status_code == 200
        assert mailer.messages == []
        assert self._row_for(app, email).invite_sent_at is None
        ticket, = _tickets(mailer)
        assert ticket.subject == f"New HPC User Request 'Grace Hopper' for {led_project[1]}"
        assert ticket.context['note'] == 'NUSD: needs Casper'
        assert ticket.context['phone'] == '', 'sparse by the sponsor\'s choice'
        assert ticket.requested_by == 'benkirk'

    def test_an_undelivered_mail_leaves_the_row_unstamped(self, auth_client, app, mailer,
                                                          committed, led_project):
        mailer.status = 'failed'
        email = _address()
        committed['emails'].append(email)
        resp = self._post(auth_client, led_project[1], email, send_invite='1')
        assert 'No invitation link sent: relay down' in _html(resp)
        assert self._row_for(app, email).invite_sent_at is None

    def test_a_known_user_gets_no_link(self, auth_client, mailer, led_project, session):
        from sam.core.users import User
        me = User.get_by_username(session, 'benkirk')
        resp = self._post(auth_client, led_project[1], me.primary_email, send_invite='1')
        assert 'already had an account' in _html(resp)
        assert mailer.messages == []


class TestResend:

    def _resend(self, auth_client, projcode, row_id):
        return auth_client.post(
            f'/project-invitations/{projcode}/requests/{row_id}/resend-invite')

    def test_resend_voids_the_old_link(self, auth_client, client, app, mailer, make_invite,
                                       led_project):
        row_id, old = make_invite()
        resp = self._resend(auth_client, led_project[1], row_id)
        assert resp.status_code == 200 and 'Invitation link sent' in _html(resp)
        assert 'closeActiveModal' in resp.headers.get('HX-Trigger', '')
        message, = mailer.messages
        assert _row(app, row_id).invite_sent_at != SENT, "a resend re-stamps"
        assert 'A newer invitation was sent' in _html(client.get(f'/register/invite/{old}'))
        new = message.context['invite_url'].rsplit('/', 1)[1]
        assert 'Accept and continue' in _html(client.get(f'/register/invite/{new}'))

    def test_a_completed_row_is_not_resent(self, auth_client, app, mailer, make_invite,
                                           led_project):
        row_id, _ = make_invite()
        _set(app, row_id, completed_at=datetime.now())
        resp = self._resend(auth_client, led_project[1], row_id)
        assert 'cannot be sent an invitation link' in _html(resp)
        assert mailer.messages == []

    def test_a_row_of_another_project_is_404(self, auth_client, make_invite, snapshot_other):
        row_id, _ = make_invite()
        assert self._resend(auth_client, snapshot_other, row_id).status_code == 404

    def test_the_tab_offers_resend_and_shows_the_badge(self, auth_client, make_invite,
                                                       led_project):
        row_id, _ = make_invite()
        html = _html(auth_client.get(f'/project-invitations/{led_project[1]}/invitations'))
        assert f'/requests/{row_id}/invite-preview' in html
        assert 'awaiting invitee' in html


@pytest.fixture
def snapshot_other(session, led_project):
    from sam.projects.projects import Project
    return (session.query(Project.projcode)
            .filter(Project.is_active, Project.project_id != led_project[0])
            .order_by(Project.project_id).limit(1).scalar())


class TestRosterPaste:

    def test_each_new_person_gets_a_link_and_a_known_user_is_enrolled(
            self, auth_client, app, mailer, make_event, committed, session):
        from sam.core.account_requests import AccountRequest, EventEnrollment
        from sam.core.users import User
        from webapp.extensions import db
        code, event_id = make_event(invite_only=True)
        me = User.get_by_username(session, 'benkirk')
        first, second = _address(), _address()
        roster = f'Ada Lovelace <{first}>\nAlan Turing <{second}>\nBen Kirk <{me.primary_email}>'
        resp = auth_client.post(f'/project-invitations/events/{code}/roster',
                                data={'roster': roster, 'send_invite': '1'})
        html = _html(resp)
        assert resp.status_code == 200 and 'Invitation links sent' in html
        assert sorted(m.recipient.address for m in mailer.messages) == sorted([first, second])
        assert _tickets(mailer) == [], 'links out: the tickets wait for the invitees'
        for message in mailer.messages:
            assert message.context['event_name'] == 'ZZ Invite Workshop'
            assert message.context['event_instructions'] == 'Bring a laptop.'
            assert message.context['accounts_needed_by']
        with app.app_context():
            rows = db.session.query(AccountRequest).filter_by(event_id=event_id).all()
            assert {r.email for r in rows} == {first, second}
            assert all(r.invite_sent_at is not None for r in rows)
            assert db.session.query(EventEnrollment).filter_by(
                event_id=event_id, user_id=me.user_id).count() == 1

    def test_unticked_sends_no_links_but_files_a_ticket_per_person(self, auth_client, mailer,
                                                                    make_event):
        code, _ = make_event()
        first, second = _address(), _address()
        auth_client.post(f'/project-invitations/events/{code}/roster',
                         data={'roster': f'Ada Lovelace <{first}>\nAlan Turing <{second}>'})
        assert mailer.messages == []
        tickets = _tickets(mailer)
        assert sorted(t.context['email'] for t in tickets) == sorted([first, second])
        assert all(t.subject.endswith(f'for {code}') for t in tickets)

    def test_admin_events_copy_sends_too(self, auth_client, mailer, make_event):
        code, _ = make_event()
        html = _html(auth_client.get(f'/admin/htmx/events/{code}/roster-form'))
        assert 'name="send_invite"' in html
        auth_client.post(f'/admin/htmx/events/{code}/roster',
                         data={'roster': f'Ada Lovelace <{_address()}>', 'send_invite': '1'})
        assert len(mailer.messages) == 1

    def test_a_link_for_an_invitation_only_event_completes(
            self, auth_client, client, app, mailer, make_event):
        from sam.core.account_requests import AccountRequest
        from webapp.extensions import db
        code, event_id = make_event(invite_only=True)
        email = _address()
        auth_client.post(f'/project-invitations/events/{code}/roster',
                         data={'roster': f'Ada Lovelace <{email}>', 'send_invite': '1'})
        token = mailer.messages[0].context['invite_url'].rsplit('/', 1)[1]
        client.post(f'/register/invite/{token}/accept', data={'accept': '1'})
        resp = client.post(f'/register/invite/{token}', data=PERSON)
        assert resp.status_code == 302
        assert len(mailer.messages) == 1, 'completing sends no second link'
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=email).one()
            assert row.completed_at is not None and row.event_id == event_id


# -- previews -----------------------------------------------------------------

PLACEHOLDER = 'PREVIEW-link-is-created-when-sent'


@pytest.fixture
def no_token(monkeypatch):
    """A preview must never sign a real link."""
    def _boom(*_a, **_k):
        raise AssertionError('a preview must not sign an invite token')
    monkeypatch.setattr('webapp.register.tokens.invite_token', _boom)


@pytest.fixture
def no_ledger_write(monkeypatch):
    """A preview must never write notification_log."""
    from sam.notify.ledger import NotificationLedger

    def _boom(*_a, **_k):
        raise AssertionError('a preview must not write notification_log')
    monkeypatch.setattr(NotificationLedger, 'record', _boom)


def _rows_for(app, **filters):
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    with app.app_context():
        return db.session.query(AccountRequest).filter_by(**filters).count()


class TestInvitePreview:

    def _preview(self, auth_client, projcode, email, **extra):
        return auth_client.post(f'/project-invitations/{projcode}/invite-preview', data={
            'email': email, 'first_name': 'Grace', 'last_name': 'Hopper', **extra})

    def test_the_form_carries_the_button_and_pane(self, auth_client, led_project):
        html = _html(auth_client.get(f'/project-invitations/{led_project[1]}/invite-form'))
        assert f'hx-post="/project-invitations/{led_project[1]}/invite-preview"' in html
        assert 'id="invitePreviewPane"' in html

    def test_a_new_address_previews_the_mail_and_writes_nothing(
            self, auth_client, app, mailer, no_token, no_ledger_write, led_project):
        email = _address()
        resp = self._preview(auth_client, led_project[1], email, send_invite='1')
        html = _html(resp)
        assert resp.status_code == 200
        assert 'You are invited to request an NCAR HPC account' in html
        assert PLACEHOLDER in html and 'Grace Hopper' in html
        assert 'valid for 30 days' in html
        assert _rows_for(app, email=email) == 0
        assert mailer.messages == []

    def test_an_unticked_box_is_said(self, auth_client, no_token, led_project):
        html = _html(self._preview(auth_client, led_project[1], _address()))
        assert 'The box is unticked' in html

    def test_a_known_user_gets_an_info_panel(self, auth_client, led_project, session):
        from sam.core.users import User
        me = User.get_by_username(session, 'benkirk')
        html = _html(self._preview(auth_client, led_project[1], me.primary_email))
        assert 'already has an active account (benkirk)' in html
        assert 'srcdoc' not in html

    def test_a_waiting_address_gets_an_info_panel(self, app, auth_client, make_invite,
                                                  led_project):
        row_id, _ = make_invite()
        html = _html(self._preview(auth_client, led_project[1], _row(app, row_id).email))
        assert 'already waiting' in html

    def test_an_incomplete_form_asks_for_more(self, auth_client, led_project):
        resp = auth_client.post(f'/project-invitations/{led_project[1]}/invite-preview',
                                data={'email': 'x'})
        assert resp.status_code == 200 and 'Fill in the email' in _html(resp)

    def test_it_is_guarded_like_the_send(self, non_admin_client, led_project):
        resp = non_admin_client.post(
            f'/project-invitations/{led_project[1]}/invite-preview', data={})
        assert resp.status_code in (302, 403)


class TestResendPreview:

    def _get(self, auth_client, projcode, row_id):
        return auth_client.get(f'/project-invitations/{projcode}/requests/{row_id}'
                               f'/invite-preview')

    def test_it_shows_the_mail_and_a_send_button(self, app, auth_client, make_invite,
                                                 led_project, request):
        row_id, _ = make_invite()
        request.getfixturevalue('no_token')     # after the fixture signed its own link
        html = _html(self._get(auth_client, led_project[1], row_id))
        assert PLACEHOLDER in html and 'stops working' in html
        assert f'/requests/{row_id}/resend-invite' in html and 'Send link' in html
        assert 'data-bs-toggle="modal"' not in html
        assert _row(app, row_id).invite_sent_at == SENT

    def test_a_completed_row_offers_no_send(self, app, auth_client, make_invite, led_project):
        row_id, _ = make_invite()
        _set(app, row_id, completed_at=datetime.now())
        html = _html(self._get(auth_client, led_project[1], row_id))
        assert 'cannot be sent an invitation link' in html
        assert 'resend-invite' not in html

    def test_a_row_of_another_project_is_not_found(self, auth_client, make_invite,
                                                   snapshot_other):
        row_id, _ = make_invite()
        resp = self._get(auth_client, snapshot_other, row_id)
        assert resp.status_code == 200 and 'not found' in _html(resp)


class TestRosterPreview:

    def _preview(self, auth_client, code, roster, admin=False, **extra):
        url = (f'/admin/htmx/events/{code}/roster-preview' if admin
               else f'/project-invitations/events/{code}/roster-preview')
        return auth_client.post(url, data={'roster': roster, **extra})

    def test_the_form_carries_the_button_and_pane(self, auth_client, make_event):
        code, _ = make_event()
        html = _html(auth_client.get(f'/project-invitations/events/{code}/roster-form'))
        assert f'/project-invitations/events/{code}/roster-preview' in html
        assert 'id="rosterPreviewPane"' in html

    def test_it_counts_outcomes_and_writes_nothing(
            self, auth_client, app, mailer, no_token, no_ledger_write, make_event, session):
        from sam.core.account_requests import EventEnrollment
        from sam.core.users import User
        from webapp.extensions import db
        code, event_id = make_event()
        me = User.get_by_username(session, 'benkirk')
        first, second = _address(), _address()
        roster = (f'Ada Lovelace <{first}>\nAlan Turing <{second}>\n'
                  f'Ben Kirk <{me.primary_email}>')
        html = _html(self._preview(auth_client, code, roster, send_invite='1'))
        assert '3 people: 2 get a link, 1 already known (enrolled, no email).' in html
        assert 'name="preview_recipient"' in html
        assert _rows_for(app, event_id=event_id) == 0
        with app.app_context():
            assert db.session.query(EventEnrollment).filter_by(event_id=event_id).count() == 0
        assert mailer.messages == []

    def test_the_picker_round_trips(self, auth_client, no_token, make_event):
        code, _ = make_event()
        first, second = _address(), _address()
        roster = f'Ada Lovelace <{first}>\nAlan Turing <{second}>'
        html = _html(self._preview(auth_client, code, roster, preview_recipient=second))
        assert '<option value="1" selected' in html
        assert second in html.split('>To<')[1].split('</div>')[0]

    def test_the_admin_copy_previews_too(self, auth_client, no_token, make_event):
        code, _ = make_event()
        html = _html(self._preview(auth_client, code, f'Ada Lovelace <{_address()}>',
                                   admin=True))
        assert PLACEHOLDER in html

    def test_links_off_is_explained(self, auth_client, app, make_event, monkeypatch):
        code, _ = make_event()
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', False)
        html = _html(self._preview(auth_client, code, 'Ada Lovelace <a@b.edu>', admin=True))
        assert 'Invitation links are off' in html

    def test_bad_lines_are_named(self, auth_client, make_event):
        code, _ = make_event()
        html = _html(self._preview(auth_client, code, 'just-an-address@example.edu'))
        assert 'Fix these lines first' in html and 'line 1' in html


# -- invitation-only events ---------------------------------------------------

@pytest.fixture
def signed_in(app, session):
    from sam import User
    user = User.get_by_username(session, 'benkirk')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.user_id)
        sess['_fresh'] = True
    return client


class TestInvitationOnlyEvents:

    def test_the_code_link_is_refused(self, client, make_event):
        code, _ = make_event(invite_only=True)
        html = _html(client.get(f'/register/{code}'))
        assert f'{code} is by invitation only' in html

    def test_a_posted_code_is_refused(self, client, make_event, no_mail):
        code, _ = make_event(invite_only=True)
        data = {**PERSON, 'email': _address(), 'event_code': code}
        html = _html(client.post('/register/', data=data))
        assert 'by invitation only' in html

    def test_signed_in_self_enroll_is_refused(self, signed_in, make_event):
        code, _ = make_event(invite_only=True)
        assert 'by invitation only' in _html(signed_in.get(f'/register/{code}'))
        assert 'by invitation only' in _html(signed_in.post(f'/register/{code}/enroll'))

    def test_it_is_left_out_of_the_picker(self, client, monkeypatch):
        monkeypatch.setattr('webapp.register.blueprint.upcoming_events_data', lambda: [
            {'event_code': 'ZZ-OPEN', 'name': 'Open', 'invite_only': False},
            {'event_code': 'ZZ-CLOSED', 'name': 'Roster only', 'invite_only': True}])
        html = _html(client.get('/register/'))
        assert 'value="ZZ-OPEN"' in html and 'ZZ-CLOSED' not in html

    def test_the_public_card_says_by_invitation(self, client, app, monkeypatch):
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', True)
        monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data', lambda: [{
            'event_id': 1, 'event_code': 'ZZ-ROSTER', 'name': 'Roster only', 'instructions': None,
            'project_code': 'SCSG0001', 'accounts_needed_by': date(2030, 1, 1),
            'closes_at': None, 'invite_only': True}])
        html = _html(client.get('/status/events'))
        assert 'By invitation' in html and '/register/ZZ-ROSTER' not in html

    def test_the_lifecycle_form_round_trips_it(self, auth_client, app, make_event):
        from sam.core.account_requests import AccountRequestEvent
        from webapp.extensions import db
        code, _ = make_event()

        def _flag():
            with app.app_context():
                return db.session.query(AccountRequestEvent).filter_by(
                    event_code=code).one().invite_only

        html = _html(auth_client.get(f'/project-invitations/events/{code}/edit-form'))
        assert 'name="invite_only_present"' in html and 'name="invite_only"' in html
        auth_client.put(f'/project-invitations/events/{code}',
                        data={'invite_only_present': '1', 'invite_only': '1'})
        assert _flag() is True
        auth_client.put(f'/project-invitations/events/{code}', data={'name': 'Renamed'})
        assert _flag() is True, 'an edit that never drew the box keeps it'
        auth_client.put(f'/admin/events/{code}', data={'invite_only_present': '1'})
        assert _flag() is False


# -- self-registration --------------------------------------------------------

class TestSelfRegistrationStampsTheAgreement:

    @pytest.fixture(scope='class')
    def gate_app(self, test_db_url, status_db_url):
        from webapp.run import create_app
        return create_app(config_overrides={
            'SQLALCHEMY_DATABASE_URI': test_db_url,
            'SQLALCHEMY_BINDS': {'system_status': status_db_url},
            'ACCOUNT_REGISTRATION_GATE_ENABLED': True,
        })

    def test_submit_records_the_sha_and_the_accept_time(self, gate_app, app, committed,
                                                        monkeypatch):
        from sam.core.account_requests import AccountRequest
        from sam.notify import NotifyConfig, Notifier, NullTransport
        from webapp.extensions import db
        from webapp.register.eula import eula_sha
        monkeypatch.setattr('webapp.register.blueprint.get_notifier',
                            lambda **_: Notifier(config=NotifyConfig(enabled=False),
                                                 transport=NullTransport(), ledger=None))
        email = _address()
        committed['emails'].append(email)
        client = gate_app.test_client()
        client.post('/register/accept', data={'accept': '1'})
        resp = client.post('/register/', data={**PERSON, 'email': email,
                                               'purpose_note': 'Running the class.'})
        assert resp.status_code == 302
        with app.app_context():
            row = db.session.query(AccountRequest).filter_by(email=email).one()
            assert row.eula_sha == eula_sha() and row.eula_accepted_at is not None


# -- who may skip the link -----------------------------------------------------

@pytest.fixture
def lead_client(auth_client, monkeypatch):
    """benkirk as a plain project lead: no MANAGE_ACCOUNT_REQUESTS, no SYSTEM_ADMIN."""
    from webapp.utils import rbac
    from webapp.utils.rbac import Permission
    real = rbac.get_user_permissions
    monkeypatch.setattr(
        rbac, 'get_user_permissions',
        lambda user, *a, **k: {p for p in real(user, *a, **k)
                               if p not in (Permission.MANAGE_ACCOUNT_REQUESTS,
                                            Permission.SYSTEM_ADMIN)})
    return auth_client


@pytest.fixture
def operator_client(auth_client, monkeypatch):
    from webapp.utils import rbac
    from webapp.utils.rbac import Permission
    real = rbac.get_user_permissions
    monkeypatch.setattr(rbac, 'get_user_permissions',
                        lambda user, *a, **k: real(user, *a, **k) | {Permission.MANAGE_ACCOUNT_REQUESTS})
    return auth_client


class TestOnlyTheAccountTeamMaySkipTheLink:
    """A project lead never sees the box and always sends the link; the
    account team sees it ticked and may untick it (a sparse ticket they chase)."""

    def test_a_lead_gets_no_box_and_the_link_goes_regardless(self, lead_client, app, mailer,
                                                              committed, led_project):
        html = _html(lead_client.get(f'/project-invitations/{led_project[1]}/invite-form'))
        assert 'name="send_invite"' not in html
        assert 'will be emailed a link' in html
        email = _address()
        committed['emails'].append(email)
        resp = lead_client.post(f'/project-invitations/{led_project[1]}/invite', data={
            'email': email, 'first_name': 'Grace', 'last_name': 'Hopper'})
        assert resp.status_code == 200
        message, = mailer.messages
        assert message.kind == 'account_invite' and message.recipient.address == email
        assert _tickets(mailer) == []

    def test_the_account_team_sees_the_box_ticked(self, operator_client, led_project):
        html = _html(operator_client.get(f'/project-invitations/{led_project[1]}/invite-form'))
        box = html[html.index('name="send_invite"'):][:120]
        assert 'checked' in box

    def test_a_lead_pasting_a_roster_sends_every_link(self, lead_client, mailer, make_event):
        code, _ = make_event()
        html = _html(lead_client.get(f'/project-invitations/events/{code}/roster-form'))
        assert 'name="send_invite"' not in html and 'emailed a link' in html
        first, second = _address(), _address()
        lead_client.post(f'/project-invitations/events/{code}/roster',
                         data={'roster': f'Ada Lovelace <{first}>\nAlan Turing <{second}>'})
        assert sorted(m.recipient.address for m in mailer.messages) == sorted([first, second])
        assert _tickets(mailer) == []
