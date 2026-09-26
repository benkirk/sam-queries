"""The public Upcoming Events card on /status/events.

The listing is patched rather than committed: the route reads through
Flask-SQLAlchemy's own session, and the query itself is covered in
test_account_requests_queries.py.
"""

from datetime import date

import pytest
from sqlalchemy.exc import OperationalError


EVENT = {'event_id': 424242, 'event_code': 'ZZ-CARD-TEST', 'name': 'Card test workshop',
         'instructions': 'Bring a laptop.', 'project_code': 'SCSG0001',
         'accounts_needed_by': date(2030, 1, 15), 'closes_at': None}


@pytest.fixture
def listing(app, monkeypatch):
    """Registration on, one listed event."""
    monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', True)
    monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data',
                        lambda: [EVENT])


def _body(client):
    resp = client.get('/status/events')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


class TestTheCard:

    def test_anonymous_sees_the_card_above_the_reservations(self, client, listing):
        body = _body(client)
        assert 'Card test workshop' in body and 'Bring a laptop.' in body
        assert '/register/ZZ-CARD-TEST' in body
        assert body.index('Upcoming Events') < body.index('Maintenance &amp; Reservations')

    def test_anonymous_copy_follows_the_login_gate(self, client, listing, app, monkeypatch):
        monkeypatch.setitem(app.config, 'ACCOUNT_REGISTRATION_LOGIN_REQUIRED', True)
        assert 'Sign in to register' in _body(client)
        monkeypatch.setitem(app.config, 'ACCOUNT_REGISTRATION_LOGIN_REQUIRED', False)
        assert 'Sign in to register' not in _body(client)

    def test_a_signed_in_enrollee_gets_the_badge_not_the_button(self, auth_client, listing,
                                                                 monkeypatch):
        monkeypatch.setattr('sam.queries.account_requests.enrolled_event_ids',
                            lambda session, user_id: {EVENT['event_id']})
        body = _body(auth_client)
        assert 'Enrolled' in body
        assert 'fa-user-plus me-1' not in body

    def test_no_card_without_a_listed_event(self, client, app, monkeypatch):
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', True)
        monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data',
                            lambda: [])
        assert 'Upcoming Events' not in _body(client)


class TestTheGates:

    def test_registration_off_hides_the_card_and_skips_the_query(self, client, app,
                                                                 monkeypatch):
        """The link would 404: the register blueprint is unmounted."""
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', False)

        def _boom():
            raise AssertionError('queried with registration off')
        monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data', _boom)
        assert 'Upcoming Events' not in _body(client)

    def test_a_sam_database_failure_renders_the_page_without_the_card(self, client, app,
                                                                      monkeypatch):
        monkeypatch.setitem(app.config, 'ACCOUNT_INVITATIONS_ENABLED', True)

        def _down():
            raise OperationalError('SELECT 1', {}, Exception('SAM is down'))
        monkeypatch.setattr('webapp.dashboards.event_lifecycle.upcoming_events_data', _down)
        body = _body(client)
        assert 'Upcoming Events' not in body
        assert 'Maintenance &amp; Reservations' in body


class TestInvalidation:

    def test_close_and_reopen_invalidate_the_listing(self, app, monkeypatch):
        from unittest.mock import MagicMock
        from webapp.dashboards import event_lifecycle

        calls = []
        monkeypatch.setattr(event_lifecycle, 'invalidate_upcoming_events',
                            lambda: calls.append(1))
        monkeypatch.setattr(event_lifecycle, 'management_transaction',
                            MagicMock())
        with app.test_request_context():
            event_lifecycle.switch_event(MagicMock(event_code='X'), 'close', {})
            event_lifecycle.switch_event(MagicMock(event_code='X'), 'reopen', {})
        assert len(calls) == 2


class TestTheRealCache:
    """The suite runs NullCache, under which memoize and delete_memoized never
    execute. Swap a real backend in for the one listing."""

    @pytest.fixture
    def simple_cache(self, app, monkeypatch):
        from cachelib import SimpleCache
        from webapp.extensions import cache
        monkeypatch.setitem(app.extensions['cache'], cache, SimpleCache())

    def test_second_read_hits_and_invalidation_misses(self, app, simple_cache, monkeypatch):
        from webapp.dashboards import event_lifecycle
        reads = []
        monkeypatch.setattr(event_lifecycle, 'upcoming_listed_events',
                            lambda session: reads.append(1) or [{'event_code': 'X'}])
        with app.app_context():
            assert event_lifecycle.upcoming_events_data() == [{'event_code': 'X'}]
            event_lifecycle.upcoming_events_data()
            assert len(reads) == 1
            event_lifecycle.invalidate_upcoming_events()
            event_lifecycle.upcoming_events_data()
            assert len(reads) == 2
