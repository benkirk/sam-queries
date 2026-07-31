"""Click wiring for `sam-search contracts` — guards, exit codes, envelopes.

The `mock_db_session` fixture patches `cli.cmds.search.Session`, a **different
import site** from `test_cli_contracts.py`'s `cli.cmds.admin.Session`. Both
patches are required: without the second, the CLI opens its own connection and
escapes the test's SAVEPOINT, so nothing it writes is rolled back and nothing
this test creates is visible to it.

Exit codes are the point of several tests here. `sam-search contracts` uses the
three-outcome convention shared by every other `sam-search` subcommand —
0 found / 1 not found / 2 error — which is deliberately *not*
`sam-admin contracts --validate`'s "2 means findings exist".
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli
from tests.factories._seq import next_seq
from tests.factories.core import make_user
from tests.factories.projects import make_contract, make_contract_source

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    """A runner with a wide terminal.

    Rich honours ``COLUMNS``, and the result tables ellipsize to fit. At the
    default 80 columns the factory-generated contract numbers get cut, so
    row-content assertions would be testing layout instead of behaviour.
    """
    return CliRunner(env={'COLUMNS': '200'})


@pytest.fixture
def mock_db_session(session):
    """Bind the CLI group to the test's SAVEPOINT session."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.cmds.search.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


@pytest.fixture
def token():
    return f"zqc{next_seq('CLISEARCH')}"


@pytest.fixture
def contract(session, token):
    """One open contract with every displayed field populated."""
    return make_contract(
        session,
        contract_number=f'AGS-{token}',
        title=f'A study of {token} in the boundary layer',
        source=make_contract_source(session, name='NSF'),
        monitor=make_user(session),
        start_date=datetime.now() - timedelta(days=365),
    )


class TestGuards:
    """`sum(inputs) != 1`, the user/project idiom — not per-flag checks."""

    def test_no_arguments_is_an_error_with_help(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts'])
        assert result.exit_code == 1
        assert 'Please provide exactly one of' in result.output
        assert 'Usage:' in result.output

    def test_number_and_search_together_are_rejected(self, runner,
                                                     mock_db_session):
        result = runner.invoke(cli, ['contracts', 'AGS-1', '--search', 'x'])
        assert result.exit_code == 1
        assert 'Please provide exactly one of' in result.output

    def test_filters_alone_are_a_valid_query(self, runner, mock_db_session,
                                             contract):
        """"every open NSF contract" needs no pattern."""
        result = runner.invoke(cli, ['contracts', '--source', 'NSF',
                                     '--limit', '3'])
        assert result.exit_code == 0, result.output

    def test_help_documents_the_wildcard_rule(self, runner):
        result = runner.invoke(cli, ['contracts', '--help'])
        assert result.exit_code == 0
        # The rule diverges from `user --search`; the help must say so
        # honestly rather than repeat that command's inaccurate promise.
        assert 'LIKE pattern' in result.output
        assert 'case-insensitive' in result.output


class TestExitCodes:

    def test_found_exits_zero(self, runner, mock_db_session, contract):
        result = runner.invoke(cli, ['contracts', contract.contract_number])
        assert result.exit_code == 0, result.output

    def test_unknown_number_exits_not_found(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', 'NO-SUCH-CONTRACT-9999'])
        assert result.exit_code == 1
        assert 'not found' in result.output.lower()

    def test_pattern_with_no_matches_exits_not_found(self, runner,
                                                     mock_db_session):
        result = runner.invoke(cli, ['contracts', '--search',
                                     'zzz-no-such-contract-zzz'])
        assert result.exit_code == 1
        assert 'No contracts found' in result.output


class TestRichOutput:

    def test_detail_shows_the_core_fields(self, runner, mock_db_session,
                                          contract):
        result = runner.invoke(cli, ['contracts', contract.contract_number])
        assert result.exit_code == 0, result.output
        assert contract.contract_number in result.output
        for label in ('Source', 'Status', 'Period', 'PI', 'Monitor'):
            assert label in result.output

    def test_list_projects_is_opt_in(self, runner, mock_db_session, contract):
        without = runner.invoke(cli, ['contracts', contract.contract_number])
        assert 'Linked projects' not in without.output

        with_ = runner.invoke(cli, ['contracts', contract.contract_number,
                                    '--list-projects'])
        assert with_.exit_code == 0, with_.output

    def test_search_renders_a_table(self, runner, mock_db_session, contract,
                                    token):
        result = runner.invoke(cli, ['contracts', '--search', token])
        assert result.exit_code == 0, result.output
        assert contract.contract_number in result.output

    def test_all_widens_the_scope(self, runner, mock_db_session, session,
                                  token):
        make_contract(session, contract_number=f'OCE-{token}-old',
                      title=f'expired {token}',
                      start_date=datetime.now() - timedelta(days=800),
                      end_date=datetime.now() - timedelta(days=30))

        default = runner.invoke(cli, ['contracts', '--search', token])
        assert f'OCE-{token}-old' not in default.output

        widened = runner.invoke(cli, ['contracts', '--search', token, '--all'])
        assert widened.exit_code == 0, widened.output
        assert f'OCE-{token}-old' in widened.output


class TestJsonOutput:
    """`--format` is a group-level flag: `sam-search --format json contracts`."""

    def test_detail_envelope_is_pure_json(self, runner, mock_db_session,
                                          contract):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     contract.contract_number])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data['kind'] == 'contract'
        assert data['contract_number'] == contract.contract_number
        assert isinstance(data['projects'], list)

    def test_search_envelope_is_pure_json(self, runner, mock_db_session,
                                          contract, token):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     '--search', token])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data['kind'] == 'contract_search_results'
        assert data['pattern'] == token
        assert data['scope'] == 'open'
        assert data['count'] == len(data['contracts'])
        assert data['contracts'][0]['contract_number'] == \
            contract.contract_number

    def test_filters_are_echoed_back(self, runner, mock_db_session, contract,
                                     token):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     '--search', token, '--source', 'NSF'])
        data = json.loads(result.output)
        assert data['filters'] == {'source': 'NSF'}

    def test_not_found_still_emits_its_envelope_and_exits_one(
            self, runner, mock_db_session):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     'NO-SUCH-CONTRACT-9999'])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data == {'kind': 'contract', 'error': 'not_found',
                        'contract_number': 'NO-SUCH-CONTRACT-9999'}

    def test_empty_search_still_emits_its_envelope_and_exits_one(
            self, runner, mock_db_session):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     '--search', 'zzz-no-such-zzz'])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data['kind'] == 'contract_search_results'
        assert data['count'] == 0
        assert data['contracts'] == []

    def test_dates_serialise_to_iso(self, runner, mock_db_session, contract):
        result = runner.invoke(cli, ['--format', 'json', 'contracts',
                                     contract.contract_number])
        data = json.loads(result.output)
        assert data['start_date'].startswith(
            contract.start_date.strftime('%Y-%m-%d'))
