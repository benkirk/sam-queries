"""The after-commit directory check on the ldapsync PUTs: lever-gated, fail-open, never a failed PUT."""
import json
import logging

import pytest

from ldapsync_helpers import PREFIX, admin_auth, ldapsync_client, ldapsync_keys  # noqa: F401

USER = {'userName': 'hookuser', 'unixUid': 4242, 'upid': 4242, 'active': True, 'locked': False,
        'chargingExempt': False, 'firstname': 'Hook', 'lastname': 'User'}
ORG = {'organizationId': 375, 'name': 'High-End Services Section', 'acronym': 'HSS',
       'active': True}


@pytest.fixture
def no_commit(monkeypatch):
    """The sync functions answer their key without touching the database."""
    import webapp.api.ldapsync.sync as route
    monkeypatch.setattr(route.sync, 'sync_user', lambda session, data, **kw: data['unix_uid'])
    monkeypatch.setattr(route.sync, 'sync_organization',
                        lambda session, data: data['organization_id'])


@pytest.fixture
def recorder(monkeypatch):
    calls = []
    import sam.queries.directory as queries
    monkeypatch.setattr(queries, 'run_check',
                        lambda session, kind, data, key, **kw: calls.append((kind, key)))
    return calls


def _put(client, rule, body):
    return client.put(f'{PREFIX}/ldapsync/{rule}', data=json.dumps(body), headers=admin_auth(),
                      content_type='application/json')


def test_lever_off_means_no_read(ldapsync_client, no_commit, recorder):
    resp = _put(ldapsync_client, 'user', USER)
    assert resp.status_code == 200 and resp.get_json() == 4242
    assert recorder == []


def test_lever_on_checks_after_the_put(ldapsync_client, app, monkeypatch, no_commit, recorder):
    monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
    assert _put(ldapsync_client, 'user', USER).status_code == 200
    assert _put(ldapsync_client, 'organization', ORG).status_code == 200
    assert recorder == [('person', 4242), ('organization', 375)]


def test_a_failing_check_never_fails_the_put(ldapsync_client, app, monkeypatch, no_commit, caplog):
    monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
    import sam.queries.directory as queries

    def boom(*args, **kwargs):
        raise RuntimeError('directory exploded')

    monkeypatch.setattr(queries, 'run_check', boom)
    with caplog.at_level(logging.WARNING):
        resp = _put(ldapsync_client, 'user', USER)
    assert resp.status_code == 200 and resp.get_json() == 4242
    warnings = [r for r in caplog.records if 'directory check person key=4242 failed' in r.message]
    assert len(warnings) == 1 and 'RuntimeError' in warnings[0].message
    assert 'exploded' not in warnings[0].message


def test_a_rejected_put_runs_no_check(ldapsync_client, app, monkeypatch, recorder):
    monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
    assert _put(ldapsync_client, 'user', {**USER, 'active': None}).status_code == 400
    assert recorder == []


def test_the_gid_put_has_no_check(ldapsync_client, app, monkeypatch, recorder):
    import webapp.api.ldapsync.sync as route
    monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
    monkeypatch.setattr(route.sync, 'sync_gid_allocation', lambda session, data: data['start_gid'])
    assert _put(ldapsync_client, 'gidAllocation', {'startGid': 70000, 'endGid': 70010}).status_code == 200
    assert recorder == []


def test_with_the_real_check_and_a_fake_reader(ldapsync_client, app, monkeypatch, no_commit, caplog):
    """The whole path below the route: the registry hands out the fake, one line is logged."""
    from factories.directory import FakeDirectory, make_person_record
    import sam.integration.directory.registry as registry
    fake = FakeDirectory()
    fake.add(make_person_record(upid=4242, username='hookuser', unix_uid=4242, emails=(), phones=()))
    monkeypatch.setattr(registry, 'reader_from_environment', lambda: fake)
    monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
    with caplog.at_level(logging.INFO, logger='sam.queries.directory'):
        assert _put(ldapsync_client, 'user', USER).status_code == 200
    assert fake.calls == [('person', 4242)]
    assert any('directory check person hookuser key=4242 found=True' in r.message
               for r in caplog.records)


class TestApplyOnSync:
    """With a kind's lever on, the hook reconciles in a second transaction, still fail-open."""

    @pytest.fixture
    def armed(self, app, monkeypatch, no_commit):
        from factories.directory import FakeDirectory, make_person_record
        import sam.integration.directory.registry as registry
        fake = FakeDirectory()
        fake.add(make_person_record(upid=4242, username='hookuser', unix_uid=4242, emails=(),
                                    phones=(), first_name='Hooked'))
        monkeypatch.setattr(registry, 'reader_from_environment', lambda: fake)
        monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
        monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
        return fake

    def test_the_reconcile_runs_with_the_record_already_read(self, ldapsync_client, armed, monkeypatch, caplog):
        import sam.manage.directory as manage
        seen = []
        monkeypatch.setattr(manage, 'reconcile_record',
                            lambda session, kind, key, record, **kw: seen.append((kind, key, record.upid)) or None)
        with caplog.at_level(logging.INFO):
            assert _put(ldapsync_client, 'user', USER).status_code == 200
        assert seen == [('person', 4242, 4242)]
        assert armed.calls == [('person', 4242)]

    def test_a_failing_reconcile_never_fails_the_put(self, ldapsync_client, armed, monkeypatch, caplog):
        import sam.manage.directory as manage

        def boom(*args, **kwargs):
            raise RuntimeError('apply exploded')
        monkeypatch.setattr(manage, 'reconcile_record', boom)
        with caplog.at_level(logging.WARNING):
            resp = _put(ldapsync_client, 'user', USER)
        assert resp.status_code == 200 and resp.get_json() == 4242
        assert any('directory check person key=4242 failed: RuntimeError' in r.message
                   for r in caplog.records)

    def test_no_lever_means_no_reconcile_call(self, ldapsync_client, armed, monkeypatch):
        import sam.manage.directory as manage
        monkeypatch.delenv('DIRECTORY_APPLY_PERSON')
        monkeypatch.setattr(manage, 'reconcile_record', lambda *a, **k: pytest.fail('must not run'))
        assert _put(ldapsync_client, 'user', USER).status_code == 200
