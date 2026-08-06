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

    def test_null_fields_are_omitted_rather_than_emitted(self, xras_client):
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

    def test_no_raw_internal_org_strings_survive_the_fixup(self, xras_client):
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


# ---------------------------------------------------------------------------
# requests/* and dates/requests/*
# ---------------------------------------------------------------------------

def _first_projcode(xras_client):
    """Take a projcode from the roster's own output so the tests survive an
    obfuscated-snapshot refresh."""
    body = json.loads(xras_client.get(
        '/api/xras/v1/requests/user/benkirk', headers=_auth()).data)
    masters = body['result']['masters']
    if not masters:
        pytest.skip('snapshot has no projects for benkirk')
    return masters[0]['requestNumber']


class TestRequestsEnvelope:
    def test_result_is_wrapped_unlike_people(self, xras_client):
        body = json.loads(xras_client.get(
            '/api/xras/v1/requests/request/ZZZZ9999', headers=_auth()).data)
        assert set(body) == {'message', 'result'}

    def test_unknown_request_number_is_200_with_empty_masters(self, xras_client):
        """Not a 404 — measured at exactly 62 bytes in production."""
        resp = xras_client.get(
            '/api/xras/v1/requests/request/ZZZZ9999', headers=_auth())
        assert resp.status_code == 200
        assert resp.data == (
            b'{"message":null,"result":{"projectIdLabel":null,"masters":[]}}')
        assert len(resp.data) == 62

    def test_project_id_label_is_emitted_as_an_explicit_null(self, xras_client):
        """`AccountingRequestResponse` carries no NON_NULL, and nothing in
        legacy ever assigns this field."""
        body = json.loads(xras_client.get(
            f'/api/xras/v1/requests/request/{_first_projcode(xras_client)}',
            headers=_auth()).data)
        assert 'projectIdLabel' in body['result']
        assert body['result']['projectIdLabel'] is None


class TestRequestShape:
    @pytest.fixture
    def one_request(self, xras_client):
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/requests/request/{code}', headers=_auth()).data)
        return body['result']['masters'][0]

    def test_master_key_order(self, one_request):
        assert list(one_request) == ['requestNumber', 'requests']

    def test_request_key_order_and_no_xras_action_ids(self, one_request):
        """`xrasActionIds` is never set by RequestFactory, and `Request` is
        NON_NULL — so the key must be absent, not null."""
        for request in one_request['requests']:
            assert list(request) == [
                'requestType', 'requestBeginDate', 'requestEndDate',
                'allocationType', 'projectTitle', 'projectId',
                'fos', 'allocations',
            ]

    def test_fos_is_always_one_primary_element(self, one_request):
        for request in one_request['requests']:
            assert len(request['fos']) == 1
            assert list(request['fos'][0]) == ['xrasFosTypeId', 'isPrimary']
            assert request['fos'][0]['isPrimary'] is True

    def test_allocation_key_order_is_a_subsequence(self, one_request):
        """`actionType`, `xrasActionId` and `xrasActionResourceId` are never
        emitted; `remainingAmount` and `resourceRepositoryKey` are optional."""
        canonical = ['allocationBeginDate', 'allocationEndDate',
                     'allocatedAmount', 'remainingAmount',
                     'resourceRepositoryKey', 'actions']
        for request in one_request['requests']:
            for allocation in request['allocations']:
                keys = list(allocation)
                assert keys == [k for k in canonical if k in keys]
                assert 'actionType' not in allocation

    def test_action_key_order_is_a_subsequence(self, one_request):
        """`amount` and `endDate` are OPTIONAL — measured 811/1109 and 867/1109
        in production, tracking actionType."""
        canonical = ['orderApplied', 'actionType', 'amount', 'endDate',
                     'dateApplied']
        for request in one_request['requests']:
            for allocation in request['allocations']:
                for action in allocation['actions']:
                    keys = list(action)
                    assert keys == [k for k in canonical if k in keys]

    def test_order_applied_is_one_based_and_dense(self, one_request):
        for request in one_request['requests']:
            for allocation in request['allocations']:
                orders = [a['orderApplied'] for a in allocation['actions']]
                assert orders == list(range(1, len(orders) + 1))

    def test_amounts_are_one_decimal_strings(self, one_request):
        """`String.format("%.1f", ...)`, and a STRING in JSON, not a number."""
        import re
        one_dp = re.compile(r'^-?\d+\.\d$')
        seen = 0
        for request in one_request['requests']:
            for allocation in request['allocations']:
                for key in ('allocatedAmount', 'remainingAmount'):
                    value = allocation.get(key)
                    if value is None:
                        continue
                    assert isinstance(value, str), f'{key} is not a string'
                    assert one_dp.match(value), f'{key}={value!r}'
                    seen += 1
                for action in allocation['actions']:
                    if 'amount' in action:
                        assert one_dp.match(action['amount'])
                        seen += 1
        assert seen, 'no amounts exercised'

    def test_dates_are_yyyy_mm_dd_strings(self, one_request):
        import re
        iso = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        for request in one_request['requests']:
            assert iso.match(request['requestBeginDate'])
            assert iso.match(request['requestEndDate'])


class TestRequestOrdering:
    @pytest.fixture
    def master(self, xras_client):
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/requests/request/{code}', headers=_auth()).data)
        return body['result']['masters'][0]

    def test_requests_are_ordered_by_end_date(self, master):
        """`xras_request`'s ORDER BY is load-bearing: it sets this order AND
        decides the New/Renewal tie-break."""
        ends = [r['requestEndDate'] for r in master['requests']]
        assert ends == sorted(ends)

    def test_allocations_are_ordered_by_begin_date_descending(self, master):
        for request in master['requests']:
            begins = [a['allocationBeginDate'] for a in request['allocations']]
            assert begins == sorted(begins, reverse=True)

    def test_exactly_one_new_per_master(self, master):
        types = [r['requestType'] for r in master['requests']]
        assert types.count('New') == 1
        assert set(types) <= {'New', 'Renewal'}

    def test_new_is_the_earliest_begin_date_first_wins_on_a_tie(self, master):
        """Java's comparison is a strict `.after()`, so a tie keeps the
        incumbent — the first row in end-date order wins."""
        earliest = min(r['requestBeginDate'] for r in master['requests'])
        first = next(r for r in master['requests']
                     if r['requestBeginDate'] == earliest)
        assert first['requestType'] == 'New'
        for other in master['requests']:
            if other is not first:
                assert other['requestType'] == 'Renewal'

    def test_repeated_calls_are_byte_identical(self, xras_client):
        """Every ORDER BY on this surface must be a TOTAL order.

        `xras_allocation`'s `ORDER BY al.start_date DESC` is not — one
        production project has 11 allocations sharing a start_date — so without
        a primary-key tiebreaker MySQL may return tied rows in any order and two
        identical requests produce different bytes. That is a contract bug in
        its own right, and it is how this was caught: CI failed on the
        case-insensitivity check below while the same run passed locally.
        """
        code = _first_projcode(xras_client)
        for path in (f'/api/xras/v1/requests/request/{code}',
                     '/api/xras/v1/requests/user/benkirk'):
            bodies = {xras_client.get(path, headers=_auth()).data
                      for _ in range(6)}
            assert len(bodies) == 1, f'{path} is not byte-stable across calls'

    def test_masters_are_sorted_by_projcode(self, xras_client):
        """Deliberate divergence: legacy emits Java HashMap bucket order."""
        body = json.loads(xras_client.get(
            '/api/xras/v1/requests/user/benkirk', headers=_auth()).data)
        codes = [m['requestNumber'] for m in body['result']['masters']]
        assert codes == sorted(codes)


class TestRequestsByRole:
    def test_co_pi_is_valid_but_always_empty(self, xras_client):
        """`xras_role` emits only 'Pi' and 'AllocationManager' — nothing ever
        produces the 'CoPi' literal the controller maps to."""
        resp = xras_client.get(
            '/api/xras/v1/requests/role/co_pi/benkirk', headers=_auth())
        assert resp.status_code == 200
        assert json.loads(resp.data)['result']['masters'] == []

    def test_role_segment_is_case_insensitive(self, xras_client):
        lower = xras_client.get(
            '/api/xras/v1/requests/role/pi/benkirk', headers=_auth())
        upper = xras_client.get(
            '/api/xras/v1/requests/role/PI/benkirk', headers=_auth())
        assert lower.data == upper.data

    def test_unknown_role_is_400_rather_than_legacys_500(self, xras_client):
        """Deliberate divergence: legacy's IllegalArgumentException lands in the
        catch-all and produces a 500 carrying only an opaque timestamp."""
        resp = xras_client.get(
            '/api/xras/v1/requests/role/bogus/benkirk', headers=_auth())
        assert resp.status_code == 400
        assert json.loads(resp.data)['message'] == 'Invalid role bogus'

    def test_role_is_validated_before_the_username(self, xras_client):
        """Matching legacy's ordering — a bad role wins over a bad user."""
        resp = xras_client.get(
            '/api/xras/v1/requests/role/bogus/nosuchuser1', headers=_auth())
        assert resp.status_code == 400

    def test_unknown_user_404_uses_different_wording_than_people(
            self, xras_client):
        resp = xras_client.get(
            '/api/xras/v1/requests/user/nosuchuser1', headers=_auth())
        assert resp.status_code == 404
        assert json.loads(resp.data)['message'] == 'User nosuchuser1 not found'


class TestRequestDates:
    def test_dates_are_epoch_millis_not_strings(self, xras_client):
        """The only endpoint whose dates are not yyyy-MM-dd: its DTO holds a raw
        java.util.Date and no date module is configured on the mapper."""
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/dates/requests/{code}', headers=_auth()).data)
        entry = body['result'][0]
        assert list(entry) == [
            'requestNumber', 'requestBeginDate', 'requestEndDate']
        assert isinstance(entry['requestBeginDate'], int)

    def test_millis_land_on_denver_midnight(self, xras_client):
        """A fixed -6 offset would drift an hour for winter dates."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/dates/requests/{code}', headers=_auth()).data)
        for key in ('requestBeginDate', 'requestEndDate'):
            millis = body['result'][0][key]
            local = datetime.fromtimestamp(
                millis / 1000, timezone.utc).astimezone(ZoneInfo('America/Denver'))
            assert (local.hour, local.minute, local.second) == (0, 0, 0)

    def test_comma_list_returns_one_entry_per_projcode(self, xras_client):
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/dates/requests/{code},{code}', headers=_auth()).data)
        assert [e['requestNumber'] for e in body['result']] == [code, code]

    def test_whitespace_after_a_comma_is_not_trimmed(self, xras_client):
        """Legacy's `split(",")` does not trim, so ` CODE` silently misses.
        Reproduced — a client relying on it sees a shorter list, not an error."""
        code = _first_projcode(xras_client)
        body = json.loads(xras_client.get(
            f'/api/xras/v1/dates/requests/{code}, {code}', headers=_auth()).data)
        assert len(body['result']) == 1
