"""Admin Last seen page and the user card's Last seen section.

Both read the `system_status` ledger (a per-worker SQLite file that may
commit) and the committed SAM snapshot, so seeded ledger rows name `benkirk`.
"""

from datetime import timedelta

import pytest

from system_status.queries.last_seen import record_seen_at
from system_status.timeutil import utcnow_naive

PAGE = '/admin/users/last-seen'
TABLE = '/admin/htmx/users/last-seen'
CARD = '/admin/user/benkirk'


@pytest.fixture
def seen_on_cheyenne(status_session):
    record_seen_at(status_session, 'pbs', 'cheyenne', ['benkirk'],
                   utcnow_naive() - timedelta(days=1500))
    status_session.commit()


@pytest.fixture
def seen_just_now(status_session):
    record_seen_at(status_session, 'login', 'derecho', ['benkirk'],
                   utcnow_naive() - timedelta(minutes=1))
    status_session.commit()


class TestAccess:

    @pytest.mark.parametrize('url', [PAGE, TABLE])
    def test_admin_reaches_page_and_table(self, auth_client, status_session, url):
        assert auth_client.get(url).status_code == 200

    @pytest.mark.parametrize('url', [PAGE, TABLE])
    def test_without_view_users_is_refused(self, non_admin_client, url):
        assert non_admin_client.get(url).status_code == 403

    def test_page_seeds_the_form_from_the_query_string(self, auth_client, status_session):
        html = auth_client.get(f'{PAGE}?bucket=stale&kind=pbs&q=ben').get_data(as_text=True)
        assert 'name="bucket" value="stale"' in html
        assert 'name="kind" value="pbs"' in html
        assert 'value="ben"' in html


class TestTable:

    def test_seeded_row_renders_with_its_source(self, auth_client, seen_on_cheyenne):
        html = auth_client.get(f'{TABLE}?active_only=1&q=benkirk').get_data(as_text=True)
        assert 'pbs · cheyenne' in html
        assert '4 years ago' in html

    def test_bucket_chip_filters_rows(self, auth_client, seen_on_cheyenne):
        stale = auth_client.get(f'{TABLE}?active_only=1&q=benkirk&bucket=stale').get_data(as_text=True)
        recent = auth_client.get(f'{TABLE}?active_only=1&q=benkirk&bucket=recent').get_data(as_text=True)
        assert 'pbs · cheyenne' in stale
        assert 'No users match these filters.' in recent

    def test_a_fresh_sighting_reads_now(self, auth_client, seen_just_now):
        html = auth_client.get(f'{TABLE}?active_only=1&q=benkirk&bucket=current').get_data(as_text=True)
        assert 'login · derecho' in html
        assert '>now' in html and 'minute ago' not in html
        assert '1 UTC' not in html        # the exact time stays in the title, not the cell
        recent = auth_client.get(f'{TABLE}?active_only=1&q=benkirk&bucket=recent').get_data(as_text=True)
        assert 'No users match these filters.' in recent

    def test_unseen_snapshot_users_are_never(self, auth_client, status_session):
        html = auth_client.get(f'{TABLE}?active_only=1&bucket=never').get_data(as_text=True)
        assert 'never' in html and 'No users match' not in html

    def test_unreadable_ledger_degrades_to_a_200(self, auth_client, monkeypatch):
        from webapp.dashboards.admin import last_seen_routes
        monkeypatch.setattr(last_seen_routes, '_ledger_missing', lambda: True)
        resp = auth_client.get(TABLE)
        assert resp.status_code == 200
        assert 'unavailable' in resp.get_data(as_text=True)


class TestUserCard:

    def test_card_lists_each_source(self, auth_client, seen_on_cheyenne):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'Last seen' in html and 'cheyenne' in html

    def test_card_reads_now_for_a_fresh_sighting(self, auth_client, seen_just_now):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'derecho' in html and '>now' in html and 'minute ago' not in html

    def test_card_says_never_seen(self, auth_client, status_session):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'Never seen on any tracked system.' in html

    def test_card_degrades_when_the_ledger_raises(self, auth_client, monkeypatch):
        import system_status.queries.last_seen as ls

        def boom(*a, **kw):
            raise RuntimeError('status DB down')
        monkeypatch.setattr(ls, 'get_last_seen', boom)
        resp = auth_client.get(CARD)
        assert resp.status_code == 200
        assert 'Last-seen data is unavailable.' in resp.get_data(as_text=True)

    def test_self_view_has_no_last_seen_section(self, auth_client, status_session):
        html = auth_client.get('/user/info').get_data(as_text=True)
        assert 'Never seen on any tracked system.' not in html
        assert 'Last-seen data is unavailable.' not in html
