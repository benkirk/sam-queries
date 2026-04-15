"""
API endpoint tests for Project Access endpoints.

Tests the /api/v1/project_access/ endpoints which provide project group
status data per access branch for LDAP provisioning systems.
"""

import pytest


class TestProjectAccessAllBranches:
    """Test GET /api/v1/project_access/."""

    def test_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/project_access/')
        assert response.status_code == 200

    def test_response_is_dict(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        assert isinstance(data, dict)

    def test_at_least_one_branch(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        assert len(data) >= 1, 'Expected at least one access branch in response'

    def test_each_branch_is_a_list(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        for branch_name, projects in data.items():
            assert isinstance(projects, list), \
                f'Branch {branch_name!r} value should be a list'

    def test_project_has_required_fields(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        required = {'groupName', 'panel', 'autoRenewing', 'projectActive',
                    'status', 'expiration', 'resourceGroupStatuses'}
        for branch_name, projects in data.items():
            for proj in projects[:3]:  # spot-check
                missing = required - proj.keys()
                assert not missing, \
                    f'Branch {branch_name!r}, project {proj.get("groupName")!r} missing: {missing}'

    def test_resource_group_status_fields(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        for branch_name, projects in data.items():
            for proj in projects[:3]:
                for rgs in proj['resourceGroupStatuses']:
                    assert 'resourceName' in rgs
                    assert 'endDate' in rgs

    def test_auto_renewing_always_false(self, auth_client):
        data = auth_client.get('/api/v1/project_access/').get_json()
        for branch_name, projects in data.items():
            for proj in projects[:10]:
                assert proj['autoRenewing'] is False


class TestProjectAccessSingleBranch:
    """Test GET /api/v1/project_access/<branch>."""

    def test_hpc_branch_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/project_access/hpc')
        assert response.status_code == 200

    def test_single_branch_response_keyed_by_branch(self, auth_client):
        data = auth_client.get('/api/v1/project_access/hpc').get_json()
        assert 'hpc' in data
        assert len(data) == 1, 'Single-branch response should have exactly one key'

    def test_single_branch_contains_projects(self, auth_client):
        data = auth_client.get('/api/v1/project_access/hpc').get_json()
        assert isinstance(data['hpc'], list)
        assert len(data['hpc']) >= 1

    def test_unknown_branch_returns_404(self, auth_client):
        response = auth_client.get('/api/v1/project_access/nonexistent-branch')
        assert response.status_code == 404


class TestProjectAccessAuth:
    """Test authentication and authorization."""

    def test_unauthenticated_all_branches(self, client):
        response = client.get('/api/v1/project_access/')
        assert response.status_code in [302, 401], \
            'Unauthenticated request should be redirected or denied'

    def test_unauthenticated_single_branch(self, client):
        response = client.get('/api/v1/project_access/hpc')
        assert response.status_code in [302, 401], \
            'Unauthenticated request should be redirected or denied'


class TestProjectAccessCacheRefresh:
    """Test POST /api/v1/project_access/refresh."""

    def test_refresh_returns_200(self, auth_client):
        response = auth_client.post('/api/v1/project_access/refresh')
        assert response.status_code == 200

    def test_refresh_returns_status_ok(self, auth_client):
        data = auth_client.post('/api/v1/project_access/refresh').get_json()
        assert data == {'status': 'ok'}
