"""
Allocations Dashboard Performance Tests

Tests for the performance optimization changes:
- Dashboard route responses (happy path, sad path, edge cases)
- Flask-Caching behavior
- Batched facility overview queries
- Matplotlib lru_cache behavior
- Extended allocation query edge cases
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from sam.queries.allocations import get_allocation_summary


# ============================================================================
# Blueprint Helper Functions
# ============================================================================

class TestGroupByResourceFacility:
    """Tests for group_by_resource_facility()."""

    def test_groups_by_resource_and_facility(self):
        from webapp.dashboards.allocations.blueprint import group_by_resource_facility

        data = [
            {'resource': 'Derecho', 'facility': 'UNIV', 'allocation_type': 'NSC', 'total_amount': 100},
            {'resource': 'Derecho', 'facility': 'WNA', 'allocation_type': 'Small', 'total_amount': 50},
            {'resource': 'Casper', 'facility': 'UNIV', 'allocation_type': 'NSC', 'total_amount': 200},
        ]
        result = group_by_resource_facility(data)

        assert 'Derecho' in result
        assert 'Casper' in result
        assert 'UNIV' in result['Derecho']
        assert 'WNA' in result['Derecho']
        assert len(result['Derecho']['UNIV']) == 1
        assert len(result['Casper']['UNIV']) == 1

    def test_empty_input(self):
        from webapp.dashboards.allocations.blueprint import group_by_resource_facility

        result = group_by_resource_facility([])
        assert result == {}

    def test_single_item(self):
        from webapp.dashboards.allocations.blueprint import group_by_resource_facility

        data = [{'resource': 'Derecho', 'facility': 'UNIV', 'allocation_type': 'Small', 'total_amount': 10}]
        result = group_by_resource_facility(data)
        assert len(result) == 1
        assert len(result['Derecho']['UNIV']) == 1

    def test_multiple_types_same_facility(self):
        from webapp.dashboards.allocations.blueprint import group_by_resource_facility

        data = [
            {'resource': 'Derecho', 'facility': 'UNIV', 'allocation_type': 'NSC', 'total_amount': 100},
            {'resource': 'Derecho', 'facility': 'UNIV', 'allocation_type': 'Small', 'total_amount': 50},
        ]
        result = group_by_resource_facility(data)
        assert len(result['Derecho']['UNIV']) == 2


class TestGetAllFacilityOverviews:
    """Tests for batched get_all_facility_overviews()."""

    def test_returns_dict_keyed_by_resource(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, _ = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        assert isinstance(result, dict)
        if result:
            assert 'Derecho' in result

    def test_multiple_resources_single_query(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, _ = get_all_facility_overviews(session, ['Derecho', 'Casper'], datetime.now())
        assert isinstance(result, dict)

    def test_empty_resource_list(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, type_rates = get_all_facility_overviews(session, [], datetime.now())
        assert result == {}
        assert type_rates == {}

    def test_nonexistent_resource(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, type_rates = get_all_facility_overviews(session, ['NonexistentResource123'], datetime.now())
        assert result == {}
        assert type_rates == {}

    def test_facility_overview_has_required_keys(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, _ = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho'):
            for item in result['Derecho']:
                assert 'facility' in item
                assert 'total_amount' in item
                assert 'annualized_rate' in item
                assert 'count' in item
                assert 'percent' in item

    def test_percentages_sum_to_100(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, _ = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho') and len(result['Derecho']) > 1:
            total_pct = sum(f['percent'] for f in result['Derecho'])
            assert abs(total_pct - 100.0) < 0.1

    def test_sorted_by_annualized_rate_desc(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result, _ = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho') and len(result['Derecho']) > 1:
            rates = [f['annualized_rate'] for f in result['Derecho']]
            assert rates == sorted(rates, reverse=True)

    def test_historical_date(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        past = datetime(2024, 1, 1)
        result, _ = get_all_facility_overviews(session, ['Derecho'], past)
        assert isinstance(result, dict)

    def test_future_date_returns_empty_or_less(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        future = datetime.now() + timedelta(days=3650)
        result, _ = get_all_facility_overviews(session, ['Derecho'], future)
        assert isinstance(result, dict)

    def test_type_rates_keyed_by_resource_facility_type(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        _, type_rates = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        assert isinstance(type_rates, dict)
        for key, rate in type_rates.items():
            assert len(key) == 3          # (resource, facility, allocation_type)
            assert isinstance(rate, float)
            assert rate >= 0.0

    def test_type_rates_sum_equals_facility_rate(self, session):
        """Sum of per-type annualized rates within a facility must equal the facility rate."""
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        overviews, type_rates = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if not overviews.get('Derecho'):
            return
        for fac_overview in overviews['Derecho']:
            facility = fac_overview['facility']
            facility_rate = fac_overview['annualized_rate']
            type_sum = sum(
                r for (res, fac, _), r in type_rates.items()
                if res == 'Derecho' and fac == facility
            )
            assert abs(type_sum - facility_rate) < 0.01


class TestGetResourceTypes:
    """Tests for get_resource_types()."""

    def test_returns_dict(self, session):
        from webapp.dashboards.allocations.blueprint import get_resource_types

        result = get_resource_types(session)
        assert isinstance(result, dict)
        assert 'Derecho' in result

    def test_known_resource_types(self, session):
        from webapp.dashboards.allocations.blueprint import get_resource_types

        result = get_resource_types(session)
        assert result.get('Derecho') in ('HPC', 'DAV', 'DISK', 'ARCHIVE')


# ============================================================================
# Dashboard Routes
# ============================================================================

class TestAllocationsIndexRoute:
    """Tests for GET /allocations/."""

    def test_index_returns_200(self, auth_client):
        response = auth_client.get('/allocations/')
        assert response.status_code == 200

    def test_index_contains_dashboard_title(self, auth_client):
        response = auth_client.get('/allocations/')
        assert b'Allocations Dashboard' in response.data

    def test_index_with_date_filter(self, auth_client):
        response = auth_client.get('/allocations/?active_at=2025-01-15')
        assert response.status_code == 200

    def test_index_with_invalid_date(self, auth_client):
        response = auth_client.get('/allocations/?active_at=not-a-date')
        assert response.status_code == 200
        assert b'Allocations Dashboard' in response.data

    def test_index_with_resource_filter(self, auth_client):
        response = auth_client.get('/allocations/?resources=Derecho')
        assert response.status_code == 200

    def test_index_with_multiple_resources(self, auth_client):
        response = auth_client.get('/allocations/?resources=Derecho&resources=Casper')
        assert response.status_code == 200

    def test_index_contains_svg_chart(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode().lower()
        assert '<svg' in html or 'no active allocations' in html

    def test_index_no_chartjs_canvas(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        assert 'facility-pie-chart' not in html
        assert 'data-labels' not in html

    def test_index_unauthenticated_redirects(self, client):
        response = client.get('/allocations/')
        assert response.status_code in (302, 401)


class TestProjectsFragmentRoute:
    """Tests for GET /allocations/projects (AJAX fragment)."""

    def test_missing_params_returns_error(self, auth_client):
        response = auth_client.get('/allocations/projects')
        assert b'Missing required parameters' in response.data

    def test_missing_facility_returns_error(self, auth_client):
        response = auth_client.get('/allocations/projects?resource=Derecho&allocation_type=Small')
        assert b'Missing required parameters' in response.data

    def test_missing_allocation_type_returns_error(self, auth_client):
        response = auth_client.get('/allocations/projects?resource=Derecho&facility=UNIV')
        assert b'Missing required parameters' in response.data

    def test_valid_params_returns_200(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV&allocation_type=Small'
        )
        assert response.status_code == 200

    def test_nonexistent_combo_returns_no_projects(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=FakeResource&facility=FAKE&allocation_type=FAKE'
        )
        assert b'No active projects found' in response.data

    def test_invalid_date_returns_error(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV&allocation_type=Small&active_at=bad'
        )
        assert b'Invalid date format' in response.data

    def test_with_date_param(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV&allocation_type=Small&active_at=2025-06-01'
        )
        assert response.status_code == 200


class TestUsageModalRoute:
    """Tests for GET /allocations/usage/<projcode>/<resource>."""

    def test_known_project_returns_200(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/Derecho')
        assert response.status_code == 200

    def test_nonexistent_project_returns_error(self, auth_client):
        response = auth_client.get('/allocations/usage/FAKE9999/Derecho')
        # Route is now guarded by @require_project_access, which returns a
        # 404 JSON body via get_project_or_404 on unknown projcodes
        # (replaces the prior hand-rolled inline early return).
        assert response.status_code == 404
        assert b'not found' in response.data
        assert b'FAKE9999' in response.data

    def test_invalid_date_returns_error(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/Derecho?active_at=bad')
        assert b'Invalid date format' in response.data

    def test_no_allocation_returns_message(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/NonexistentResource')
        assert response.status_code == 200


class TestTransactionsFragmentRoute:
    """Tests for GET /allocations/transactions_fragment."""

    def test_default_returns_200(self, auth_client):
        response = auth_client.get('/allocations/transactions_fragment')
        assert response.status_code == 200
        assert b'<table' in response.data

    def test_sort_by_amount_asc(self, auth_client):
        response = auth_client.get(
            '/allocations/transactions_fragment?sort_by=transaction_amount&sort_dir=asc'
        )
        assert response.status_code == 200

    def test_unknown_sort_by_is_ignored(self, auth_client):
        """Bogus sort_by from a malicious URL should not 500 — route silently
        falls back to the default sort."""
        response = auth_client.get('/allocations/transactions_fragment?sort_by=nope')
        assert response.status_code == 200

    def test_pagination_params(self, auth_client):
        response = auth_client.get(
            '/allocations/transactions_fragment?page=2&per_page=10'
        )
        assert response.status_code == 200

    def test_unauthenticated_redirects(self, client):
        response = client.get('/allocations/transactions_fragment')
        assert response.status_code in (302, 401)


class TestAdjustmentsFragmentRoute:
    """Tests for GET /allocations/adjustments_fragment."""

    def test_default_returns_200(self, auth_client):
        response = auth_client.get('/allocations/adjustments_fragment')
        assert response.status_code == 200
        assert b'<table' in response.data

    def test_sort_by_amount_desc(self, auth_client):
        response = auth_client.get(
            '/allocations/adjustments_fragment?sort_by=amount&sort_dir=desc'
        )
        assert response.status_code == 200

    def test_unknown_sort_by_is_ignored(self, auth_client):
        response = auth_client.get('/allocations/adjustments_fragment?sort_by=nope')
        assert response.status_code == 200

    def test_projcode_filter(self, auth_client):
        response = auth_client.get(
            '/allocations/adjustments_fragment?projcode=FAKE999'
        )
        assert response.status_code == 200
        # Unknown project → no rows
        assert b'No adjustments match' in response.data

    def test_unauthenticated_redirects(self, client):
        response = client.get('/allocations/adjustments_fragment')
        assert response.status_code in (302, 401)


class TestAuditDetailsFragmentRoutes:
    """Tests for the per-row detail fragments (transaction_details / adjustment_details)."""

    def test_transaction_details_unknown_id_returns_not_found(self, auth_client):
        response = auth_client.get('/allocations/transaction_details/99999999')
        assert response.status_code == 200
        assert b'Transaction not found' in response.data

    def test_adjustment_details_unknown_id_returns_not_found(self, auth_client):
        response = auth_client.get('/allocations/adjustment_details/99999999')
        assert response.status_code == 200
        assert b'Adjustment not found' in response.data

    def test_transaction_details_unauthenticated_redirects(self, client):
        response = client.get('/allocations/transaction_details/1')
        assert response.status_code in (302, 401)

    def test_adjustment_details_unauthenticated_redirects(self, client):
        response = client.get('/allocations/adjustment_details/1')
        assert response.status_code in (302, 401)


# ============================================================================
# Caching Behavior
# ============================================================================

class TestCaching:
    """Tests for Flask-Caching on allocations routes."""

    def test_cache_is_null_in_testing(self, app):
        assert app.config.get('CACHE_TYPE') == 'NullCache'

    def test_cache_extension_initialized(self, app):
        from webapp.extensions import cache
        assert cache is not None
        assert hasattr(cache, 'get')
        assert hasattr(cache, 'set')

    def test_repeated_requests_succeed(self, auth_client):
        """Both requests should succeed (cache hit or miss)."""
        r1 = auth_client.get('/allocations/')
        r2 = auth_client.get('/allocations/')
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_different_query_strings_are_separate(self, auth_client):
        r1 = auth_client.get('/allocations/?resources=Derecho')
        r2 = auth_client.get('/allocations/?resources=Casper')
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_cache_config_defaults(self, app):
        """Verify default cache settings are applied."""
        assert app.config.get('CACHE_DEFAULT_TIMEOUT') == 300


# ============================================================================
# Matplotlib lru_cache Behavior
# ============================================================================

class TestMatplotlibCaching:
    """Tests for lru_cache on chart generation functions."""

    def test_svg_in_allocations_response(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode().lower()
        assert '<svg' in html or 'no active allocations' in html

    def test_no_chartjs_canvas_in_response(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        assert '<canvas' not in html

    def test_facility_pie_cache_hit(self):
        from webapp.dashboards.charts import generate_facility_pie_chart_matplotlib
        generate_facility_pie_chart_matplotlib.cache_clear()

        data = [
            {'facility': 'UNIV', 'annualized_rate': 500, 'count': 10, 'percent': 62.5},
            {'facility': 'WNA', 'annualized_rate': 300, 'count': 5, 'percent': 37.5},
        ]
        r1 = generate_facility_pie_chart_matplotlib(data)
        r2 = generate_facility_pie_chart_matplotlib(data)
        assert r1 is r2
        assert generate_facility_pie_chart_matplotlib.cache_info().hits >= 1

    def test_facility_pie_cache_miss_on_different_data(self):
        from webapp.dashboards.charts import generate_facility_pie_chart_matplotlib
        generate_facility_pie_chart_matplotlib.cache_clear()

        data_a = [{'facility': 'UNIV', 'annualized_rate': 500, 'count': 10, 'percent': 100}]
        data_b = [{'facility': 'WNA', 'annualized_rate': 300, 'count': 5, 'percent': 100}]
        r1 = generate_facility_pie_chart_matplotlib(data_a)
        r2 = generate_facility_pie_chart_matplotlib(data_b)
        assert r1 != r2
        assert generate_facility_pie_chart_matplotlib.cache_info().misses >= 2

    def test_alloc_type_pie_cache_hit(self):
        from webapp.dashboards.charts import generate_allocation_type_pie_chart_matplotlib
        generate_allocation_type_pie_chart_matplotlib.cache_clear()

        data = [
            {'allocation_type': 'NSC', 'total_amount': 1000, 'count': 5, 'avg_amount': 200},
            {'allocation_type': 'Small', 'total_amount': 500, 'count': 10, 'avg_amount': 50},
        ]
        r1 = generate_allocation_type_pie_chart_matplotlib(data)
        r2 = generate_allocation_type_pie_chart_matplotlib(data)
        assert r1 is r2
        assert generate_allocation_type_pie_chart_matplotlib.cache_info().hits >= 1

    def test_nodetype_history_cache_hit(self):
        from webapp.dashboards.charts import generate_nodetype_history_matplotlib
        from datetime import datetime
        generate_nodetype_history_matplotlib.cache_clear()

        ts = datetime(2025, 1, 1)
        data = [{'timestamp': ts, 'nodes_total': 100, 'nodes_available': 80,
                 'nodes_down': 5, 'nodes_allocated': 15,
                 'utilization_percent': 85.0, 'memory_utilization_percent': 60.0}]
        r1 = generate_nodetype_history_matplotlib(data)
        r2 = generate_nodetype_history_matplotlib(data)
        assert r1 is r2
        assert generate_nodetype_history_matplotlib.cache_info().hits >= 1

    def test_queue_history_cache_hit(self):
        from webapp.dashboards.charts import generate_queue_history_matplotlib
        from datetime import datetime
        generate_queue_history_matplotlib.cache_clear()

        ts = datetime(2025, 1, 1)
        data = [{'timestamp': ts, 'running_jobs': 50, 'pending_jobs': 10,
                 'held_jobs': 2, 'active_users': 15, 'cores_allocated': 1000,
                 'cores_pending': 200, 'gpus_allocated': 0, 'gpus_pending': 0}]
        r1 = generate_queue_history_matplotlib(data)
        r2 = generate_queue_history_matplotlib(data)
        assert r1 is r2
        assert generate_queue_history_matplotlib.cache_info().hits >= 1

    def test_timeseries_cache_hit(self):
        from webapp.dashboards.charts import generate_usage_timeseries_matplotlib
        from datetime import date
        generate_usage_timeseries_matplotlib.cache_clear()

        data = {'dates': [date(2025, 1, 1), date(2025, 1, 2)], 'values': [100.0, 200.0]}
        r1 = generate_usage_timeseries_matplotlib(data)
        r2 = generate_usage_timeseries_matplotlib(data)
        assert r1 is r2
        assert generate_usage_timeseries_matplotlib.cache_info().hits >= 1

    def test_empty_data_returns_fallback(self):
        from webapp.dashboards.charts import generate_facility_pie_chart_matplotlib
        result = generate_facility_pie_chart_matplotlib([])
        assert 'text-muted' in result
        assert '<svg' not in result.lower()

    def test_cache_info_exposed(self):
        from webapp.dashboards.charts import (
            generate_facility_pie_chart_matplotlib,
            generate_allocation_type_pie_chart_matplotlib,
            generate_usage_timeseries_matplotlib,
            generate_nodetype_history_matplotlib,
            generate_queue_history_matplotlib,
        )
        for fn in [generate_facility_pie_chart_matplotlib,
                   generate_allocation_type_pie_chart_matplotlib,
                   generate_usage_timeseries_matplotlib,
                   generate_nodetype_history_matplotlib,
                   generate_queue_history_matplotlib]:
            assert hasattr(fn, 'cache_info')
            assert hasattr(fn, 'cache_clear')


# ============================================================================
# Extended Allocation Query Tests
# ============================================================================

class TestAllocationSummaryEdgeCases:
    """Edge case tests for get_allocation_summary()."""

    def test_multi_resource_list_filter(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name=['Derecho', 'Casper'],
            projcode='TOTAL',
            active_only=True
        )
        resources = {r['resource'] for r in result}
        assert resources.issubset({'Derecho', 'Casper'})

    def test_projcode_none_returns_individual_projects(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode=None,
            active_only=True
        )
        assert all('projcode' in r for r in result)

    def test_projcode_total_aggregates(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode='TOTAL',
            active_only=True
        )
        assert all('projcode' not in r for r in result)

    def test_active_at_past_date(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            active_only=True,
            active_at=datetime(2024, 6, 15)
        )
        assert isinstance(result, list)

    def test_active_at_future_date(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            active_only=True,
            active_at=datetime.now() + timedelta(days=3650)
        )
        assert isinstance(result, list)

    def test_inactive_allocations(self, session):
        result_active = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode='TOTAL',
            active_only=True
        )
        result_all = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode='TOTAL',
            active_only=False
        )
        active_total = sum(r['total_amount'] for r in result_active)
        all_total = sum(r['total_amount'] for r in result_all)
        assert all_total >= active_total

    def test_single_facility_filter(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            facility_name='UNIV',
            projcode='TOTAL',
            active_only=True
        )
        assert all(r['facility'] == 'UNIV' for r in result)

    def test_multi_facility_filter(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            facility_name=['UNIV', 'WNA'],
            projcode='TOTAL',
            active_only=True
        )
        facilities = {r['facility'] for r in result}
        assert facilities.issubset({'UNIV', 'WNA'})

    def test_annualized_rate_for_single_alloc(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode=None,
            active_only=True
        )
        singles = [r for r in result if r['count'] == 1]
        for s in singles:
            assert s['annualized_rate'] is not None
            assert s['annualized_rate'] >= 0

    def test_aggregated_rows_have_null_rate(self, session):
        result = get_allocation_summary(
            session=session,
            resource_name='Derecho',
            projcode='TOTAL',
            active_only=True
        )
        multis = [r for r in result if r['count'] > 1]
        for m in multis:
            assert m['annualized_rate'] is None


# ============================================================================
# Module-level cache reset fixture
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_usage_cache_globals():
    """
    Reset usage_cache module globals before/after every test in this file.

    Several tests (TestGetAllFacilityUsageOverviews, TestShowUsageToggle, etc.)
    call helpers or routes that lazily initialize the TTLCache singleton.  Without
    this reset the first test to run outside a Flask context initializes the cache
    with env-var defaults (TTL=3600) and that TTLCache bleeds into later tests that
    expect the cache to be disabled (TestingConfig sets TTL=0).
    """
    import sam.queries.usage_cache as uc
    uc._cache = None
    uc._disabled = False
    yield
    uc._cache = None
    uc._disabled = False


# ============================================================================
# Usage Facility Overview Helper
# ============================================================================

class TestGetAllFacilityUsageOverviews:
    """Tests for get_all_facility_usage_overviews()."""

    def test_empty_resource_list_returns_empty(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, [], datetime.now())
        assert result == {}

    def test_returns_dict_keyed_by_resource(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, ['Derecho'], datetime.now())
        assert isinstance(result, dict)

    def test_nonexistent_resource_returns_empty(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, ['FakeResource999'], datetime.now())
        assert result == {}

    def test_usage_overview_has_required_keys(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho'):
            for item in result['Derecho']:
                assert 'facility' in item
                assert 'total_used' in item
                assert 'annualized_rate' in item  # aliased to total_used for chart compat
                assert 'count' in item
                assert 'percent' in item

    def test_annualized_rate_equals_total_used(self, session):
        """annualized_rate must equal total_used so the chart function reads correct values."""
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho'):
            for item in result['Derecho']:
                assert item['annualized_rate'] == item['total_used']

    def test_percentages_sum_to_100_when_usage_exists(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_usage_overviews
        result = get_all_facility_usage_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho'):
            # Only facilities with actual usage are included
            used = [f for f in result['Derecho'] if f['total_used'] > 0]
            if len(used) > 1:
                total_pct = sum(f['percent'] for f in result['Derecho'])
                assert abs(total_pct - 100.0) < 0.1


# ============================================================================
# TTLCache Module (sam.queries.usage_cache)
# ============================================================================

class TestUsageCacheModule:
    """
    Tests for sam.queries.usage_cache module.

    These tests operate outside a Flask request context so _get_config()
    falls back to env vars (default TTL=3600, SIZE=200 → cache *enabled*).
    The module-level _reset_usage_cache_globals autouse fixture ensures a
    clean (None, False) state before each test.
    """

    # --- usage_cache_info() ---

    def test_info_has_required_fields(self):
        from sam.queries.usage_cache import usage_cache_info
        info = usage_cache_info()
        assert set(info.keys()) >= {'enabled', 'currsize', 'maxsize', 'ttl'}

    def test_info_disabled_when_flag_set(self):
        import sam.queries.usage_cache as uc
        uc._disabled = True
        info = uc.usage_cache_info()
        assert info['enabled'] is False

    def test_info_enabled_after_initialization(self):
        import sam.queries.usage_cache as uc
        uc._get_cache()  # triggers initialization using env-var defaults (TTL=3600)
        info = uc.usage_cache_info()
        assert info['enabled'] is True
        assert info['currsize'] == 0

    # --- _get_cache() disabled path ---

    def test_get_cache_returns_none_when_ttl_zero(self):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        with patch.object(uc, '_get_config', side_effect=lambda k, d: 0):
            cache = uc._get_cache()
        assert cache is None
        assert uc._disabled is True

    def test_get_cache_returns_none_when_size_zero(self):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        with patch.object(uc, '_get_config', side_effect=lambda k, d: 0):
            cache = uc._get_cache()
        assert cache is None

    # --- purge_usage_cache() ---

    def test_purge_disabled_cache_returns_zero(self):
        import sam.queries.usage_cache as uc
        uc._disabled = True
        n = uc.purge_usage_cache()
        assert n == 0

    def test_purge_empty_enabled_cache_returns_zero(self, session):
        from sam.queries.usage_cache import purge_usage_cache
        n = purge_usage_cache()
        assert n == 0

    def test_purge_clears_all_entries(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()  # initialize
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage', return_value=[]):
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
            uc.cached_allocation_usage(session=session, resource_name='Casper')
        n = uc.purge_usage_cache()
        assert n == 2
        assert len(uc._cache) == 0

    # --- cached_allocation_usage() cache hit/miss ---

    def test_cache_hit_avoids_second_db_call(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = [{'resource': 'Derecho', 'total_amount': 100}]
            r1 = uc.cached_allocation_usage(session=session, resource_name='Derecho')
            r2 = uc.cached_allocation_usage(session=session, resource_name='Derecho')
        assert mock_fn.call_count == 1
        assert r1 == r2

    def test_different_resources_are_separate_cache_keys(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = []
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
            uc.cached_allocation_usage(session=session, resource_name='Casper')
        assert mock_fn.call_count == 2

    def test_list_order_does_not_affect_cache_key(self, session):
        """['Derecho', 'Casper'] and ['Casper', 'Derecho'] must hit the same entry."""
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = []
            uc.cached_allocation_usage(session=session, resource_name=['Derecho', 'Casper'])
            uc.cached_allocation_usage(session=session, resource_name=['Casper', 'Derecho'])
        assert mock_fn.call_count == 1

    def test_disabled_cache_calls_db_every_time(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._disabled = True
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = []
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
        assert mock_fn.call_count == 2

    # --- force_refresh ---

    def test_force_refresh_bypasses_cache_hit(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = []
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
            uc.cached_allocation_usage(session=session, resource_name='Derecho', force_refresh=True)
        assert mock_fn.call_count == 2

    def test_force_refresh_updates_cached_value(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._get_cache()
        first_result  = [{'resource': 'Derecho', 'total_amount': 100}]
        second_result = [{'resource': 'Derecho', 'total_amount': 200}]
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = first_result
            uc.cached_allocation_usage(session=session, resource_name='Derecho')
            mock_fn.return_value = second_result
            r = uc.cached_allocation_usage(session=session, resource_name='Derecho', force_refresh=True)
        assert r == second_result

    def test_force_refresh_on_disabled_cache_still_calls_db(self, session):
        import sam.queries.usage_cache as uc
        from unittest.mock import patch
        uc._disabled = True
        with patch('sam.queries.usage_cache.get_allocation_summary_with_usage') as mock_fn:
            mock_fn.return_value = []
            uc.cached_allocation_usage(session=session, resource_name='Derecho', force_refresh=True)
        assert mock_fn.call_count == 1


# ============================================================================
# Cache Admin Routes
# ============================================================================

class TestCachePurgeRoute:
    """Tests for POST /allocations/cache/purge."""

    def test_requires_login(self, client):
        response = client.post('/allocations/cache/purge')
        assert response.status_code in (302, 401)

    def test_json_request_returns_json(self, auth_client):
        response = auth_client.post(
            '/allocations/cache/purge',
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert isinstance(data['entries_cleared'], int)
        assert data['entries_cleared'] >= 0

    def test_htmx_request_returns_json(self, auth_client):
        response = auth_client.post(
            '/allocations/cache/purge',
            headers={'HX-Request': 'true'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        assert data['status'] == 'ok'

    def test_form_post_redirects_to_index(self, auth_client):
        response = auth_client.post('/allocations/cache/purge')
        assert response.status_code == 302
        assert '/allocations/' in response.headers['Location']


class TestCacheStatusRoute:
    """Tests for GET /allocations/cache/status."""

    def test_requires_login(self, client):
        response = client.get('/allocations/cache/status')
        assert response.status_code in (302, 401)

    def test_returns_200_json(self, auth_client):
        response = auth_client.get('/allocations/cache/status')
        assert response.status_code == 200
        assert response.content_type.startswith('application/json')

    def test_response_has_required_fields(self, auth_client):
        response = auth_client.get('/allocations/cache/status')
        data = response.get_json()
        assert 'enabled' in data
        assert 'currsize' in data
        assert 'maxsize' in data
        assert 'ttl' in data

    def test_cache_config_keys_present(self, app):
        """App config must expose the cache sizing knobs (set via SAMWebappConfig defaults)."""
        assert app.config.get('ALLOCATION_USAGE_CACHE_TTL') is not None
        assert app.config.get('ALLOCATION_USAGE_CACHE_SIZE') is not None

    def test_enabled_field_is_boolean(self, auth_client):
        """enabled field must be a bool regardless of whether the cache is on or off."""
        response = auth_client.get('/allocations/cache/status')
        data = response.get_json()
        assert isinstance(data['enabled'], bool)


# ============================================================================
# force_refresh Integration (Route Smoke Tests)
# ============================================================================

class TestForceRefreshParameter:
    """Smoke tests: routes accept force_refresh without error."""

    def test_dashboard_force_refresh_true(self, auth_client):
        response = auth_client.get('/allocations/?force_refresh=true')
        assert response.status_code == 200

    def test_dashboard_force_refresh_false(self, auth_client):
        response = auth_client.get('/allocations/?force_refresh=false')
        assert response.status_code == 200

    def test_projects_fragment_force_refresh(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV'
            '&allocation_type=Small&force_refresh=true'
        )
        assert response.status_code == 200

    def test_usage_modal_force_refresh(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/Derecho?force_refresh=true')
        assert response.status_code == 200


# ============================================================================
# active_at Midnight Normalization
# ============================================================================

class TestActiveAtNormalization:
    """Tests for the midnight normalization applied to default active_at."""

    def test_default_renders_todays_date(self, auth_client):
        """Dashboard with no active_at param should render today's date."""
        today = datetime.now().strftime('%Y-%m-%d')
        response = auth_client.get('/allocations/')
        assert today.encode() in response.data

    def test_explicit_date_preserved(self, auth_client):
        """An explicit ?active_at= param should appear verbatim in the response."""
        response = auth_client.get('/allocations/?active_at=2025-06-01')
        assert b'2025-06-01' in response.data


# ============================================================================
# Show Usage Toggle (Route Integration)
# ============================================================================

class TestShowUsageToggle:
    """Tests for ?show_usage=true toggle on dashboard routes."""

    def test_dashboard_show_usage_false(self, auth_client):
        response = auth_client.get('/allocations/?show_usage=false')
        assert response.status_code == 200

    def test_dashboard_show_usage_true(self, auth_client):
        response = auth_client.get('/allocations/?show_usage=true')
        assert response.status_code == 200

    def test_projects_fragment_show_usage_true(self, auth_client):
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV'
            '&allocation_type=Small&show_usage=true'
        )
        assert response.status_code == 200

    def test_projects_fragment_usage_renders_progress_or_empty(self, auth_client):
        """show_usage=true produces progress bars or the empty-state message."""
        response = auth_client.get(
            '/allocations/projects?resource=Derecho&facility=UNIV'
            '&allocation_type=Small&show_usage=true'
        )
        html = response.data.decode()
        assert 'progress' in html or 'No active projects' in html

    def test_usage_no_crash_on_zero_usage_facilities(self, auth_client):
        """Facilities with zero usage must not crash the chart renderer."""
        response = auth_client.get('/allocations/?show_usage=true&resources=Derecho')
        assert response.status_code == 200
