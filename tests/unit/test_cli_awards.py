"""Click wiring for `sam-search awards` — guards, exit codes, envelopes.

**No test here touches the network.** `search_awards` and `resolve_award` are
stubbed at `sam.integration.awards.<name>`, i.e. on the *package object*, because
`cli.awards.commands` imports them inside the function body — a
`from ... import` at module scope would bind the real callable and silently
ignore the patch.

The exit-code tests are the point of the file. This command has three
outcomes and they must never collapse into two: "NSF has no award 1234567"
and "NSF is down" are different answers, and the second one must not read as
the first.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli
from sam.integration.awards.base import (
    AwardRecord, AwardSourceUnavailable, PersonRef,
)
from tests.factories._seq import next_seq
from tests.factories.projects import make_contract, make_contract_source

pytestmark = pytest.mark.unit


def _award(**overrides):
    """An NSF-shaped record; override any field."""
    defaults = dict(
        provenance='NSF Awards API',
        contract_number='AGS-1852977',
        title='The Management and Operation of NCAR',
        start_date=date(2018, 10, 1),
        end_date=date(2028, 9, 30),
        url='https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977',
        program_name='NCAR-Nat Center Atmosph Resear',
        pi=PersonRef(name='Eric Barron', email='barron@ucar.edu'),
        monitor=PersonRef(name='Carrie E. Black', email='cblack@nsf.gov'),
        unavailable_fields=frozenset(),
    )
    defaults.update(overrides)
    return AwardRecord(**defaults)


def _usaspending(**overrides):
    defaults = dict(
        provenance='USAspending',
        contract_number='FA9550-21-1-0105',
        title='BOUNDARY LAYER TURBULENCE SURFACE SENSOR ARRAY',
        start_date=date(2021, 1, 13),
        end_date=date(2022, 1, 12),
        url='https://www.usaspending.gov/award/CONT_AWD_X/',
        program_name=None,
        pi=None,
        monitor=None,
        unavailable_fields=frozenset({'pi', 'monitor'}),
    )
    defaults.update(overrides)
    return AwardRecord(**defaults)


@pytest.fixture
def runner():
    """Wide terminal — the result tables ellipsize to fit (see
    test_cli_contracts_search for the same reasoning)."""
    return CliRunner(env={'COLUMNS': '200'})


@pytest.fixture
def mock_db_session(session):
    """Bind the CLI group to the test's SAVEPOINT session."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.cmds.search.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


class TestGuards:

    def test_no_arguments_is_an_error_with_help(self, runner, mock_db_session):
        result = runner.invoke(cli, ['awards'])
        assert result.exit_code == 1
        assert 'Please provide exactly one of' in result.output
        assert 'Usage:' in result.output

    def test_number_and_search_together_are_rejected(self, runner,
                                                     mock_db_session):
        result = runner.invoke(cli, ['awards', 'AGS-1', '--search', 'x'])
        assert result.exit_code == 1

    def test_help_documents_the_three_exit_codes(self, runner):
        result = runner.invoke(cli, ['awards', '--help'])
        assert result.exit_code == 0
        assert 'Exit codes' in result.output


class TestLookupExitCodes:
    """Three outcomes, never conflated."""

    def test_found_exits_zero(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   return_value=_award()):
            result = runner.invoke(cli, ['awards', 'AGS-1852977'])
        assert result.exit_code == 0, result.output

    def test_no_such_award_exits_not_found(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award', return_value=None):
            result = runner.invoke(cli, ['awards', 'AGS-9999999'])
        assert result.exit_code == 1
        assert 'No award found' in result.output

    def test_unreachable_source_exits_error_not_not_found(
            self, runner, mock_db_session):
        """The distinction this command exists to preserve."""
        with patch('sam.integration.awards.resolve_award',
                   side_effect=AwardSourceUnavailable('nsf.gov unreachable')):
            result = runner.invoke(cli, ['awards', 'AGS-1852977'])
        assert result.exit_code == 2
        assert 'unavailable' in result.output.lower()
        assert 'No award found' not in result.output


class TestSourceInference:
    """`supports()` needs a source; a CLI user has no dropdown to supply one."""

    def test_nsf_is_retried_when_no_source_is_given(self, runner,
                                                    mock_db_session):
        # First call (source=None) misses, second (source='NSF') hits.
        with patch('sam.integration.awards.resolve_award',
                   side_effect=[None, _award()]) as resolve:
            result = runner.invoke(cli, ['awards', 'AGS-1852977'])

        assert result.exit_code == 0, result.output
        assert [c.args[0] for c in resolve.call_args_list] == [None, 'NSF']

    def test_no_retry_for_a_number_that_is_not_an_nsf_award_id(
            self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   return_value=None) as resolve:
            result = runner.invoke(cli, ['awards', 'DE-SC0012671'])
        assert result.exit_code == 1
        assert resolve.call_count == 1

    def test_explicit_source_is_not_second_guessed(self, runner,
                                                   mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   return_value=None) as resolve:
            runner.invoke(cli, ['awards', '1852977', '--source', 'DOD'])
        assert [c.args[0] for c in resolve.call_args_list] == ['DOD']

    def test_source_is_taken_from_the_sam_contract(self, runner,
                                                   mock_db_session, session):
        number = f'AGS-{next_seq("AWARDCLI")}'
        make_contract(session, contract_number=number, title='known',
                      source=make_contract_source(session, name='NSF'))

        with patch('sam.integration.awards.resolve_award',
                   return_value=_award(contract_number=number)) as resolve:
            result = runner.invoke(cli, ['awards', number])

        assert result.exit_code == 0, result.output
        assert resolve.call_args_list[0].args[0] == 'NSF'


class TestCrossReference:

    def test_unknown_number_says_it_is_not_in_sam(self, runner,
                                                  mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   return_value=_award(contract_number='AGS-0000001')):
            result = runner.invoke(cli, ['awards', 'AGS-0000001'])
        assert result.exit_code == 0, result.output
        assert 'Not in SAM' in result.output

    def test_known_number_is_compared(self, runner, mock_db_session, session):
        number = f'AGS-{next_seq("AWARDCLI")}'
        contract = make_contract(
            session, contract_number=number, title='Agreeing title',
            source=make_contract_source(session, name='NSF'),
            start_date=datetime(2019, 9, 1), end_date=datetime(2023, 8, 31))

        record = _award(contract_number=number, title=contract.title,
                        start_date=date(2019, 9, 1), end_date=date(2023, 8, 31),
                        program_name=None, pi=None, monitor=None)
        with patch('sam.integration.awards.resolve_award',
                   return_value=record):
            result = runner.invoke(cli, ['awards', number])

        assert result.exit_code == 0, result.output
        assert 'In SAM' in result.output
        assert 'SAM agrees with the source' in result.output

    def test_suspect_match_is_surfaced_as_a_warning_not_as_data(
            self, runner, mock_db_session, session):
        """#403's guard: SAM's `014421` resolves to a 2009 award titled
        "MEALS". The output must say so, not present it as this contract's."""
        number = f'AGS-{next_seq("AWARDCLI")}'
        make_contract(session, contract_number=number,
                      title='Four-Dimensional Weather',
                      source=make_contract_source(session, name='NSF'),
                      start_date=datetime(2019, 9, 1),
                      end_date=datetime(2023, 8, 31))

        record = _award(contract_number=number, title='MEALS',
                        start_date=date(2009, 2, 20), end_date=date(2009, 4, 18),
                        program_name=None, pi=None, monitor=None)
        with patch('sam.integration.awards.resolve_award',
                   return_value=record):
            result = runner.invoke(cli, ['--format', 'json', 'awards', number])

        data = json.loads(result.output)
        assert data['in_sam']['status'] == 'suspect_match'
        assert data['in_sam']['divergences'] == []
        assert data['in_sam']['source_summary']['title'] == 'MEALS'


class TestSearchExitCodes:

    def test_hits_exit_zero(self, runner, mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            result = runner.invoke(cli, ['awards', '--search', 'ncar'])
        assert result.exit_code == 0, result.output

    def test_no_hits_exits_not_found(self, runner, mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])):
            result = runner.invoke(cli, ['awards', '--search', 'zzz'])
        assert result.exit_code == 1
        assert 'No awards found' in result.output

    def test_total_outage_exits_error_not_not_found(self, runner,
                                                    mock_db_session):
        """Zero results because everything was down is not "does not exist"."""
        errors = [{'provenance': 'NSF Awards API', 'reason': 'down'},
                  {'provenance': 'USAspending', 'reason': 'down'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([], errors)):
            result = runner.invoke(cli, ['awards', '--search', 'ncar'])
        assert result.exit_code == 2

    def test_partial_outage_still_succeeds_and_warns(self, runner,
                                                     mock_db_session):
        errors = [{'provenance': 'NSF Awards API', 'reason': 'down'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([_usaspending()], errors)):
            result = runner.invoke(cli, ['awards', '--search', 'turbulence'])

        assert result.exit_code == 0, result.output
        assert 'results are partial' in result.output
        assert 'NSF Awards API' in result.output


class TestSearchOutput:

    def test_unavailable_fields_are_a_positive_note(self, runner,
                                                    mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([_usaspending()], [])):
            result = runner.invoke(cli, ['awards', '--search', 'turbulence'])
        assert 'cannot supply PI and Monitor' in result.output

    def test_rows_already_in_sam_are_annotated(self, runner, mock_db_session,
                                               session):
        number = f'AGS-{next_seq("AWARDCLI")}'
        make_contract(session, contract_number=number, title='known already')

        with patch('sam.integration.awards.search_awards',
                   return_value=([_award(contract_number=number),
                                  _award(contract_number='AGS-0000002')], [])):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         '--search', 'ncar'])

        data = json.loads(result.output)
        assert data['already_in_sam'] == 1
        by_number = {r['contract_number']: r for r in data['results']}
        assert by_number[number]['in_sam']['contract_number'] == number
        assert by_number['AGS-0000002']['in_sam'] is None

    def test_search_envelope_is_pure_json(self, runner, mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award(), _usaspending()], [])):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         '--search', 'turbulence',
                                         '--limit', '4'])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data['kind'] == 'award_search_results'
        assert data['query'] == 'turbulence'
        assert data['limit'] == 4
        assert data['count'] == 2
        assert data['errors'] == []
        # provenance per record is what makes a mixed result interpretable
        assert {r['provenance'] for r in data['results']} == {
            'NSF Awards API', 'USAspending'}

    def test_errors_are_always_present_even_when_empty(self, runner,
                                                       mock_db_session):
        """A partial result that looks complete is the failure this prevents."""
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         '--search', 'ncar'])
        assert 'errors' in json.loads(result.output)

    def test_lookup_envelope_is_pure_json(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   return_value=_award()):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         'AGS-1852977'])
        data = json.loads(result.output)
        assert data['kind'] == 'award'
        assert data['contract_number'] == 'AGS-1852977'
        assert data['award']['provenance'] == 'NSF Awards API'
        assert data['award']['pi']['email'] == 'barron@ucar.edu'

    def test_not_found_still_emits_its_envelope(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award', return_value=None):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         'DE-SC0012671'])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data == {'kind': 'award', 'error': 'not_found',
                        'contract_number': 'DE-SC0012671', 'source': None}

    def test_unavailable_emits_its_own_envelope(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award',
                   side_effect=AwardSourceUnavailable('nsf.gov down')):
            result = runner.invoke(cli, ['--format', 'json', 'awards',
                                         'DE-SC0012671', '--source', 'DOE'])
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data['error'] == 'source_unavailable'
        assert data['source'] == 'DOE'
        assert 'down' in data['reason']


class TestScoping:

    def test_source_is_passed_through_to_search_awards(self, runner,
                                                       mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])) as search:
            runner.invoke(cli, ['awards', '--search', 'x', '--source', 'NSF'])
        assert search.call_args.kwargs['sources'] == ['NSF']

    def test_limit_is_passed_through(self, runner, mock_db_session):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])) as search:
            runner.invoke(cli, ['awards', '--search', 'x', '--limit', '3'])
        assert search.call_args.kwargs['limit'] == 3

    def test_cli_injects_a_longer_timeout_than_the_webapp_default(
            self, runner, mock_db_session):
        """DEFAULT_TIMEOUT is 10 s for the htmx path; a CLI has no worker to
        hold and must not be capped by that."""
        from cli.awards.commands import CLI_TIMEOUT
        from sam.integration.awards.client import DEFAULT_TIMEOUT

        assert CLI_TIMEOUT > DEFAULT_TIMEOUT

        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])) as search:
            runner.invoke(cli, ['awards', '--search', 'x'])

        providers = search.call_args.kwargs['providers']
        assert providers, 'the CLI must inject its own provider pool'
        assert all(p.client.timeout == CLI_TIMEOUT for p in providers)
