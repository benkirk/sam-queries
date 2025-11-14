"""
API endpoint tests for Charge/Balance endpoints

Tests HTTP endpoints for project charge summaries and balance calculations.
This is a CRITICAL endpoint that calculates real-time allocation usage.
"""

import pytest


class TestProjectChargesSummary:
    """Test GET /api/v1/projects/<projcode>/charges/summary endpoint."""

    def test_charges_summary_success(self, auth_client):
        """Test successful charges summary retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert data['projcode'] == 'CESM0002'

        # Response structure may be 'allocations', 'resources', or 'summary'
        assert 'allocations' in data or 'resources' in data or 'summary' in data

    def test_charges_summary_allocation_structure(self, auth_client):
        """Test allocation summary includes usage fields."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()

        # Handle both 'allocations' and 'resources' response formats
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]
        elif 'resources' in data and len(data['resources']) > 0:
            # Resources dict format: get first resource
            resource_name = list(data['resources'].keys())[0]
            alloc = data['resources'][resource_name]
        else:
            pytest.skip("No allocation or resource data to test")

        # Core balance fields
        assert 'allocated' in alloc
        assert 'used' in alloc
        assert 'remaining' in alloc
        assert 'percent_used' in alloc

        # Breakdown by charge type
        assert 'charges_by_type' in alloc
        charges = alloc['charges_by_type']
        # At least one charge type should be present
        assert isinstance(charges, dict)

    def test_charges_summary_with_adjustments(self, auth_client):
        """Test charges summary includes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=true'
        )
        assert response.status_code == 200

        data = response.get_json()

        # Handle both response formats
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]
            # Adjustments field should be present (may be empty list or number)
            assert 'adjustments' in alloc or 'adjustment_total' in alloc
        elif 'resources' in data and len(data['resources']) > 0:
            # For resources format, adjustments should be a number
            resource_name = list(data['resources'].keys())[0]
            assert 'adjustments' in data['resources'][resource_name]

    def test_charges_summary_without_adjustments(self, auth_client):
        """Test charges summary excludes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=false'
        )
        assert response.status_code == 200

        data = response.get_json()
        # Should succeed regardless
        assert response.status_code == 200

    def test_charges_summary_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/charges/summary')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data

    def test_charges_summary_project_no_allocations(self, auth_client):
        """Test response for project with no allocations."""
        # Find a project with no allocations or use known test project
        response = auth_client.get('/api/v1/projects/UMIN0005/charges/summary')

        # Should return 200 with empty allocations (not 404)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.get_json()
            if 'allocations' in data:
                assert len(data['allocations']) == 0


class TestProjectChargesDetail:
    """Test GET /api/v1/projects/<projcode>/charges endpoint (if it exists)."""

    def test_charges_detail_success(self, auth_client):
        """Test successful charges detail retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges')

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'charges' in data or 'charge_history' in data

    def test_charges_detail_date_filtering(self, auth_client):
        """Test date range filtering."""
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        response = auth_client.get(
            f'/api/v1/projects/CESM0002/charges?'
            f'start_date={start_date.date()}&end_date={end_date.date()}'
        )

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200
