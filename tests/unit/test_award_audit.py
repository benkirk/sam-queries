"""Unit tests for `compare_contract` — the `--check-sources` comparison.

Sits beside `test_award_providers.py` / `test_award_people.py`. Every test
stubs `sam.integration.awards.resolve_award`; **nothing here touches the
network**.

The four noise rules each get their own class, because each one costs real
findings if it regresses: without them the report tells an operator to
overwrite correct data with a stranger's.
"""

from datetime import date, datetime
from unittest.mock import patch

import pytest

from sam.integration.awards import AwardRecord, AwardSourceUnavailable, PersonRef
from sam.integration.awards.audit import compare_contract
from tests.factories.core import make_user
from tests.factories.projects import (
    make_contract, make_contract_source, make_nsf_program,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def contract(session):
    """An NSF contract with every comparable field populated."""
    return make_contract(
        session,
        contract_number="AGS-1852977",
        title="Understanding the Atmoshperic Boundary Layer",
        source=make_contract_source(session, name="NSF"),
        monitor=make_user(session),
        nsf_program=make_nsf_program(session, name="Physical & Dynamic Meteorology"),
        url="https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977",
        start_date=datetime(2019, 9, 1),
        end_date=datetime(2023, 8, 31),
    )


def _matching(contract, **overrides):
    """A record that agrees with *contract* on every field, before overrides."""
    defaults = dict(
        provenance='NSF Awards API',
        contract_number=contract.contract_number,
        title=contract.title,
        start_date=contract.start_date.date(),
        end_date=contract.end_date.date(),
        url=contract.url,
        program_name=contract.nsf_program.nsf_program_name,
        pi=None,
        monitor=None,
        unavailable_fields=frozenset(),
    )
    defaults.update(overrides)
    return AwardRecord(**defaults)


def _compare(session, contract, record_or_exc):
    kwargs = ({'side_effect': record_or_exc}
              if isinstance(record_or_exc, Exception)
              else {'return_value': record_or_exc})
    with patch('sam.integration.awards.resolve_award', **kwargs):
        return compare_contract(session, contract)


class TestStatuses:

    def test_agreement_yields_no_divergences(self, session, contract):
        result = _compare(session, contract, _matching(contract))
        assert result['status'] == 'ok'
        assert result['divergences'] == []
        assert result['provenance'] == 'NSF Awards API'

    def test_no_such_award(self, session, contract):
        result = _compare(session, contract, None)
        assert result['status'] == 'no_record'
        assert result['divergences'] == []

    def test_transport_failure_is_a_status_not_an_exception(self, session, contract):
        """A dead API is not a data-hygiene finding and must not abort a run."""
        result = _compare(session, contract, AwardSourceUnavailable('NSF is down'))
        assert result['status'] == 'unavailable'
        assert result['reason'] == 'NSF is down'
        assert result['divergences'] == []

    def test_blank_number_short_circuits(self, session, contract):
        """`resolve_award` returns None for an empty number rather than asking."""
        result = _compare(session, contract, None)
        assert result['status'] == 'no_record'


class TestScalarDivergence:

    def test_single_stale_field_is_reported(self, session, contract):
        record = _matching(contract, end_date=date(2025, 8, 31))
        result = _compare(session, contract, record)
        assert result['status'] == 'ok'
        assert result['divergences'] == [
            {'field': 'end_date',
             'sam': date(2023, 8, 31), 'source': date(2025, 8, 31)},
        ]

    def test_program_divergence_reports_raw_names(self, session, contract):
        record = _matching(contract, program_name='ATMOSPHERIC AND GEOSPACE SCIENCES')
        result = _compare(session, contract, record)
        diverged = {d['field']: d for d in result['divergences']}
        assert diverged['nsf_program']['sam'] == 'Physical & Dynamic Meteorology'
        assert diverged['nsf_program']['source'] == 'ATMOSPHERIC AND GEOSPACE SCIENCES'

    def test_blank_provider_field_is_never_a_divergence(self, session, contract):
        """"The agency didn't tell us" is not "SAM is wrong"."""
        record = _matching(contract, title=None, program_name=None)
        result = _compare(session, contract, record)
        assert result['divergences'] == []

    def test_title_case_and_whitespace_are_not_divergences(self, session, contract):
        record = _matching(contract,
                           title='  understanding the   ATMOSHPERIC boundary layer ')
        result = _compare(session, contract, record)
        assert result['divergences'] == []


class TestNumberNormalization:
    """Rule 3 — NSF rebuilds the number, so raw compares flag manual spacing."""

    @pytest.mark.parametrize("provider_number", [
        "AGS-1852977",
        "AGS- 1852977",
        "AGS - 1852977",
        "  ags-1852977  ",
    ])
    def test_equivalent_spellings_agree(self, session, contract, provider_number):
        record = _matching(contract, contract_number=provider_number)
        result = _compare(session, contract, record)
        assert result['divergences'] == []

    def test_a_genuinely_different_number_is_reported(self, session, contract):
        record = _matching(contract, contract_number='OCE-1852977')
        result = _compare(session, contract, record)
        assert [d['field'] for d in result['divergences']] == ['contract_number']


class TestPeople:
    """Rules 1 and 2."""

    def test_unavailable_person_fields_are_skipped(self, session, contract):
        """USAspending structurally has no program officer — not a divergence."""
        record = _matching(
            contract, provenance='USAspending',
            pi=PersonRef(name='Someone Else'),
            monitor=PersonRef(name='Someone Else'),
            unavailable_fields=frozenset({'pi', 'monitor'}))
        result = _compare(session, contract, record)
        assert result['divergences'] == []
        assert result['hints'] == []

    def test_unresolvable_person_is_a_hint_not_a_divergence(self, session, contract):
        """314 of 387 monitors exist purely as contract contacts."""
        record = _matching(contract,
                           monitor=PersonRef(name='Sean Kennan',
                                             email='skennan@nsf.invalid'))
        result = _compare(session, contract, record)
        assert result['divergences'] == []
        assert result['hints'] == [
            {'field': 'monitor',
             'source': 'Sean Kennan <skennan@nsf.invalid>',
             'note': 'no matching SAM user'},
        ]

    def test_resolved_different_user_is_a_divergence(self, session, contract):
        """The stale-Monitor case: ~1 in 3 sampled contracts."""
        replacement = make_user(session)
        record = _matching(contract, monitor=PersonRef(name='Whoever'))
        with patch('sam.integration.awards.resolve_award', return_value=record), \
             patch('sam.integration.awards.resolve_person',
                   return_value=replacement):
            result = compare_contract(session, contract)
        assert result['divergences'] == [
            {'field': 'monitor',
             'sam': contract.contract_monitor.username,
             'source': replacement.username},
        ]

    def test_resolved_same_user_agrees(self, session, contract):
        record = _matching(contract, monitor=PersonRef(name='Whoever'))
        with patch('sam.integration.awards.resolve_award', return_value=record), \
             patch('sam.integration.awards.resolve_person',
                   return_value=contract.contract_monitor):
            result = compare_contract(session, contract)
        assert result['divergences'] == []
        assert result['hints'] == []


class TestUrl:
    """Rule 4 — never compared, only offered where SAM has none."""

    def test_different_url_is_not_a_divergence(self, session, contract):
        """~1,895 legacy rows carry the old scheme-less showAward URL."""
        record = _matching(contract, url='https://www.nsf.gov/completely/different')
        result = _compare(session, contract, record)
        assert result['divergences'] == []
        assert result['hints'] == []

    def test_missing_url_is_offered_as_a_hint(self, session, contract):
        contract.url = None
        session.flush()
        record = _matching(contract, url='https://www.nsf.gov/awardsearch/x')
        result = _compare(session, contract, record)
        assert result['hints'] == [
            {'field': 'url', 'source': 'https://www.nsf.gov/awardsearch/x',
             'note': 'source has a URL, SAM has none'},
        ]


class TestSuspectMatch:
    """USAspending's keyword fallback finds *something* for any short number.

    SAM's `014421` (a DOD 4DWX contract) resolves to a 2009 award titled
    "MEALS"; reporting that field-by-field would tell an operator to overwrite
    a correct title with a stranger's.
    """

    def test_all_three_diverging_is_a_suspect_match(self, session, contract):
        record = _matching(contract, provenance='USAspending', title='MEALS',
                           start_date=date(2009, 2, 20), end_date=date(2009, 4, 18))
        result = _compare(session, contract, record)
        assert result['status'] == 'suspect_match'
        assert result['divergences'] == []
        assert result['source_summary']['title'] == 'MEALS'
        assert result['source_summary']['start_date'] == date(2009, 2, 20)

    def test_two_of_three_stays_a_divergence(self, session, contract):
        """A no-cost extension moves the dates; the title stays put."""
        record = _matching(contract, start_date=date(2019, 10, 1),
                           end_date=date(2025, 8, 31))
        result = _compare(session, contract, record)
        assert result['status'] == 'ok'
        assert {d['field'] for d in result['divergences']} == {'start_date', 'end_date'}

    def test_missing_provider_title_cannot_trigger_it(self, session, contract):
        """A blank title is not a divergence, so the signature can't complete."""
        record = _matching(contract, title=None,
                           start_date=date(2009, 2, 20), end_date=date(2009, 4, 18))
        result = _compare(session, contract, record)
        assert result['status'] == 'ok'
        assert {d['field'] for d in result['divergences']} == {'start_date', 'end_date'}
