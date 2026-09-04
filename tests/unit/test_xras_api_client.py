"""The outbound XRAS Allocations API client.

Mirrors ``tests/unit/test_award_providers.py``: canned payloads as module
constants with invented identities, transport exercised through a mocked
``session.request`` with ``time.sleep`` no-op'd, and the three-outcome model
(**found / not-found / unreachable**) asserted explicitly, because every
consumer downstream branches on it.

No test here touches the network. The live surface is
``scripts/xras/probe_outgoing.py``, which is opt-in and skips without a key.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
import requests

from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api import lookups as xras_lookups
from sam.integration.xras_api import people as xras_people
from sam.integration.xras_api.base import (
    XrasApiNotConfigured,
    XrasSourceUnavailable,
)
from sam.integration.xras_api.client import XrasApiClient, _XrasTransport
from sam.integration.xras_api.config import XrasApiConfig, xras_api_configured

pytestmark = pytest.mark.unit


# canned payloads
# Shapes copied from the live API; identities invented. If XRAS renames a
# field, the probe script catches it against production and these break here.

PERSON = {
    'username': 'invented-user-00001',
    'firstName': 'Ada', 'middleName': None, 'lastName': 'Invented',
    'email': 'ada@example.invalid', 'phone': None,
    'organization': 'Example University',
    'academicStatus': 'Graduate Student',
    'residenceCountry': 'United States',
    'isReconciled': False, 'orcid': None, 'hasOrcidToken': False,
}

RESOURCES = [
    {'resourceId': 1, 'resourceName': 'Example HPC',
     'resourceRepositoryKey': 101, 'productionBeginDate': '2024-01-01',
     'productionEndDate': None},
    {'resourceId': 2, 'resourceName': 'Example Storage',
     'resourceRepositoryKey': 102, 'productionBeginDate': '2024-01-01',
     'productionEndDate': None},
]


def _request(request_id: int, number: str) -> dict:
    return {
        'requestId': request_id, 'requestNumber': number,
        'requestStatus': 'Approved', 'requestType': 'New',
        'roles': [{'person': dict(PERSON),
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13,
                              'beginDate': '2026-01-01', 'endDate': None,
                              'isAccountToBeCreated': True}]}],
        'actions': [], 'fos': [], 'grants': [],
    }


def _envelope(result):
    """XRAS wraps every payload in ``{message, result}``."""
    return {'message': 'OK', 'result': result}


def _response(status, payload=None, text=''):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload if payload is not None else {}
    return resp


def _client(monkeypatch, responses, **config_kwargs):
    """A configured client whose transport is a scripted list of responses."""
    config = XrasApiConfig(enabled=True, api_key='not-a-real-key',
                           **config_kwargs)
    client = XrasApiClient(config)
    monkeypatch.setattr(client.session, 'request',
                        MagicMock(side_effect=responses))
    monkeypatch.setattr('sam.integration.xras_api.client.time.sleep',
                        lambda _s: None)
    return client


@pytest.fixture(autouse=True)
def _reset_xras_cache(monkeypatch):
    """Start each test with the XRAS cache disabled; reset it after.

    ``delenv`` is load-bearing: CI runs pytest inside the compose ``webapp``
    container, where ``CACHE_REDIS_URL`` is force-set. With Redis the adapter
    is a ``RedisTTLAdapter``, so dropping the in-process memo would leave the
    keyspace intact and xdist workers would share one Redis.
    """
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    xras_cache._CACHE.reset_for_tests()
    yield
    xras_cache._CACHE.reset_for_tests(disabled=False)


# configuration

class TestConfiguration:
    """Fail-closed, and both halves of the predicate matter."""

    def test_it_is_off_by_default(self, monkeypatch):
        for key in ('XRAS_OUTGOING_ENABLED', 'XRAS_API_KEY'):
            monkeypatch.delenv(key, raising=False)
        assert XrasApiConfig.from_environment().configured is False
        assert xras_api_configured() is False

    def test_a_key_without_the_lever_stays_silent(self, monkeypatch):
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        assert xras_api_configured() is False

    def test_the_lever_without_a_key_stays_silent(self, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        assert xras_api_configured() is False

    def test_both_halves_configure_it(self, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        assert xras_api_configured() is True

    def test_defaults_match_the_probed_process(self, monkeypatch):
        for key in ('XRAS_API_BASE', 'XRAS_ALLOCATIONS_PROCESS',
                    'XRAS_API_USER'):
            monkeypatch.delenv(key, raising=False)
        config = XrasApiConfig.from_environment()
        assert config.base_url == 'https://api.xras.org'
        assert config.allocations_process == 'NCAR'
        assert config.api_user == 'arcguest'

    def test_a_trailing_slash_on_the_base_url_is_dropped(self, monkeypatch):
        monkeypatch.setenv('XRAS_API_BASE', 'https://api.xras.org/')
        assert XrasApiConfig.from_environment().base_url == 'https://api.xras.org'

    def test_unconfigured_raises_a_subclass_of_unavailable(self, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        with pytest.raises(XrasApiNotConfigured):
            XrasApiClient.from_environment()
        # The point of the subclass: a caller that only knows about
        # "could not ask" is already correct.
        with pytest.raises(XrasSourceUnavailable):
            XrasApiClient.from_environment()

    def test_the_summary_never_carries_the_key(self, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'super-secret-value')
        summary = XrasApiConfig.from_environment().summary()
        assert summary['api_key_set'] is True
        assert 'super-secret-value' not in repr(summary)
        assert not any('key' == k or k.endswith('_key') for k in summary)


# the write surface must not exist

class TestItIsStructurallyReadOnly:
    """The same credential can create requests and merge people (§ 4.7).

    So GET-only is enforced by there being nothing else to call, not by
    reviewers remembering.
    """

    def test_the_client_exposes_no_write_verb(self):
        forbidden = ('post', 'put', 'patch', 'delete', 'request', 'post_json',
                     'put_json', 'patch_json', 'delete_json', 'send')
        for name in forbidden:
            assert not hasattr(XrasApiClient, name), \
                f'XrasApiClient.{name} exists — the client must stay GET-only'

    def test_the_only_transport_primitive_is_get(self):
        # The transport primitive lives on the shared base now; the read client
        # adds only read verbs on top of it. The GET-only invariant belongs on
        # whichever class actually issues the request, so pin it there — the
        # write verbs are the admin subclass's ``_write``, not on this base.
        source = inspect.getsource(_XrasTransport)
        assert "'GET'" in source
        for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'"):
            assert verb not in source, f'{verb} appears in the transport base'
        # And the read client itself still carries no HTTP-verb literal at all.
        client_source = inspect.getsource(XrasApiClient)
        for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'"):
            assert verb not in client_source, f'{verb} appears in the client'


# transport

class TestTransport:
    """Retry policy: a 4xx is an answer, a 5xx is worth another attempt."""

    def test_404_is_none_not_an_error(self, monkeypatch):
        client = _client(monkeypatch, [_response(404, {'message': 'not found'})])
        assert client.get_person('nobody') is None
        assert client.session.request.call_count == 1

    def test_4xx_does_not_retry(self, monkeypatch):
        # 401 is what an unprovisioned context returns — a configuration
        # fact, not an outage, and retrying cannot change it.
        client = _client(monkeypatch, [_response(401, text='denied')])
        with pytest.raises(XrasSourceUnavailable):
            client.get_resources()
        assert client.session.request.call_count == 1

    def test_5xx_retries_then_succeeds(self, monkeypatch):
        client = _client(monkeypatch, [_response(503),
                                       _response(200, _envelope(RESOURCES))])
        assert client.get_resources() == RESOURCES
        assert client.session.request.call_count == 2

    def test_exhausted_retries_raise(self, monkeypatch):
        client = _client(monkeypatch, [_response(503)] * 3)
        with pytest.raises(XrasSourceUnavailable):
            client.get_resources()
        assert client.session.request.call_count == 3

    def test_network_error_retries(self, monkeypatch):
        client = _client(monkeypatch, [requests.ConnectionError('down'),
                                       _response(200, _envelope(RESOURCES))])
        assert client.get_resources() == RESOURCES
        assert client.session.request.call_count == 2

    def test_a_non_json_body_is_unavailable(self, monkeypatch):
        resp = _response(200)
        resp.json.side_effect = ValueError('Expecting value')
        client = _client(monkeypatch, [resp])
        with pytest.raises(XrasSourceUnavailable):
            client.get_resources()

    def test_every_call_carries_the_four_headers(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(RESOURCES))])
        client.get_resources()
        headers = client.session.headers
        assert headers['XA-API-KEY'] == 'not-a-real-key'
        assert headers['XA-ALLOCATIONS-PROCESS'] == 'NCAR'
        assert headers['XA-USER'] == 'arcguest'

    def test_the_context_is_hardcoded_to_report(self, monkeypatch):
        """`submit` cannot see the Reports family at all — there is no knob."""
        client = _client(monkeypatch, [_response(200, _envelope(RESOURCES))])
        assert client.session.headers['XA-CONTEXT'] == 'report'
        assert 'context' not in inspect.signature(
            XrasApiClient.__init__).parameters

    def test_the_timeout_is_always_passed(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(RESOURCES))])
        client.get_resources()
        assert client.session.request.call_args.kwargs['timeout'] == 10


# envelope and endpoint shapes

class TestEndpoints:

    def test_the_envelope_is_unwrapped_centrally(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(PERSON))])
        assert client.get_person('invented-user-00001') == PERSON

    def test_a_body_without_an_envelope_passes_through(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, RESOURCES)])
        assert client.get_resources() == RESOURCES

    def test_a_null_result_reads_as_not_found(self, monkeypatch):
        # XRAS answers `{"message": "...", "result": null}` for a route that
        # exists and matched nothing — the same meaning as a 404 to a caller.
        client = _client(monkeypatch, [_response(200, _envelope(None))])
        assert client.get_person('nobody') is None

    def test_the_person_carries_what_account_creation_needs(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(PERSON))])
        person = client.get_person('invented-user-00001')
        # residenceCountry is the field the inbound payload does NOT carry.
        assert person['residenceCountry'] == 'United States'
        assert person['isReconciled'] is False

    def test_a_username_is_url_quoted(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(PERSON))])
        client.get_person('a b/c')
        url = client.session.request.call_args.args[1]
        assert url.endswith('/v1/people/a%20b%2Fc')

    def test_request_lookup_uses_the_reports_path(self, monkeypatch):
        """`GET /v1/requests/<number>` 401s — the number is not its key."""
        client = _client(monkeypatch,
                         [_response(200, _envelope([_request(1, 'NCAR0001')]))])
        row = client.get_request_line('NCAR0001')
        assert row['requestNumber'] == 'NCAR0001'
        url = client.session.request.call_args.args[1]
        assert '/v1/reports/request_numbers/NCAR0001' in url

    def test_the_family_lookup_keeps_every_line_not_just_the_first(self, monkeypatch):
        """A project with a New and a Renewal returns a list; the family needs both."""
        client = _client(monkeypatch, [_response(200, _envelope(
            [_request(111, 'NCAR0001'), _request(222, 'NCAR0001')]))])
        family = client.get_request_family_by_number('NCAR0001')
        assert [r['requestId'] for r in family] == [111, 222]

    def test_the_family_lookup_wraps_a_bare_object_and_survives_empty(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope(_request(5, 'X')))])
        assert [r['requestId'] for r in client.get_request_family_by_number('X')] == [5]
        client = _client(monkeypatch, [_response(404, {'message': 'no'})])
        assert client.get_request_family_by_number('X') == []

    def test_search_people_hits_the_search_route(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([PERSON]))])
        assert client.search_people('Invented') == [PERSON]
        call = client.session.request.call_args
        assert call.args[1].endswith('/v1/search/people')
        assert call.kwargs['params'] == {'q': 'Invented'}

    def test_person_roles_hits_the_reports_username_route(self, monkeypatch):
        payload = {'panels': [], 'requestRoles': []}
        client = _client(monkeypatch, [_response(200, _envelope(payload))])
        assert client.get_person_roles('Invented') == payload
        url = client.session.request.call_args.args[1]
        assert url.endswith('/v1/reports/username/Invented')

    def test_person_roles_url_quotes_the_username(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope({}))])
        client.get_person_roles('a b/c')
        url = client.session.request.call_args.args[1]
        assert url.endswith('/v1/reports/username/a%20b%2Fc')

    def test_person_roles_404_reads_as_none(self, monkeypatch):
        # A merged-away placeholder 404s here exactly as get_person does.
        client = _client(monkeypatch, [_response(404, {})])
        assert client.get_person_roles('gone-user-00001') is None

    def test_get_opportunity_hits_the_single_id_route(self, monkeypatch):
        opp = {'opportunityId': 532220, 'opportunityName': 'Small'}
        client = _client(monkeypatch, [_response(200, _envelope(opp))])
        assert client.get_opportunity(532220) == opp
        url = client.session.request.call_args.args[1]
        assert url.endswith('/v1/opportunities/532220')

    def test_get_opportunity_404_reads_as_none(self, monkeypatch):
        client = _client(monkeypatch, [_response(404, {})])
        assert client.get_opportunity(999999) is None

    def test_get_fos_types_hits_the_types_route(self, monkeypatch):
        fos = [{'fosTypeId': 500032, 'fosName': 'Regional Climate'}]
        client = _client(monkeypatch, [_response(200, _envelope(fos))])
        assert client.get_fos_types() == fos
        url = client.session.request.call_args.args[1]
        assert url.endswith('/v1/types/fos')


class TestPagination:
    """Descending requestId, strictly-less-than, smallest id asks for the next."""

    def test_it_walks_pages_until_they_run_out(self, monkeypatch):
        page1 = [_request(30, 'NCAR0030'), _request(29, 'NCAR0029')]
        page2 = [_request(28, 'NCAR0028')]
        client = _client(monkeypatch, [_response(200, _envelope(page1)),
                                       _response(200, _envelope(page2))])
        rows = list(client.iter_requests(status='Approved', page_size=2))
        assert [r['requestNumber'] for r in rows] == \
            ['NCAR0030', 'NCAR0029', 'NCAR0028']
        # A short page ends the walk without a third round trip.
        assert client.session.request.call_count == 2

    def test_the_cursor_is_the_smallest_id_seen(self, monkeypatch):
        page1 = [_request(30, 'NCAR0030'), _request(29, 'NCAR0029')]
        client = _client(monkeypatch, [_response(200, _envelope(page1)),
                                       _response(200, _envelope([]))])
        list(client.iter_requests(page_size=2))
        second = client.session.request.call_args_list[1]
        assert second.kwargs['params']['prevMinRequestId'] == 29

    def test_max_pages_stops_the_walk(self, monkeypatch):
        page = [_request(30, 'NCAR0030'), _request(29, 'NCAR0029')]
        client = _client(monkeypatch, [_response(200, _envelope(page))] * 5)
        rows = list(client.iter_requests(page_size=2, max_pages=2))
        assert len(rows) == 4
        assert client.session.request.call_count == 2

    def test_a_page_that_does_not_advance_stops_the_walk(self, monkeypatch):
        """Defensive: a server repeating a page would otherwise loop forever."""
        page = [_request(30, 'NCAR0030'), _request(29, 'NCAR0029')]
        client = _client(monkeypatch, [_response(200, _envelope(page))] * 5)
        pages = list(client.iter_request_pages(page_size=2))
        assert len(pages) == 2      # the repeat is detected on the second page

    def test_pages_are_countable_so_a_cap_is_never_silent(self, monkeypatch):
        """The page-level primitive exists so a caller can see it was capped."""
        page = [_request(i, f'NCAR{i:04d}') for i in range(40, 38, -1)]
        client = _client(monkeypatch, [_response(200, _envelope(page)),
                                       _response(200, _envelope(
                                           [_request(20, 'NCAR0020'),
                                            _request(19, 'NCAR0019')]))])
        pages = list(client.iter_request_pages(page_size=2, max_pages=2))
        assert len(pages) == 2

    def test_status_is_passed_through_as_a_filter(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([]))])
        client.get_requests_page(status='Approved', limit=200)
        params = client.session.request.call_args.kwargs['params']
        assert params == {'limit': 200, 'status': 'Approved'}

    def test_active_sends_active_and_drops_status(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([]))])
        client.get_requests_page(status=None, limit=200, active=True)
        params = client.session.request.call_args.kwargs['params']
        assert params == {'limit': 200, 'active': 'true'}

    def test_active_false_serializes_as_the_string_false(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([]))])
        client.get_requests_page(status=None, active=False)
        assert client.session.request.call_args.kwargs['params']['active'] == 'false'

    def test_active_and_status_together_raise(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([]))])
        with pytest.raises(ValueError, match='mutually exclusive'):
            client.get_requests_page(status='Approved', active=True)

    def test_iter_supersedes_status_with_active(self, monkeypatch):
        """The iterator drops `status` when `active` is given (they conflict)."""
        page = [_request(30, 'NCAR0030'), _request(29, 'NCAR0029')]
        client = _client(monkeypatch, [_response(200, _envelope(page)),
                                       _response(200, _envelope([]))])
        list(client.iter_request_pages(page_size=2, active=True))  # status defaults
        params = client.session.request.call_args_list[0].kwargs['params']
        assert params.get('active') == 'true' and 'status' not in params


# caching

class TestCaching:

    def test_a_person_lookup_is_memoised(self, monkeypatch):
        xras_cache._adapters.clear()          # re-enable; the fixture pins it off
        calls = []

        def fake_person(self, username):
            calls.append(username)
            return dict(PERSON)

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_person', fake_person)

        xras_people.get_person('invented-user-00001')
        xras_people.get_person('invented-user-00001')
        assert len(calls) == 1

    def test_the_key_is_casefolded(self, monkeypatch):
        xras_cache._adapters.clear()
        calls = []
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_person',
                            lambda self, u: calls.append(u) or dict(PERSON))
        xras_people.get_person('Invented-User-00001')
        xras_people.get_person('invented-user-00001')
        assert len(calls) == 1

    def test_an_outage_is_never_memoised(self, monkeypatch):
        # Re-enable — with the cache off this would pass vacuously, since
        # nothing is memoised either way.
        xras_cache._adapters.clear()
        calls = []

        def boom(self, username):
            calls.append(username)
            raise XrasSourceUnavailable('down')

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_person', boom)

        for _ in range(2):
            with pytest.raises(XrasSourceUnavailable):
                xras_people.get_person('invented-user-00001')
        assert len(calls) == 2

    def test_a_definite_negative_is_memoised(self, monkeypatch):
        """A 404 is a real answer; re-asking costs a round trip per render."""
        xras_cache._adapters.clear()
        calls = []
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_person',
                            lambda self, u: calls.append(u) or None)
        assert xras_people.get_person('nobody') is None
        assert xras_people.get_person('nobody') is None
        assert len(calls) == 1

    def test_it_is_registered_with_the_webapp_facade(self):
        """One line in `_BUCKETED_CACHE_MODULES` buys the admin card row."""
        from webapp.caching import _BUCKETED_CACHE_MODULES
        assert 'sam.integration.xras_api.cache' in _BUCKETED_CACHE_MODULES

    def test_the_cli_can_scope_a_refresh_to_it(self):
        """The click.Choice is hand-maintained — it drifts silently."""
        from cli.cmds.admin import cli
        choice = next(p for p in cli.commands['cache'].params
                      if p.name == 'category')
        assert 'xras_api' in choice.type.choices

    def test_the_bucket_prefixes_are_namespaced(self):
        """Bucket names are global Redis key prefixes."""
        assert all(p.startswith('xras_') for p in xras_cache._CACHE.prefixes)


class TestLookups:
    """The FoS + opportunity cached wrappers — the request/opportunity modals'
    name resolution."""

    def test_fos_types_is_memoised(self, monkeypatch):
        xras_cache._adapters.clear()
        calls = []
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            XrasApiClient, 'get_fos_types',
            lambda self: calls.append(1) or [{'fosTypeId': 1, 'fosName': 'X'}])
        xras_lookups.get_fos_types()
        xras_lookups.get_fos_types()
        assert len(calls) == 1

    def test_fos_name_map_keys_by_int_id_prefers_name(self, monkeypatch):
        xras_cache._adapters.clear()
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_fos_types', lambda self: [
            {'fosTypeId': 500032, 'fosName': 'Regional Climate', 'fosAbbr': 'RC'},
            {'fosTypeId': 500003, 'fosName': None, 'fosAbbr': 'ASC'}])
        m = xras_lookups.fos_name_map()
        assert m[500032] == 'Regional Climate'
        assert m[500003] == 'ASC'          # falls back to abbr

    def test_fos_name_map_is_empty_on_outage(self, monkeypatch):
        """A FoS name is a nicety — an outage must not fail the request view."""
        xras_cache._adapters.clear()
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')

        def boom(self):
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr(XrasApiClient, 'get_fos_types', boom)
        assert xras_lookups.fos_name_map() == {}

    def test_opportunity_is_memoised_by_id(self, monkeypatch):
        xras_cache._adapters.clear()
        calls = []
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            XrasApiClient, 'get_opportunity',
            lambda self, oid: calls.append(oid) or {'opportunityId': oid})
        xras_lookups.get_opportunity(532220)
        xras_lookups.get_opportunity(532220)
        assert len(calls) == 1


class TestResourceKeys:

    def test_it_extracts_the_join_keys(self, monkeypatch):
        xras_cache._adapters.clear()
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'get_resources',
                            lambda self: list(RESOURCES))
        assert xras_people.resource_repository_keys() == [101, 102]

    def test_a_non_numeric_key_is_dropped(self, monkeypatch):
        xras_cache._adapters.clear()
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            XrasApiClient, 'get_resources',
            lambda self: [{'resourceRepositoryKey': 'nope'},
                          {'resourceRepositoryKey': 7}])
        # It could never have joined to the integer column, so reporting it
        # as "XRAS sent a key SAM lacks" would be a lie.
        assert xras_people.resource_repository_keys() == [7]


class TestOpportunityResolution:
    """`GET /v1/opportunities/list/:ids` — the one outbound call the mapping needs.

    WARNING: Required, not incidental: a live `reports/requests` row carries
    `opportunityId` and `opportunity_name` and nothing else — no
    `allocationTypeId`, no panels — so the sweep cannot derive a mapping from the
    enumeration it already has.
    """

    def _opp(self, oid):
        return {'opportunityId': oid, 'opportunityName': f'Opp {oid}',
                'allocationTypeInfo': {'allocationTypeId': 500023},
                'panels': [{'panelId': 500022, 'isPrimary': True}]}

    def test_it_asks_for_the_ids_in_the_path(self, monkeypatch):
        client = _client(monkeypatch,
                         [_response(200, _envelope([self._opp(1), self._opp(2)]))])
        result = client.get_opportunities([1, 2])
        assert [o['opportunityId'] for o in result] == [1, 2]

        (_method, url), _kwargs = client.session.request.call_args
        assert url.endswith('/v1/opportunities/list/1,2')

    def test_it_resolves_closed_opportunities_not_just_open_ones(self, monkeypatch):
        """The whole reason for the `/list/` route. Of the 27 opportunities the
        NCAR process has run, 22 are closed — and every one can still arrive on an
        inbound action, so `/v1/opportunities` (open only) is useless here."""
        client = _client(monkeypatch, [_response(200, _envelope([self._opp(531428)]))])
        assert client.get_opportunities([531428])[0]['opportunityId'] == 531428
        (_method, url), _kwargs = client.session.request.call_args
        assert '/v1/opportunities/list/' in url
        assert not url.endswith('/v1/opportunities')

    def test_it_chunks_long_id_lists(self, monkeypatch):
        """Ids travel in the **path**, so this bounds URL length — the thing a
        proxy in front of the API is likeliest to truncate."""
        ids = list(range(1, 121))
        client = _client(monkeypatch, [_response(200, _envelope([self._opp(i)]))
                                       for i in (1, 51, 101)])
        result = client.get_opportunities(ids)
        assert client.session.request.call_count == 3        # 50 + 50 + 20
        assert len(result) == 3
        for (_method, url), _kwargs in client.session.request.call_args_list:
            assert len(url) < 400

    def test_an_empty_id_list_makes_no_request(self, monkeypatch):
        """`/v1/opportunities/list/` with an empty segment is a different route
        that answers 404 — so not asking is the correct behavior, not an
        optimization."""
        client = _client(monkeypatch, [])
        assert client.get_opportunities([]) == []
        assert client.session.request.call_count == 0

    def test_ids_xras_does_not_know_are_simply_absent(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([]))])
        assert client.get_opportunities([999999]) == []

    def test_the_open_list_is_a_different_route(self):
        """`/v1/opportunities` and `/v1/opportunities/list/:ids` answer different
        questions and neither subsumes the other: the open list sees an
        opportunity nobody has submitted against yet, the by-id form resolves
        closed ones. The sweep needs both."""
        assert hasattr(XrasApiClient, 'get_open_opportunities')
        assert hasattr(XrasApiClient, 'get_opportunities')

    def test_the_open_list_asks_for_no_ids(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _envelope([self._opp(535388)]))])
        result = client.get_open_opportunities()
        assert [o['opportunityId'] for o in result] == [535388]
        (_method, url), _kwargs = client.session.request.call_args
        assert url.endswith('/v1/opportunities')
        assert '/list/' not in url
