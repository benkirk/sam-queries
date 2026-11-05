"""``POST /api/v1/xras/notes``: key only, validated, upserted per request.

The route commits on Flask-SQLAlchemy's own connection, so the rows it writes escape
the per-test SAVEPOINT; ``notes_cleanup`` deletes them by request number.
"""
import base64
import copy
import json

import bcrypt
import pytest
from sqlalchemy import delete

from factories import next_seq
from sam.integration.xras import XrasRequestNote
from sam.security.permissions import Permission
from sam.security.rbac_catalog import GrantDef, RoleDef, build_catalog
from webapp.utils import api_auth, rbac

URL = '/api/v1/xras/notes'


def _basic(username, password):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()}


def _batch(*numbers, **note_overrides):
    note = {'request_id': 1454171, 'action_id': 410334, 'decision': 'route_to_human',
            'button': None, 'end_date': None,
            'hours_by_resource': {'Derecho': 1000000},
            'requested_hours': {'Casper': 20000, 'Derecho': 1000000},
            'admin_comment': 'Approved as requested.', 'rationale': 'Within the rules.',
            'pi_history': 'This person has 1 past request on record.',
            'findings': [{'id': 'new.us_university', 'severity': 'warn', 'message': 'A US university.',
                          'sources': [{'label': 'NSF award 2320667', 'kind': 'nsf',
                                       'url': 'https://www.nsf.gov/awardsearch/showAward?AWD_ID=2320667'}]}],
            'links': {'xras': 'https://admin-ncar.xras.org/actions/410334/show_process'}}
    note.update(note_overrides)
    return {'source': 'arc-allocation-recommender', 'run_id': '2026-11-05T06:00',
            'generated_at': '2026-11-05T06:01:00-07:00', 'ruleset_version': '2026.10',
            'notes': [dict(copy.deepcopy(note), request_number=n) for n in numbers]}


@pytest.fixture
def notes_cleanup(SessionFactory):
    numbers = []
    yield numbers
    with SessionFactory() as s:
        s.execute(delete(XrasRequestNote).where(XrasRequestNote.request_number.in_(numbers)))
        s.commit()


@pytest.fixture
def number(notes_cleanup):
    value = next_seq('NOTEAPI')
    notes_cleanup.append(value)
    return value


def _rows(SessionFactory, number):
    with SessionFactory() as s:
        return s.query(XrasRequestNote).filter_by(request_number=number).all()


class TestAuth:

    def test_no_key_is_401(self, client):
        assert client.post(URL, json=_batch('NCAR0001')).status_code == 401

    def test_a_session_is_not_a_key(self, auth_client):
        assert auth_client.post(URL, json=_batch('NCAR0001')).status_code == 401


class TestDbMode:

    @pytest.fixture
    def keys(self, app, monkeypatch):
        h = bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setitem(app.config, 'API_KEYS', {'heuv': h, 'ssg': h})
        monkeypatch.setitem(app.config, 'RBAC_SOURCE', 'db')
        monkeypatch.setitem(app.config, 'RBAC_DB_TTL', 60)
        monkeypatch.setattr(rbac, '_DB_CACHE', {'at': None, 'catalog': None})
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: build_catalog(
            [RoleDef('api_systems_integration', frozenset({Permission.VIEW_PROJECTS}))],
            [GrantDef('apikey', 'ssg', role='api_systems_integration'),
             GrantDef('apikey', 'heuv', role='api_systems_integration'),
             GrantDef('apikey', 'heuv', permission=Permission.POST_XRAS_NOTES)], source='db'))
        api_auth._DB_KEY_CACHE.update(at=None, map={})
        api_auth._VERIFY_CACHE.clear()
        yield
        api_auth._DB_KEY_CACHE.update(at=None, map={})
        api_auth._VERIFY_CACHE.clear()

    def test_a_key_without_the_permission_is_403(self, client, keys, number):
        resp = client.post(URL, json=_batch(number), headers=_basic('ssg', 'pw'))
        assert resp.status_code == 403

    def test_the_granted_key_writes_as_itself(self, client, keys, number, SessionFactory):
        resp = client.post(URL, json=_batch(number), headers=_basic('heuv', 'pw'))
        assert resp.status_code == 200, resp.get_json()
        [row] = _rows(SessionFactory, number)
        assert row.created_by == 'heuv'


class TestUpsert:

    def test_twice_is_one_row_with_the_second_run(self, api_key_client, number, SessionFactory):
        first = api_key_client.post(URL, json=_batch(number))
        assert first.status_code == 200
        assert first.get_json() == {'source': 'arc-allocation-recommender',
                                    'run_id': '2026-11-05T06:00', 'received': 1,
                                    'request_numbers': [number]}
        again = _batch(number, decision='approve')
        again['run_id'] = '2026-11-06T06:00'
        assert api_key_client.post(URL, json=again).status_code == 200
        [row] = _rows(SessionFactory, number)
        assert (row.run_id, row.decision, row.created_by) == ('2026-11-06T06:00', 'approve', 'collector')
        assert row.generated_at.isoformat() == '2026-11-05T06:01:00'
        assert row.values['links']['xras'].endswith('/show_process')
        assert row.findings[0]['sources'][0]['label'] == 'NSF award 2320667'

    def test_an_offset_becomes_naive_mountain(self, api_key_client, number, SessionFactory):
        body = _batch(number)
        body['generated_at'] = '2026-11-05T13:01:00Z'
        assert api_key_client.post(URL, json=body).status_code == 200
        assert _rows(SessionFactory, number)[0].generated_at.isoformat() == '2026-11-05T06:01:00'


class TestValidation:

    def _post(self, client, body, **kw):
        return client.post(URL, data=body if isinstance(body, (bytes, str)) else json.dumps(body),
                           content_type='application/json', **kw)

    def test_bad_json_is_400(self, api_key_client):
        resp = self._post(api_key_client, '{"notes": [')
        assert resp.status_code == 400 and resp.get_json()['error'] == 'Request body must be JSON'

    @pytest.mark.parametrize('url', ['javascript:alert(1)', '', 'ftp://example.org/x',
                                     'https://', 'https://exa mple.org'])
    def test_a_source_url_that_is_not_http_is_400(self, api_key_client, url):
        body = _batch('NCAR0001')
        body['notes'][0]['findings'][0]['sources'][0]['url'] = url
        resp = self._post(api_key_client, body)
        assert resp.status_code == 400
        assert 'notes' in resp.get_json()['details']

    def test_a_link_that_is_not_http_is_400(self, api_key_client):
        resp = self._post(api_key_client, _batch('NCAR0001', links={'xras': 'data:text/html,x'}))
        assert resp.status_code == 400

    @pytest.mark.parametrize('field,value', [('decision', 'maybe'), ('findings', [
        {'severity': 'fatal', 'message': 'x', 'sources': []}])])
    def test_an_unknown_decision_or_severity_is_400(self, api_key_client, field, value):
        assert self._post(api_key_client, _batch('NCAR0001', **{field: value})).status_code == 400

    def test_201_notes_is_400(self, api_key_client):
        body = _batch(*[f'NCAR{n:04d}' for n in range(201)])
        assert self._post(api_key_client, body).status_code == 400

    def test_41_findings_is_400(self, api_key_client):
        finding = {'severity': 'ok', 'message': 'x', 'sources': []}
        assert self._post(api_key_client, _batch('NCAR0001', findings=[finding] * 41)).status_code == 400

    def test_a_request_twice_in_one_batch_is_400(self, api_key_client):
        resp = self._post(api_key_client, _batch('NCAR0001', 'NCAR0001'))
        assert resp.status_code == 400 and 'NCAR0001' in json.dumps(resp.get_json())

    def test_an_oversized_body_is_413(self, api_key_client):
        resp = self._post(api_key_client, b' ' * (1024 * 1024 + 1))
        assert resp.status_code == 413

    def test_a_long_comment_is_400(self, api_key_client):
        assert self._post(api_key_client, _batch('NCAR0001', admin_comment='x' * 4001)).status_code == 400
