"""Admin -> Projects -> Expirations -> Abandoned Users: the Last seen column and filter."""

import csv
import io
from datetime import datetime, timedelta

import pytest

from system_status.queries.last_seen import record_seen_at
from system_status.timeutil import utcnow_naive
from webapp.dashboards.admin.blueprint import _abandoned_rows

FRAGMENT = '/admin/expirations?view=abandoned'
EXPORT = '/admin/expirations/export?export_type=abandoned'
NOW = datetime(2026, 9, 30, 12, 0)


class TestAbandonedRows:
    """The pure half: ledger join, not-seen filter, shaping."""

    PROJCODES = {'recent': ['P1'], 'old': ['P1', 'P2'], 'never': ['P2']}
    DIRECTORY = {u: (u.title(), True, False) for u in PROJCODES}
    LEDGER = {'recent': {'login': (NOW - timedelta(days=1), 'derecho')},
              'old': {'pbs': (NOW - timedelta(days=1500), 'cheyenne'),
                      'webapp': (NOW - timedelta(days=800), 'webapp')}}

    def _rows(self, ledger=LEDGER, not_seen_since=None):
        return _abandoned_rows(self.PROJCODES, self.DIRECTORY, ledger, {'old': 'old@x.edu'},
                               now=NOW, not_seen_since=not_seen_since)

    def test_newest_sighting_across_sources(self):
        rows = {r['username']: r for r in self._rows()}
        assert [r for r in rows] == ['never', 'old', 'recent']
        assert (rows['old']['last_seen_kind'], rows['old']['last_seen_age'].days) == ('webapp', 800)
        assert rows['old']['projects'] == 'P1, P2' and rows['old']['project_count'] == 2
        assert rows['old']['email'] == 'old@x.edu' and rows['never']['email'] == 'N/A'
        assert rows['never']['last_seen'] is None

    @pytest.mark.parametrize('preset,kept', [
        (None, {'recent', 'old', 'never'}),
        ('6m', {'old', 'never'}),
        ('2y', {'old', 'never'}),
        ('bogus', {'recent', 'old', 'never'}),
    ])
    def test_not_seen_filter_keeps_never_seen(self, preset, kept):
        assert {r['username'] for r in self._rows(not_seen_since=preset)} == kept

    def test_unreadable_ledger_ignores_the_filter(self):
        rows = self._rows(ledger=None, not_seen_since='1y')
        assert len(rows) == 3 and all(r['last_seen'] is None for r in rows)


def _export(client, query=''):
    resp = client.get(EXPORT + query)
    assert resp.status_code == 200
    return list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))


class TestRoutes:
    """HTTP smoke against the committed snapshot."""

    def test_fragment_renders(self, auth_client, status_session):
        resp = auth_client.get(FRAGMENT)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Last Seen' in html or 'No abandoned users found' in html

    def test_filter_is_named_in_the_summary(self, auth_client, status_session):
        html = auth_client.get(FRAGMENT + '&not_seen_since=1y').get_data(as_text=True)
        assert 'Only users not seen in the last 1 year.' in html

    def test_unreadable_ledger_degrades_to_a_200(self, auth_client, monkeypatch):
        from webapp.dashboards.admin import last_seen_routes
        monkeypatch.setattr(last_seen_routes, 'ledger_missing', lambda: True)
        resp = auth_client.get(FRAGMENT + '&not_seen_since=1y')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        if 'No abandoned users found' not in html:
            assert 'Last-seen data is unavailable' in html

    def test_export_carries_last_seen_and_the_filter(self, auth_client, status_session):
        rows = _export(auth_client)
        if not rows:
            pytest.skip('snapshot has no abandoned users')
        assert {'Last Seen (UTC)', 'Last Seen Source'} <= set(rows[0])
        target = rows[0]['Username']
        record_seen_at(status_session, 'login', 'derecho', [target],
                       utcnow_naive() - timedelta(days=1))
        status_session.commit()

        seen = {r['Username']: r for r in _export(auth_client)}
        assert seen[target]['Last Seen Source'] == 'login · derecho'
        assert target not in {r['Username'] for r in _export(auth_client, '&not_seen_since=1y')}
