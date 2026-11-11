"""CliRunner ports of the `sam-search allocations` subcommand tests.

Phase 4f port: the legacy version at tests/integration/test_sam_search_cli.py
ran the CLI as a real subprocess against the live database. We invoke the
Click command directly via `CliRunner`, with `cli.core.context.Session`
patched to hand out our SAVEPOINT'd test session — same pattern used by
the already-ported `new_tests/unit/cli/test_sam_search_cli.py`.

Coverage rationale: `AllocationSearchCommand.execute` in
src/cli/allocations/commands.py is a thin flag-to-parameter translator
over `get_allocation_summary` / `get_allocation_summary_with_usage` (both
already covered at the service layer in test_query_functions.py). What
this file uniquely covers is the CLI flag plumbing — `--total-facilities`
becomes `facility="TOTAL"`, comma-separated `--resource Derecho,Casper`
parses correctly, `--active-at` validates date format, etc. Plus the
Rich-rendered table column shape that the legacy assertions rely on.
"""
import json
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli




class TestAllocationQueries:
    """Test allocations command with various filters and groupings."""

    # Rich auto-detects terminal width; under CliRunner the default is narrow
    # (~80 cols) which truncates long column headers ('Resource' -> 'Resou…')
    # and breaks substring assertions. The legacy subprocess version sets
    # COLUMNS=300 in the env. We do the same via fixture autouse so every
    # invoke in this class sees the wide-terminal env.
    @pytest.fixture(autouse=True)
    def _wide_terminal(self, monkeypatch):
        monkeypatch.setenv('COLUMNS', '300')
        monkeypatch.setenv('TERMINAL_WIDTH', '300')

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        """Patch the CLI's session factory to hand back our test session.

        Same pattern as new_tests/unit/cli/test_sam_search_cli.py — both call
        sites (engine creation + Session() constructor) get patched so
        the CLI runs against the test DB inside the test's outer
        SAVEPOINT'd transaction.
        """
        with patch('sam.session.create_sam_engine') as mock_create_engine, \
             patch('cli.core.context.Session') as mock_session_cls:
            mock_create_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    # ------------------------------------------------------------------
    # Help output (no DB)
    # ------------------------------------------------------------------

    def test_allocations_help(self, runner):
        """Test allocations command help text."""
        result = runner.invoke(cli, ['allocations', '--help'])

        assert result.exit_code == 0
        assert 'Usage:' in result.output
        assert 'allocations' in result.output.lower()
        assert '--resource' in result.output
        assert '--facility' in result.output
        assert '--allocation-type' in result.output
        assert '--project' in result.output
        assert 'comma-separated' in result.output
        assert '--format' in result.output and '--roots-only' in result.output
        # Click keeps each \\b example block on its own lines.
        assert '\n      sam-search allocations --project SCSG0001\n' in result.output

    # ------------------------------------------------------------------
    # Default and single-dimension grouping
    # ------------------------------------------------------------------

    def test_allocations_all_grouped(self, runner, mock_db_session):
        """Test allocations with no filters (all grouped)."""
        result = runner.invoke(cli, ['allocations'])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total' in result.output
        assert 'allocations' in result.output
        assert 'Resource' in result.output
        assert 'Facility' in result.output
        assert 'Type' in result.output
        assert 'Project' in result.output
        assert 'Total Amount' in result.output

    def test_allocations_single_resource(self, runner, mock_db_session):
        """Test filtering by a single resource."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--total-facilities', '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Derecho' in result.output
        assert 'Grand Total:' in result.output
        assert 'Resource' in result.output

    def test_allocations_multiple_resources(self, runner, mock_db_session):
        """Test filtering by multiple comma-separated resources."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho,Casper',
            '--total-facilities', '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Derecho' in result.output or 'Casper' in result.output
        assert 'Grand Total:' in result.output
        assert 'Resource' in result.output

    def test_allocations_multiple_types(self, runner, mock_db_session):
        """Test filtering by multiple allocation types."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--facility', 'UNIV', '--allocation-type', 'Small,Classroom',
            '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total:' in result.output
        assert 'Type' in result.output
        assert 'Small' in result.output or 'Classroom' in result.output

    def test_allocations_specific_project(self, runner, mock_db_session):
        """Test filtering by a specific project."""
        result = runner.invoke(cli, ['allocations', '--project', 'SCSG0001'])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'SCSG0001' in result.output
        assert 'Grand Total:' in result.output
        assert 'Resource' in result.output
        assert 'Total Amount' in result.output

    def test_allocations_multiple_projects(self, runner, mock_db_session):
        """Test filtering by multiple comma-separated projects."""
        result = runner.invoke(cli, [
            'allocations', '--project', 'SCSG0001,SCSG0002',
            '--total-resources', '--total-facilities', '--total-types',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total:' in result.output
        assert 'Project' in result.output
        assert 'SCSG0001' in result.output or 'SCSG0002' in result.output

    # ------------------------------------------------------------------
    # Verbose mode
    # ------------------------------------------------------------------

    def test_allocations_with_verbose(self, runner, mock_db_session):
        """Test allocations with --verbose flag (shows averages)."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--total-facilities', '--total-types', '--total-projects',
            '--verbose',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Avg Amount' in result.output
        assert 'Derecho' in result.output

    def test_allocations_complex_multi_dimensional(self, runner, mock_db_session):
        """Test complex query with multiple dimensions."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho,Casper',
            '--facility', 'UNIV,WNA', '--allocation-type', 'Small,Classroom',
            '--total-projects', '--verbose',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total:' in result.output
        assert 'Resource' in result.output
        assert 'Facility' in result.output
        assert 'Type' in result.output
        assert 'Avg Amount' in result.output

    def test_allocations_total_by_facility(self, runner, mock_db_session):
        """Test totaling across all dimensions except facility."""
        result = runner.invoke(cli, [
            'allocations', '--total-resources', '--total-types',
            '--total-projects', '--verbose',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Facility' in result.output
        assert 'Grand Total:' in result.output

    # ------------------------------------------------------------------
    # Date and inactive filters
    # ------------------------------------------------------------------

    def test_allocations_with_active_at(self, runner, mock_db_session):
        """Test filtering by historical date with --active-at."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--active-at', '2024-01-15', '--total-facilities',
            '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total:' in result.output

    def test_allocations_with_inactive(self, runner, mock_db_session):
        """Test including inactive allocations with --inactive."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--inactive', '--total-facilities', '--total-types',
            '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Grand Total:' in result.output

    def test_allocations_invalid_date(self, runner, mock_db_session):
        """Test that invalid date format returns error."""
        result = runner.invoke(cli, ['allocations', '--active-at', 'invalid-date'])

        assert result.exit_code == 2  # Exit code 2 for errors (invalid input)
        assert 'Invalid date format' in result.output

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_allocations_no_results(self, runner, mock_db_session):
        """Test query that returns no results."""
        result = runner.invoke(cli, [
            'allocations', '--project', 'NONEXISTENT9999',
        ])

        # Should complete successfully (no results is not an error)
        assert result.exit_code == 1
        assert 'No allocations found' in result.output

    def test_allocations_grand_total_format(self, runner, mock_db_session):
        """Test that grand total line is properly formatted."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Derecho',
            '--total-facilities', '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Grand Total:' in result.output
        assert 'allocations' in result.output

        lines = result.output.split('\n')
        total_line = [line for line in lines if 'Grand Total:' in line][0]
        assert any(char.isdigit() for char in total_line)

    def test_allocations_resource_with_spaces(self, runner, mock_db_session):
        """Test resource names with spaces (e.g., 'Casper GPU')."""
        result = runner.invoke(cli, [
            'allocations', '--resource', 'Casper GPU',
            '--total-facilities', '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output

    def test_allocations_output_columns_adapt(self, runner, mock_db_session):
        """Test that output columns adapt based on grouping."""
        # Query with TOTAL for resources - should not show Resource column in data
        result = runner.invoke(cli, [
            'allocations', '--total-resources',
            '--facility', 'UNIV', '--total-types', '--total-projects',
        ])

        assert result.exit_code == 0
        assert 'Allocation Summary' in result.output
        assert 'Facility' in result.output


class TestAllocationTrees:
    """A built shared pool (root + two inheriting members): totals count it once, rows mark members."""

    @pytest.fixture(autouse=True)
    def _wide_terminal(self, monkeypatch):
        monkeypatch.setenv('COLUMNS', '300')

    @pytest.fixture
    def pool(self, session):
        from factories.projects import make_account, make_allocation, make_allocation_type, make_project
        from factories.resources import make_resource
        from factories.summaries import make_comp_charge_summary
        from sam.resources.resources import ResourceType

        hpc = make_resource(session, commission_date=datetime(2000, 1, 1),
                            resource_type=session.query(ResourceType).filter_by(resource_type='HPC').one())
        atype = make_allocation_type(session, facility_name='UNIV')
        span = dict(start_date=datetime(2026, 1, 1), end_date=datetime(2026, 12, 31, 23, 59, 59))
        root = make_project(session, allocation_type=atype)
        root_alloc = make_allocation(session, account=make_account(session, project=root, resource=hpc),
                                     amount=1000.0, **span)
        members = []
        for charges in (30.0, 70.0):
            member = make_project(session, allocation_type=atype, parent=root)
            account = make_account(session, project=member, resource=hpc)
            make_allocation(session, account=account, amount=1000.0, parent=root_alloc, **span)
            make_comp_charge_summary(session, charges=charges,
                                     activity_date=datetime(2026, 3, 1)).account_id = account.account_id
            members.append(member)
        session.flush()
        return hpc.resource_name, root, members

    def _invoke(self, session, *args):
        with patch('sam.session.create_sam_engine') as engine, \
             patch('cli.core.context.Session') as session_cls:
            engine.return_value = (MagicMock(), None)
            session_cls.return_value = session
            return CliRunner().invoke(cli, list(args))

    def test_rollup_counts_the_pool_once(self, session, pool):
        resource, _, _ = pool
        result = self._invoke(session, 'allocations', '--resource', resource,
                              '--total-projects', '--active-at', '2026-06-01', '--format', 'json')
        data = json.loads(result.output)
        assert data['roots_only'] is True
        (row,) = data['rows']
        assert (row['count'], row['total_amount']) == (1, 1000.0)
        assert row['annualized_rate'] == pytest.approx(1000.0)

    def test_listing_marks_members_and_totals_roots(self, session, pool):
        resource, root, members = pool
        result = self._invoke(session, 'allocations', '--resource', resource, '--active-at', '2026-06-01')
        assert result.exit_code == 0
        member_line = next(line for line in result.output.splitlines() if members[0].projcode in line)
        assert f'↳ {root.projcode}' in member_line
        assert 'Grand Total (roots only; 2 sub-project rows not summed): 1 allocation, 1,000' in result.output

    def test_roots_only_hides_members(self, session, pool):
        resource, root, _ = pool
        result = self._invoke(session, '--format', 'json', 'allocations', '--resource', resource,
                              '--roots-only', '--active-at', '2026-06-01')
        assert [r['projcode'] for r in json.loads(result.output)['rows']] == [root.projcode]

    def test_member_used_is_its_own(self, session, pool):
        resource, root, members = pool
        result = self._invoke(session, 'allocations', '--resource', resource, '--show-usage',
                              '--active-at', '2026-06-01')
        assert result.exit_code == 0
        def used(projcode):     # the cell after Allocated (1,000)
            tokens = next(line for line in result.output.splitlines() if projcode in line.split()).split()
            return tokens[tokens.index('1,000') + 1]

        assert used(members[0].projcode) == '30'     # own, not the pool's 100
        assert used(root.projcode) == '100'          # the root row carries the pool
        assert 'Used is the member\'s own' in result.output

    def test_piped_table_keeps_every_column_whole(self, session, pool, monkeypatch):
        monkeypatch.setenv('COLUMNS', '40')
        resource, root, members = pool
        result = self._invoke(session, 'allocations', '--resource', resource, '--show-usage',
                              '--active-at', '2026-06-01')
        assert '…' not in result.output
        assert 'Annual Rate' in result.output and f'↳ {root.projcode}' in result.output
