"""``sam.integration.xras_api.read_memo``: one gesture reads each XRAS thing once, and a
write always forces a fresh read after it."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import XrasApiConfig
from sam.integration.xras_api.admin_client import XrasAdminClient
from sam.integration.xras_api.client import XrasApiClient
from sam.integration.xras_api.read_memo import read_memo

REQUEST = '/v1/reports/request_numbers/EXAM0001'


def _response(status=200, result=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ''
    resp.json.return_value = {'result': result}
    return resp


def _config():
    return XrasApiConfig(enabled=True, write_enabled=True, api_key='not-a-real-key')


@pytest.fixture
def reader(monkeypatch):
    client = XrasApiClient(_config())
    monkeypatch.setattr(client.session, 'request',
                        MagicMock(side_effect=lambda *a, **k: _response(result={'n': 1})))
    return client


def _gets(client):
    return client.session.request.call_count


class TestTheMemo:

    def test_off_outside_a_scope(self, reader):
        reader._get(REQUEST)
        reader._get(REQUEST)
        assert _gets(reader) == 2

    def test_a_repeat_read_inside_a_scope_is_served_from_the_memo(self, reader):
        with read_memo():
            first = reader._get(REQUEST)
            first['n'] = 'mutated by a caller'
            second = reader._get(REQUEST)
        assert _gets(reader) == 1
        assert second == {'n': 1}, 'each caller gets its own copy'

    def test_a_404_is_remembered_too(self, monkeypatch):
        client = XrasApiClient(_config())
        monkeypatch.setattr(client.session, 'request',
                            MagicMock(return_value=_response(status=404)))
        with read_memo():
            assert client._get('/v1/people/nobody') is None
            assert client._get('/v1/people/nobody') is None
        assert _gets(client) == 1

    def test_the_scope_closes(self, reader):
        with read_memo():
            reader._get(REQUEST)
        with read_memo():
            reader._get(REQUEST)
        assert _gets(reader) == 2

    def test_a_nested_scope_shares_the_outer_one(self, reader):
        with read_memo():
            reader._get(REQUEST)
            with read_memo():
                reader._get(REQUEST)
            reader._get(REQUEST)
        assert _gets(reader) == 1

    def test_the_read_and_submit_contexts_are_kept_apart(self, reader, monkeypatch):
        admin = XrasAdminClient(_config(), reader=reader)
        monkeypatch.setattr(admin.session, 'request',
                            MagicMock(return_value=_response(result={'username': 'u'})))
        with read_memo():
            reader._get('/v1/people/u')
            admin._get('/v1/people/u')
        assert (_gets(reader), _gets(admin)) == (1, 1)


class TestAWriteForgets:

    def test_a_read_after_a_write_goes_to_xras(self, reader, monkeypatch):
        admin = XrasAdminClient(_config(), reader=reader)
        monkeypatch.setattr(admin.session, 'request',
                            MagicMock(return_value=_response(result={})))
        with read_memo():
            reader._get(REQUEST)
            reader._get('/v1/resources')
            admin._write('DELETE', '/v1/requests/1/roles/2', xa_user='pi-user')
            reader._get(REQUEST)
            reader._get('/v1/resources')
        assert _gets(reader) == 3, 'the request twice, the resource catalog once'
