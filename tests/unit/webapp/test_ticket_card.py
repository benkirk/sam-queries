"""The ticket link on Admin -> Accounts and the Configuration tile."""
from datetime import datetime

import pytest

FRAGMENT = '/admin/account-requests/fragment'
CONFIG_URL = '/admin/htmx/configuration'


@pytest.fixture
def ticketed_request(app):
    """A committed open request with two committed tickets, the first closed."""
    from sqlalchemy.orm import Session

    from sam import ExternalTicket
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db

    with app.app_context():
        row = AccountRequest.create(
            db.session, email='zz.ticket.card@example.invalid', first_name='Tick',
            last_name='Card', purpose='standalone', created_by='operator1',
            verified_by='operator1')
        db.session.commit()
        rid = row.account_request_id
        with Session(db.engine) as s:
            ExternalTicket.create(s, provider='jira-servicedesk', ticket_key=f'ZZ-{rid}',
                                  entity_type='account_request', entity_id=rid,
                                  origin='created', requested_by='operator1',
                                  status='Resolved', closed=True, when=datetime(2026, 9, 1))
            ExternalTicket.create(s, provider='jira-servicedesk', ticket_key=f'ZZ-{rid}B',
                                  entity_type='account_request', entity_id=rid,
                                  origin='learned', requested_by='task:x')
            s.commit()
    yield rid
    with app.app_context():
        db.session.query(ExternalTicket).filter(
            ExternalTicket.entity_type == 'account_request',
            ExternalTicket.entity_id == rid).delete()
        db.session.query(AccountRequest).filter(
            AccountRequest.account_request_id == rid).delete()
        db.session.commit()


def test_the_card_shows_the_key_the_link_and_the_closed_warning(auth_client, ticketed_request):
    rid = ticketed_request
    resp = auth_client.get(f'{FRAGMENT}?search=zz.ticket.card')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    link = f'href="https://ithelp.ucar.edu/browse/ZZ-{rid}"'
    assert html.count(link) == 2, 'the key links from the status cell and the details'
    assert '+1' in html, 'the second ticket is counted'
    assert 'closed without an account' in html
    assert 'filed by SAM' in html and 'found after the mail' in html
    assert '>duplicate<' in html


def test_the_row_is_icons_with_words_on_hover(auth_client, ticketed_request):
    html = auth_client.get(f'{FRAGMENT}?search=zz.ticket.card').get_data(as_text=True)
    for label in ('Standalone: an account, no project',
                  'Invited by a sponsor or operator', 'No account yet'):
        assert f'aria-label="{label}"' in html, label
    assert 'mailto:zz.ticket.card@example.invalid' in html, 'the full address is in the details'


def test_a_request_without_tickets_renders_no_ticket_row(auth_client):
    resp = auth_client.get(FRAGMENT)
    assert resp.status_code == 200
    assert 'closed without an account' not in resp.get_data(as_text=True)


class TestConfigurationTile:
    def test_the_suite_default_reads_mail_only(self, auth_client):
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        section = html[html.index('Help-desk tickets'):][:3000]
        assert 'TICKET_PROVIDER' in section and 'mail only' in section
        assert 'Filed by SAM' in section

    def test_the_armed_state_never_shows_the_token(self, auth_client, monkeypatch):
        for key, value in (('TICKET_PROVIDER', 'jira-servicedesk'), ('JIRA_ENABLED', '1'),
                           ('JIRA_WRITE_ENABLED', '1'), ('JIRA_TOKEN', 'zz-sekrit-pat')):
            monkeypatch.setenv(key, value)
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        assert 'zz-sekrit-pat' not in html
        section = html[html.index('Help-desk tickets'):][:3000]
        assert 'API create, mail fallback' in section and 'RC / 3 / 20' in section

    def test_reads_alone_learn_keys(self, auth_client, monkeypatch):
        monkeypatch.setenv('JIRA_ENABLED', '1')
        monkeypatch.setenv('JIRA_TOKEN', 'not-real')
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        assert 'mail, keys learned hourly' in html

    def test_a_typo_is_shown_not_raised(self, auth_client, monkeypatch):
        monkeypatch.setenv('TICKET_PROVIDER', 'jria')
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        assert 'unknown TICKET_PROVIDER' in html
