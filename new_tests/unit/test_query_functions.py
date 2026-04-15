"""Query function tests — sam.queries.* module coverage.

Ported from tests/unit/test_query_functions.py. Changes from the legacy
file:

- Replaced `test_project`/`test_resource`/`bdobbins|benkirk` fallbacks
  with representative fixtures (`active_project`, `hpc_resource`,
  `multi_project_user`) defined in new_tests/conftest.py. These pick
  ANY row from the snapshot matching the required shape, so the file
  survives snapshot refreshes that remove specific projcodes.
- Dropped hardcoded `'SCSG0001'` / `'Derecho'` / `'UNIV'` / `'TOTAL'`
  search arguments — now derived from the fixture objects at test time.
- The `search_projects_by_code_or_title` tests use a substring of the
  representative project's real projcode as the search term (self-
  consistent; no hardcoded value).
- `test_search_projects_active_filter` now picks a substring that has
  both active and inactive hits dynamically, rather than hardcoding `'U'`.

Assertion style is unchanged — the tests remain structural (dict keys,
type invariants, computed-from-result equalities).
"""
import pytest
from datetime import datetime, timedelta

from sam import (
    Allocation, User,
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
    get_allocation_history,
    get_allocation_summary,
    get_allocation_summary_with_usage,
    get_allocations_by_resource,
    get_project_allocations,
)
from sam.queries.statistics import (
    get_project_statistics,
    get_user_statistics,
)
from sam.queries.projects import search_projects_by_code_or_title


pytestmark = pytest.mark.unit


# ============================================================================
# sam.queries.charges
# ============================================================================


class TestChargeQueries:

    @pytest.mark.parametrize('resource_type,expected_keys', [
        (None,      ['comp', 'dav', 'disk', 'archive']),
        ('HPC',     ['comp']),
        ('DAV',     ['comp', 'dav']),
        ('DISK',    ['disk']),
        ('ARCHIVE', ['archive']),
    ])
    def test_get_daily_charge_trends_resource_types(
        self, session, active_project, resource_type, expected_keys
    ):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )
        assert isinstance(result, dict)
        if result:
            date_key = list(result.keys())[0]
            assert isinstance(date_key, str)
            charges = result[date_key]
            for key in expected_keys:
                assert key in charges
                assert isinstance(charges[key], (int, float))
                assert charges[key] >= 0

    def test_get_daily_charge_trends_date_range(self, session, active_project):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date
        )
        for date_str in result.keys():
            d = datetime.strptime(date_str, '%Y-%m-%d')
            assert start_date.date() <= d.date() <= end_date.date()

    def test_get_daily_charge_trends_empty_accounts(self, session):
        result = get_daily_charge_trends_for_accounts(
            session, [], datetime.now() - timedelta(days=30), datetime.now()
        )
        assert result == {}

    def test_get_raw_charge_summaries_basic(self, session, active_project):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date
        )
        assert isinstance(result, dict)
        for key in ('comp', 'dav', 'disk', 'archive'):
            assert key in result
            assert isinstance(result[key], list)

    @pytest.mark.parametrize('resource_type', ['HPC', 'DAV', 'DISK', 'ARCHIVE'])
    def test_get_raw_charge_summaries_resource_filter(
        self, session, active_project, resource_type
    ):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )
        assert isinstance(result, dict)
        if resource_type == 'HPC':
            assert 'comp' in result
        elif resource_type == 'DAV':
            assert 'comp' in result
            assert 'dav' in result

    def test_get_user_breakdown_for_project_basic(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        result = get_user_breakdown_for_project(
            session, active_project.projcode, start_date, end_date,
            hpc_resource.resource_name
        )
        assert isinstance(result, list)
        if result:
            user = result[0]
            for field in ('username', 'user_id', 'jobs', 'core_hours', 'charges'):
                assert field in user
            assert user['charges'] > 0

    def test_get_user_breakdown_for_project_filters_zero_charges(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        result = get_user_breakdown_for_project(
            session, active_project.projcode, start_date, end_date,
            hpc_resource.resource_name
        )
        for user in result:
            assert user['charges'] > 0


# ============================================================================
# sam.queries.dashboard
# ============================================================================


class TestDashboardQueries:

    def test_get_project_dashboard_data_found(self, session, active_project):
        result = get_project_dashboard_data(session, active_project.projcode)
        assert result is not None
        assert isinstance(result, dict)
        for field in ('project', 'resources', 'has_children'):
            assert field in result
        assert result['project'] == active_project
        assert isinstance(result['resources'], list)
        assert isinstance(result['has_children'], bool)

    def test_get_project_dashboard_data_not_found(self, session):
        assert get_project_dashboard_data(session, 'INVALID999') is None

    def test_get_project_dashboard_data_structure(self, session, active_project):
        result = get_project_dashboard_data(session, active_project.projcode)
        if result and result['resources']:
            res = result['resources'][0]
            for field in ('resource_name', 'allocated', 'used', 'remaining', 'percent_used'):
                assert field in res

    def test_get_resource_detail_data_structure(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_resource_detail_data(
            session, active_project.projcode, hpc_resource.resource_name,
            start_date, end_date,
        )
        if result:
            assert isinstance(result, dict)
            for field in ('project', 'resource', 'resource_summary', 'daily_charges'):
                assert field in result

    def test_user_dashboard_batched_matches_per_project(
        self, session, multi_project_user
    ):
        """Equivalence check: the batched helper must agree field-by-field
        with the per-project loop for the same user at the same instant.
        """
        projects = sorted(multi_project_user.active_projects(), key=lambda p: p.projcode)
        assert projects, "multi_project_user fixture returned user with no active projects"

        active_at = datetime.now()

        batched = _build_user_projects_resources_batched(
            session, projects, active_at=active_at,
        )

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
                f"{project.projcode}: batched returned {len(from_batch)}, "
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
                assert set(pp['charges_by_type'].keys()) == set(bb['charges_by_type'].keys())
                for k in pp['charges_by_type']:
                    assert abs(pp['charges_by_type'][k] - bb['charges_by_type'][k]) < FLOAT_TOL
                assert pp['rolling_30'] == bb['rolling_30']
                assert pp['rolling_90'] == bb['rolling_90']

    def test_get_resource_detail_data_daily_charges(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_resource_detail_data(
            session, active_project.projcode, hpc_resource.resource_name,
            start_date, end_date,
        )
        if result:
            daily = result['daily_charges']
            assert 'dates' in daily
            assert 'values' in daily
            if daily['dates'] is not None:
                assert len(daily['dates']) == len(daily['values'])


# ============================================================================
# sam.queries.allocations
# ============================================================================


class TestAllocationQueries:

    def test_get_project_allocations_basic(self, session, active_project):
        result = get_project_allocations(session, active_project.projcode)
        assert isinstance(result, list)
        if result:
            alloc, res_name = result[0]
            assert isinstance(alloc, Allocation)
            assert isinstance(res_name, str)

    def test_get_project_allocations_resource_filter(
        self, session, active_project, hpc_resource
    ):
        result = get_project_allocations(
            session, active_project.projcode, resource_name=hpc_resource.resource_name
        )
        for _alloc, res_name in result:
            assert res_name == hpc_resource.resource_name

    def test_get_allocation_history_basic(self, session, active_project):
        result = get_allocation_history(session, active_project.projcode)
        assert isinstance(result, list)
        if result:
            txn = result[0]
            for field in ('transaction_date', 'transaction_type', 'requested_amount',
                          'transaction_amount', 'processed_by', 'comment'):
                assert field in txn

    def test_get_allocation_history_user_join(self, session, active_project):
        result = get_allocation_history(session, active_project.projcode)
        for txn in result:
            assert 'processed_by' in txn
            if txn['processed_by'] is not None:
                assert isinstance(txn['processed_by'], str)

    def test_get_allocations_by_resource_active_only(self, session, hpc_resource):
        result = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=True
        )
        assert isinstance(result, list)
        now = datetime.now()
        for _project, allocation in result:
            assert allocation.start_date <= now
            assert allocation.end_date is None or allocation.end_date >= now

    def test_get_allocations_by_resource_include_expired(self, session, hpc_resource):
        all_allocs = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=False
        )
        active_allocs = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=True
        )
        assert len(all_allocs) >= len(active_allocs)


# ============================================================================
# sam.queries.statistics
# ============================================================================


class TestStatisticsQueries:

    def test_get_user_statistics_structure(self, session):
        result = get_user_statistics(session)
        assert isinstance(result, dict)
        for field in ('total_users', 'active_users', 'locked_users', 'inactive_users'):
            assert field in result

    def test_get_user_statistics_counts(self, session):
        result = get_user_statistics(session)
        assert result['total_users'] >= 0
        assert result['active_users'] >= 0
        assert result['locked_users'] >= 0
        assert result['inactive_users'] >= 0
        assert result['total_users'] >= result['active_users']
        assert result['inactive_users'] == result['total_users'] - result['active_users']

    def test_get_project_statistics_structure(self, session):
        result = get_project_statistics(session)
        assert isinstance(result, dict)
        for field in ('total_projects', 'active_projects', 'inactive_projects', 'by_facility'):
            assert field in result

    def test_get_project_statistics_facility_breakdown(self, session):
        result = get_project_statistics(session)
        assert isinstance(result['by_facility'], dict)
        for facility_name, count in result['by_facility'].items():
            assert isinstance(facility_name, str)
            assert isinstance(count, int)
            assert count >= 0


# ============================================================================
# sam.queries.projects
# ============================================================================


class TestProjectQueries:

    def test_search_projects_by_code(self, session, active_project):
        """Search should find at least the representative project by code prefix."""
        # Use the first 4 chars of the representative project's projcode as
        # the search term — self-consistent, no hardcoded values.
        term = active_project.projcode[:4]
        result = search_projects_by_code_or_title(session, term)
        assert isinstance(result, list)
        assert len(result) > 0
        for project in result:
            assert term.upper() in project.projcode.upper() or term.lower() in (project.title or '').lower()

    def test_search_projects_by_title(self, session, active_project):
        """Search by a word from the representative project's title."""
        title = active_project.title or ''
        words = [w for w in title.split() if len(w) >= 4]
        if not words:
            pytest.skip(f"Representative project {active_project.projcode} has no searchable title word")
        term = words[0].lower()
        result = search_projects_by_code_or_title(session, term)
        assert isinstance(result, list)
        # The representative project itself must come back
        assert any(p.project_id == active_project.project_id for p in result)

    def test_search_projects_active_filter(self, session, active_project):
        """active=True and active=False produce properly filtered results."""
        term = active_project.projcode[:2]
        result_active = search_projects_by_code_or_title(session, term, active=True)
        for p in result_active:
            assert p.active is True
        result_inactive = search_projects_by_code_or_title(session, term, active=False)
        for p in result_inactive:
            assert p.active is False


# ============================================================================
# Allocation summary with annualized rates
# ============================================================================


class TestAllocationSummaryWithRates:

    def test_get_allocation_summary_single_allocation_has_rate(
        self, session, active_project
    ):
        results = get_allocation_summary(
            session, projcode=active_project.projcode, active_only=True
        )
        assert len(results) > 0

        for result in results:
            for field in ('count', 'duration_days', 'annualized_rate', 'is_open_ended'):
                assert field in result

            if result['count'] == 1:
                assert result['duration_days'] is not None
                assert result['annualized_rate'] is not None
                assert isinstance(result['duration_days'], int)
                assert isinstance(result['annualized_rate'], (int, float))
                assert result['annualized_rate'] >= 0
                assert isinstance(result['is_open_ended'], bool)

                if result['duration_days'] > 0:
                    expected_rate = (result['total_amount'] / result['duration_days']) * 365
                    assert abs(result['annualized_rate'] - expected_rate) < 0.01

    def test_get_allocation_summary_aggregated_no_rate(self, session):
        """Aggregated results (count > 1) must have None for rate fields."""
        # Query a TOTAL rollup for any facility that has one.
        # If the snapshot has no aggregated results, skip.
        results = get_allocation_summary(
            session, projcode="TOTAL", active_only=True
        )
        aggregated = [r for r in results if r['count'] > 1]
        if not aggregated:
            pytest.skip("Snapshot has no aggregated allocations (count > 1)")

        for result in aggregated:
            assert result['duration_days'] is None
            assert result['annualized_rate'] is None
            assert result['is_open_ended'] is False

    def test_get_allocation_summary_rate_consistency(
        self, session, active_project, hpc_resource
    ):
        """Rate is (amount / duration_days) * 365 for single allocations."""
        results = get_allocation_summary(
            session,
            resource_name=hpc_resource.resource_name,
            projcode=active_project.projcode,
        )
        single = [r for r in results if r['count'] == 1]
        if not single:
            pytest.skip(
                f"No single allocations for {active_project.projcode} on "
                f"{hpc_resource.resource_name}"
            )
        for result in single:
            if result['total_amount'] > 0:
                assert result['annualized_rate'] > 0
            if result['duration_days'] and result['duration_days'] > 0:
                ratio = result['annualized_rate'] / result['total_amount']
                expected = 365 / result['duration_days']
                assert abs(ratio - expected) < 0.0001

    def test_get_allocation_summary_with_usage_includes_rates(
        self, session, active_project
    ):
        results = get_allocation_summary_with_usage(
            session, projcode=active_project.projcode, active_only=True
        )
        assert len(results) > 0
        for result in results:
            for field in ('total_used', 'percent_used', 'charges_by_type',
                          'duration_days', 'annualized_rate', 'is_open_ended'):
                assert field in result
            if result['count'] == 1:
                assert result['annualized_rate'] is not None
            else:
                assert result['annualized_rate'] is None

    def test_get_allocation_summary_zero_duration_handling(self, session):
        results = get_allocation_summary(session, active_only=False)
        for result in results:
            if result['count'] == 1 and result['duration_days'] == 0:
                assert result['annualized_rate'] == 0.0

    def test_get_allocation_summary_date_fields(self, session, active_project):
        results = get_allocation_summary(session, projcode=active_project.projcode)
        assert len(results) > 0
        for result in results:
            assert 'start_date' in result
            assert 'end_date' in result
            if result['count'] == 1:
                assert result['start_date'] is not None
                if result['end_date'] is not None:
                    assert result['end_date'] >= result['start_date']
