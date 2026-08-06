"""
Tests for the XRAS legacy-compat API (`/api/xras/v1/*`).

These assert the **rules** of the legacy wire contract rather than comparing
against a committed byte corpus. The real captured corpus is production PII
(28,259 names, emails and phone numbers); byte-for-byte comparison is the job of
`utils/parity/check_legacy_apis.py --api xras`, which runs live against both
stacks where the data is real by construction and nothing needs scrubbing.

Every constant here was measured against production on 2026-08-05/06 with the
`samuel` ROLE_XRAS credential. See `docs/plans/XRAS_REIMPLEMENTATION.md`.

Auth note: a `make_api_credentials` row is invisible to an HTTP request, because
routes read Flask-SQLAlchemy's `db.session` on a separate connection that only
sees committed rows. Following `test_api_credentials_auth.py`, the DB-key loader
is monkeypatched instead.
"""

import base64
import json

import bcrypt
import pytest

from webapp.api.xras import serialize
from webapp.utils import api_auth


XRAS_PW = 'xras-test-pw'


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
    return f'Basic {token}'


@pytest.fixture(autouse=True)
def _reset_db_key_cache():
    """`_DB_KEY_CACHE` is a process-global dict — wipe it around each test."""
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})


@pytest.fixture
def xras_keys(monkeypatch):
    """Two DB-sourced keys: one holding ROLE_XRAS, one holding something else."""
    hashed = bcrypt.hashpw(XRAS_PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(
        api_auth, '_get_db_api_keys',
        lambda: {
            'samuel': {'hash': hashed, 'roles': ['ROLE_XRAS']},
            'nobody': {'hash': hashed, 'roles': ['ROLE_SOMETHING']},
        },
    )


@pytest.fixture
def xras_client(client, xras_keys):
    """Unauthenticated test client with the XRAS key map installed."""
    return client


def _auth(username='samuel'):
    return {'Authorization': _basic(username, XRAS_PW)}


# ---------------------------------------------------------------------------
# Serialization primitives
# ---------------------------------------------------------------------------

class TestSerialize:
    def test_compact_has_no_spaces_and_no_trailing_newline(self):
        out = serialize.compact({'a': 1, 'b': [1, 2]})
        assert out == '{"a":1,"b":[1,2]}'
        assert not out.endswith('\n')

    def test_key_order_is_insertion_order_not_sorted(self):
        """Legacy emits Java field-declaration order; Flask's jsonify sorts."""
        out = serialize.compact({'zebra': 1, 'apple': 2})
        assert out == '{"zebra":1,"apple":2}'

    def test_non_ascii_stays_raw_utf8(self):
        """The roster carries 78 non-ASCII bytes and zero \\uXXXX escapes."""
        out = serialize.compact({'organization': 'Université'})
        assert 'é' in out
        assert '\\u' not in out

    def test_omit_none_drops_only_none(self):
        got = serialize.omit_none(
            {'a': None, 'b': '', 'c': 0, 'd': [], 'e': False})
        assert got == {'b': '', 'c': 0, 'd': [], 'e': False}

    def test_omit_none_preserves_order(self):
        got = serialize.omit_none({'z': 1, 'y': None, 'x': 2})
        assert list(got) == ['z', 'x']

    def test_envelope_emits_null_members(self):
        """`ResponseWrapper` carries no NON_NULL — both keys always appear."""
        resp = serialize.xras_response(None)
        assert resp.get_data(as_text=True) == '{"message":null,"result":null}'

    def test_envelope_can_be_suppressed(self):
        """/people and /people/{username} are the only unwrapped endpoints."""
        resp = serialize.xras_response({'username': 'x'}, envelope=False)
        assert resp.get_data(as_text=True) == '{"username":"x"}'

    def test_content_type_has_no_charset(self):
        resp = serialize.xras_response(None)
        assert resp.headers['Content-Type'] == 'application/json'


class TestUnauthenticatedBody:
    """The 41-byte 401, verified against production with `od -c`."""

    def test_is_exactly_41_bytes(self):
        assert len(serialize.UNAUTHENTICATED_BODY.encode()) == 41

    def test_is_pretty_printed_with_space_before_colon(self):
        assert serialize.UNAUTHENTICATED_BODY == (
            '{\n  "message" : null,\n  "result" : null\n}')

    def test_carries_a_charset_unlike_every_other_body(self):
        resp = serialize.unauthenticated()
        assert resp.headers['Content-Type'] == 'application/json;charset=UTF-8'

    def test_has_no_www_authenticate_header(self):
        """Deliberate, per XrasAuthenticationEntryPoint's javadoc."""
        assert 'WWW-Authenticate' not in serialize.unauthenticated().headers


# ---------------------------------------------------------------------------
# Auth: ROLE_XRAS enforcement and the XA-header shim
# ---------------------------------------------------------------------------

class TestAuth:
    PATH = '/api/xras/v1/people/benkirk'

    def test_no_credentials_is_the_byte_exact_401(self, xras_client):
        resp = xras_client.get(self.PATH)
        assert resp.status_code == 401
        assert resp.data == serialize.UNAUTHENTICATED_BODY.encode()
        assert len(resp.data) == 41
        assert 'WWW-Authenticate' not in resp.headers

    def test_bad_password_is_the_same_401(self, xras_client):
        resp = xras_client.get(
            self.PATH, headers={'Authorization': _basic('samuel', 'wrong')})
        assert resp.status_code == 401
        assert resp.data == serialize.UNAUTHENTICATED_BODY.encode()

    def test_valid_key_without_role_xras_is_403_json_not_tomcat_html(
            self, xras_client):
        """Deliberate divergence: legacy answers 431 bytes of Tomcat HTML."""
        resp = xras_client.get(self.PATH, headers=_auth('nobody'))
        assert resp.status_code == 403
        assert resp.headers['Content-Type'] == 'application/json'
        assert json.loads(resp.data)['result'] is None

    def test_role_xras_key_is_admitted(self, xras_client):
        resp = xras_client.get(self.PATH, headers=_auth())
        assert resp.status_code in (200, 404)

    def test_browser_session_cannot_reach_xras(self, auth_client, xras_keys):
        """`roles=` closes the session path — benkirk holds every Permission
        but no API-key role, so this must not be a 200."""
        resp = auth_client.get(self.PATH)
        assert resp.status_code == 401


class TestXaHeaderShim:
    """`XrasAuthenticationFilter` translates XA-REQUESTER/XA-API-KEY to Basic."""

    PATH = '/api/xras/v1/people/benkirk'

    def _xa(self, requester='samuel', key=XRAS_PW):
        return {'XA-REQUESTER': requester, 'XA-API-KEY': key}

    def test_both_headers_authenticate(self, xras_client):
        resp = xras_client.get(self.PATH, headers=self._xa())
        assert resp.status_code in (200, 404)

    def test_xa_and_basic_produce_identical_bytes(self, xras_client):
        via_xa = xras_client.get(self.PATH, headers=self._xa())
        via_basic = xras_client.get(self.PATH, headers=_auth())
        assert via_xa.status_code == via_basic.status_code
        assert via_xa.data == via_basic.data

    def test_only_requester_header_is_401(self, xras_client):
        """The headers are stripped unconditionally, so supplying one
        synthesizes nothing and the request arrives unauthenticated."""
        resp = xras_client.get(self.PATH, headers={'XA-REQUESTER': 'samuel'})
        assert resp.status_code == 401
        assert len(resp.data) == 41

    def test_only_api_key_header_is_401(self, xras_client):
        resp = xras_client.get(self.PATH, headers={'XA-API-KEY': XRAS_PW})
        assert resp.status_code == 401

    def test_explicit_authorization_wins_over_xa_headers(self, xras_client):
        """Rule 5: an explicit Authorization header is never overwritten."""
        headers = {**self._xa(requester='nobody'), **_auth('nobody')}
        resp = xras_client.get(self.PATH, headers=headers)
        assert resp.status_code == 403       # 'nobody' lacks ROLE_XRAS

    def test_bad_xa_key_is_401(self, xras_client):
        resp = xras_client.get(self.PATH, headers=self._xa(key='wrong'))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /people, GET /people/{username}
# ---------------------------------------------------------------------------

class TestGetPerson:
    """`benkirk` is preserved verbatim by the obfuscator, so it is safe to
    assert against by name (see tests/conftest.py)."""

    def _get(self, xras_client, username):
        return xras_client.get(f'/api/xras/v1/people/{username}', headers=_auth())

    def test_returns_a_bare_object_not_an_envelope(self, xras_client):
        body = json.loads(self._get(xras_client, 'benkirk').data)
        assert 'message' not in body and 'result' not in body
        assert body['username'] == 'benkirk'

    def test_field_order_matches_persondto_declaration_order(self, xras_client):
        """Java field order, which is NOT the SQL alias order — phone and
        organization are swapped between the two."""
        canonical = ['username', 'firstName', 'middleName', 'lastName',
                     'organization', 'academicStatus', 'phone', 'email']
        keys = list(json.loads(self._get(xras_client, 'benkirk').data))
        assert keys == [k for k in canonical if k in keys]

    def test_null_fields_are_omitted_not_emitted(self, xras_client):
        """PersonDTO carries @JsonSerialize(NON_NULL)."""
        raw = self._get(xras_client, 'benkirk').data.decode()
        assert 'null' not in raw

    def test_response_is_compact_json(self, xras_client):
        raw = self._get(xras_client, 'benkirk').data.decode()
        assert ', ' not in raw and '": ' not in raw
        assert not raw.endswith('\n')

    def test_content_type_has_no_charset(self, xras_client):
        resp = self._get(xras_client, 'benkirk')
        assert resp.headers['Content-Type'] == 'application/json'

    @pytest.mark.parametrize('username', ['zz', 'zzzzz', 'nosuchuser1',
                                          'nosuchuser12345',
                                          'nosuchuser12345678901234x'])
    def test_404_body_is_the_closed_form(self, xras_client, username):
        """Measured against production: bytes == len(username) + 47, exact at
        username lengths 2, 5, 11, 15 and 25."""
        resp = self._get(xras_client, username)
        assert resp.status_code == 404
        assert resp.data == (
            f'{{"message":"username={username} not found","result":null}}'.encode())
        assert len(resp.data) == len(username) + 47

    def test_404_is_enveloped_unlike_the_200(self, xras_client):
        body = json.loads(self._get(xras_client, 'nosuchuser1').data)
        assert body['result'] is None


class TestGetRoster:
    def test_returns_a_bare_array(self, xras_client):
        resp = xras_client.get('/api/xras/v1/people', headers=_auth())
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert isinstance(body, list) and body

    def test_every_record_uses_canonical_field_order(self, xras_client):
        """Across 28k production records only 19 distinct key orders appear, and
        every one is a subsequence of the canonical order."""
        canonical = ['username', 'firstName', 'middleName', 'lastName',
                     'organization', 'academicStatus', 'phone', 'email']
        body = json.loads(xras_client.get(
            '/api/xras/v1/people', headers=_auth()).data)
        for record in body[:500]:
            keys = list(record)
            assert keys == [k for k in canonical if k in keys]

    def test_no_raw_internal_org_strings_survive(self, xras_client):
        """`fixInternalOrg` is applied unconditionally — production emits zero
        `UCAR/NCAR:` strings."""
        raw = xras_client.get('/api/xras/v1/people', headers=_auth()).data
        assert b'UCAR/NCAR:' not in raw

    def test_roster_is_ordered_by_user_id(self, xras_client, session):
        """Legacy's named query has no ORDER BY; ours states user_id so the
        3.8 MB body is deterministic rather than incidentally stable."""
        from sqlalchemy import text
        body = json.loads(xras_client.get(
            '/api/xras/v1/people', headers=_auth()).data)
        expected = [r[0] for r in session.execute(text(
            'SELECT username FROM users WHERE login_type_id = 1 '
            'ORDER BY user_id LIMIT 25')).fetchall()]
        assert [p['username'] for p in body[:25]] == expected
