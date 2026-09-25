"""
Tests for Health Check API endpoints.

Covers:
  GET /api/v1/health/       — public health: DB ping + schema drift
  GET /api/v1/health/live   — public liveness (no DB)
  GET /api/v1/health/ready  — public readiness (connectivity only)
  GET /api/v1/health/db-pool — admin-only pool statistics

``/`` and ``/ready`` deliberately diverge on schema drift; see
TestSchemaDriftContract for why that split is load-bearing.

The `non_admin_client` fixture lives in tests/conftest.py and picks any
active non-benkirk user from the snapshot — `load_user()` will resolve
that user's permissions from POSIX group membership (and they're not in
`USER_PERMISSION_OVERRIDES`), which in the test snapshot typically
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
# GET /api/v1/health/
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
    """Tests for GET /api/v1/health/ready — connectivity only."""

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

        # Same keys, and the same verdict while everything is up (they
        # diverge by design when a secondary bind is down — see below).
        assert set(d_ready.keys()) == set(d_health.keys())
        assert d_ready['service'] == d_health['service']
        assert d_ready['status'] == d_health['status'] == 'healthy'

    def test_readiness_returns_503_when_primary_db_fails(self, client):
        """The sam bind is required: its failure is a 503."""
        failing_ping = (False, None, 'simulated failure')

        with patch('webapp.api.v1.health._ping_engine', return_value=failing_ping):
            response = client.get('/api/v1/health/ready')

        assert response.status_code == 503
        assert response.get_json()['status'] == 'unhealthy'

    def test_readiness_omits_schema_check(self, client):
        """/ready must not even run the drift probe."""
        response = client.get('/api/v1/health/ready')
        assert 'sam_schema' not in response.get_json()['checks']


# ---------------------------------------------------------------------------
# Schema drift — the / vs /ready split
# ---------------------------------------------------------------------------

_DRIFT = {
    'status': 'unhealthy',
    'drift': ['users: ORM has pdb_modified_time, idms_sync_token'],
}


class TestSchemaDriftContract:
    """The 2026-08-10 outage in endpoint form.

    A production DDL change dropped columns the ORM still selected. Every
    page 500'd for ~20 minutes while all three probes reported 200, because
    `SELECT 1` cannot see a missing column.

    The split matters in both directions:
      - `/` must go 503, or monitoring never pages.
      - `/ready` must stay 200, or Kubernetes marks every replica NotReady,
        empties the Service, and converts a degraded site into an
        unreachable one — while stalling the rolling deploy carrying the fix.
    """

    def test_health_reports_schema_check_when_clean(self, client):
        """The sam_schema check is present and healthy against the test DB."""
        data = client.get('/api/v1/health/').get_json()
        assert data['checks']['sam_schema']['status'] == 'healthy'
        assert data['status'] == 'healthy'

    def test_health_returns_503_on_drift(self, client):
        with patch('webapp.api.v1.health.schema_drift', return_value=_DRIFT):
            response = client.get('/api/v1/health/')

        assert response.status_code == 503
        data = response.get_json()
        assert data['status'] == 'unhealthy'
        assert data['checks']['sam_schema']['drift'] == _DRIFT['drift']
        # Connectivity is fine — the drift alone must carry the failure.
        assert data['checks']['sam']['status'] == 'healthy'

    def test_readiness_stays_200_on_drift(self, client):
        """Load-bearing: drift must never empty the k8s Service."""
        with patch('webapp.api.v1.health.schema_drift', return_value=_DRIFT):
            response = client.get('/api/v1/health/ready')

        assert response.status_code == 200

    def test_liveness_stays_200_on_drift(self, client):
        """Drift must never restart pods either."""
        with patch('webapp.api.v1.health.schema_drift', return_value=_DRIFT):
            response = client.get('/api/v1/health/live')

        assert response.status_code == 200

    def test_schema_check_skipped_when_bind_unreachable(self, client):
        """A dead bind reports connectivity, not a bogus drift verdict."""
        failing_ping = (False, None, 'Connection refused')

        with patch('webapp.api.v1.health._ping_engine', return_value=failing_ping):
            response = client.get('/api/v1/health/')

        assert response.status_code == 503
        assert 'sam_schema' not in response.get_json()['checks']


# ---------------------------------------------------------------------------
# Secondary bind — the 2026-09-13 csg-postgres roll in endpoint form
# ---------------------------------------------------------------------------

def _secondary_bind_down(engine):
    """_ping_engine stand-in: sam answers, everything else refuses."""
    from webapp.extensions import db
    if engine is db.engine:
        return True, 4.4, None
    return False, None, 'connection refused (csg-postgres rolling)'


class TestSecondaryBindContract:
    """A CNPG rolling restart failed the system_status ping on both pods for
    3.5 minutes while sam answered in 4 ms. Readiness went 503, Kubernetes
    emptied the Service, and a status-dashboard blip became a full outage.

    - `/ready` must stay 200 and say `degraded`, so the site keeps serving.
    - `/` must go 503, so monitoring still pages.
    - `/ready?strict=1` must go 503, for compose startup ordering.
    """

    def test_readiness_stays_200_when_secondary_bind_down(self, client):
        """Load-bearing: a secondary bind must never empty the k8s Service."""
        with patch('webapp.api.v1.health._ping_engine', side_effect=_secondary_bind_down):
            response = client.get('/api/v1/health/ready')

        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'degraded'
        assert data['checks']['sam']['status'] == 'healthy'
        assert data['checks']['sam']['required'] is True
        assert data['checks']['system_status']['status'] == 'unhealthy'
        assert data['checks']['system_status']['required'] is False
        assert 'csg-postgres' in data['checks']['system_status']['error']

    def test_readiness_strict_returns_503_when_secondary_bind_down(self, client):
        with patch('webapp.api.v1.health._ping_engine', side_effect=_secondary_bind_down):
            response = client.get('/api/v1/health/ready?strict=1')

        assert response.status_code == 503
        assert response.get_json()['status'] == 'unhealthy'

    def test_health_returns_503_when_secondary_bind_down(self, client):
        """The monitoring surface still fails on any bind."""
        with patch('webapp.api.v1.health._ping_engine', side_effect=_secondary_bind_down):
            response = client.get('/api/v1/health/')

        assert response.status_code == 503
        assert response.get_json()['status'] == 'unhealthy'

    def test_degradation_is_logged_at_warning(self, client, caplog):
        """A 503 used to log as routine INFO; degradation must be visible."""
        import logging
        with caplog.at_level(logging.WARNING), \
             patch('webapp.api.v1.health._ping_engine', side_effect=_secondary_bind_down):
            client.get('/api/v1/health/ready')

        assert any('readiness degraded' in r.getMessage() and 'system_status' in r.getMessage()
                   for r in caplog.records)


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

    def test_db_pool_covers_every_inventoried_engine(self, app, auth_client, monkeypatch, tmp_path):
        """Plugin engines appear under stable keys: one per machine, one per collection."""
        from itertools import count
        from sqlalchemy import create_engine
        n = count()
        eng = lambda: create_engine(f'sqlite:///{tmp_path}/e{next(n)}.db')   # file DB: QueuePool
        monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {'engines': {'derecho': eng()}})
        monkeypatch.setitem(app.extensions, 'fs_scans', {
            'databases': {'campaign': {'engines': {'univ': eng(), 'cisl': eng()}}}})
        pools = auth_client.get('/api/v1/health/db-pool').get_json()['pools']
        assert set(pools) == {'sam', 'system_status', 'job_history:derecho',
                              'fs_scans:campaign/cisl', 'fs_scans:campaign/univ'}
        assert pools['fs_scans:campaign/univ']['reachable'] is True

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
