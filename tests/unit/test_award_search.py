"""Unit tests for free-text award search (sam.integration.awards).

No network: every test stubs ``AwardHttpClient``, reusing the ``_provider``
helper from ``test_award_providers``. The canned payloads are trimmed copies
of live responses captured 2026-07-30.

The load-bearing assertions here are the three places search and fetch
deliberately differ, because each one looks like a bug to a reader who has
not read the docstrings:

* NSF search maps through the **same** ``_to_record`` as fetch (the two
  endpoints return identical field sets, so a second mapper would be pure
  drift risk).
* ``_to_search_record`` **does** set ``contract_number`` while ``_to_record``
  deliberately does **not**.
* ``_search`` returns every result; only ``_resolve`` narrows to one.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from sam.integration.awards import cache as award_cache
from sam.integration.awards import registry
from sam.integration.awards.base import AwardRecord, AwardSourceUnavailable
from sam.integration.awards.nsf import NsfAwardProvider
from sam.integration.awards.usaspending import (
    ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES, UsaSpendingProvider,
)

from tests.unit.test_award_providers import NSF_PAYLOAD, _provider

pytestmark = pytest.mark.unit


# Two hits from api.nsf.gov/services/v1/awards.json?keyword=… — the same 62
# keys the single-award endpoint returns, trimmed to what _to_record reads.
NSF_SEARCH_PAYLOAD = {
    'response': {'award': [
        {
            'id': '2618361',
            'divAbbr': 'OCE',
            'title': 'Intermittency in Wave-breaking Turbulence',
            'startDate': '09/01/2025',
            'expDate': '08/31/2028',
            'fundProgramName': 'Physical Oceanography',
            'piFirstName': 'Sean',
            'piLastName': 'Kennan',
            'piEmail': 'skennan@example.edu',
            'poName': 'Baris Uz',
            'poEmail': 'buz@nsf.gov',
        },
        {
            'id': '2615146',
            'divAbbr': 'EAR',
            'title': 'Macrostratigraphy of Northern Laurasia',
            'startDate': '01/01/2027',
            'expDate': '12/31/2028',
            'fundProgramName': 'LET-Life & Enviro Through Time',
            'piFirstName': 'Shanan',
            'piLastName': 'Peters',
            'poName': 'Margaret Fraiser',
        },
    ]},
}

# From POST /api/v2/search/spending_by_award/ with Description requested.
USA_SEARCH_PAYLOAD = {
    'results': [
        {
            'Award ID': 'FA9550-21-1-0105',
            'Description': 'BOUNDARY LAYER TURBULENCE SURFACE SENSOR ARRAY',
            'Start Date': '2021-01-13',
            'End Date': '2022-01-12',
            'Awarding Agency': 'Department of Defense',
            'generated_internal_id': 'CONT_AWD_FA955021',
        },
        {
            'Award ID': 'N00014-25-1-2045',
            'Description': 'YIP CLOSING THE LOOP ON JOINT PHYSICS',
            'Start Date': '2024-12-01',
            'End Date': '2027-11-30',
            'Awarding Agency': 'Department of Defense',
            'generated_internal_id': 'ASST_NON_N00014251_097',
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_award_cache():
    """Search results are memoised; a shared cache would leak between tests.

    ``_adapters`` IS the cache's memo dict (the fs-scans / jobs idiom), so
    clearing it re-initialises every bucket.
    """
    award_cache._adapters.clear()
    yield
    award_cache._adapters.clear()


class TestBaseDefault:
    """search() is an optional capability, not part of the provider contract."""

    def test_a_provider_that_cannot_search_returns_empty(self):
        from sam.integration.awards.base import AwardProvider

        class Minimal(AwardProvider):
            name = 'Minimal'

            def supports(self, source_name, contract_number):
                return True

            def fetch(self, contract_number):
                return None

        assert Minimal().search('anything') == []


class TestNsfSearch:

    def test_maps_through_the_same_record_mapper_as_fetch(self):
        """The whole reason there is no second NSF mapper."""
        provider, client = _provider(get_json=NSF_SEARCH_PAYLOAD)
        records = provider.search('turbulence')

        assert [r.contract_number for r in records] == ['OCE-2618361',
                                                        'EAR-2615146']
        first = records[0]
        # Field-for-field identical to what fetch() would have produced.
        expected = NsfAwardProvider._to_record(
            NSF_SEARCH_PAYLOAD['response']['award'][0], '2618361')
        assert first == expected

    def test_search_hits_carry_everything_fetch_does(self):
        provider, _ = _provider(get_json=NSF_SEARCH_PAYLOAD)
        record = provider.search('turbulence')[0]

        assert record.title == 'Intermittency in Wave-breaking Turbulence'
        assert record.start_date == date(2025, 9, 1)
        assert record.end_date == date(2028, 8, 31)
        assert record.program_name == 'Physical Oceanography'
        assert record.pi.name == 'Sean Kennan'
        # NSF is the only source carrying a program officer — that must
        # survive the search path, not just the fetch path.
        assert record.monitor.name == 'Baris Uz'
        assert record.monitor.email == 'buz@nsf.gov'
        assert record.unavailable_fields == frozenset()

    def test_one_request_no_n_plus_one(self):
        provider, client = _provider(get_json=NSF_SEARCH_PAYLOAD)
        provider.search('turbulence')
        assert client.get_json.call_count == 1

    def test_keyword_and_limit_are_sent_as_params(self):
        provider, client = _provider(get_json=NSF_SEARCH_PAYLOAD)
        provider.search('boundary layer', limit=3)
        _, kwargs = client.get_json.call_args
        assert kwargs['params'] == {'keyword': 'boundary layer', 'rpp': 3}

    def test_limit_caps_the_results(self):
        provider, _ = _provider(get_json=NSF_SEARCH_PAYLOAD)
        assert len(provider.search('turbulence', limit=1)) == 1

    def test_blank_query_does_not_call_the_api(self):
        provider, client = _provider(get_json=NSF_SEARCH_PAYLOAD)
        assert provider.search('   ') == []
        client.get_json.assert_not_called()

    def test_empty_payload_is_not_an_error(self):
        provider, _ = _provider(get_json={'response': {'award': []}})
        assert provider.search('nothing') == []

    def test_hit_without_an_id_is_skipped(self):
        provider, _ = _provider(
            get_json={'response': {'award': [{'title': 'no id here'}]}})
        assert provider.search('x') == []


class TestUsaSpendingSearch:

    def test_sets_contract_number_unlike_the_fetch_mapper(self):
        """The deliberate asymmetry — the one thing most likely to be
        "fixed" into agreement by a later reader."""
        record = UsaSpendingProvider._to_search_record(
            USA_SEARCH_PAYLOAD['results'][0])
        assert record.contract_number == 'FA9550-21-1-0105'

        # …while the fetch mapper leaves it None, on purpose.
        fetched = UsaSpendingProvider._to_record(
            USA_SEARCH_PAYLOAD['results'][0], {}, 'CONT_AWD_FA955021')
        assert fetched.contract_number is None

    def test_title_comes_from_the_inline_description(self):
        provider, _ = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        record = provider.search('turbulence')[0]
        assert record.title == 'BOUNDARY LAYER TURBULENCE SURFACE SENSOR ARRAY'
        assert record.start_date == date(2021, 1, 13)
        assert record.end_date == date(2022, 1, 12)
        assert record.url.endswith('/CONT_AWD_FA955021/')

    def test_program_name_is_none_because_cfda_is_detail_only(self):
        """Search returns summaries; this is the asymmetry that forces the
        pick-then-chain design rather than prefilling straight from a hit."""
        provider, _ = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        assert provider.search('turbulence')[0].program_name is None

    def test_people_stay_structurally_unavailable(self):
        provider, _ = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        record = provider.search('turbulence')[0]
        assert record.pi is None and record.monitor is None
        assert record.unavailable_fields == frozenset({'pi', 'monitor'})

    def test_searches_both_type_groups_separately(self):
        """Trap 2: mixing assistance and contract codes returns nothing."""
        provider, client = _provider(nsf=False, post_json={'results': []})
        provider.search('turbulence')

        assert client.post_json.call_count == 2
        sent = [call.args[1]['filters']['award_type_codes']
                for call in client.post_json.call_args_list]
        assert sent == [ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES]
        # …and each carries the keyword, never an award_ids filter.
        for call in client.post_json.call_args_list:
            assert call.args[1]['filters']['keywords'] == ['turbulence']

    def test_description_is_requested_from_the_search_endpoint(self):
        provider, client = _provider(nsf=False, post_json={'results': []})
        provider.search('x')
        assert 'Description' in client.post_json.call_args.args[1]['fields']

    def test_hit_without_an_internal_id_is_skipped(self):
        provider, _ = _provider(
            nsf=False, post_json={'results': [{'Award ID': 'X', 'Description': 'y'}]})
        assert provider.search('x') == []

    def test_blank_query_does_not_call_the_api(self):
        provider, client = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        assert provider.search('  ') == []
        client.post_json.assert_not_called()


class TestSearchNoLongerCollapsesToOne:
    """``_search`` used to discard four results it had already paid for."""

    def test_search_returns_every_result(self):
        provider, _ = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        hits = provider._search({'keywords': ['x']})
        assert len(hits) == 2

    def test_resolve_still_narrows_to_the_first(self):
        provider, _ = _provider(nsf=False, post_json=USA_SEARCH_PAYLOAD)
        hit = provider._resolve('FA9550-21-1-0105')
        assert hit['Award ID'] == 'FA9550-21-1-0105'

    def test_fetch_is_unchanged_by_the_multi_result_refactor(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = USA_SEARCH_PAYLOAD
        client.get_json.return_value = {
            'description': 'BOUNDARY LAYER TURBULENCE SURFACE SENSOR ARRAY',
            'period_of_performance': {'start_date': '2021-01-13',
                                      'end_date': '2022-01-12'},
            'cfda_info': [{'cfda_number': '12.800',
                           'cfda_title': 'Air Force Defense Research'}],
        }
        record = provider.fetch('FA9550-21-1-0105')
        assert record.contract_number is None          # still, on purpose
        assert record.program_name == '12.800 Air Force Defense Research'


class TestSearchProviders:
    """supports() is number-scoped, so search restates the tiering rule."""

    def test_no_sources_fans_out_to_everything(self):
        assert len(registry.search_providers()) == 2

    def test_nsf_narrows_to_the_nsf_provider(self):
        picked = registry.search_providers(['NSF'])
        assert [p.name for p in picked] == ['NSF Awards API']

    def test_any_other_source_falls_back_to_the_generic(self):
        for source in ('DOD', 'DOE', 'NASA'):
            picked = registry.search_providers([source])
            assert [p.name for p in picked] == ['USAspending'], source

    def test_source_matching_is_case_insensitive(self):
        assert registry.search_providers(['nsf'])[0].source == 'NSF'


class TestSearchAwards:

    def _pool(self, nsf_records=(), usa_records=(), nsf_exc=None):
        nsf, usa = MagicMock(), MagicMock()
        nsf.name, nsf.source = 'NSF Awards API', 'NSF'
        usa.name, usa.source = 'USAspending', None
        nsf.search.side_effect = nsf_exc or (lambda *a, **k: list(nsf_records))
        usa.search.side_effect = lambda *a, **k: list(usa_records)
        return [nsf, usa]

    def test_records_from_every_provider_are_concatenated(self):
        a = AwardRecord(provenance='NSF Awards API', contract_number='AGS-1')
        b = AwardRecord(provenance='USAspending', contract_number='X-2')
        records, errors = registry.search_awards(
            'q', providers=self._pool([a], [b]))
        assert records == [a, b]
        assert errors == []

    def test_one_dead_provider_does_not_kill_the_search(self):
        """The whole point of returning (records, errors) rather than raising:
        "NSF is down" must not cost the operator USAspending's hits."""
        b = AwardRecord(provenance='USAspending', contract_number='X-2')
        records, errors = registry.search_awards(
            'q',
            providers=self._pool(
                usa_records=[b],
                nsf_exc=AwardSourceUnavailable('nsf.gov unreachable')))

        assert records == [b]
        assert len(errors) == 1
        assert errors[0]['provenance'] == 'NSF Awards API'
        assert 'unreachable' in errors[0]['reason']

    def test_every_provider_failing_reports_errors_not_an_exception(self):
        pool = self._pool(nsf_exc=AwardSourceUnavailable('down'))
        pool[1].search.side_effect = AwardSourceUnavailable('also down')
        records, errors = registry.search_awards('q', providers=pool)
        assert records == []
        assert len(errors) == 2

    def test_blank_query_short_circuits(self):
        pool = self._pool()
        assert registry.search_awards('   ', providers=pool) == ([], [])
        pool[0].search.assert_not_called()

    def test_sources_scopes_the_fan_out(self):
        a = AwardRecord(provenance='NSF Awards API', contract_number='AGS-1')
        pool = self._pool([a], [])
        records, _ = registry.search_awards('q', sources=['NSF'],
                                            providers=pool)
        assert records == [a]
        pool[1].search.assert_not_called()

    def test_results_are_cached_per_provider_term_and_limit(self):
        a = AwardRecord(provenance='NSF Awards API', contract_number='AGS-1')
        pool = self._pool([a], [])

        registry.search_awards('turbulence', limit=5, providers=pool)
        registry.search_awards('turbulence', limit=5, providers=pool)
        assert pool[0].search.call_count == 1        # second was a cache hit

        # A different limit is a different question, not a truncation.
        registry.search_awards('turbulence', limit=10, providers=pool)
        assert pool[0].search.call_count == 2

    def test_an_outage_is_never_memoised(self):
        pool = self._pool(nsf_exc=AwardSourceUnavailable('down'))
        registry.search_awards('q', providers=pool)
        registry.search_awards('q', providers=pool)
        assert pool[0].search.call_count == 2


class TestBuildProviders:
    """The CLI needs a longer timeout than the htmx-scoped default."""

    def test_builds_a_fresh_independent_set(self):
        first, second = registry.build_providers(), registry.build_providers()
        assert [p.name for p in first] == ['NSF Awards API', 'USAspending']
        assert first[0] is not second[0]
        assert first[0] is not registry.providers()[0]

    def test_injected_client_reaches_every_provider(self):
        client = MagicMock()
        for provider in registry.build_providers(client):
            assert provider.client is client
