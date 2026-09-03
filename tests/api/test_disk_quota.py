"""API endpoint tests for /api/v1/disk_quota/.

Per-project disk allocations + paths for DASG provisioning. Reproduces legacy
``GET /api/protected/admin/dasg/diskquota``.
"""

import pytest


class TestDiskQuota:
    """Test GET /api/v1/disk_quota/."""

    def test_returns_200(self, auth_client):
        assert auth_client.get('/api/v1/disk_quota/').status_code == 200

    def test_returns_list(self, auth_client):
        data = auth_client.get('/api/v1/disk_quota/').get_json()
        assert isinstance(data, list)

    def test_record_key_set(self, auth_client):
        data = auth_client.get('/api/v1/disk_quota/').get_json()
        for rec in data[:5]:
            assert set(rec.keys()) == {
                'projcode', 'groupName', 'dataManager',
                'resourceName', 'quota', 'paths',
            }
            assert isinstance(rec['paths'], list)


class TestDiskQuotaAuth:

    def test_unauthenticated(self, client):
        assert client.get('/api/v1/disk_quota/').status_code in [302, 401]


class TestDiskQuotaCacheRefresh:

    def test_refresh_returns_status_ok(self, auth_client):
        response = auth_client.post('/api/v1/disk_quota/refresh')
        assert response.status_code == 200
        assert response.get_json() == {'status': 'ok'}
