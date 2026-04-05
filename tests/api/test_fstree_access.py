"""
API endpoint tests for FairShare Tree endpoints.

Tests the /api/v1/fstree_access/ endpoints which provide PBS fairshare
tree data for batch scheduler and LDAP tooling.
"""

import pytest


class TestFstreeAllResources:
    """Test GET /api/v1/fstree_access/."""

    def test_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/fstree_access/')
        assert response.status_code == 200

    def test_response_has_name_and_facilities(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/').get_json()
        assert 'name' in data
        assert 'facilities' in data

    def test_name_is_fairShareTree(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/').get_json()
        assert data['name'] == 'fairShareTree'

    def test_facilities_is_list(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/').get_json()
        assert isinstance(data['facilities'], list)

    def test_at_least_one_facility(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/').get_json()
        assert len(data['facilities']) >= 1

    def test_facility_has_allocationTypes(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/').get_json()
        for fac in data['facilities'][:2]:
            assert 'allocationTypes' in fac, \
                f'Facility {fac.get("name")!r} missing allocationTypes'


class TestFstreeSingleResource:
    """Test GET /api/v1/fstree_access/<resource_name>."""

    def test_derecho_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/fstree_access/Derecho')
        assert response.status_code == 200

    def test_derecho_response_has_fairShareTree_name(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/Derecho').get_json()
        assert data['name'] == 'fairShareTree'

    def test_derecho_resources_only_contain_derecho(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/Derecho').get_json()
        for fac in data['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        assert res['name'] == 'Derecho', \
                            f'Expected only Derecho, got {res["name"]!r}'

    def test_unknown_resource_returns_404(self, auth_client):
        response = auth_client.get('/api/v1/fstree_access/NonexistentResource99')
        assert response.status_code == 404

    def test_resource_entry_has_required_fields(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/Derecho').get_json()
        required = {'name', 'accountStatus', 'cutoffThreshold',
                    'adjustedUsage', 'balance', 'allocationAmount', 'users'}
        for fac in data['facilities'][:1]:
            for at in fac['allocationTypes'][:1]:
                for proj in at['projects'][:3]:
                    for res in proj['resources']:
                        missing = required - res.keys()
                        assert not missing, \
                            f'Resource {res.get("name")!r} missing: {missing}'

    def test_account_status_is_valid(self, auth_client):
        data = auth_client.get('/api/v1/fstree_access/Derecho').get_json()
        valid = {'Normal', 'Overspent', 'Exceed One Threshold', 'Exceed Two Thresholds'}
        for fac in data['facilities'][:2]:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    for res in proj['resources']:
                        assert res['accountStatus'] in valid, \
                            f'accountStatus {res["accountStatus"]!r} is not valid'


class TestFstreeAuth:
    """Test authentication and authorization."""

    def test_unauthenticated_all_resources(self, client):
        response = client.get('/api/v1/fstree_access/')
        assert response.status_code in [302, 401], \
            'Unauthenticated request should be redirected or denied'

    def test_unauthenticated_single_resource(self, client):
        response = client.get('/api/v1/fstree_access/Derecho')
        assert response.status_code in [302, 401], \
            'Unauthenticated request should be redirected or denied'


class TestFstreeCacheRefresh:
    """Test POST /api/v1/fstree_access/refresh."""

    def test_refresh_returns_200(self, auth_client):
        response = auth_client.post('/api/v1/fstree_access/refresh')
        assert response.status_code == 200

    def test_refresh_returns_status_ok(self, auth_client):
        data = auth_client.post('/api/v1/fstree_access/refresh').get_json()
        assert data == {'status': 'ok'}
