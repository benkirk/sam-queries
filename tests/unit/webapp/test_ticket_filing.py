"""send_ticket's provider path: file once, link the key, fall back to mail.

Drives ``send_ticket`` directly with a fake provider and a fake ledger. The
row is flushed in ``db.session`` and rolled back; the ``external_ticket`` link
is written on its own committed session (as in production), so it is deleted
by entity id afterwards.
"""
from dataclasses import dataclass, field
from typing import List

import pytest
from sqlalchemy.orm import Session

from factories.tickets import FakeTicketProvider
from sam import ExternalTicket
from sam.integration.tickets import (DEFAULT_AUTOMATION_NOTE, TicketRejected,
                                     TicketSourceUnavailable)
from sam.notify.base import DeliveryResult


@dataclass
class _Ledger:
    sent_keys: set = field(default_factory=set)
    rows: List[dict] = field(default_factory=list)

    def already_sent(self, key):
        return key in self.sent_keys

    def record(self, message, *, status, transport, detail=None, rendered=None):
        self.rows.append({'status': status, 'transport': transport, 'detail': detail,
                          'channel': message.recipient.channel.value,
                          'recipient': message.recipient.address, 'key': message.dedup_key,
                          'template': rendered.template_text if rendered else None})
        return len(self.rows)

    def resolve(self, log_id, *, status, detail=None):
        self.rows[log_id - 1].update(status=status, detail=detail, resolved=True)


class _Mailer:
    def __init__(self):
        self.ledger = _Ledger()
        self.mailed = []

    def send(self, message, **_):
        self.mailed.append(message)
        return DeliveryResult(ok=True, status='sent', message=message)


@pytest.fixture
def mailer(app, monkeypatch):
    from sam.notify import Notifier, NotifyConfig, NullTransport
    stub = _Mailer()

    def get_notifier(*, read_only=False):
        if read_only:
            return Notifier(config=NotifyConfig(enabled=False), transport=NullTransport(),
                            ledger=None)
        return stub
    monkeypatch.setattr('webapp.register.handoff_mail.get_notifier', get_notifier)
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', 'help@example.invalid')
    return stub


@pytest.fixture
def provider(monkeypatch):
    fake = FakeTicketProvider()
    monkeypatch.setattr('sam.integration.tickets.registry.provider_from_environment',
                        lambda **_: fake)
    return fake


@pytest.fixture
def ctx(app):
    """A request context with a flushed, uncommitted row; links cleaned up by id."""
    from sam.core.account_requests import AccountRequest
    from webapp.extensions import db
    made = []

    def build(email='zz.ticket.filing@example.invalid'):
        row = AccountRequest.create(db.session, email=email, first_name='Tick',
                                    last_name='Et', purpose='standalone', created_by='benkirk')
        made.append(row.account_request_id)
        return row

    with app.test_request_context('/'):
        yield build
        db.session.rollback()
        with Session(db.engine) as s:
            s.query(ExternalTicket).filter(ExternalTicket.entity_type == 'account_request',
                                           ExternalTicket.entity_id.in_(made)).delete()
            s.commit()


def _links(app, request_id):
    from webapp.extensions import db
    with Session(db.engine) as s:
        return [(t.provider, t.origin, t.requested_by) for t in s.query(ExternalTicket)
                .filter_by(entity_type='account_request', entity_id=request_id)]


def test_files_once_through_the_provider_and_links_the_key(app, ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    row = ctx()
    rid = row.account_request_id
    result = send_ticket(row, requested_by='oper')
    assert (result.status, result.detail) == ('sent', 'FAKE-1')

    draft, = provider.created
    assert draft.handle == f'SAM-AR-{rid}'
    assert draft.summary.endswith(f'[SAM-AR-{rid}]')
    assert f'SAM request:     #{rid}\n' in draft.body, 'the URL rides outside the body'
    assert draft.link_url.endswith(f'/admin/account-requests?request={rid}')
    key, note, internal = provider.comments[0]
    assert key == 'FAKE-1' and internal
    assert note.startswith(DEFAULT_AUTOMATION_NOTE) and draft.link_url in note

    entry, = mailer.ledger.rows
    assert entry == {'status': 'sent', 'transport': 'fake', 'detail': 'FAKE-1',
                     'channel': 'ticket', 'recipient': 'fake',
                     'key': f'account_ticket:{rid}', 'template': 'account_ticket.txt',
                     'resolved': True}
    assert _links(app, rid) == [('fake', 'created', 'oper')]
    assert mailer.mailed == []


def test_a_ticket_a_mail_already_filed_is_linked_not_duplicated(app, ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    row = ctx()
    provider.add(f'SAM-AR-{row.account_request_id}', key='FAKE-OLD')
    result = send_ticket(row)
    assert result.detail == 'FAKE-OLD'
    assert provider.created == []
    assert mailer.ledger.rows[0]['status'] == 'sent'
    assert mailer.ledger.rows[0]['detail'] == 'found FAKE-OLD'
    assert _links(app, row.account_request_id) == [('fake', 'learned', 'self')]


def test_already_sent_in_either_era_short_circuits(ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    row = ctx()
    mailer.ledger.sent_keys.add(f'account_ticket:{row.account_request_id}')
    assert send_ticket(row).status == 'suppressed'
    assert provider.calls == [] and mailer.mailed == [] and mailer.ledger.rows == []


def test_an_outage_falls_back_to_mail_for_the_rest_of_the_request(app, ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    row, other = ctx(), ctx('zz.ticket.filing2@example.invalid')
    provider.raise_with = TicketSourceUnavailable('ithelp down')
    provider.raise_on = ('create',)
    send_ticket(row)
    queued, = mailer.ledger.rows
    assert queued['status'] == 'failed' and 'ithelp down' in queued['detail']
    assert [m.dedup_key for m in mailer.mailed] == [f'account_ticket:{row.account_request_id}']
    assert mailer.mailed[0].context['queue_url'], 'the mail carries its link'

    calls = len(provider.calls)
    send_ticket(other)
    assert len(provider.calls) == calls, 'the second filing skips the provider'
    assert len(mailer.mailed) == 2
    assert _links(app, row.account_request_id) == []


def test_a_create_that_timed_out_after_filing_is_linked_not_mailed(app, ctx, mailer, provider):
    """The desk commits, then the 5 s budget expires: the one duplicate-ticket path."""
    from webapp.register.handoff_mail import send_ticket

    def create_then_time_out(draft):
        provider.add(draft.handle, key='FAKE-LATE')
        raise TicketSourceUnavailable('POST ...: Read timed out')
    provider.create = create_then_time_out
    row = ctx()
    result = send_ticket(row, requested_by='oper')
    assert (result.status, result.detail) == ('sent', 'FAKE-LATE')
    assert mailer.mailed == []
    assert mailer.ledger.rows[0]['status'] == 'sent'
    assert _links(app, row.account_request_id) == [('fake', 'created', 'oper')]
    assert [op for op, _ in provider.calls] == ['find', 'find'], 'asked once more, then stopped'


def test_a_rejected_create_mails_without_asking_again(ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    provider.raise_with = TicketRejected('bad field', status=400)
    provider.raise_on = ('create',)
    send_ticket(ctx())
    assert [op for op, _ in provider.calls] == ['find', 'create']
    assert mailer.ledger.rows[0]['status'] == 'failed' and len(mailer.mailed) == 1


def test_one_provider_serves_the_whole_request(ctx, mailer, monkeypatch):
    from webapp.register.handoff_mail import send_ticket
    built = []

    def build(**_):
        built.append(FakeTicketProvider(prefix='FAKE-G'))
        return built[-1]
    monkeypatch.setattr('sam.integration.tickets.registry.provider_from_environment', build)
    send_ticket(ctx())
    send_ticket(ctx('zz.ticket.filing2@example.invalid'))
    assert len(built) == 1 and len(built[0].created) == 2


def test_a_provider_bug_on_create_falls_back_too(ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    provider.raise_with = KeyError('boom')
    provider.raise_on = ('create',)
    send_ticket(ctx())
    assert mailer.ledger.rows[0]['status'] == 'failed'
    assert len(mailer.mailed) == 1


def test_a_find_outage_falls_back_before_any_ledger_row(ctx, mailer, provider):
    from webapp.register.handoff_mail import send_ticket
    provider.raise_with = TicketSourceUnavailable('down')
    send_ticket(ctx())
    assert mailer.ledger.rows == [] and len(mailer.mailed) == 1


def test_write_off_means_the_mail_path(ctx, mailer, monkeypatch):
    from webapp.register.handoff_mail import send_ticket
    fake = FakeTicketProvider(write_configured=False)
    monkeypatch.setattr('sam.integration.tickets.registry.provider_from_environment',
                        lambda **_: fake)
    send_ticket(ctx())
    assert fake.calls == [] and len(mailer.mailed) == 1


def test_an_unknown_provider_name_means_the_mail_path(ctx, mailer, monkeypatch):
    from webapp.register.handoff_mail import send_ticket
    monkeypatch.setenv('TICKET_PROVIDER', 'jria')
    send_ticket(ctx())
    assert len(mailer.mailed) == 1


def test_the_suite_default_is_the_mail_path(ctx, mailer):
    from webapp.register.handoff_mail import ticket_provider, send_ticket
    assert ticket_provider() is None
    send_ticket(ctx())
    assert mailer.mailed[0].recipient.address == 'help@example.invalid'


def test_the_webapp_budget_is_one_short_attempt(ctx, monkeypatch):
    from webapp.register.handoff_mail import ticket_provider
    for key, value in (('TICKET_PROVIDER', 'jira-servicedesk'), ('JIRA_ENABLED', '1'),
                       ('JIRA_WRITE_ENABLED', '1'), ('JIRA_TOKEN', 'not-real')):
        monkeypatch.setenv(key, value)
    provider = ticket_provider()
    assert provider.name == 'jira-servicedesk'
    assert (provider.config.timeout, provider.config.max_retries) == (5.0, 1)
