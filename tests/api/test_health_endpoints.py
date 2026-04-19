"""
Tests for Health Check API endpoints.

Covers:
  GET /api/v1/health/       — public health + DB ping
  GET /api/v1/health/live   — public liveness (no DB)
  GET /api/v1/health/ready  — public readiness (delegates to health)
  GET /api/v1/health/db-pool — admin-only pool statistics

The `non_admin_client` fixture lives in tests/conftest.py and picks any
active non-benkirk user from the snapshot — `load_user()` will resolve
that user's permissions from POSIX group membership (no entry in
`TestingConfig.DEV_GROUP_MAPPING`), which in the test snapshot typically
means an empty role/permission set.
"""

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# GET /api/v1/health/live
# ---------------------------------------------------------------------------

class TestLivenessEndpoint:
    """Tests for GET /api/v1/health/live — no DB call, always fast."""

    def test_liveness_returns_200(self, client):
        """Liveness probe succeeds without authentication."""
        response = client.get('/api/v1/health/live')
        assert response.status_code == 200

    def test_liveness_payload(self, client):
        """Response contains expected keys."""
        response = client.get('/api/v1/health/live')
        data = response.get_json()
        assert data['status'] == 'alive'
        assert data['service'] == 'sam-webapp'

    def test_liveness_no_db_field(self, client):
        """Liveness response must NOT include DB check data."""
        response = client.get('/api/v1/health/live')
        data = response.get_json()
        assert 'checks' not in data
        assert 'timestamp' not in data


# ---------------------------------------------------------------------------
# GET /api/v1/health/  (and /ready which delegates to it)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Tests for GET /api/v1/health/ — DB ping, public."""

    def test_health_returns_200_when_dbs_up(self, client):
        """Returns 200 when all DB connections succeed."""
        response = client.get('/api/v1/health/')
        assert response.status_code == 200

    def test_health_payload_structure(self, client):
        """Response contains top-level required keys."""
        response = client.get('/api/v1/health/')
        data = response.get_json()
        assert 'status' in data
        assert 'service' in data
        assert 'timestamp' in data
        assert 'checks' in data

    def test_health_service_name(self, client):
        """Service field is always 'sam-webapp'."""
        response = client.get('/api/v1/health/')
        data = response.get_json()
        assert data['service'] == 'sam-webapp'

    def test_health_checks_structure(self, client):
        """Each DB check contains status and latency_ms when healthy."""
        response = client.get('/api/v1/health/')
        data = response.get_json()
        checks = data['checks']

        assert 'sam' in checks
        assert checks['sam']['status'] == 'healthy'
        assert 'latency_ms' in checks['sam']
        assert isinstance(checks['sam']['latency_ms'], (int, float))
        assert checks['sam']['latency_ms'] >= 0

    def test_health_status_healthy_string(self, client):
        """Top-level status is 'healthy' when all checks pass."""
        response = client.get('/api/v1/health/')
        data = response.get_json()
        assert data['status'] == 'healthy'

    def test_health_public_no_auth_required(self, client):
        """Endpoint is accessible without authentication."""
        response = client.get('/api/v1/health/')
        # Must not redirect to login (302) or return 401
        assert response.status_code != 302
        assert response.status_code != 401

    def test_health_returns_503_when_db_fails(self, client):
        """Returns 503 and 'unhealthy' status when a DB ping fails."""
        failing_ping = (False, None, 'Connection refused: test-induced failure')

        with patch('webapp.api.v1.health._ping_engine', return_value=failing_ping):
            response = client.get('/api/v1/health/')

        assert response.status_code == 503
        data = response.get_json()
        assert data['status'] == 'unhealthy'

    def test_health_error_field_on_failure(self, client):
        """When a check fails the check dict contains 'error', not 'latency_ms'."""
        failing_ping = (False, None, 'timeout')

        with patch('webapp.api.v1.health._ping_engine', return_value=failing_ping):
            response = client.get('/api/v1/health/')

        data = response.get_json()
        for check in data['checks'].values():
            assert check['status'] == 'unhealthy'
            assert 'error' in check
            assert 'latency_ms' not in check


# ---------------------------------------------------------------------------
# GET /api/v1/health/ready
# ---------------------------------------------------------------------------

class TestReadinessEndpoint:
    """Tests for GET /api/v1/health/ready — delegates to health()."""

    def test_readiness_returns_200_when_dbs_up(self, client):
        """Returns 200 when all DB connections succeed."""
        response = client.get('/api/v1/health/ready')
        assert response.status_code == 200

    def test_readiness_matches_health_response(self, client):
        """Readiness and health payloads share the same top-level structure."""
        r_ready = client.get('/api/v1/health/ready')
        r_health = client.get('/api/v1/health/')

        d_ready = r_ready.get_json()
        d_health = r_health.get_json()

        # Same keys
        assert set(d_ready.keys()) == set(d_health.keys())
        # Same service and status values (timestamps will differ)
        assert d_ready['service'] == d_health['service']
        assert d_ready['status'] == d_health['status']

    def test_readiness_returns_503_when_db_fails(self, client):
        """Returns 503 when a DB ping fails."""
        failing_ping = (False, None, 'simulated failure')

        with patch('webapp.api.v1.health._ping_engine', return_value=failing_ping):
            response = client.get('/api/v1/health/ready')

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/health/db-pool
# ---------------------------------------------------------------------------

class TestDbPoolEndpoint:
    """Tests for GET /api/v1/health/db-pool — admin only."""

    def test_db_pool_unauthenticated_redirects(self, client):
        """Unauthenticated request is redirected to login."""
        response = client.get('/api/v1/health/db-pool')
        # Flask-Login redirects to login page
        assert response.status_code in (302, 401)

    def test_db_pool_non_admin_forbidden(self, non_admin_client):
        """Non-admin authenticated user receives 403."""
        response = non_admin_client.get('/api/v1/health/db-pool')
        assert response.status_code == 403

        data = response.get_json()
        assert 'error' in data

    def test_db_pool_admin_returns_200(self, auth_client):
        """Admin user receives 200 with pool statistics."""
        response = auth_client.get('/api/v1/health/db-pool')
        assert response.status_code == 200

    def test_db_pool_response_structure(self, auth_client):
        """Response contains 'pools' dict and 'timestamp'."""
        response = auth_client.get('/api/v1/health/db-pool')
        data = response.get_json()

        assert 'pools' in data
        assert 'timestamp' in data
        assert isinstance(data['pools'], dict)

    def test_db_pool_sam_pool_present(self, auth_client):
        """'sam' engine pool stats are always present."""
        response = auth_client.get('/api/v1/health/db-pool')
        data = response.get_json()

        assert 'sam' in data['pools']

    def test_db_pool_stats_keys(self, auth_client):
        """Each pool entry contains all expected stat keys."""
        response = auth_client.get('/api/v1/health/db-pool')
        data = response.get_json()

        expected_keys = {
            'pool_size', 'checked_in', 'checked_out',
            'overflow', 'max_overflow', 'utilization_pct', 'health',
        }

        for pool_name, stats in data['pools'].items():
            assert expected_keys.issubset(stats.keys()), \
                f"Pool '{pool_name}' missing keys: {expected_keys - stats.keys()}"

    def test_db_pool_utilization_pct_range(self, auth_client):
        """utilization_pct is a non-negative number."""
        response = auth_client.get('/api/v1/health/db-pool')
        data = response.get_json()

        for pool_name, stats in data['pools'].items():
            assert isinstance(stats['utilization_pct'], (int, float)), \
                f"Pool '{pool_name}' utilization_pct is not a number"
            assert stats['utilization_pct'] >= 0, \
                f"Pool '{pool_name}' utilization_pct is negative"

    def test_db_pool_health_values(self, auth_client):
        """health field is either 'healthy' or 'warning'."""
        response = auth_client.get('/api/v1/health/db-pool')
        data = response.get_json()

        valid_values = {'healthy', 'warning'}
        for pool_name, stats in data['pools'].items():
            assert stats['health'] in valid_values, \
                f"Pool '{pool_name}' health '{stats['health']}' is not one of {valid_values}"
