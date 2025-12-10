"""
Integration Tests for sam_search_cli.py CLI

Tests the command-line interface by invoking the CLI as a subprocess and
validating output, exit codes, and behavior. These are integration tests
that use the real database and test the full CLI stack.

Test Organization:
- TestUserExactSearch: Exact username lookups
- TestUserPatternSearch: Pattern-based user searches
- TestUserSpecialSearches: Abandoned users, active project searches
- TestProjectExactSearch: Exact project code lookups
- TestProjectPatternSearch: Pattern-based project searches
- TestProjectExpirations: Upcoming and recent expirations
- TestCLIBehavior: Exit codes, error handling, help text
"""

import pytest
import subprocess
import sys
from pathlib import Path


# Path to the CLI script (relative to repository root)
CLI_PATH = str(Path(__file__).parent.parent.parent / 'src' / 'sam_search_cli.py')


def run_cli(*args, expect_success=True):
    """
    Run the CLI with given arguments and return result.

    Args:
        *args: Command line arguments
        expect_success: If True, assert returncode is 0

    Returns:
        subprocess.CompletedProcess with stdout, stderr, returncode
    """
    result = subprocess.run(
        [CLI_PATH] + list(args),
        capture_output=True,
        text=True,
        timeout=60
    )

    if expect_success:
        if result.returncode != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            assert result.returncode == 0, f"CLI failed with exit code {result.returncode}"

    return result


# ============================================================================
# User Exact Search Tests
# ============================================================================
class TestUserExactSearch:
    """Test exact username lookups."""

    def test_user_exact_found(self):
        """Test finding a specific user by exact username."""
        result = run_cli('user', 'benkirk')

        assert 'User Information:' in result.stdout
        assert 'benkirk' in result.stdout
        assert 'Ben Kirk' in result.stdout
        assert 'benkirk@ucar.edu' in result.stdout
        assert 'Active Projects' in result.stdout

    def test_user_exact_not_found(self):
        """Test that non-existent user returns exit code 1."""
        result = run_cli('user', 'nonexistentuser12345', expect_success=False)

        assert result.returncode == 1
        assert 'not found' in result.stdout.lower()
        assert 'nonexistentuser12345' in result.stdout

    def test_user_with_list_projects(self):
        """Test user lookup with --list-projects flag."""
        result = run_cli('user', 'benkirk', '--list-projects')

        assert 'User Information:' in result.stdout
        assert 'Active projects for benkirk' in result.stdout
        assert 'SCSG0001' in result.stdout or 'Project Information' in result.stdout

    def test_user_with_verbose(self):
        """Test user lookup with --verbose flag."""
        result = run_cli('user', 'benkirk', '--verbose')

        assert 'User Information:' in result.stdout
        assert 'Organization(s)' in result.stdout or 'Active Projects' in result.stdout

    def test_user_with_verbose_and_projects(self):
        """Test user lookup with both --verbose and --list-projects."""
        result = run_cli('user', 'benkirk', '--verbose', '--list-projects')

        assert 'User Information:' in result.stdout
        assert 'Active projects for benkirk' in result.stdout


# ============================================================================
# User Pattern Search Tests
# ============================================================================
class TestUserPatternSearch:
    """Test pattern-based user searches."""

    def test_user_pattern_search(self):
        """Test searching for users with a pattern."""
        result = run_cli('user', '--search', 'ben%')

        assert 'Found' in result.stdout
        assert 'user(s)' in result.stdout
        # Should find multiple users 
        assert 'benkirk' in result.stdout

    def test_user_pattern_with_limit(self):
        """Test pattern search with --limit flag."""
        result = run_cli('user', '--search', 'a%', '--limit', '5')

        assert 'Found 5 user(s)' in result.stdout

    def test_user_pattern_with_verbose(self):
        """Test pattern search with --verbose flag."""
        result = run_cli('user', '--search', 'ben%', '--limit', '3', '--verbose')

        assert 'Found' in result.stdout
        assert 'ID' in result.stdout
        assert 'Email' in result.stdout
        assert 'Active' in result.stdout

    def test_user_pattern_no_results(self):
        """Test pattern search with no matching users."""
        result = run_cli('user', '--search', 'zzzzzzzzz%', expect_success=False)

        assert result.returncode == 1
        assert 'No users found' in result.stdout

    def test_user_pattern_different_limit(self):
        """Test pattern search with different limit values."""
        result = run_cli('user', '--search', 'j%', '--limit', '10')

        # Should find up to 10 users
        assert 'Found' in result.stdout
        line_count = len([line for line in result.stdout.split('\n') if line.strip() and line[0].isdigit()])
        assert line_count <= 10


# ============================================================================
# User Special Searches
# ============================================================================
class TestUserSpecialSearches:
    """Test special user search commands."""

    def test_abandoned_users(self):
        """Test finding abandoned users (active users with no active projects)."""
        result = run_cli('user', '--abandoned')

        assert 'active' in result.stdout.lower()
        assert 'users' in result.stdout.lower()
        # Either found some or found none (both valid)
        assert ('Found' in result.stdout or 'Examining' in result.stdout)

    def test_users_with_active_project(self):
        """Test finding users with at least one active project."""
        result = run_cli('user', '--has-active-project')

        assert 'users' in result.stdout.lower()
        # Should find at least benkirk
        assert 'Found' in result.stdout or 'benkirk' in result.stdout

    def test_users_with_active_project_verbose(self):
        """Test users with active projects with --verbose."""
        result = run_cli('user', '--has-active-project', '--verbose')

        # With verbose, should show detailed user information
        assert 'users' in result.stdout.lower()


# ============================================================================
# Project Exact Search Tests
# ============================================================================
class TestProjectExactSearch:
    """Test exact project code lookups."""

    @pytest.mark.xfail(reason="Likely to fail on obfuscated database.")
    def test_project_exact_found(self):
        """Test finding a specific project by exact code."""
        result = run_cli('project', 'SCSG0001')

        assert 'Project Information:' in result.stdout
        assert 'SCSG0001' in result.stdout
        assert 'CSG systems project' in result.stdout
        assert 'Status' in result.stdout

    def test_project_exact_not_found(self):
        """Test that non-existent project returns exit code 1."""
        result = run_cli('project', 'ZZZZ9999', expect_success=False)

        assert result.returncode == 1
        assert 'not found' in result.stdout.lower()
        assert 'ZZZZ9999' in result.stdout

    def test_project_with_list_users(self):
        """Test project lookup with --list-users flag."""
        result = run_cli('project', 'SCSG0001', '--list-users')

        assert 'Project Information:' in result.stdout
        assert 'user' in result.stdout.lower() or 'benkirk' in result.stdout

    def test_project_with_verbose(self):
        """Test project lookup with --verbose flag."""
        result = run_cli('project', 'SCSG0001', '--verbose')

        assert 'Project Information:' in result.stdout
        # Verbose should show abstract or organizations
        assert 'Abstract' in result.stdout or 'Organizations' in result.stdout

    def test_project_with_verbose_and_users(self):
        """Test project lookup with both --verbose and --list-users."""
        result = run_cli('project', 'SCSG0001', '--verbose', '--list-users')

        assert 'Project Information:' in result.stdout
        assert 'Active user' in result.stdout
        assert 'Email' in result.stdout  # Verbose user info


# ============================================================================
# Project Pattern Search Tests
# ============================================================================
class TestProjectPatternSearch:
    """Test pattern-based project searches."""

    def test_project_pattern_search(self):
        """Test searching for projects with a pattern."""
        result = run_cli('project', '--search', 'SCSG%')

        assert 'Found' in result.stdout
        assert 'project(s)' in result.stdout
        assert 'SCSG' in result.stdout

    def test_project_pattern_with_limit(self):
        """Test pattern search with --limit flag."""
        result = run_cli('project', '--search', 'U%', '--limit', '5')

        assert 'Found 5 project(s)' in result.stdout

    def test_project_pattern_with_verbose(self):
        """Test pattern search with --verbose flag."""
        result = run_cli('project', '--search', 'SCSG%', '--verbose')

        assert 'Found' in result.stdout
        assert 'ID' in result.stdout
        assert 'Lead' in result.stdout or 'Users' in result.stdout

    def test_project_pattern_no_results(self):
        """Test pattern search with no matching projects."""
        result = run_cli('project', '--search', 'ZZZZZ%', expect_success=False)

        assert result.returncode == 1
        assert 'No projects found' in result.stdout


# ============================================================================
# Project Expiration Tests
# ============================================================================
class TestProjectExpirations:
    """Test project expiration searches."""

    def test_upcoming_expirations(self):
        """Test finding projects with upcoming expirations."""
        result = run_cli('project', '--upcoming-expirations')

        # Either found expirations or found none
        assert 'allocations expiring' in result.stdout or 'Found 0' in result.stdout
        if 'days remaining' in result.stdout:
            # Should show project codes and days
            assert result.stdout.count('days remaining') >= 1

    def test_upcoming_expirations_with_users(self):
        """Test upcoming expirations with --list-users flag."""
        result = run_cli('project', '--upcoming-expirations', '--list-users')

        assert 'allocations expiring' in result.stdout or 'Found' in result.stdout

    def test_recent_expirations(self):
        """Test finding recently expired projects."""
        result = run_cli('project', '--recent-expirations')

        # Either found expirations or found none
        assert 'expired projects' in result.stdout or 'Found 0' in result.stdout
        if 'days since expiration' in result.stdout:
            # Should show project codes and days
            assert result.stdout.count('days since expiration') >= 1

    def test_recent_expirations_with_users(self):
        """Test recent expirations with --list-users flag."""
        result = run_cli('project', '--recent-expirations', '--list-users')

        assert 'expired projects' in result.stdout
        # Should show the abandoned users analysis
        assert 'abandoned' in result.stdout.lower() or 'expiring' in result.stdout.lower()

    def test_upcoming_expirations_verbose(self):
        """Test upcoming expirations with --verbose flag."""
        result = run_cli('project', '--upcoming-expirations', '--verbose')

        assert 'allocations expiring' in result.stdout
        # Verbose should show full project information if any found
        if 'days remaining' in result.stdout:
            assert 'Project Information:' in result.stdout or 'Use --verbose' not in result.stdout


# ============================================================================
# CLI Behavior Tests
# ============================================================================
class TestCLIBehavior:
    """Test CLI behavior, exit codes, and error handling."""

    def test_exit_code_success(self):
        """Test that successful commands return exit code 0."""
        result = run_cli('user', 'benkirk')
        assert result.returncode == 0

    def test_exit_code_not_found(self):
        """Test that not found returns exit code 1."""
        result = run_cli('user', 'nonexistent', expect_success=False)
        assert result.returncode == 1

    def test_help_user(self):
        """Test user command help text."""
        result = run_cli('user', '--help')

        assert 'Usage:' in result.stdout
        assert 'user' in result.stdout.lower()
        assert '--list-projects' in result.stdout

    def test_help_project(self):
        """Test project command help text."""
        result = run_cli('project', '--help')

        assert 'Usage:' in result.stdout
        assert 'project' in result.stdout.lower()
        assert '--list-users' in result.stdout

    def test_main_help(self):
        """Test main help text."""
        result = run_cli('--help')

        assert 'Usage:' in result.stdout
        assert 'user' in result.stdout.lower()
        assert 'project' in result.stdout.lower()

    def test_invalid_command(self):
        """Test that invalid command returns error."""
        result = run_cli('invalid_command', expect_success=False)

        assert result.returncode != 0

    def test_global_flag_inactive_projects(self):
        """Test --inactive-projects global flag."""
        result = run_cli('--inactive-projects', 'user', 'benkirk', '--list-projects')

        assert 'User Information:' in result.stdout
        # Should show "All projects" instead of "Active projects"
        assert 'All projects for benkirk' in result.stdout or 'projects for benkirk' in result.stdout

    def test_global_flag_inactive_users(self):
        """Test --inactive-users global flag with pattern search."""
        result = run_cli('--inactive-users', 'user', '--search', 'test%', '--limit', '5')

        # Should search inactive users too
        assert result.returncode in [0, 1]  # Either found or not found


# ============================================================================
# Edge Case Tests
# ============================================================================
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_user_pattern_with_underscore(self):
        """Test pattern search with underscore wildcard."""
        result = run_cli('user', '--search', 'ben_', '--limit', '5')

        # Should match 4-letter usernames starting with "ben"
        assert result.returncode in [0, 1]  # Either found or not found

    def test_empty_pattern(self):
        """Test searching with minimal pattern."""
        result = run_cli('user', '--search', 'a', '--limit', '3')

        # Should find users containing 'a'
        assert 'Found' in result.stdout

    def test_limit_zero(self):
        """Test with limit of 0 (edge case)."""
        result = run_cli('user', '--search', 'ben%', '--limit', '0', expect_success=False)

        # Should either error or return no results
        assert result.returncode in [0, 1, 2]

    def test_concurrent_flags(self):
        """Test that mutually exclusive flags are handled."""
        # This should error - can't have both upcoming and recent expirations
        result = run_cli('project', '--upcoming-expirations', '--recent-expirations',
                        expect_success=False)

        assert result.returncode == 1  # Click error exit code is 1 (or custom exit)


# ============================================================================
# Output Format Tests
# ============================================================================
class TestOutputFormats:
    """Test output formatting and display."""

    def test_user_output_has_sections(self):
        """Test that user output has expected sections."""
        result = run_cli('user', 'benkirk')

        assert 'Username' in result.stdout
        assert 'Name' in result.stdout
        assert 'User ID' in result.stdout
        assert 'Status' in result.stdout

    def test_project_output_has_sections(self):
        """Test that project output has expected sections."""
        result = run_cli('project', 'SCSG0001')

        assert 'Title' in result.stdout
        assert 'Code' in result.stdout
        assert 'Status' in result.stdout

    def test_allocation_display(self):
        """Test that allocations are displayed correctly."""
        result = run_cli('project', 'SCSG0001')

        assert 'Allocations & Usage' in result.stdout
        # Should show at least one resource with allocation
        assert 'Remaining' in result.stdout or 'Used' in result.stdout

    def test_user_list_format(self):
        """Test user list formatting in pattern search."""
        result = run_cli('user', '--search', 'ben%', '--limit', '3')

        # Should have numbered list
        assert '1' in result.stdout
        assert 'Username' in result.stdout
        assert 'Name' in result.stdout

    def test_project_list_format(self):
        """Test project list formatting in pattern search."""
        result = run_cli('project', '--search', 'SCSG%')

        # Should have numbered list
        assert '1' in result.stdout
        # Should show project code
        assert 'SCSG' in result.stdout


# ============================================================================
# Allocation Query Tests
# ============================================================================
class TestAllocationQueries:
    """Test allocation query command with various filters and groupings."""

    def test_allocations_help(self):
        """Test allocations command help text."""
        result = run_cli('allocations', '--help')

        assert 'Usage:' in result.stdout
        assert 'allocations' in result.stdout.lower()
        assert '--resource' in result.stdout
        assert '--facility' in result.stdout
        assert '--allocation-type' in result.stdout
        assert '--project' in result.stdout
        assert 'comma-separated' in result.stdout

    def test_allocations_all_grouped(self):
        """Test allocations with no filters (all grouped)."""
        result = run_cli('allocations')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout
        assert 'allocations' in result.stdout
        # Should have column headers
        assert 'Resource' in result.stdout
        assert 'Facility' in result.stdout
        assert 'Type' in result.stdout
        assert 'Project' in result.stdout
        assert 'Count' in result.stdout
        assert 'Total Amount' in result.stdout

    def test_allocations_single_resource(self):
        """Test filtering by a single resource."""
        result = run_cli('allocations', '--resource', 'Derecho', '--total-facilities',
                        '--total-types', '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Derecho' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should show resource column but not facility/type/project
        assert 'Resource' in result.stdout
        assert result.stdout.count('Facility') <= 1  # Only in header or not at all

    def test_allocations_multiple_resources(self):
        """Test filtering by multiple comma-separated resources."""
        result = run_cli('allocations', '--resource', 'Derecho,Casper',
                        '--total-facilities', '--total-types', '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Derecho' in result.stdout or 'Casper' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should group by resource
        assert 'Resource' in result.stdout

    def test_allocations_multiple_types(self):
        """Test filtering by multiple allocation types."""
        result = run_cli('allocations', '--resource', 'Derecho',
                        '--facility', 'UNIV', '--allocation-type', 'Small,Classroom',
                        '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should show type column since we're grouping by it
        assert 'Type' in result.stdout
        # Should have at least one of these types in results
        # (but not requiring both since data may change)
        assert 'Small' in result.stdout or 'Classroom' in result.stdout

    def test_allocations_specific_project(self):
        """Test filtering by a specific project."""
        result = run_cli('allocations', '--project', 'SCSG0001')

        assert 'Allocation Summary' in result.stdout
        assert 'SCSG0001' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should show all grouping dimensions
        assert 'Resource' in result.stdout
        assert 'Count' in result.stdout

    def test_allocations_multiple_projects(self):
        """Test filtering by multiple comma-separated projects."""
        result = run_cli('allocations', '--project', 'SCSG0001,SCSG0002',
                        '--total-resources', '--total-facilities', '--total-types')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should show project column
        assert 'Project' in result.stdout
        # Should have at least one of these projects
        assert 'SCSG0001' in result.stdout or 'SCSG0002' in result.stdout

    def test_allocations_with_verbose(self):
        """Test allocations with --verbose flag (shows averages)."""
        result = run_cli('allocations', '--resource', 'Derecho',
                        '--total-facilities', '--total-types', '--total-projects',
                        '--verbose')

        assert 'Allocation Summary' in result.stdout
        assert 'Avg Amount' in result.stdout
        assert 'Derecho' in result.stdout

    def test_allocations_complex_multi_dimensional(self):
        """Test complex query with multiple dimensions."""
        result = run_cli('allocations', '--resource', 'Derecho,Casper',
                        '--facility', 'UNIV,WNA', '--allocation-type', 'Small,Classroom',
                        '--total-projects', '--verbose')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should show resource, facility, and type columns
        assert 'Resource' in result.stdout
        assert 'Facility' in result.stdout
        assert 'Type' in result.stdout
        assert 'Avg Amount' in result.stdout  # verbose

    def test_allocations_total_by_facility(self):
        """Test totaling across all dimensions except facility."""
        result = run_cli('allocations', '--total-resources', '--total-types',
                        '--total-projects', '--verbose')

        assert 'Allocation Summary' in result.stdout
        assert 'Facility' in result.stdout
        # Should not show other dimension columns
        line_counts = result.stdout.count('Resource')
        assert line_counts <= 1  # Only in potential header if at all
        assert 'Grand Total:' in result.stdout

    def test_allocations_with_active_at(self):
        """Test filtering by historical date with --active-at."""
        result = run_cli('allocations', '--resource', 'Derecho',
                        '--active-at', '2024-01-15', '--total-facilities',
                        '--total-types', '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout
        # Should complete successfully (data may or may not exist for that date)
        assert result.returncode == 0

    def test_allocations_with_inactive(self):
        """Test including inactive allocations with --inactive."""
        result = run_cli('allocations', '--resource', 'Derecho',
                        '--inactive', '--total-facilities', '--total-types',
                        '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Grand Total:' in result.stdout

    def test_allocations_invalid_date(self):
        """Test that invalid date format returns error."""
        result = run_cli('allocations', '--active-at', 'invalid-date',
                        expect_success=False)

        assert result.returncode == 1
        assert 'Invalid date format' in result.stdout

    def test_allocations_no_results(self):
        """Test query that returns no results."""
        result = run_cli('allocations', '--project', 'NONEXISTENT9999',
                        expect_success=True)

        # Should complete successfully but show no results
        assert 'No allocations found' in result.stdout

    def test_allocations_grand_total_format(self):
        """Test that grand total line is properly formatted."""
        result = run_cli('allocations', '--resource', 'Derecho',
                        '--total-facilities', '--total-types', '--total-projects')

        assert 'Grand Total:' in result.stdout
        # Should show count and amount
        assert 'allocations' in result.stdout
        # Should have comma formatting for large numbers
        lines = result.stdout.split('\n')
        total_line = [line for line in lines if 'Grand Total:' in line][0]
        # Check that numbers appear after "Grand Total:"
        assert any(char.isdigit() for char in total_line)

    def test_allocations_resource_with_spaces(self):
        """Test resource names with spaces (e.g., 'Casper GPU')."""
        result = run_cli('allocations', '--resource', 'Casper GPU',
                        '--total-facilities', '--total-types', '--total-projects')

        # Should complete successfully
        assert result.returncode == 0
        assert 'Allocation Summary' in result.stdout

    def test_allocations_output_columns_adapt(self):
        """Test that output columns adapt based on grouping."""
        # Query with TOTAL for resources - should not show Resource column
        result = run_cli('allocations', '--total-resources',
                        '--facility', 'UNIV', '--total-types', '--total-projects')

        assert 'Allocation Summary' in result.stdout
        assert 'Facility' in result.stdout
        # Resource column should not appear in data (only potentially in title)
        lines = result.stdout.split('\n')
        # Count lines that look like data rows (not headers/borders)
        data_lines = [l for l in lines if 'UNIV' in l or 'WNA' in l or 'CISL' in l]
        # These lines should not have "Resource" as a data value
        for line in data_lines:
            parts = line.split()
            if parts:  # Non-empty line
                # First real column should be facility name, not "Resource"
                assert not (parts[0] == 'Resource' and len(parts) > 1)
