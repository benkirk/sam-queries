"""
CliRunner tests for `sam-admin contracts --validate`.

Covers the Click wiring, the flag guards, and the JSON envelope. The check
logic itself is tested at the query layer in `tests/unit/test_contract_audit.py`.

`--check-sources` is stubbed at `sam.integration.awards.resolve_award` — the
same patch target the existing award tests use, and the reason
`sam/integration/awards/audit.py` reaches through the module object rather
than binding the name at import. **No test here touches the network.**
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.admin import cli
from sam.integration.awards import AwardRecord, AwardSourceUnavailable, PersonRef
from sam.queries.contract_audit import CHECKS

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_db_session(session):
    """Bind the CLI group to the test's SAVEPOINT session.

    Both patches are required: without the second the CLI opens its own
    connection and escapes the transaction the test rolls back. `Session` is
    patched at its *import site* in `cli.cmds.admin`, not in `sam.session`.
    """
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.core.context.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


def _award(**overrides):
    """An NSF-shaped record that agrees with nothing in particular."""
    defaults = dict(
        provenance='NSF Awards API',
        contract_number='AGS-1852977',
        title='A test award',
        start_date=date(2019, 9, 1),
        end_date=date(2023, 8, 31),
        url='https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977',
        program_name='Physical & Dynamic Meteorology',
        pi=PersonRef(name='Nobody Here'),
        monitor=PersonRef(name='Nobody Here'),
        unavailable_fields=frozenset(),
    )
    defaults.update(overrides)
    return AwardRecord(**defaults)


class TestGuards:
    """Bad flag combinations are rejected before any query runs."""

    def test_no_action_specified(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts'])
        assert result.exit_code == 2
        assert 'no action specified' in result.output

    def test_all_requires_validate(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', '--all'])
        assert result.exit_code == 1
        assert '--all requires --validate' in result.output

    def test_check_sources_requires_validate(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', '--check-sources'])
        assert result.exit_code == 1
        assert '--check-sources requires --validate' in result.output

    def test_limit_requires_check_sources(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', '--validate', '--limit', '5'])
        assert result.exit_code == 1
        assert '--limit requires --check-sources' in result.output


class TestRichOutput:

    def test_validate_runs(self, runner, mock_db_session):
        """0 (clean) and 2 (findings) are both valid — the snapshot decides."""
        result = runner.invoke(cli, ['contracts', '--validate'])
        assert result.exit_code in (0, 2), result.output
        assert 'open contracts' in result.output

    def test_all_widens_the_scope(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', '--validate', '--all'])
        assert result.exit_code in (0, 2), result.output
        assert 'all contracts' in result.output

    def test_verbose_is_accepted(self, runner, mock_db_session):
        result = runner.invoke(cli, ['contracts', '--validate', '-v'])
        assert result.exit_code in (0, 2), result.output


class TestJsonOutput:

    def test_entire_stdout_is_valid_json(self, runner, mock_db_session):
        """The contrast with `user --validate`, which leaks Rich into JSON.

        `output_json` writes pure JSON, so the whole stream must parse — no
        slicing from the first brace (cf. tests/integration/test_cli_json_output.py).
        """
        result = runner.invoke(cli, ['--format', 'json', 'contracts', '--validate'])
        assert result.exit_code in (0, 2), result.output
        data = json.loads(result.stdout)
        assert data['kind'] == 'contract_audit'

    def test_envelope_top_level_keys(self, runner, mock_db_session):
        result = runner.invoke(cli, ['--format', 'json', 'contracts', '--validate'])
        data = json.loads(result.stdout)
        for required in ('kind', 'scope', 'contracts_audited', 'checked_sources',
                         'total_findings', 'checks', 'program_findings',
                         'source_check'):
            assert required in data, f"missing top-level key: {required}"
        assert data['scope'] == 'open'
        assert data['checked_sources'] is False
        assert data['source_check'] is None

    def test_every_check_is_present_even_when_clean(self, runner, mock_db_session):
        """A missing section reads as "not run"; a zero count reads as clean."""
        result = runner.invoke(cli, ['--format', 'json', 'contracts', '--validate'])
        data = json.loads(result.stdout)
        assert [c['key'] for c in data['checks']] == [k for k, _l, _s in CHECKS]
        for check in data['checks']:
            assert check['count'] == len(check['findings'])
            assert check['severity'] in ('high', 'medium', 'low')

    def test_findings_carry_the_contract_schema(self, runner, mock_db_session):
        result = runner.invoke(cli, ['--format', 'json', 'contracts', '--validate',
                                     '--all'])
        data = json.loads(result.stdout)
        findings = [f for c in data['checks'] for f in c['findings']]
        if not findings:
            pytest.skip('snapshot has no contract findings')
        contract = findings[0]['contract']
        for field in ('contract_id', 'contract_number', 'title',
                      'contract_source', 'is_active', 'pi_username'):
            assert field in contract

    def test_all_scope_is_reported(self, runner, mock_db_session):
        result = runner.invoke(cli, ['--format', 'json', 'contracts', '--validate',
                                     '--all'])
        data = json.loads(result.stdout)
        assert data['scope'] == 'all'


class TestCheckSources:
    """Network path, always stubbed."""

    def _invoke(self, runner, *extra):
        return runner.invoke(cli, ['--format', 'json', 'contracts', '--validate',
                                   '--check-sources', '--limit', '3',
                                   '--sleep', '0', *extra])

    def test_source_check_is_populated(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award', return_value=_award()):
            result = self._invoke(runner)
        assert result.exit_code in (0, 2), result.output
        data = json.loads(result.stdout)
        assert data['checked_sources'] is True
        assert data['source_check']['checked'] == 3

    def test_unavailable_source_does_not_abort_the_run(self, runner, mock_db_session):
        """A dead API costs one "unchecked" contract, not the whole audit."""
        with patch('sam.integration.awards.resolve_award',
                   side_effect=AwardSourceUnavailable('NSF is down')):
            result = self._invoke(runner)
        assert result.exit_code in (0, 2), result.output
        data = json.loads(result.stdout)
        assert data['source_check']['unchecked'] == 3
        assert data['source_check']['checked'] == 0

    def test_no_record_is_distinct_from_divergence(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award', return_value=None):
            result = self._invoke(runner)
        data = json.loads(result.stdout)
        assert data['source_check']['no_record'] == 3
        assert data['source_check']['divergent'] == 0

    def test_usaspending_person_fields_are_skipped(self, runner, mock_db_session):
        """`unavailable_fields` is structural absence, not divergence."""
        record = _award(provenance='USAspending', contract_number=None,
                        pi=None, monitor=None,
                        unavailable_fields=frozenset({'pi', 'monitor'}))
        with patch('sam.integration.awards.resolve_award', return_value=record):
            result = self._invoke(runner)
        data = json.loads(result.stdout)
        diverged = {d['field']
                    for c in data['source_check']['contracts']
                    for d in c['divergences']}
        assert 'pi' not in diverged
        assert 'monitor' not in diverged

    def test_rich_mode_renders_the_source_section(self, runner, mock_db_session):
        with patch('sam.integration.awards.resolve_award', return_value=_award()):
            result = runner.invoke(cli, ['contracts', '--validate',
                                         '--check-sources', '--limit', '2',
                                         '--sleep', '0'])
        assert result.exit_code in (0, 2), result.output
        assert 'Funding-source check' in result.output
