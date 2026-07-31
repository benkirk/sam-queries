"""Unit tests for the award-source providers (sam.integration.awards).

No network: every test stubs ``AwardHttpClient``. The canned payloads are
trimmed copies of live responses captured while writing this feature, so a
mapping change that breaks against the real API breaks here too.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from sam.integration.awards.base import AwardSourceUnavailable
from sam.integration.awards.client import AwardHttpClient
from sam.integration.awards.nsf import NsfAwardProvider, nsf_award_id
from sam.integration.awards.usaspending import (
    ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES,
    UsaSpendingProvider, award_id_candidates,
)

pytestmark = pytest.mark.unit


# Trimmed from api.nsf.gov/services/v1/awards/1852977.json. The "Atmoshperic"
# typo is NSF's and is reproduced verbatim in SAM's stored row — do not fix it.
NSF_PAYLOAD = {
    'response': {'award': [{
        'id': '1852977',
        'agency': 'NSF',
        'divAbbr': 'AGS',
        'title': 'The Management and Operation of the National Center '
                 'for Atmoshperic Research (NCAR)',
        'startDate': '10/01/2018',
        'expDate': '09/30/2028',
        'fundProgramName': 'NCAR-Nat Center Atmosph Resear',
        'primaryProgram': ['01001819DB NSF RESEARCH & RELATED ACTIVIT'],
        'piFirstName': 'Eric',
        'piMiddeInitial': 'J',
        'piLastName': 'Barron',
        'piEmail': 'barron@ucar.edu',
        'poName': 'Carrie E. Black',
        'poEmail': 'cblack@nsf.gov',
    }]},
}


def _provider(nsf=True, **returns):
    """A provider wired to a stub client. ``**returns`` sets return_values."""
    client = MagicMock()
    for method, value in returns.items():
        getattr(client, method).return_value = value
    return (NsfAwardProvider(client=client) if nsf
            else UsaSpendingProvider(client=client)), client


class TestNsfAwardId:
    """Every contract_number shape that actually occurs in the sam DB."""

    @pytest.mark.parametrize('number,expected', [
        ('AGS-1852977', '1852977'),          # division-prefixed (the common case)
        ('2317820', '2317820'),              # bare numeric
        ('GRFP-2009067341', '2009067341'),   # 10-digit fellowship id
        ('OCE- 1419584', '1419584'),         # stray space after the hyphen
        ('AGS - 2410913', '2410913'),        # stray spaces both sides
        ('  AGS-1852977  ', '1852977'),      # surrounding whitespace
    ])
    def test_parses_real_formats(self, number, expected):
        assert nsf_award_id(number) == expected

    @pytest.mark.parametrize('number', [
        'OCE-UCSC0001',   # non-numeric tail
        'NCAR0880',       # no hyphen, not all digits
        'DE-SC0012671',   # a DOE number misfiled under source NSF
        '',
        None,
    ])
    def test_rejects_unparseable(self, number):
        assert nsf_award_id(number) is None


class TestNsfProvider:

    def test_supports_only_nsf_with_an_award_id(self):
        provider, _ = _provider()
        assert provider.supports('NSF', 'AGS-1852977')
        assert provider.supports('nsf', '2317820')       # case-insensitive
        assert not provider.supports('DOE', 'AGS-1852977')
        assert not provider.supports('NSF', 'OCE-UCSC0001')

    def test_maps_a_canned_payload(self):
        provider, _ = _provider(get_json=NSF_PAYLOAD)
        record = provider.fetch('AGS-1852977')

        assert record.provenance == 'NSF Awards API'
        # divAbbr + id, so a sloppily typed number comes back normalised.
        assert record.contract_number == 'AGS-1852977'
        assert record.title.endswith('Atmoshperic Research (NCAR)')
        assert record.start_date == date(2018, 10, 1)
        assert record.end_date == date(2028, 9, 30)
        assert record.url == \
            'https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977'
        assert record.pi.name == 'Eric Barron'
        assert record.pi.email == 'barron@ucar.edu'
        assert record.monitor.name == 'Carrie E. Black'
        assert record.monitor.email == 'cblack@nsf.gov'
        assert record.unavailable_fields == frozenset()

    def test_normalises_a_sloppily_typed_number(self):
        provider, client = _provider(get_json=NSF_PAYLOAD)
        record = provider.fetch('AGS - 1852977')
        assert record.contract_number == 'AGS-1852977'
        assert '1852977.json' in client.get_json.call_args[0][0]

    def test_program_comes_from_fund_not_primary(self):
        """primaryProgram is a funding *account* string.

        66 existing contracts point at nsf_program rows created by pasting
        one in ("01002324DB NSF RESEARCH & RELATED ACTIVIT"). Never map it.
        """
        provider, _ = _provider(get_json=NSF_PAYLOAD)
        record = provider.fetch('AGS-1852977')
        assert record.program_name == 'NCAR-Nat Center Atmosph Resear'

    def test_bare_id_when_division_missing(self):
        payload = {'response': {'award': [
            dict(NSF_PAYLOAD['response']['award'][0], divAbbr='')]}}
        provider, _ = _provider(get_json=payload)
        assert provider.fetch('2317820').contract_number == '2317820'

    def test_unparseable_date_degrades_to_none(self):
        payload = {'response': {'award': [
            dict(NSF_PAYLOAD['response']['award'][0], expDate='not a date')]}}
        provider, _ = _provider(get_json=payload)
        record = provider.fetch('AGS-1852977')
        assert record.start_date == date(2018, 10, 1)
        assert record.end_date is None

    def test_empty_award_list_is_not_found(self):
        provider, _ = _provider(get_json={'response': {'award': []}})
        assert provider.fetch('AGS-9999999') is None

    def test_unparseable_number_is_not_found_without_a_request(self):
        provider, client = _provider(get_json=NSF_PAYLOAD)
        assert provider.fetch('OCE-UCSC0001') is None
        client.get_json.assert_not_called()

    def test_transport_failure_propagates(self):
        """Not found and unreachable must stay distinguishable."""
        provider, client = _provider()
        client.get_json.side_effect = AwardSourceUnavailable('boom')
        with pytest.raises(AwardSourceUnavailable):
            provider.fetch('AGS-1852977')


class TestUsaSpendingCandidates:
    """Award ids are punctuation-stripped, but inconsistently."""

    @pytest.mark.parametrize('number,expected', [
        ('DE-SC0012671', ['DE-SC0012671', 'DESC0012671']),
        ('DE-FC02-97ER62402', ['DE-FC02-97ER62402', 'DEFC0297ER62402']),
        ('FA9550-14-C-0035', ['FA9550-14-C-0035', 'FA955014C0035']),
        ('80NSSC19K0855', ['80NSSC19K0855']),   # already clean — no duplicate
    ])
    def test_candidate_sets(self, number, expected):
        assert award_id_candidates(number) == expected

    def test_blank_number_yields_nothing(self):
        assert award_id_candidates('   ') == []


class TestUsaSpendingProvider:

    SEARCH_HIT = {'results': [{
        'Award ID': 'DESC0012671',
        'Recipient Name': 'UNIVERSITY OF WYOMING',
        'Start Date': '2014-08-15',
        'End Date': '2020-10-14',
        'generated_internal_id': 'ASST_NON_DESC0012671_089',
    }]}

    DETAIL = {
        'description': 'ATMOSPHERE TO GRID:  ADDRESSING BARRIERS TO ENERGY '
                       'CONVERSION AND DELIVERY',
        'period_of_performance': {'start_date': '2014-08-15',
                                  'end_date': '2020-10-14'},
        'cfda_info': [{'cfda_number': '81.049',
                       'cfda_title': 'Office of Science Financial Assistance Program'}],
    }

    def test_declines_nsf(self):
        provider, _ = _provider(nsf=False)
        assert not provider.supports('NSF', 'AGS-1852977')
        assert provider.supports('DOE', 'DE-SC0012671')
        assert not provider.supports('DOE', '  ')

    def test_maps_a_canned_payload(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = self.SEARCH_HIT
        client.get_json.return_value = self.DETAIL

        record = provider.fetch('DE-SC0012671')

        assert record.provenance == 'USAspending'
        assert record.start_date == date(2014, 8, 15)
        assert record.end_date == date(2020, 10, 14)
        assert record.url == \
            'https://www.usaspending.gov/award/ASST_NON_DESC0012671_089/'
        assert record.program_name == \
            '81.049 Office of Science Financial Assistance Program'
        # The operator's own number is left alone: USAspending reports the
        # punctuation-stripped spelling, which no other system uses.
        assert record.contract_number is None

    def test_supplies_no_people_and_says_so(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = self.SEARCH_HIT
        client.get_json.return_value = self.DETAIL

        record = provider.fetch('DE-SC0012671')
        assert record.pi is None and record.monitor is None
        assert record.unavailable_fields == frozenset({'pi', 'monitor'})

    def test_description_is_truncated_to_the_title_column(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = self.SEARCH_HIT
        client.get_json.return_value = dict(self.DETAIL, description='X' * 400)

        record = provider.fetch('DE-SC0012671')
        assert len(record.title) == 255      # contract.title is varchar(255)

    def test_submits_both_candidates_in_one_filter(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = self.SEARCH_HIT
        client.get_json.return_value = self.DETAIL

        provider.fetch('DE-SC0012671')
        filters = client.post_json.call_args[0][1]['filters']
        assert filters['award_ids'] == ['DE-SC0012671', 'DESC0012671']

    def test_award_type_groups_are_never_mixed(self):
        """Mixing assistance and contract codes errors or returns nothing."""
        provider, client = _provider(nsf=False)
        client.post_json.return_value = {'results': []}

        assert provider.fetch('DE-SC0012671') is None

        groups = [call[0][1]['filters']['award_type_codes']
                  for call in client.post_json.call_args_list]
        assert ASSISTANCE_TYPE_CODES in groups
        assert CONTRACT_TYPE_CODES in groups
        for group in groups:
            assert group in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES)

    def test_falls_back_to_keyword_search_for_suffixed_ids(self):
        """NA18NWS4620043 misses on exact match; the award is …B."""
        provider, client = _provider(nsf=False)
        suffixed = {'results': [{
            'Award ID': 'NA18NWS4620043B',
            'generated_internal_id': 'ASST_NON_NA18NWS4620043B_013',
        }]}
        # Both exact-id queries miss, then the keyword query hits.
        client.post_json.side_effect = [{'results': []}, {'results': []},
                                        suffixed]
        client.get_json.return_value = self.DETAIL

        record = provider.fetch('NA18NWS4620043')
        assert record is not None
        assert 'NA18NWS4620043B' in record.url

        last_filters = client.post_json.call_args[0][1]['filters']
        assert last_filters['keywords'] == ['NA18NWS4620043']

    def test_no_hit_anywhere_is_not_found(self):
        provider, client = _provider(nsf=False)
        client.post_json.return_value = {'results': []}
        assert provider.fetch('NNG04EA00C') is None

    def test_transport_failure_propagates(self):
        provider, client = _provider(nsf=False)
        client.post_json.side_effect = AwardSourceUnavailable('boom')
        with pytest.raises(AwardSourceUnavailable):
            provider.fetch('DE-SC0012671')


class TestAwardHttpClient:
    """Retry policy: a 4xx is an answer, a 5xx is worth another attempt."""

    @staticmethod
    def _client(monkeypatch, responses):
        client = AwardHttpClient(max_retries=3)
        monkeypatch.setattr(client.session, 'request',
                            MagicMock(side_effect=responses))
        monkeypatch.setattr('sam.integration.awards.client.time.sleep',
                            lambda _s: None)
        return client

    @staticmethod
    def _response(status, payload=None):
        resp = MagicMock()
        resp.status_code = status
        resp.text = ''
        resp.json.return_value = payload if payload is not None else {}
        return resp

    def test_404_is_none_not_an_error(self, monkeypatch):
        client = self._client(monkeypatch, [self._response(404)])
        assert client.get_json('https://example.test/a') is None
        assert client.session.request.call_count == 1

    def test_4xx_does_not_retry(self, monkeypatch):
        client = self._client(monkeypatch, [self._response(400)])
        with pytest.raises(AwardSourceUnavailable):
            client.get_json('https://example.test/a')
        assert client.session.request.call_count == 1

    def test_5xx_retries_then_succeeds(self, monkeypatch):
        client = self._client(monkeypatch, [self._response(503),
                                            self._response(200, {'ok': True})])
        assert client.get_json('https://example.test/a') == {'ok': True}
        assert client.session.request.call_count == 2

    def test_exhausted_retries_raise(self, monkeypatch):
        client = self._client(monkeypatch, [self._response(503)] * 3)
        with pytest.raises(AwardSourceUnavailable):
            client.get_json('https://example.test/a')
        assert client.session.request.call_count == 3

    def test_network_error_retries(self, monkeypatch):
        client = self._client(monkeypatch, [
            requests.ConnectionError('down'),
            self._response(200, {'ok': True}),
        ])
        assert client.get_json('https://example.test/a') == {'ok': True}
