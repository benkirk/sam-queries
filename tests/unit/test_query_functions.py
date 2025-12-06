"""
Query Function Tests

Tests for sam/queries/ module functions including charge aggregations,
dashboard queries, allocation lookups, and statistics.

This test suite focuses on high-value functions that were previously
untested to increase coverage from 75% to 80%+.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from sam import (
    User, Project, Account, Allocation, Resource,
    CompChargeSummary, DavChargeSummary, DiskChargeSummary, ArchiveChargeSummary
)
from sam.queries.charges import (
    get_daily_charge_trends_for_accounts,
    get_raw_charge_summaries_for_accounts,
    get_project_usage_summary,
    get_daily_usage_trend,
    get_queue_usage_breakdown,
    get_user_usage_on_project,
    get_user_breakdown_for_project,
)
from sam.queries.dashboard import (
    get_project_dashboard_data,
    get_resource_detail_data,
)
from sam.queries.allocations import (
    get_project_allocations,
    get_allocation_history,
    get_allocations_by_resource,
)
from sam.queries.statistics import (
    get_user_statistics,
    get_project_statistics,
)
from sam.queries.projects import (
    search_projects_by_code_or_title,
)


# ============================================================================
# Test Classes (Organized by Module)
# ============================================================================

class TestChargeQueries:
    """Test functions from sam.queries.charges module."""

    # Tests for get_daily_charge_trends_for_accounts()

    @pytest.mark.parametrize('resource_type,expected_keys', [
        (None, ['comp', 'dav', 'disk', 'archive']),
        ('HPC', ['comp']),
        ('DAV', ['comp', 'dav']),
        ('DISK', ['disk']),
        ('ARCHIVE', ['archive']),
    ])
    def test_get_daily_charge_trends_resource_types(self, session, test_project, resource_type, expected_keys):
        """Test daily charge trends with different resource type filters."""
        # Get account IDs for test project
        account_ids = [acc.account_id for acc in test_project.accounts]

        # Date range: last 30 days
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        # Call function
        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )

        # Verify structure
        assert isinstance(result, dict), "Should return dict"

        if result:  # May be empty if no charges in range
            # Check date format
            date_key = list(result.keys())[0]
            assert isinstance(date_key, str), "Keys should be date strings"

            # Check charge types
            charges = result[date_key]
            for key in expected_keys:
                assert key in charges, f"Should have {key} charges"
                assert isinstance(charges[key], (int, float)), f"{key} should be numeric"
                assert charges[key] >= 0, f"{key} should be non-negative"

    def test_get_daily_charge_trends_date_range(self, session, test_project):
        """Test daily charge trends respects date range."""
        account_ids = [acc.account_id for acc in test_project.accounts]

        # Narrow date range: last 7 days only
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date
        )

        # All dates should be within range
        for date_str in result.keys():
            date = datetime.strptime(date_str, '%Y-%m-%d')
            assert start_date.date() <= date.date() <= end_date.date()

    def test_get_daily_charge_trends_empty_accounts(self, session):
        """Test daily charge trends with empty account list."""
        result = get_daily_charge_trends_for_accounts(
            session, [], datetime.now() - timedelta(days=30), datetime.now()
        )
        assert result == {}, "Empty account list should return empty dict"

    # Tests for get_raw_charge_summaries_for_accounts()

    def test_get_raw_charge_summaries_basic(self, session, test_project):
        """Test raw charge summaries returns correct structure."""
        account_ids = [acc.account_id for acc in test_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date
        )

        # Verify structure
        assert isinstance(result, dict)
        assert 'comp' in result
        assert 'dav' in result
        assert 'disk' in result
        assert 'archive' in result

        # Each should be a list
        for key, value in result.items():
            assert isinstance(value, list), f"{key} should be list"

    @pytest.mark.parametrize('resource_type', ['HPC', 'DAV', 'DISK', 'ARCHIVE'])
    def test_get_raw_charge_summaries_resource_filter(self, session, test_project, resource_type):
        """Test raw charge summaries with resource type filters."""
        account_ids = [acc.account_id for acc in test_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )

        assert isinstance(result, dict)
        # Verify filtering logic
        if resource_type == 'HPC':
            # Should have comp but not dav
            assert 'comp' in result
        elif resource_type == 'DAV':
            # Should have both comp and dav
            assert 'comp' in result
            assert 'dav' in result

    # Tests for get_project_usage_summary()

    def test_get_project_usage_summary_with_charges(self, session, test_project, test_resource):
        """Test project usage summary for project with charges."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)  # Wider range

        result = get_project_usage_summary(
            session,
            test_project.projcode,
            start_date,
            end_date,
            test_resource.resource_name
        )

        # Verify structure
        assert isinstance(result, dict)
        assert 'total_jobs' in result
        assert 'total_core_hours' in result
        assert 'total_charges' in result

        # Verify types (MySQL aggregates can return Decimal)
        assert isinstance(result['total_jobs'], (int, Decimal))
        assert isinstance(result['total_core_hours'], (int, float, Decimal))
        assert isinstance(result['total_charges'], (int, float, Decimal))

        # Non-negative values
        assert result['total_jobs'] >= 0
        assert result['total_core_hours'] >= 0.0
        assert result['total_charges'] >= 0.0

    def test_get_project_usage_summary_no_charges(self, session):
        """Test project usage summary handles null charges gracefully."""
        # Use impossible date range
        start_date = datetime(1900, 1, 1)
        end_date = datetime(1900, 1, 2)

        result = get_project_usage_summary(
            session,
            'SCSG0001',  # Known project
            start_date,
            end_date,
            'Derecho'
        )

        # Should return zeros for null aggregations
        assert result['total_jobs'] == 0
        assert result['total_core_hours'] == 0.0
        assert result['total_charges'] == 0.0

    # Tests for get_daily_usage_trend()

    def test_get_daily_usage_trend_grouping(self, session, test_project):
        """Test daily usage trend groups by date correctly."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_daily_usage_trend(
            session, test_project.projcode, start_date, end_date
        )

        assert isinstance(result, list)

        if result:  # May be empty
            # Check structure of first item
            day = result[0]
            assert 'date' in day
            assert 'jobs' in day
            assert 'core_hours' in day
            assert 'charges' in day

            # Check types (MySQL aggregates can return Decimal)
            assert isinstance(day['jobs'], (int, Decimal))
            assert isinstance(day['core_hours'], (int, float, Decimal))
            assert isinstance(day['charges'], (int, float, Decimal))

            # Check ordering (should be ascending by date)
            if len(result) > 1:
                assert result[0]['date'] <= result[1]['date']

    def test_get_daily_usage_trend_resource_filter(self, session, test_project, test_resource):
        """Test daily usage trend with resource filter."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_daily_usage_trend(
            session, test_project.projcode, start_date, end_date,
            resource=test_resource.resource_name
        )

        assert isinstance(result, list)
        # Resource filter should work without error

    # Tests for get_queue_usage_breakdown()

    def test_get_queue_usage_breakdown_basic(self, session, test_project):
        """Test queue usage breakdown returns sorted results."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_queue_usage_breakdown(
            session, test_project.projcode, start_date, end_date
        )

        assert isinstance(result, list)

        if result:
            # Check structure
            queue = result[0]
            assert 'queue' in queue
            assert 'machine' in queue
            assert 'jobs' in queue
            assert 'core_hours' in queue
            assert 'charges' in queue

            # Check types (MySQL aggregates can return Decimal)
            assert isinstance(queue['jobs'], (int, Decimal))
            assert isinstance(queue['core_hours'], (int, float, Decimal))
            assert isinstance(queue['charges'], (int, float, Decimal))

            # Check ordering (descending by charges)
            if len(result) > 1:
                assert result[0]['charges'] >= result[1]['charges']

    def test_get_queue_usage_breakdown_machine_filter(self, session, test_project):
        """Test queue usage breakdown with machine filter."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_queue_usage_breakdown(
            session, test_project.projcode, start_date, end_date,
            machine='Derecho'
        )

        assert isinstance(result, list)
        # All results should be for specified machine
        for queue in result:
            assert queue['machine'] == 'Derecho'

    # Tests for get_user_usage_on_project()

    def test_get_user_usage_on_project_limit(self, session, test_project):
        """Test user usage respects limit parameter."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_user_usage_on_project(
            session, test_project.projcode, start_date, end_date, limit=5
        )

        assert isinstance(result, list)
        assert len(result) <= 5, "Should respect limit parameter"

    def test_get_user_usage_on_project_ordering(self, session, test_project):
        """Test user usage orders by charges descending."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_user_usage_on_project(
            session, test_project.projcode, start_date, end_date, limit=10
        )

        if len(result) > 1:
            # Check descending order by charges
            assert result[0]['charges'] >= result[1]['charges']

            # Check structure
            user = result[0]
            assert 'username' in user
            assert 'user_id' in user
            assert 'jobs' in user
            assert 'core_hours' in user
            assert 'charges' in user

    # Tests for get_user_breakdown_for_project()

    def test_get_user_breakdown_for_project_basic(self, session, test_project, test_resource):
        """Test user breakdown returns per-user stats."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_user_breakdown_for_project(
            session, test_project.projcode, start_date, end_date,
            test_resource.resource_name
        )

        assert isinstance(result, list)

        if result:
            user = result[0]
            assert 'username' in user
            assert 'user_id' in user
            assert 'jobs' in user
            assert 'core_hours' in user
            assert 'charges' in user

            # All users should have charges > 0 (HAVING clause)
            assert user['charges'] > 0

    def test_get_user_breakdown_for_project_filters_zero_charges(self, session, test_project, test_resource):
        """Test user breakdown excludes users with zero charges."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        result = get_user_breakdown_for_project(
            session, test_project.projcode, start_date, end_date,
            test_resource.resource_name
        )

        # Every user in results should have charges > 0
        for user in result:
            assert user['charges'] > 0, "Should exclude zero-charge users"


class TestDashboardQueries:
    """Test functions from sam.queries.dashboard module."""

    def test_get_project_dashboard_data_found(self, session, test_project):
        """Test project dashboard data for existing project."""
        result = get_project_dashboard_data(session, test_project.projcode)

        assert result is not None, "Should find existing project"
        assert isinstance(result, dict)
        assert 'project' in result
        assert 'resources' in result
        assert 'has_children' in result

        assert result['project'] == test_project
        assert isinstance(result['resources'], list)
        assert isinstance(result['has_children'], bool)

    def test_get_project_dashboard_data_not_found(self, session):
        """Test project dashboard data returns None for invalid project."""
        result = get_project_dashboard_data(session, 'INVALID999')
        assert result is None, "Should return None for nonexistent project"

    def test_get_project_dashboard_data_structure(self, session, test_project):
        """Test project dashboard data returns correct dict structure."""
        result = get_project_dashboard_data(session, test_project.projcode)

        if result and result['resources']:
            resource = result['resources'][0]
            # Check resource dict keys
            assert 'resource_name' in resource
            assert 'allocated' in resource
            assert 'used' in resource
            assert 'remaining' in resource
            assert 'percent_used' in resource

    @pytest.mark.parametrize('resource_name', ['Derecho', 'Campaign'])
    def test_get_resource_detail_data_by_type(self, session, test_project, resource_name):
        """Test resource detail data for different resource types."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_resource_detail_data(
            session, test_project.projcode, resource_name, start_date, end_date
        )

        if result:  # May be None if project doesn't have this resource
            assert isinstance(result, dict)
            assert 'project' in result
            assert 'resource' in result
            assert 'resource_summary' in result
            assert 'daily_charges' in result

    def test_get_resource_detail_data_daily_charges(self, session, test_project):
        """Test resource detail data returns daily charge arrays."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        result = get_resource_detail_data(
            session, test_project.projcode, 'Derecho', start_date, end_date
        )

        if result:
            daily = result['daily_charges']
            assert 'dates' in daily
            assert 'values' in daily

            # Should be parallel arrays (or both None)
            if daily['dates'] is not None:
                assert len(daily['dates']) == len(daily['values'])


class TestAllocationQueries:
    """Test functions from sam.queries.allocations module."""

    def test_get_project_allocations_basic(self, session, test_project):
        """Test get project allocations returns tuple list."""
        result = get_project_allocations(session, test_project.projcode)

        assert isinstance(result, list)

        if result:
            # Each item should be (Allocation, resource_name) tuple
            alloc, res_name = result[0]
            assert isinstance(alloc, Allocation)
            assert isinstance(res_name, str)

    def test_get_project_allocations_resource_filter(self, session, test_project, test_resource):
        """Test get project allocations with resource filter."""
        result = get_project_allocations(
            session, test_project.projcode, resource_name=test_resource.resource_name
        )

        # All results should be for specified resource
        for alloc, res_name in result:
            assert res_name == test_resource.resource_name

    def test_get_allocation_history_basic(self, session, test_project):
        """Test allocation history returns transaction dicts."""
        result = get_allocation_history(session, test_project.projcode)

        assert isinstance(result, list)

        if result:
            txn = result[0]
            # Check dict structure
            assert 'transaction_date' in txn
            assert 'transaction_type' in txn
            assert 'requested_amount' in txn
            assert 'transaction_amount' in txn
            assert 'processed_by' in txn  # May be None
            assert 'comment' in txn

    def test_get_allocation_history_user_join(self, session, test_project):
        """Test allocation history includes user info."""
        result = get_allocation_history(session, test_project.projcode)

        # processed_by may be None (outerjoin), but should be present
        for txn in result:
            assert 'processed_by' in txn
            # If not None, should be a string (full_name)
            if txn['processed_by'] is not None:
                assert isinstance(txn['processed_by'], str)

    def test_get_allocations_by_resource_active_only(self, session, test_resource):
        """Test allocations by resource with active_only=True."""
        result = get_allocations_by_resource(
            session, test_resource.resource_name, active_only=True
        )

        assert isinstance(result, list)

        now = datetime.now()
        for project, allocation in result:
            # Should be currently active
            assert allocation.start_date <= now
            assert allocation.end_date is None or allocation.end_date >= now

    def test_get_allocations_by_resource_include_expired(self, session, test_resource):
        """Test allocations by resource with active_only=False."""
        result_all = get_allocations_by_resource(
            session, test_resource.resource_name, active_only=False
        )
        result_active = get_allocations_by_resource(
            session, test_resource.resource_name, active_only=True
        )

        # Should return more (or equal) results when including expired
        assert len(result_all) >= len(result_active)


class TestStatisticsQueries:
    """Test functions from sam.queries.statistics module."""

    def test_get_user_statistics_structure(self, session):
        """Test user statistics returns correct dict structure."""
        result = get_user_statistics(session)

        assert isinstance(result, dict)
        assert 'total_users' in result
        assert 'active_users' in result
        assert 'locked_users' in result
        assert 'inactive_users' in result

    def test_get_user_statistics_counts(self, session):
        """Test user statistics returns valid counts."""
        result = get_user_statistics(session)

        # All counts should be non-negative integers
        assert result['total_users'] >= 0
        assert result['active_users'] >= 0
        assert result['locked_users'] >= 0
        assert result['inactive_users'] >= 0

        # Logical consistency
        assert result['total_users'] >= result['active_users']
        assert result['inactive_users'] == result['total_users'] - result['active_users']

    def test_get_project_statistics_structure(self, session):
        """Test project statistics returns correct dict structure."""
        result = get_project_statistics(session)

        assert isinstance(result, dict)
        assert 'total_projects' in result
        assert 'active_projects' in result
        assert 'inactive_projects' in result
        assert 'by_facility' in result

    def test_get_project_statistics_facility_breakdown(self, session):
        """Test project statistics includes facility breakdown."""
        result = get_project_statistics(session)

        # by_facility should be a dict
        assert isinstance(result['by_facility'], dict)

        # Each facility should have a count
        for facility_name, count in result['by_facility'].items():
            assert isinstance(facility_name, str)
            assert isinstance(count, int)
            assert count >= 0


class TestProjectQueries:
    """Test functions from sam.queries.projects module."""

    def test_search_projects_by_code(self, session):
        """Test searching projects by code (case-insensitive)."""
        # Search for known project
        result = search_projects_by_code_or_title(session, 'SCSG')

        assert isinstance(result, list)
        assert len(result) > 0, "Should find SCSG projects"

        # All results should match pattern
        for project in result:
            assert 'SCSG' in project.projcode.upper()

    def test_search_projects_by_title(self, session):
        """Test searching projects by title (case-insensitive)."""
        # Search for common word
        result = search_projects_by_code_or_title(session, 'system')

        assert isinstance(result, list)

        # Results should match in code or title
        for project in result:
            search_term = 'system'
            assert (search_term in project.projcode.lower() or
                    search_term in project.title.lower())

    def test_search_projects_active_filter(self, session):
        """Test searching projects with active filter."""
        # Search with active=True
        result_active = search_projects_by_code_or_title(session, 'U', active=True)

        # All results should be active
        for project in result_active:
            assert project.active == True

        # Search with active=False
        result_inactive = search_projects_by_code_or_title(session, 'U', active=False)

        # All results should be inactive
        for project in result_inactive:
            assert project.active == False
