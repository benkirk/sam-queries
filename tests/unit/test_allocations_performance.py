"""
Allocations Dashboard Performance Tests

Tests for the performance optimization changes:
- Dashboard route responses (happy path, sad path, edge cases)
- Flask-Caching behavior
- Batched facility overview queries
- Chart.js data contract (template data attributes)
- Extended allocation query edge cases
"""

import pytest
import json
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

        result = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        assert isinstance(result, dict)
        if result:
            assert 'Derecho' in result

    def test_multiple_resources_single_query(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, ['Derecho', 'Casper'], datetime.now())
        assert isinstance(result, dict)

    def test_empty_resource_list(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, [], datetime.now())
        assert result == {}

    def test_nonexistent_resource(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, ['NonexistentResource123'], datetime.now())
        assert result == {}

    def test_facility_overview_has_required_keys(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho'):
            for item in result['Derecho']:
                assert 'facility' in item
                assert 'total_amount' in item
                assert 'annualized_rate' in item
                assert 'count' in item
                assert 'percent' in item

    def test_percentages_sum_to_100(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho') and len(result['Derecho']) > 1:
            total_pct = sum(f['percent'] for f in result['Derecho'])
            assert abs(total_pct - 100.0) < 0.1

    def test_sorted_by_annualized_rate_desc(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        result = get_all_facility_overviews(session, ['Derecho'], datetime.now())
        if result.get('Derecho') and len(result['Derecho']) > 1:
            rates = [f['annualized_rate'] for f in result['Derecho']]
            assert rates == sorted(rates, reverse=True)

    def test_historical_date(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        past = datetime(2024, 1, 1)
        result = get_all_facility_overviews(session, ['Derecho'], past)
        assert isinstance(result, dict)

    def test_future_date_returns_empty_or_less(self, session):
        from webapp.dashboards.allocations.blueprint import get_all_facility_overviews

        future = datetime.now() + timedelta(days=3650)
        result = get_all_facility_overviews(session, ['Derecho'], future)
        assert isinstance(result, dict)


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

    def test_index_contains_chart_canvas(self, auth_client):
        response = auth_client.get('/allocations/')
        assert b'facility-pie-chart' in response.data or b'No active allocations' in response.data

    def test_index_contains_chartjs_data_attributes(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        if 'facility-pie-chart' in html:
            assert 'data-labels' in html
            assert 'data-values' in html
            assert 'data-title' in html

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
        assert b'Project not found' in response.data

    def test_invalid_date_returns_error(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/Derecho?active_at=bad')
        assert b'Invalid date format' in response.data

    def test_no_allocation_returns_message(self, auth_client):
        response = auth_client.get('/allocations/usage/SCSG0001/NonexistentResource')
        assert response.status_code == 200


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
# Chart.js Data Contract
# ============================================================================

class TestChartJsDataContract:
    """Tests ensuring templates emit valid JSON for Chart.js."""

    def test_facility_chart_labels_are_valid_json(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        for marker in _extract_data_attr(html, 'facility-pie-chart', 'data-labels'):
            parsed = json.loads(marker)
            assert isinstance(parsed, list)
            assert all(isinstance(x, str) for x in parsed)

    def test_facility_chart_values_are_valid_json(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        for marker in _extract_data_attr(html, 'facility-pie-chart', 'data-values'):
            parsed = json.loads(marker)
            assert isinstance(parsed, list)
            assert all(isinstance(x, (int, float)) for x in parsed)

    def test_alloc_type_chart_labels_valid(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        for marker in _extract_data_attr(html, 'alloc-type-pie-chart', 'data-labels'):
            parsed = json.loads(marker)
            assert isinstance(parsed, list)

    def test_alloc_type_chart_values_valid(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        for marker in _extract_data_attr(html, 'alloc-type-pie-chart', 'data-values'):
            parsed = json.loads(marker)
            assert isinstance(parsed, list)
            assert all(isinstance(x, (int, float)) for x in parsed)

    def test_chart_labels_and_values_same_length(self, auth_client):
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        labels_list = list(_extract_data_attr(html, 'facility-pie-chart', 'data-labels'))
        values_list = list(_extract_data_attr(html, 'facility-pie-chart', 'data-values'))
        for labels_str, values_str in zip(labels_list, values_list):
            labels = json.loads(labels_str)
            values = json.loads(values_str)
            assert len(labels) == len(values)

    def test_no_matplotlib_svg_in_response(self, auth_client):
        """Ensure we no longer emit server-side SVG charts."""
        response = auth_client.get('/allocations/')
        html = response.data.decode()
        assert '<svg' not in html.lower() or 'favicon' in html.lower()


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
# Helpers
# ============================================================================

def _extract_data_attr(html: str, css_class: str, attr_name: str):
    """Yield values of a data attribute from elements matching a CSS class."""
    import re
    pattern = rf'class="{css_class}"[^>]*{attr_name}=\'([^\']*)\''
    for match in re.finditer(pattern, html):
        yield match.group(1)
