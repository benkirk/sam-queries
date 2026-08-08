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
import pathlib

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


# ---------------------------------------------------------------------------
# POST /api/xras/v1/actions — the capture slice
# ---------------------------------------------------------------------------

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / 'fixtures' / 'xras' / 'actions'
)
ACTION_FIXTURES = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))


def _payload(name):
    return (FIXTURE_DIR / name).read_text()


@pytest.fixture
def action_log(app, monkeypatch):
    """Read and clean up the audit rows the route commits on its own connection.

    The route deliberately commits **outside** ``db.session`` so the row survives a
    handler rollback (see ``webapp/api/xras/actions.py``). That is exactly why the
    suite's per-test SAVEPOINT cannot undo these writes: they land on a different
    connection and are already committed, so they must be deleted explicitly or they
    leak into the shared xdist database.

    Rows are identified by **capturing the ids the route mints**, not by a
    ``id > watermark`` range. Two reasons, both learned the hard way:

    * A ``DELETE ... WHERE id > n`` predicate takes an open-ended gap lock up to the
      supremum, which collides with every other worker's ``INSERT`` — a reliable
      ``1213 Deadlock found`` under ``-n auto``. Deleting by primary key takes record
      locks on exactly the rows involved.
    * A watermark read on a table other workers are concurrently inserting into is
      racy in the other direction too: this test would *see* their rows.
    """
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from webapp.api.xras import actions as actions_module
    from webapp.extensions import db

    minted = []
    original_record = actions_module._record

    def _capturing_record(**kwargs):
        log_id = original_record(**kwargs)
        minted.append(log_id)
        return log_id

    monkeypatch.setattr(actions_module, '_record', _capturing_record)

    class Reader:
        """The rows this test's request(s) created, as detached plain dicts."""

        def rows(self):
            if not minted:
                return []
            with app.app_context(), Session(db.engine) as session:
                found = session.execute(
                    select(XrasActionLog)
                    .where(XrasActionLog.xras_action_log_id.in_(minted))
                    .order_by(XrasActionLog.xras_action_log_id)
                ).scalars().all()
                return [
                    {
                        'id': r.xras_action_log_id,
                        'remote_actor': r.remote_actor,
                        'status': r.status,
                        'action_type': r.action_type,
                        'request_number': r.request_number,
                        'raw_payload': r.raw_payload,
                        'error_messages': r.error_messages,
                        'projcode_result': r.projcode_result,
                        'processed_time': r.processed_time,
                        'received_time': r.received_time,
                        # Sprint B columns. http_status separates the two things
                        # status='failed' conflates (400 vs 422); processed_by and
                        # replay_of_id are the replay chain.
                        'http_status': r.http_status,
                        'processed_by': r.processed_by,
                        'replay_of_id': r.replay_of_id,
                    }
                    for r in found
                ]

        def one(self):
            rows = self.rows()
            assert len(rows) == 1, f'expected exactly 1 audit row, got {len(rows)}'
            return rows[0]

        def by_id(self, log_id):
            for row in self.rows():
                if row['id'] == log_id:
                    return row
            raise AssertionError(f'no captured audit row with id={log_id}')

    yield Reader()

    if minted:
        with app.app_context(), Session(db.engine) as session:
            # DESCENDING id, one PK-targeted DELETE each — NOT a single
            # `IN (...)`, which is what this used to be.
            #
            # `replay_of_id` is a self-FK, so once a test replays an action the
            # minted set contains both a parent and its child. A single statement
            # gives InnoDB no ordering guarantee and it fails with
            # `1451 Cannot delete or update a parent row`. Descending id is
            # provably safe here: a replay is always inserted after the row it
            # replays, so a child's id always exceeds its parent's — deleting high
            # to low removes every child before its parent, to any chain depth.
            #
            # Still by primary key, per the note above: a range predicate would
            # take an open-ended gap lock and deadlock against concurrent inserts.
            for log_id in sorted(minted, reverse=True):
                session.execute(
                    delete(XrasActionLog).where(
                        XrasActionLog.xras_action_log_id == log_id)
                )
            session.commit()


class TestPostActionsAuth:
    """Auth on the write surface, which must match the read surface exactly."""

    PATH = '/api/xras/v1/actions'

    def test_no_credentials_is_the_byte_exact_401(self, xras_client):
        resp = xras_client.post(self.PATH, data='{}',
                                content_type='application/json')
        assert resp.status_code == 401
        assert resp.data.decode() == serialize.UNAUTHENTICATED_BODY
        assert 'WWW-Authenticate' not in resp.headers

    def test_valid_key_without_role_xras_is_403(self, xras_client):
        resp = xras_client.post(self.PATH, data='{}',
                                content_type='application/json',
                                headers=_auth('nobody'))
        assert resp.status_code == 403
        assert resp.headers['Content-Type'].startswith('application/json')

    def test_unauthenticated_post_writes_no_audit_row(self, xras_client, action_log):
        """Auth runs before the view, so a rejected post must leave no trace."""
        xras_client.post(self.PATH, data='{}', content_type='application/json')
        assert action_log.rows() == []


class TestPostActionsCapture:
    """Capture mode: authenticate, parse, audit, return 200, dispatch nothing."""

    PATH = '/api/xras/v1/actions'

    @pytest.mark.parametrize('name', ACTION_FIXTURES)
    def test_every_real_payload_is_accepted_and_audited(
            self, xras_client, action_log, name):
        """The four real production payloads, end to end through the route."""
        resp = xras_client.post(self.PATH, data=_payload(name),
                                content_type='application/json',
                                headers=_auth())
        assert resp.status_code == 200
        assert json.loads(resp.data) == {'message': 'OK', 'result': None}

        row = action_log.one()
        expected = json.loads(_payload(name))
        assert row['status'] == 'received'
        assert row['action_type'] == expected['actionType']
        assert row['request_number'] == expected['requestNumber']
        assert row['remote_actor'] == 'samuel'
        assert row['error_messages'] is None

    def test_raw_payload_is_stored_verbatim(self, xras_client, action_log):
        """Byte-for-byte, before parsing — that is what makes a row replayable."""
        body = _payload('extension_ucub0166_ok.json')
        xras_client.post(self.PATH, data=body,
                         content_type='application/json', headers=_auth())
        assert action_log.one()['raw_payload'] == body

    def test_capture_mode_does_not_set_a_terminal_state(
            self, xras_client, action_log):
        """'received' is precisely true and distinct from 'manual'.

        Operators query ``status='received'`` to see the capture backlog, so it must
        not be conflated with "a human must handle this".
        """
        xras_client.post(self.PATH, data=_payload('new_ncar4253_ok.json'),
                         content_type='application/json', headers=_auth())
        row = action_log.one()
        assert row['status'] == 'received'
        assert row['processed_time'] is None
        assert row['projcode_result'] is None

    def test_the_spec_documented_url_form_also_works(self, xras_client, action_log):
        """All real posts use the bare form, but the ACCESS spec documents this one.

        If the broker is ever corrected to match its own docs, every post would 404.
        """
        resp = xras_client.post(
            '/api/xras/v1/actions/388536/1445132/New',
            data=_payload('new_ncar4232_failed.json'),
            content_type='application/json', headers=_auth())
        assert resp.status_code == 200
        assert action_log.one()['request_number'] == 'NCAR4232'

    def test_dispatch_marks_manual_when_capture_is_off(
            self, app, xras_client, action_log):
        """With capture off and no handlers registered, every type parks as 'manual'.

        Legacy answers a bare 200 here too, but leaves no record that SAM quietly
        deferred the action to a human — the distinction this table exists to make.
        """
        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
        try:
            resp = xras_client.post(
                self.PATH, data=_payload('extension_ufsu0023_failed.json'),
                content_type='application/json', headers=_auth())
        finally:
            app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True

        assert resp.status_code == 200
        assert json.loads(resp.data) == {'message': 'OK', 'result': None}
        row = action_log.one()
        assert row['status'] == 'manual'
        assert row['processed_time'] is not None

    def test_both_timestamps_come_from_the_same_clock(
            self, app, xras_client, action_log):
        """``processed_time`` must not precede ``received_time``.

        The column's ``DEFAULT CURRENT_TIMESTAMP`` resolves in the **MySQL server's**
        timezone, which is UTC in the dev/CI container, while SAM's convention is
        naive-Mountain and ``_finish`` uses ``datetime.now()``. Letting the default
        supply ``received_time`` therefore put it 6 hours ahead of ``processed_time``
        — a processed row that looked like it completed before it arrived. Both now
        come from the app clock; this is the guard.
        """
        from datetime import datetime, timedelta

        before = datetime.now()
        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
        try:
            xras_client.post(
                self.PATH, data=_payload('extension_ucub0166_ok.json'),
                content_type='application/json', headers=_auth())
        finally:
            app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True
        after = datetime.now()

        row = action_log.one()
        assert row['received_time'] <= row['processed_time']
        # And both sit inside the window the request actually spanned, which a
        # UTC-defaulted timestamp would miss by hours.
        margin = timedelta(minutes=5)
        assert before - margin <= row['received_time'] <= after + margin


class TestDispatchArms:
    """The three terminal states the route maps, with a stub handler standing in.

    No real handler is registered yet, so ``processed`` and ``failed`` are unreachable
    from a live post — but the route code that maps them ships now, and code that
    cannot be exercised is code that has never run. A stub proves the wiring: the audit
    row transitions, the status code, and the 422 body carrying the accumulated list.
    """

    PATH = '/api/xras/v1/actions'

    @pytest.fixture
    def dispatching(self, app):
        """Capture off, with a clean handler registry restored afterwards."""
        from sam.xras import dispatch

        saved_handlers = dict(dispatch._HANDLERS)
        dispatch._HANDLERS.clear()
        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
        try:
            yield dispatch
        finally:
            app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True
            app.config.pop('XRAS_ACTIONS_ENABLED', None)
            dispatch._HANDLERS.clear()
            dispatch._HANDLERS.update(saved_handlers)

    def test_a_processed_action_records_its_projcode_and_answers_200(
            self, xras_client, action_log, dispatching):
        """The status this table has never once held."""
        dispatching.register('extend', lambda s, a: dispatching.DispatchResult(
            status='processed', service='extend', projcode='UCUB0166'))

        resp = xras_client.post(
            self.PATH, data=_payload('extension_ucub0166_ok.json'),
            content_type='application/json', headers=_auth())

        assert resp.status_code == 200
        row = action_log.one()
        assert row['status'] == 'processed'
        assert row['projcode_result'] == 'UCUB0166'
        assert row['processed_time'] is not None

    def test_a_rejected_action_is_422_carrying_the_ordered_error_list(
            self, xras_client, action_log, dispatching):
        """The headline deliverable — XRAS admins read this body directly. It is the
        accumulated list, in order, not a summary."""
        from sam.xras.errors import ActionErrors

        def rejecting(session, action):
            errs = ActionErrors()
            errs.report('Missing title')
            errs.report('PI jdoe is not in database')
            errs.raise_if_any()

        dispatching.register('extend', rejecting)
        resp = xras_client.post(
            self.PATH, data=_payload('extension_ucub0166_ok.json'),
            content_type='application/json', headers=_auth())

        assert resp.status_code == 422
        body = json.loads(resp.data)
        assert body['result']['errors'] == [
            'Missing title', 'PI jdoe is not in database']
        assert body['message'] == '2 errors processing action'

        row = action_log.one()
        assert row['status'] == 'failed'
        assert row['http_status'] == 422
        assert row['error_messages'] == 'Missing title\nPI jdoe is not in database'

    def test_a_disabled_action_type_parks_as_manual_without_running_the_handler(
            self, app, xras_client, action_log, dispatching):
        """The triage lever, end to end. An operator narrowing ``XRAS_ACTIONS_ENABLED``
        at 3am must get an audited ``manual`` row, not a dropped action."""
        ran = []
        dispatching.register('extend', lambda s, a: ran.append(1) or
                             dispatching.DispatchResult(status='processed',
                                                        service='extend'))
        app.config['XRAS_ACTIONS_ENABLED'] = 'Supplement'

        resp = xras_client.post(
            self.PATH, data=_payload('extension_ucub0166_ok.json'),
            content_type='application/json', headers=_auth())

        assert resp.status_code == 200
        assert ran == []
        assert action_log.one()['status'] == 'manual'

    def test_an_enabled_action_type_still_dispatches(
            self, app, xras_client, action_log, dispatching):
        """The other half of the lever: narrowing it must not disable everything."""
        dispatching.register('extend', lambda s, a: dispatching.DispatchResult(
            status='processed', service='extend', projcode='UCUB0166'))
        app.config['XRAS_ACTIONS_ENABLED'] = 'Extension,Supplement'

        xras_client.post(self.PATH, data=_payload('extension_ucub0166_ok.json'),
                         content_type='application/json', headers=_auth())
        assert action_log.one()['status'] == 'processed'

    def test_capture_mode_outranks_an_enabled_type(
            self, app, xras_client, action_log, dispatching):
        """The interlock is not the lever. While legacy is still the system of record,
        no allowlist setting may cause a dispatch — a double-apply against live
        allocations has no undo."""
        ran = []
        dispatching.register('extend', lambda s, a: ran.append(1) or
                             dispatching.DispatchResult(status='processed',
                                                        service='extend'))
        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True
        app.config['XRAS_ACTIONS_ENABLED'] = 'all'

        xras_client.post(self.PATH, data=_payload('extension_ucub0166_ok.json'),
                         content_type='application/json', headers=_auth())
        assert ran == []
        assert action_log.one()['status'] == 'received'


class TestPostActionsErrors:
    """The status-code split that is this project's headline improvement.

    Legacy answers 500 with an opaque timestamp for a malformed body *and* for a
    failed validation, and 200 for an action it silently parked. All four are
    distinguished here.
    """

    PATH = '/api/xras/v1/actions'

    def test_malformed_json_is_400_not_500(self, xras_client, action_log):
        resp = xras_client.post(self.PATH, data='{"actionType": ',
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert body['result']['errors'][0].startswith('Malformed JSON body')

    def test_malformed_json_still_writes_a_row_with_null_action_type(
            self, xras_client, action_log):
        """The case the audit trail matters most for: we cannot even name the action."""
        xras_client.post(self.PATH, data='not json at all',
                         content_type='application/json', headers=_auth())
        row = action_log.one()
        assert row['status'] == 'failed'
        assert row['action_type'] is None
        assert row['request_number'] is None
        assert row['raw_payload'] == 'not json at all'
        assert 'Malformed JSON body' in row['error_messages']

    def test_a_json_array_body_is_400(self, xras_client, action_log):
        resp = xras_client.post(self.PATH, data='[1, 2, 3]',
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 400
        assert 'Expected a JSON object' in action_log.one()['error_messages']

    def test_schema_rejection_is_422_carrying_an_ordered_list(
            self, xras_client, action_log):
        """A bool in a String-declared field is the one thing the schema rejects.

        Everything else about the payload is tolerated by design, so this is the
        available proof that the 422 path reports rather than swallows.
        """
        body = json.dumps({'actionType': 'New', 'requestNumber': 'NCAR9999',
                           'awardPeriod': True})
        resp = xras_client.post(self.PATH, data=body,
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 422
        payload = json.loads(resp.data)
        assert payload['message'] == '1 error processing action'
        assert payload['result']['errors'] == ['awardPeriod: Not a valid string.']

        row = action_log.one()
        assert row['status'] == 'failed'
        assert row['action_type'] == 'New'
        assert row['request_number'] == 'NCAR9999'
        assert row['error_messages'] == 'awardPeriod: Not a valid string.'

    def test_error_messages_accumulate_rather_than_short_circuit(
            self, xras_client, action_log):
        """Legacy gathers every problem into one ordered list and raises once.

        Reporting all of them lets an operator fix a request in one pass instead of
        five, which is the whole point of the 422 body.
        """
        body = json.dumps({
            'awardPeriod': True,
            'fos': [{'fosTypeId': True}],
            'resources': [{'awardedAmount': True}],
        })
        resp = xras_client.post(self.PATH, data=body,
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 422
        errors = json.loads(resp.data)['result']['errors']
        assert len(errors) == 3
        assert sorted(errors) == [
            'awardPeriod: Not a valid string.',
            'fos.0.fosTypeId: Not a valid string.',
            'resources.0.awardedAmount: Not a valid string.',
        ]
        assert action_log.one()['error_messages'].count('\n') == 2

    def test_an_empty_object_body_is_accepted_and_audited(
            self, xras_client, action_log):
        """Structurally valid but empty: not a 400, and not a schema rejection.

        The schema must get out of the way so the *handlers* can report what is
        missing into the accumulated 422 list.
        """
        resp = xras_client.post(self.PATH, data='{}',
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 200
        row = action_log.one()
        assert row['status'] == 'received'
        assert row['action_type'] is None


class TestReplay:
    """``webapp.api.xras.replay.replay_action`` — the operator's re-submit path.

    Exercised at the function level rather than through the dashboard route: the
    route is a five-line wrapper whose interesting behaviour is the permission
    gate (covered in ``tests/unit/test_xras_dashboard.py``), while everything that
    can actually go wrong lives here.

    Every row these tests create is minted through ``actions._record``, so the
    ``action_log`` fixture captures and deletes them. That is not incidental —
    ``replay.py`` calls ``actions._record`` through the *module attribute* for
    exactly this reason. A ``from .actions import _record`` would bind at import
    time, sail past the fixture's monkeypatch, and leak committed rows into the
    shared xdist database.
    """

    def _seed(self, xras_client, name='extension_ucub0166_ok.json'):
        """Post a real payload and return the id of the row it created."""
        resp = xras_client.post(
            '/api/xras/v1/actions', data=_payload(name),
            content_type='application/json', headers=_auth())
        assert resp.status_code == 200
        return resp.get_json()  # body is {'message': 'OK', 'result': None}

    def test_replay_writes_a_new_linked_row(self, app, xras_client, action_log):
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        assert new_id != original['id'], 'replay must create a row, not edit one'
        replayed = action_log.by_id(new_id)
        assert replayed['replay_of_id'] == original['id']
        assert replayed['processed_by'] == 'benkirk'

    def test_replay_preserves_the_payload_byte_for_byte(
            self, app, xras_client, action_log):
        """``raw_payload`` is byte-exact on purpose — a re-serialisation would
        silently make the replay a different request from the one that arrived."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        assert action_log.by_id(new_id)['raw_payload'] == original['raw_payload']

    def test_replay_inherits_the_original_remote_actor(
            self, app, xras_client, action_log):
        """The bytes still originated at XRAS, so ``remote_actor`` stays theirs.

        The human goes in ``processed_by`` — which is also the only column wide
        enough for a username (``remote_actor`` is varchar(11))."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        assert action_log.by_id(new_id)['remote_actor'] == original['remote_actor']

    def test_replay_does_not_stamp_the_original(self, app, xras_client, action_log):
        """Marking the parent 'replayed' would destroy its own outcome, which IS
        the audit record. "Has been replayed" is derived from the relationship."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            replay_action(original['id'], actor='benkirk')

        after = action_log.by_id(original['id'])
        assert after['status'] == original['status'] == 'received'
        assert after['processed_by'] is None
        assert after['replay_of_id'] is None

    def test_replay_lands_replayed_under_capture_mode(
            self, app, xras_client, action_log):
        """Capture mode is on because legacy is still applying these actions, so a
        dispatching replay would double-apply. It re-validates instead."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True
        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        row = action_log.by_id(new_id)
        assert row['status'] == 'replayed'
        assert row['processed_time'] is not None

    def test_replay_dispatches_when_capture_is_off(
            self, app, xras_client, action_log):
        """With the kill switch off a replay behaves exactly like a fresh post —
        which today means the manual-fallback path, since no handler is registered."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
        try:
            with app.app_context():
                new_id = replay_action(original['id'], actor='benkirk')
        finally:
            app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True

        assert action_log.by_id(new_id)['status'] == 'manual'

    def test_replaying_a_rejected_payload_fails_again(
            self, app, xras_client, action_log):
        """A replay must be able to FAIL, and fail the same way.

        This is the regression check the feature buys while capture mode is on: a
        payload harvested months ago is re-validated against today's schema code.
        """
        from webapp.api.xras.replay import replay_action

        resp = xras_client.post(
            '/api/xras/v1/actions',
            data=json.dumps({'actionType': 'New', 'requestNumber': 'NCAR9999',
                             'awardPeriod': True}),
            content_type='application/json', headers=_auth())
        assert resp.status_code == 422
        original = action_log.one()
        assert original['status'] == 'failed'

        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        row = action_log.by_id(new_id)
        assert row['status'] == 'failed'
        assert row['http_status'] == 422
        assert row['error_messages'] == 'awardPeriod: Not a valid string.'
        assert row['replay_of_id'] == original['id']

    def test_replaying_a_malformed_body_records_a_400(
            self, app, xras_client, action_log):
        from webapp.api.xras.replay import replay_action

        resp = xras_client.post('/api/xras/v1/actions', data='{"actionType": ',
                                content_type='application/json', headers=_auth())
        assert resp.status_code == 400
        original = action_log.one()

        with app.app_context():
            new_id = replay_action(original['id'], actor='benkirk')

        row = action_log.by_id(new_id)
        assert row['status'] == 'failed'
        assert row['http_status'] == 400
        assert row['action_type'] is None

    def test_replaying_a_replay_chains_rather_than_flattening(
            self, app, xras_client, action_log):
        """``replay_of_id`` points at whatever was replayed, so the lineage stays a
        tree. Collapsing it to the root would lose who replayed what, when."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            first = replay_action(original['id'], actor='benkirk')
            second = replay_action(first, actor='mcjones')

        assert action_log.by_id(first)['replay_of_id'] == original['id']
        assert action_log.by_id(second)['replay_of_id'] == first
        assert action_log.by_id(second)['processed_by'] == 'mcjones'

    def test_replaying_a_missing_id_raises_lookup_error(self, app, action_log):
        from webapp.api.xras.replay import replay_action

        with app.app_context():
            with pytest.raises(LookupError):
                replay_action(999_999_999, actor='benkirk')

    def test_a_long_actor_name_is_truncated_not_a_dataerror(
            self, app, xras_client, action_log):
        """``processed_by`` is varchar(35). An over-long actor must not turn an
        audit write into a 500 — the row is the whole point."""
        from webapp.api.xras.replay import replay_action

        self._seed(xras_client)
        original = action_log.one()

        with app.app_context():
            new_id = replay_action(original['id'], actor='x' * 80)

        assert action_log.by_id(new_id)['processed_by'] == 'x' * 35


class TestHttpStatusColumn:
    """``http_status`` — added in Sprint B because ``status='failed'`` conflates a
    malformed body (400) with a schema rejection (422), and triage needs both."""

    def test_capture_records_200(self, xras_client, action_log):
        xras_client.post('/api/xras/v1/actions',
                         data=_payload('new_ncar4253_ok.json'),
                         content_type='application/json', headers=_auth())
        assert action_log.one()['http_status'] == 200

    def test_malformed_body_records_400(self, xras_client, action_log):
        xras_client.post('/api/xras/v1/actions', data='not json at all',
                         content_type='application/json', headers=_auth())
        row = action_log.one()
        assert row['status'] == 'failed' and row['http_status'] == 400

    def test_schema_rejection_records_422(self, xras_client, action_log):
        xras_client.post(
            '/api/xras/v1/actions',
            data=json.dumps({'actionType': 'New', 'awardPeriod': True}),
            content_type='application/json', headers=_auth())
        row = action_log.one()
        assert row['status'] == 'failed' and row['http_status'] == 422

    def test_the_two_failures_are_distinguishable(self, xras_client, action_log):
        """The point of the column, stated as a test: both are status='failed'."""
        xras_client.post('/api/xras/v1/actions', data='{oops',
                         content_type='application/json', headers=_auth())
        xras_client.post(
            '/api/xras/v1/actions',
            data=json.dumps({'actionType': 'New', 'awardPeriod': True}),
            content_type='application/json', headers=_auth())

        rows = action_log.rows()
        assert len(rows) == 2
        assert {r['status'] for r in rows} == {'failed'}
        assert sorted(r['http_status'] for r in rows) == [400, 422]

    def test_an_over_long_action_type_does_not_break_the_audit_write(
            self, xras_client, action_log):
        """``action_type`` is varchar(32) and on the 422 path it comes straight off
        an UNVALIDATED payload dict. It must be truncated, not allowed to raise."""
        resp = xras_client.post(
            '/api/xras/v1/actions',
            data=json.dumps({'actionType': 'N' * 200, 'awardPeriod': True}),
            content_type='application/json', headers=_auth())
        assert resp.status_code == 422
        assert action_log.one()['action_type'] == 'N' * 32
