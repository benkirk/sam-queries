"""learn_ticket_keys / refresh_ticket_status / sync_tickets against the fake."""
from datetime import datetime, timedelta

import pytest

from factories import make_account_request, make_external_ticket, make_notification_log
from factories.tickets import FakeTicketProvider
from sam import ExternalTicket
from sam.integration.tickets import TicketRejected, TicketSourceUnavailable
from sam.integration.tickets.learn import (describe, learn_ticket_keys,
                                           refresh_ticket_status, sync_tickets)
from sam.queries.account_notices import ticket_handle

CLOCK = datetime.now().replace(microsecond=0)


def _mailed(session, *, age_days=1):
    row = make_account_request(session)
    make_notification_log(session, kind='account_ticket', status='sent',
                          entity_type='account_request', entity_id=row.account_request_id,
                          dedup_key=f'account_ticket:{row.account_request_id}',
                          age=timedelta(days=age_days))
    return row


def _links(session, row):
    return session.query(ExternalTicket).filter_by(
        entity_type='account_request', entity_id=row.account_request_id).all()


class TestLearn:
    def test_a_hit_inserts_a_learned_row(self, session, fake_provider):
        row = _mailed(session)
        fake_provider.add(ticket_handle(row.account_request_id), key='FAKE-7',
                          status='Waiting for support')
        counts = learn_ticket_keys(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['learned'] >= 1
        link, = _links(session, row)
        assert (link.provider, link.ticket_key, link.origin, link.status) == (
            'fake', 'FAKE-7', 'learned', 'Waiting for support')
        assert link.requested_by == 'task:account_requests_reconcile'
        assert link.synced_at == CLOCK and link.closed_at is None

    def test_a_miss_writes_nothing(self, session, fake_provider):
        row = _mailed(session)
        counts = learn_ticket_keys(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['missed'] >= 1
        assert _links(session, row) == []

    def test_a_linked_row_is_not_looked_up_again(self, session, fake_provider):
        row = _mailed(session)
        make_external_ticket(session, entity_id=row.account_request_id)
        seen = []
        real_find = fake_provider.find
        fake_provider.find = lambda h: seen.append(h) or real_find(h)
        learn_ticket_keys(session, fake_provider, clock=CLOCK, limit=500)
        assert ticket_handle(row.account_request_id) not in seen
        assert len(_links(session, row)) == 1

    def test_the_cap(self, session, fake_provider):
        for _ in range(3):
            _mailed(session)
        counts = learn_ticket_keys(session, fake_provider, clock=CLOCK, limit=2)
        assert counts['checked'] == 2

    def test_a_mid_loop_failure_stops_and_keeps_earlier_stamps(self, session):
        provider = FakeTicketProvider()
        first, second = _mailed(session), _mailed(session)
        provider.add(ticket_handle(second.account_request_id), key='FAKE-2')
        real_find = provider.find

        def find(handle):
            if handle == ticket_handle(first.account_request_id):
                raise TicketSourceUnavailable('down')
            return real_find(handle)
        provider.find = find
        counts = learn_ticket_keys(session, provider, clock=CLOCK, limit=2)
        assert counts['error'] == 'down'
        assert [t.ticket_key for t in _links(session, second)] == ['FAKE-2'], (
            'newest first: the second row was learned before the first failed')

    def test_a_key_already_linked_elsewhere_is_skipped(self, session, fake_provider):
        row = _mailed(session)
        make_external_ticket(session, entity_id=-99, provider='fake', ticket_key='FAKE-DUP')
        fake_provider.add(ticket_handle(row.account_request_id), key='FAKE-DUP')
        counts = learn_ticket_keys(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['error'] == ''
        assert _links(session, row) == []


class TestRefresh:
    def test_closed_is_stamped_once_and_synced_every_time(self, session, fake_provider):
        link = make_external_ticket(session, entity_id=-50, provider='fake', ticket_key='FAKE-50')
        fake_provider.add('h', key='FAKE-50', status='In Progress')
        counts = refresh_ticket_status(session, fake_provider, clock=CLOCK, limit=500)
        assert (link.status, link.closed_at, link.synced_at) == ('In Progress', None, CLOCK)
        assert counts['closed'] == 0

        fake_provider.set_status('FAKE-50', 'Resolved', closed=True)
        later = CLOCK + timedelta(hours=7)
        counts = refresh_ticket_status(session, fake_provider, clock=later, limit=500)
        assert (link.status, link.closed_at, link.synced_at) == ('Resolved', later, later)
        assert counts['closed'] == 1

        again = later + timedelta(hours=7)
        refresh_ticket_status(session, fake_provider, clock=again, limit=500)
        assert link.synced_at == later, 'a closed ticket is not re-read'

    def test_the_throttle(self, session, fake_provider):
        make_external_ticket(session, entity_id=-51, provider='fake', ticket_key='FAKE-51',
                             synced_at=CLOCK - timedelta(hours=1))
        fake_provider.add('h', key='FAKE-51')
        counts = refresh_ticket_status(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['checked'] == 0

    def test_a_vanished_ticket_is_stamped_read(self, session, fake_provider):
        link = make_external_ticket(session, entity_id=-52, provider='fake',
                                    ticket_key='FAKE-GONE', status='Open')
        counts = refresh_ticket_status(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['missing'] == 1
        assert (link.status, link.synced_at) == ('Open', CLOCK)

    def test_a_failure_stops(self, session, fake_provider):
        make_external_ticket(session, entity_id=-53, provider='fake', ticket_key='FAKE-53')
        fake_provider.raise_with = TicketRejected('token rejected', status=401)
        counts = refresh_ticket_status(session, fake_provider, clock=CLOCK, limit=500)
        assert counts['error'] == 'token rejected'


class TestSync:
    def test_the_suite_selects_mail(self, session):
        result = sync_tickets(session, clock=CLOCK)
        assert result == {'skipped': True, 'reason': 'TICKET_PROVIDER selects mail'}
        assert describe(result) == 'tickets skipped (TICKET_PROVIDER selects mail)'

    def test_unconfigured_is_skipped_before_any_row(self, session):
        provider = FakeTicketProvider(configured=False)
        result = sync_tickets(session, clock=CLOCK, provider=provider)
        assert result['skipped'] and 'not configured' in result['reason']
        assert provider.calls == []

    def test_jira_selected_but_reads_off(self, session, monkeypatch):
        monkeypatch.setenv('TICKET_PROVIDER', 'jira-servicedesk')
        result = sync_tickets(session, clock=CLOCK)
        assert result['skipped'] and 'jira-servicedesk' in result['reason']

    def test_an_unknown_provider_name_is_skipped(self, session, monkeypatch):
        monkeypatch.setenv('TICKET_PROVIDER', 'jria')
        result = sync_tickets(session, clock=CLOCK)
        assert result['skipped'] and 'unknown TICKET_PROVIDER' in result['reason']

    def test_a_learn_failure_skips_the_refresh(self, session, fake_provider):
        _mailed(session)
        fake_provider.raise_with = TicketSourceUnavailable('down')
        result = sync_tickets(session, clock=CLOCK, provider=fake_provider)
        assert result['learn']['error'] == 'down'
        assert result['refresh']['error'] == 'not attempted'
        assert describe(result).endswith('; down')

    def test_configured(self, session, fake_provider):
        row = _mailed(session)
        fake_provider.add(ticket_handle(row.account_request_id))
        result = sync_tickets(session, clock=CLOCK, provider=fake_provider, limit=500)
        assert not result['skipped'] and result['learn']['learned'] >= 1
        assert describe(result).startswith('tickets: ')
