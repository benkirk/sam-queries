"""`NotificationAddressing` — operator-added cc/bcc rows."""

from datetime import datetime, timedelta

import pytest
from factories.notify import make_addressing_row
from sqlalchemy.exc import IntegrityError

from sam import NotificationAddressing
from sam.notify.addressing_store import normalize_address
from sam.queries.notifications import get_addressing_rows


class TestCreate:

    def test_it_stamps_the_app_clock_and_normalizes(self, session):
        before = datetime.now()
        row = make_addressing_row(session, scope='expiration-WNA', field='bcc',
                                  address='  Ops@Example.EDU ')
        assert row.notification_addressing_id
        assert row.address == 'ops@example.edu'
        assert row.family == 'expiration'
        assert row.creation_time.tzinfo is None
        assert before - timedelta(seconds=1) <= row.creation_time <= datetime.now()

    @pytest.mark.parametrize('scope', ['xras', 'xras_supplement', 'expiration',
                                       'expiration-UNIV', 'task_summary'])
    def test_every_offered_scope_is_accepted(self, session, scope):
        assert make_addressing_row(session, scope=scope).scope == scope

    @pytest.mark.parametrize('kwargs', [
        {'scope': 'nope'}, {'scope': 'xras_update-WNA'}, {'scope': ''},
        {'field': 'to'}, {'address': 'not-an-address'}, {'address': 'a b@x.edu'},
        {'address': 'a@x.edu,b@x.edu'}, {'address': ''}, {'created_by': ''},
    ])
    def test_it_validates(self, session, kwargs):
        args = {'scope': 'xras', 'field': 'cc', 'address': 'a@x.edu',
                'created_by': 'benkirk', **kwargs}
        with pytest.raises(ValueError):
            NotificationAddressing.create(session, **args)

    def test_the_entry_is_unique(self, session):
        row = make_addressing_row(session)
        assert NotificationAddressing.get_by_entry(
            session, scope=row.scope, field=row.field,
            address=row.address.upper()) is row
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                make_addressing_row(session, scope=row.scope, field=row.field,
                                    address=row.address)

    def test_the_same_address_may_serve_several_scopes_and_fields(self, session):
        address = make_addressing_row(session, scope='xras', field='cc').address
        make_addressing_row(session, scope='xras', field='bcc', address=address)
        make_addressing_row(session, scope='expiration', field='cc', address=address)


class TestTheQuery:

    def test_rows_come_back_ordered(self, session):
        a = make_addressing_row(session, scope='xras', field='cc')
        b = make_addressing_row(session, scope='expiration', field='bcc')
        rows = get_addressing_rows(session)
        assert rows.index(b) < rows.index(a)
        assert str(a) == f'cc:{a.address} (xras)'


class TestNormalizeAddress:

    def test_it_trims_and_lower_cases(self):
        assert normalize_address(' PI@X.edu ') == 'pi@x.edu'

    @pytest.mark.parametrize('bad', ['', None, '@x.edu', 'pi@', 'pi', 'a, b@x.edu'])
    def test_it_refuses_what_is_not_one_mailbox(self, bad):
        with pytest.raises(ValueError):
            normalize_address(bad)
