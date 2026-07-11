"""
API endpoint tests for the Queue endpoints.

Tests the /api/v1/queue/ endpoints which provide active job-queue
configuration for batch scheduler / systems-integration tooling.
"""

import pytest


class TestQueueAllResources:
    """Test GET /api/v1/queue/."""

    def test_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/queue/')
        assert response.status_code == 200

    def test_response_has_name_and_resources(self, auth_client):
        data = auth_client.get('/api/v1/queue/').get_json()
        assert 'name' in data
        assert 'resources' in data

    def test_name_is_queues(self, auth_client):
        data = auth_client.get('/api/v1/queue/').get_json()
        assert data['name'] == 'queues'

    def test_resources_is_list(self, auth_client):
        data = auth_client.get('/api/v1/queue/').get_json()
        assert isinstance(data['resources'], list)

    def test_resource_has_queues_with_fields(self, auth_client):
        data = auth_client.get('/api/v1/queue/').get_json()
        assert len(data['resources']) >= 1
        for res in data['resources'][:2]:
            assert 'resourceName' in res
            assert isinstance(res['queues'], list)
            for q in res['queues'][:3]:
                assert set(q.keys()) == {
                    'queueName', 'wallClockHoursLimit',
                    'startDate', 'endDate', 'cosId',
                }


class TestQueueSingleResource:
    """Test GET /api/v1/queue/<resource_name>."""

    def _first_resource(self, auth_client):
        data = auth_client.get('/api/v1/queue/').get_json()
        return data['resources'][0]['resourceName']

    def test_single_resource_returns_200(self, auth_client):
        rname = self._first_resource(auth_client)
        response = auth_client.get(f'/api/v1/queue/{rname}')
        assert response.status_code == 200

    def test_single_resource_only_contains_that_resource(self, auth_client):
        rname = self._first_resource(auth_client)
        data = auth_client.get(f'/api/v1/queue/{rname}').get_json()
        assert [r['resourceName'] for r in data['resources']] == [rname]

    def test_unknown_resource_returns_404(self, auth_client):
        response = auth_client.get('/api/v1/queue/NonexistentResource99')
        assert response.status_code == 404


class TestQueueAuth:
    """Unauthenticated callers are rejected."""

    def test_unauthenticated_all_resources(self, client):
        response = client.get('/api/v1/queue/')
        assert response.status_code in [302, 401]

    def test_unauthenticated_single_resource(self, client):
        response = client.get('/api/v1/queue/Derecho')
        assert response.status_code in [302, 401]


class TestQueueCacheRefresh:
    """Test POST /api/v1/queue/refresh."""

    def test_refresh_returns_200(self, auth_client):
        response = auth_client.post('/api/v1/queue/refresh')
        assert response.status_code == 200

    def test_refresh_returns_status_ok(self, auth_client):
        data = auth_client.post('/api/v1/queue/refresh').get_json()
        assert data == {'status': 'ok'}
