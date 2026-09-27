"""ExternalTicket and the query helpers over it."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from factories import make_account_request, make_external_ticket, make_notification_log
from sam import ExternalTicket
from sam.integration.tickets.queries import (learn_candidates, refresh_candidates,
                                             tickets_for)
from sam.queries.account_requests import events_for, request_views


class TestExternalTicket:
    def test_create_stamps_the_app_clock(self, session):
        row = ExternalTicket.create(session, provider='jira-servicedesk', ticket_key=' RC-1x ',
                                    entity_type='account_request', entity_id=-1,
                                    origin='created', requested_by='benkirk',
                                    status='Waiting for support', synced_at=datetime(2026, 1, 1))
        assert row.external_ticket_id
        assert row.ticket_key == 'RC-1x'
        assert row.closed_at is None and row.is_active
        assert abs(row.creation_time - datetime.now()) < timedelta(minutes=1)

    def test_a_duplicate_provider_key_is_refused(self, session):
        first = make_external_ticket(session, entity_id=-2)
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                make_external_ticket(session, entity_id=-3, ticket_key=first.ticket_key)

    def test_the_same_key_under_another_provider_is_fine(self, session):
        first = make_external_ticket(session, entity_id=-2)
        make_external_ticket(session, entity_id=-2, provider='other', ticket_key=first.ticket_key)

    @pytest.mark.parametrize('kwargs', [{'origin': 'guessed'}, {'ticket_key': ' '},
                                        {'requested_by': ''}])
    def test_validation(self, session, kwargs):
        base = dict(provider='p', ticket_key='K-1', entity_type='account_request',
                    entity_id=1, origin='learned', requested_by='x')
        with pytest.raises(ValueError):
            ExternalTicket.create(session, **{**base, **kwargs})

    def test_mark_synced_stamps_closed_once(self, session):
        row = make_external_ticket(session, entity_id=-4)
        t1, t2 = datetime(2026, 9, 1, 10), datetime(2026, 9, 2, 10)
        row.mark_synced(status='Resolved', closed=True, when=t1)
        row.mark_synced(status='Closed', closed=True, when=t2)
        assert (row.status, row.closed_at, row.synced_at) == ('Closed', t1, t2)
        assert not row.is_active
        assert session.query(ExternalTicket).filter(
            ExternalTicket.external_ticket_id == row.external_ticket_id,
            ~ExternalTicket.is_active).count() == 1


class TestQueries:
    def test_tickets_for_is_oldest_first_per_entity(self, session):
        a = make_external_ticket(session, entity_id=-10, when=datetime(2026, 9, 2))
        b = make_external_ticket(session, entity_id=-10, when=datetime(2026, 9, 1))
        c = make_external_ticket(session, entity_id=-11)
        found = tickets_for(session, 'account_request', [-10, -11, -12])
        assert [t.ticket_key for t in found[-10]] == [b.ticket_key, a.ticket_key]
        assert [t.ticket_key for t in found[-11]] == [c.ticket_key]
        assert -12 not in found
        assert tickets_for(session, 'account_request', []) == {}

    def test_learn_candidates(self, session):
        now = datetime.now()
        fresh = make_account_request(session)
        linked = make_account_request(session)
        old = make_account_request(session)
        failed = make_account_request(session)
        for row, age, status in ((fresh, 1, 'sent'), (linked, 1, 'sent'),
                                 (old, 61, 'sent'), (failed, 1, 'failed')):
            make_notification_log(session, kind='account_ticket', status=status,
                                  entity_type='account_request',
                                  entity_id=row.account_request_id,
                                  dedup_key=f'account_ticket:{row.account_request_id}',
                                  age=timedelta(days=age))
        make_external_ticket(session, entity_id=linked.account_request_id)
        ids = learn_candidates(session, now=now, max_age_days=60, limit=500)
        assert fresh.account_request_id in ids
        for excluded in (linked, old, failed):
            assert excluded.account_request_id not in ids

    def test_refresh_candidates(self, session):
        now = datetime.now()
        never = make_external_ticket(session, entity_id=-20, provider='fakep')
        stale = make_external_ticket(session, entity_id=-21, provider='fakep',
                                     synced_at=now - timedelta(hours=7))
        make_external_ticket(session, entity_id=-22, provider='fakep',
                             synced_at=now - timedelta(hours=1))
        make_external_ticket(session, entity_id=-23, provider='fakep', closed=True)
        make_external_ticket(session, entity_id=-24, provider='other')
        rows = refresh_candidates(session, provider='fakep', now=now,
                                  stale_after=timedelta(hours=6), limit=10)
        assert [r.external_ticket_id for r in rows] == [never.external_ticket_id,
                                                        stale.external_ticket_id]
        assert len(refresh_candidates(session, provider='fakep', now=now,
                                      stale_after=timedelta(hours=6), limit=1)) == 1


class TestRequestViews:
    def test_views_carry_tickets_with_derived_urls(self, session):
        row = make_account_request(session)
        make_external_ticket(session, entity_id=row.account_request_id, ticket_key='RC-77x',
                             status='In Progress')
        make_external_ticket(session, entity_id=row.account_request_id, provider='retired')
        view, = request_views(session, [row], resolutions={},
                              events=events_for(session, [row]))
        first, second = view['tickets']
        assert first['key'] == 'RC-77x'
        assert first['url'] == 'https://ithelp.ucar.edu/browse/RC-77x'
        assert first['status'] == 'In Progress' and first['origin'] == 'learned'
        assert second['url'] == ''

    def test_no_tickets_is_an_empty_list(self, session):
        row = make_account_request(session)
        view, = request_views(session, [row], resolutions={}, events={})
        assert view['tickets'] == []
