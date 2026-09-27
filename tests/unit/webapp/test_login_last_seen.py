"""A real login stamps the last-seen ledger; a ledger failure never blocks it."""

from system_status import AccessSource, UserDef, UserLastSeen


def _webapp_rows(status_session):
    return (status_session.query(UserDef.username)
            .join(UserLastSeen.user).join(UserLastSeen.source)
            .filter(AccessSource.kind == 'webapp').all())


def test_stub_login_records_a_webapp_sighting(client, status_session):
    resp = client.post('/auth/login', data={'username': 'benkirk', 'password': 'x'})
    assert resp.status_code == 302
    status_session.expire_all()
    assert _webapp_rows(status_session) == [('benkirk',)]


def test_failed_login_records_nothing(client, status_session):
    client.post('/auth/login', data={'username': 'benkirk', 'password': ''})
    assert _webapp_rows(status_session) == []


def test_ledger_failure_does_not_block_login(client, status_session, monkeypatch):
    import system_status.queries.last_seen as last_seen

    def boom(*a, **kw):
        raise RuntimeError('status DB down')
    monkeypatch.setattr(last_seen, 'record_seen_at', boom)
    resp = client.post('/auth/login', data={'username': 'benkirk', 'password': 'x'})
    assert resp.status_code == 302
    assert _webapp_rows(status_session) == []
