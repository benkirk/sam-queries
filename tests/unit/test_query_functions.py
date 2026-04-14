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
    get_user_breakdown_for_project,
)
from sam.queries.dashboard import (
    _build_project_resources_data,
    _build_user_projects_resources_batched,
    get_project_dashboard_data,
    get_resource_detail_data,
    get_user_dashboard_data,
)
from sam.queries.allocations import (
    get_project_allocations,
    get_allocation_history,
    get_allocations_by_resource,
    get_allocation_summary,
    get_allocation_summary_with_usage,
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

    def test_user_dashboard_batched_matches_per_project(self, session):
        """
        The new batched helper _build_user_projects_resources_batched must
        return resource dicts equivalent to looping the per-project
        _build_project_resources_data over the same projects.

        Pin a fixed datetime for both paths so any "time has passed" between
        the two calls cannot drift the elapsed_pct / days_until_expiration
        fields. Compare every numeric/scalar field; relax only on float
        equality (sums of floats can differ in the last bit between paths).
        """
        # bdobbins is the canonical multi-project profile target. Falls back
        # to benkirk if bdobbins isn't in the local DB.
        sam_user = (User.get_by_username(session, 'bdobbins')
                    or User.get_by_username(session, 'benkirk'))
        if sam_user is None:
            pytest.skip("Neither bdobbins nor benkirk found in test DB")

        projects = sorted(sam_user.active_projects(), key=lambda p: p.projcode)
        if not projects:
            pytest.skip(f"User {sam_user.username} has no active projects")

        # Pin time to make both paths see identical "now"
        active_at = datetime.now()

        batched = _build_user_projects_resources_batched(
            session, projects, active_at=active_at,
        )

        # Build the per-project equivalent and compare project by project.
        SCALAR_FIELDS = (
            'resource_name', 'allocation_id', 'parent_allocation_id',
            'is_inheriting', 'account_id', 'status', 'start_date', 'end_date',
            'days_until_expiration', 'date_group_key', 'bar_state',
            'resource_type',
        )
        FLOAT_FIELDS = (
            'allocated', 'used', 'remaining', 'percent_used',
            'adjustments', 'elapsed_pct',
        )
        FLOAT_TOL = 1e-6

        for project in projects:
            per_project = sorted(
                _build_project_resources_data(project, active_at=active_at),
                key=lambda r: r['resource_name'],
            )
            from_batch = batched.get(project.project_id, [])

            assert len(per_project) == len(from_batch), (
                f"{project.projcode}: batched returned {len(from_batch)} resources, "
                f"per-project returned {len(per_project)}"
            )

            for pp, bb in zip(per_project, from_batch):
                ctx = f"{project.projcode}/{pp['resource_name']}"
                for f in SCALAR_FIELDS:
                    assert pp[f] == bb[f], f"{ctx}: {f} differs ({pp[f]!r} vs {bb[f]!r})"
                for f in FLOAT_FIELDS:
                    assert abs(float(pp[f]) - float(bb[f])) < FLOAT_TOL, (
                        f"{ctx}: {f} differs ({pp[f]} vs {bb[f]})"
                    )
                # charges_by_type: dict of floats — same keys, same values
                assert set(pp['charges_by_type'].keys()) == set(bb['charges_by_type'].keys()), (
                    f"{ctx}: charges_by_type key set differs"
                )
                for k in pp['charges_by_type']:
                    assert abs(pp['charges_by_type'][k] - bb['charges_by_type'][k]) < FLOAT_TOL, (
                        f"{ctx}: charges_by_type[{k}] differs"
                    )
                # rolling_30 / rolling_90 may legitimately be None or a dict;
                # only meaningful for projects with a threshold. Equality OK
                # because both code paths read the same source.
                assert pp['rolling_30'] == bb['rolling_30'], f"{ctx}: rolling_30 differs"
                assert pp['rolling_90'] == bb['rolling_90'], f"{ctx}: rolling_90 differs"

        print(f"✅ Batched and per-project paths agree across "
              f"{len(projects)} projects for {sam_user.username}")

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


class TestAllocationSummaryWithRates:
    """Test allocation summary functions with annualized rate calculations."""

    def test_get_allocation_summary_single_allocation_has_rate(self, session):
        """Test that single allocations (count=1) have annualized rate calculated."""
        # Query for a specific project which should return single allocations
        results = get_allocation_summary(
            session,
            projcode="SCSG0001",
            active_only=True
        )

        assert len(results) > 0, "Should find allocations for SCSG0001"

        for result in results:
            assert 'count' in result
            assert 'duration_days' in result
            assert 'annualized_rate' in result
            assert 'is_open_ended' in result

            # Single allocations should have rate calculated
            if result['count'] == 1:
                assert result['duration_days'] is not None, "Single allocation should have duration"
                assert result['annualized_rate'] is not None, "Single allocation should have annualized rate"
                assert isinstance(result['duration_days'], int), "Duration should be integer"
                assert isinstance(result['annualized_rate'], (int, float)), "Rate should be numeric"
                assert result['annualized_rate'] >= 0, "Rate should be non-negative"
                assert isinstance(result['is_open_ended'], bool), "is_open_ended should be boolean"

                # Verify calculation: rate = (amount / days) * 365
                if result['duration_days'] > 0:
                    expected_rate = (result['total_amount'] / result['duration_days']) * 365
                    assert abs(result['annualized_rate'] - expected_rate) < 0.01, "Rate calculation should be correct"

    def test_get_allocation_summary_aggregated_no_rate(self, session):
        """Test that aggregated allocations (count > 1) have None for rate fields."""
        # Query that aggregates multiple allocations
        results = get_allocation_summary(
            session,
            facility_name="UNIV",
            projcode="TOTAL",  # Aggregate across all projects
            active_only=True
        )

        assert len(results) > 0, "Should find aggregated allocations"

        # Find at least one aggregated result
        aggregated = [r for r in results if r['count'] > 1]
        assert len(aggregated) > 0, "Should have at least one aggregated result"

        for result in aggregated:
            # Aggregated results should have None for rate fields
            assert result['duration_days'] is None, "Aggregated result should have None duration"
            assert result['annualized_rate'] is None, "Aggregated result should have None rate"
            assert result['is_open_ended'] == False, "Aggregated result should have False for is_open_ended"

    def test_get_allocation_summary_rate_consistency(self, session):
        """Test that annualized rates are consistent across different queries."""
        # Get summary for specific resource and project
        results_detailed = get_allocation_summary(
            session,
            resource_name="Derecho",
            projcode="SCSG0001"
        )

        # Should have at least one result with rate
        single_allocs = [r for r in results_detailed if r['count'] == 1]
        assert len(single_allocs) > 0, "Should have single allocations"

        for result in single_allocs:
            # Rates should be positive for positive amounts
            if result['total_amount'] > 0:
                assert result['annualized_rate'] > 0, "Positive amount should have positive rate"

            # Rate should scale with amount (for same duration)
            if result['duration_days'] and result['duration_days'] > 0:
                ratio = result['annualized_rate'] / result['total_amount']
                # Ratio should be 365 / duration_days
                expected_ratio = 365 / result['duration_days']
                assert abs(ratio - expected_ratio) < 0.0001, "Rate scaling should be correct"

    def test_get_allocation_summary_with_usage_includes_rates(self, session):
        """Test that get_allocation_summary_with_usage includes annualized rates."""
        # Query for a specific project
        results = get_allocation_summary_with_usage(
            session,
            projcode="SCSG0001",
            active_only=True
        )

        assert len(results) > 0, "Should find allocations with usage"

        for result in results:
            # Should have usage fields
            assert 'total_used' in result
            assert 'percent_used' in result
            assert 'charges_by_type' in result

            # Should also have rate fields
            assert 'duration_days' in result
            assert 'annualized_rate' in result
            assert 'is_open_ended' in result

            # Check consistency
            if result['count'] == 1:
                assert result['annualized_rate'] is not None
            else:
                assert result['annualized_rate'] is None

    def test_get_allocation_summary_zero_duration_handling(self, session):
        """Test handling of allocations with zero or very short durations."""
        # Query all allocations
        results = get_allocation_summary(
            session,
            active_only=False
        )

        # Check that zero-duration allocations are handled gracefully
        for result in results:
            if result['count'] == 1 and result['duration_days'] == 0:
                # Should have zero rate, not error
                assert result['annualized_rate'] == 0.0, "Zero duration should have zero rate"

    def test_get_allocation_summary_date_fields(self, session):
        """Test that date fields are present and valid in results."""
        results = get_allocation_summary(
            session,
            projcode="SCSG0001"
        )

        assert len(results) > 0

        for result in results:
            assert 'start_date' in result
            assert 'end_date' in result

            # For single allocations, should have valid dates
            if result['count'] == 1:
                assert result['start_date'] is not None, "Should have start date"
                # end_date can be None (open-ended) or datetime
                if result['end_date'] is not None:
                    assert result['end_date'] >= result['start_date'], "End should be after start"
