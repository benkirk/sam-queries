"""
API endpoint tests for the WallClock Exemption endpoints.

Tests the /api/v1/wallclock_exemption/ endpoints which provide active
per-user queue wallclock overrides for batch scheduler tooling.
"""

import pytest


class TestWceAllResources:
    """Test GET /api/v1/wallclock_exemption/."""

    def test_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/wallclock_exemption/')
        assert response.status_code == 200

    def test_response_has_name_and_resources(self, auth_client):
        data = auth_client.get('/api/v1/wallclock_exemption/').get_json()
        assert 'name' in data
        assert 'resources' in data

    def test_name_is_exemptions(self, auth_client):
        data = auth_client.get('/api/v1/wallclock_exemption/').get_json()
        assert data['name'] == 'exemptions'

    def test_resources_is_list(self, auth_client):
        data = auth_client.get('/api/v1/wallclock_exemption/').get_json()
        assert isinstance(data['resources'], list)

    def test_nested_shape(self, auth_client):
        data = auth_client.get('/api/v1/wallclock_exemption/').get_json()
        for res in data['resources'][:2]:
            assert 'resourceName' in res
            assert isinstance(res['queues'], list)
            for q in res['queues'][:3]:
                assert 'queueName' in q
                assert isinstance(q['limits'], list)
                for lim in q['limits'][:3]:
                    assert set(lim.keys()) == {'username', 'wallClockLimit'}


class TestWceSingleResource:
    """Test GET /api/v1/wallclock_exemption/<resource_name>."""

    def _first_resource(self, auth_client):
        data = auth_client.get('/api/v1/wallclock_exemption/').get_json()
        if not data['resources']:
            pytest.skip('snapshot has no active wallclock exemptions')
        return data['resources'][0]['resourceName']

    def test_single_resource_returns_200(self, auth_client):
        rname = self._first_resource(auth_client)
        response = auth_client.get(f'/api/v1/wallclock_exemption/{rname}')
        assert response.status_code == 200

    def test_single_resource_only_contains_that_resource(self, auth_client):
        rname = self._first_resource(auth_client)
        data = auth_client.get(f'/api/v1/wallclock_exemption/{rname}').get_json()
        assert [r['resourceName'] for r in data['resources']] == [rname]

    def test_unknown_resource_returns_404(self, auth_client):
        response = auth_client.get('/api/v1/wallclock_exemption/NonexistentResource99')
        assert response.status_code == 404


class TestWceAuth:
    """Unauthenticated callers are rejected."""

    def test_unauthenticated_all_resources(self, client):
        response = client.get('/api/v1/wallclock_exemption/')
        assert response.status_code in [302, 401]

    def test_unauthenticated_single_resource(self, client):
        response = client.get('/api/v1/wallclock_exemption/Derecho')
        assert response.status_code in [302, 401]


class TestWceCacheRefresh:
    """Test POST /api/v1/wallclock_exemption/refresh."""

    def test_refresh_returns_200(self, auth_client):
        response = auth_client.post('/api/v1/wallclock_exemption/refresh')
        assert response.status_code == 200

    def test_refresh_returns_status_ok(self, auth_client):
        data = auth_client.post('/api/v1/wallclock_exemption/refresh').get_json()
        assert data == {'status': 'ok'}
