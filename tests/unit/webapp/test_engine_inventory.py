"""engine_sources(): the one enumeration behind the Configuration card and /database."""
import types
from datetime import datetime

from sqlalchemy import create_engine

from webapp.utils.engine_inventory import engine_sources


def _fake_plugins(monkeypatch, app):
    jh = {'derecho': create_engine('sqlite://'), 'casper': create_engine('sqlite://')}
    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {'engines': jh, 'enabled': True})

    class _Queries:
        def __init__(self, filesystems, database):
            self.fs = filesystems[0]

        def scan_dates(self):
            if self.fs == 'broken':
                raise RuntimeError('boom')
            return [datetime(2026, 9, 1), datetime(2026, 9, 20)]

    fs = {
        'module': types.SimpleNamespace(FsScanQueries=_Queries),
        'databases': {
            'desc1': {'engines': {'scratch': create_engine('sqlite://')}},
            'campaign': {'engines': {'univ': create_engine('sqlite://'),
                                     'broken': create_engine('sqlite://'),
                                     'cisl': create_engine('sqlite://')}},
            'empty': {'engines': {}},
        },
    }
    monkeypatch.setitem(app.extensions, 'fs_scans', fs)


def test_sam_and_status_only_when_plugins_off(app):
    from webapp.extensions import db
    with app.app_context():
        sources = engine_sources(app, db)
        assert [s.key for s in sources] == ['sam', 'system_status']
        assert sources[0].engine is db.engine
        assert sources[1].engine is db.engines['system_status']


def test_plugins_in_card_order(app, monkeypatch):
    from webapp.extensions import db
    _fake_plugins(monkeypatch, app)
    with app.app_context():
        sources = engine_sources(app, db)
    assert [s.key for s in sources] == [
        'sam', 'system_status', 'job_history.derecho', 'job_history.casper',
        'fs_scans.campaign.broken', 'fs_scans.campaign.cisl', 'fs_scans.campaign.univ',
        'fs_scans.desc1.scratch',
    ]
    univ = sources[6]
    assert (univ.label, univ.title) == ('fs_scans (campaign)', 'fs_scans (campaign) / univ')
    assert (univ.database, univ.collection, univ.schema) == ('campaign', 'univ', None)  # sqlite
    assert sources[0].title == 'sam' and sources[2].title == 'job_history (derecho)'


def test_configuration_card_rows_follow_inventory(app, monkeypatch):
    from webapp.extensions import db
    from webapp.utils.config_inspect import gather_runtime_state
    _fake_plugins(monkeypatch, app)
    with app.app_context():
        rows = gather_runtime_state(app, db)['databases']
    assert [r['name'] for r in rows] == [
        'sam', 'system_status', 'job_history (derecho)', 'job_history (casper)',
        'fs_scans (campaign)', 'fs_scans (desc1)',
    ]
    assert 'clock' in rows[0] and 'clock' not in rows[1]
    scans = rows[4]['scans']
    assert scans['collection_count'] == 3
    assert {c['name']: c['scan_date'] for c in scans['collections']} == {
        'broken': None, 'cisl': '2026-09-20', 'univ': '2026-09-20'}
    assert scans['oldest_scan'] == scans['newest_scan'] == '2026-09-20'
