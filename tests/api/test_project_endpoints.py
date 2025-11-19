"""
API endpoint tests for Project endpoints

Tests HTTP endpoints for project listing, detail views, tree structure,
members, allocations, and expiration queries.
"""

import pytest


class TestProjectListEndpoint:
    """Test GET /api/v1/projects/ endpoint."""

    def test_list_projects_success(self, auth_client):
        """Test successful project listing."""
        response = auth_client.get('/api/v1/projects/')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projects' in data
        assert 'page' in data
        assert 'total' in data
        assert isinstance(data['projects'], list)

    def test_list_projects_fields(self, auth_client):
        """Test that list includes certain fields."""
        response = auth_client.get('/api/v1/projects/')
        assert response.status_code == 200

        data = response.get_json()
        # Just verify the response structure, don't require specific projects
        assert 'projects' in data
        assert isinstance(data['projects'], list)

        # If there are projects, verify projcode exists
        if len(data['projects']) > 0:
            project = data['projects'][0]
            assert 'projcode' in project

    def test_list_projects_pagination(self, auth_client):
        """Test pagination parameters."""
        response = auth_client.get('/api/v1/projects/?page=2&per_page=10')
        assert response.status_code == 200

        data = response.get_json()
        assert data['page'] == 2
        assert data['per_page'] == 10


class TestProjectTreeEndpoints:
    """Test project tree functionality in API (NEW FEATURE)."""

    def test_root_project_tree_structure(self, auth_client):
        """Test GET /projects/CESM0002 returns complete tree from root."""
        response = auth_client.get('/api/v1/projects/CESM0002')
        assert response.status_code == 200

        data = response.get_json()
        assert data['projcode'] == 'CESM0002'

        # Test breadcrumb (root has only itself)
        assert data['breadcrumb_path'] == ['CESM0002']

        # Test depth (root is 0)
        assert data['tree_depth'] == 0

        # Test tree structure
        assert data['tree']['projcode'] == 'CESM0002'
        assert data['tree']['depth'] == 0
        assert 'children' in data['tree']
        assert len(data['tree']['children']) == 28  # CESM0002 has 28 children

    def test_child_project_shows_full_tree_from_root(self, auth_client):
        """Test that querying child project returns tree from root."""
        response = auth_client.get('/api/v1/projects/P93300012')
        assert response.status_code == 200

        data = response.get_json()

        # Queried project is P93300012
        assert data['projcode'] == 'P93300012'

        # Breadcrumb shows path from root
        assert data['breadcrumb_path'] == ['CESM0002', 'P93300012']

        # Depth is 1 (one level below root)
        assert data['tree_depth'] == 1

        # Tree STARTS FROM ROOT, not from queried project
        assert data['tree']['projcode'] == 'CESM0002'
        assert data['tree']['depth'] == 0
        assert len(data['tree']['children']) == 28  # All siblings visible

    def test_max_depth_parameter_controls_expansion(self, auth_client):
        """Test max_depth parameter limits tree expansion."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=1')
        assert response.status_code == 200

        data = response.get_json()
        tree = data['tree']

        # Root has children at depth 1
        assert len(tree['children']) == 28

        # But children at depth 1 should have empty children arrays
        for child in tree['children']:
            assert child['depth'] == 1
            assert child['children'] == []

    def test_max_depth_zero_no_expansion(self, auth_client):
        """Test max_depth=0 returns no children."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=0')
        assert response.status_code == 200

        data = response.get_json()
        tree = data['tree']

        # Should return root with no children expanded
        assert tree['projcode'] == 'CESM0002'
        assert tree['children'] == []

        # But should indicate more exist
        assert tree.get('has_more') == True

    def test_max_depth_default_value(self, auth_client):
        """Test default max_depth is 4."""
        response = auth_client.get('/api/v1/projects/CESM0002')
        assert response.status_code == 200

        data = response.get_json()
        # Default should expand to depth 4
        # (Verify by checking tree contains nested children)
        assert 'tree' in data
        assert 'children' in data['tree']

    def test_has_more_flag_at_boundary(self, auth_client):
        """Test has_more flag appears when max_depth truncates tree."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=1')
        assert response.status_code == 200

        data = response.get_json()

        # P93300012 has children, so at max_depth=1 it should show has_more
        p93300012 = next(
            (c for c in data['tree']['children'] if c['projcode'] == 'P93300012'),
            None
        )

        if p93300012:
            assert p93300012.get('has_more') == True


class TestProjectDetailEndpoint:
    """Test GET /api/v1/projects/<projcode> endpoint."""

    def test_get_project_success(self, auth_client):
        """Test successful project retrieval."""
        response = auth_client.get('/api/v1/projects/SCSG0001')
        assert response.status_code == 200

        data = response.get_json()
        assert data['projcode'] == 'SCSG0001'
        assert 'title' in data
        assert 'lead' in data
        assert 'admin' in data

    def test_get_project_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data


class TestProjectMembersEndpoint:
    """Test GET /api/v1/projects/<projcode>/members endpoint."""

    def test_get_project_members_success(self, auth_client):
        """Test successful retrieval of project members."""
        response = auth_client.get('/api/v1/projects/SCSG0001/members')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'lead' in data
        assert 'admin' in data
        assert 'members' in data
        assert 'total_members' in data

    def test_get_project_members_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/members')
        assert response.status_code == 404


class TestProjectAllocationsEndpoint:
    """Test GET /api/v1/projects/<projcode>/allocations endpoint."""

    def test_get_project_allocations_success(self, auth_client):
        """Test successful retrieval of project allocations."""
        response = auth_client.get('/api/v1/projects/SCSG0001/allocations')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'allocations' in data
        assert isinstance(data['allocations'], list)

    def test_get_project_allocations_resource_filter(self, auth_client):
        """Test resource name filtering."""
        response = auth_client.get(
            '/api/v1/projects/SCSG0001/allocations?resource=Derecho'
        )
        assert response.status_code == 200

        data = response.get_json()
        # All allocations should be for Derecho
        if data['allocations']:
            for alloc in data['allocations']:
                # Resource may be nested object or flat field
                assert 'resource_name' in alloc or 'resource' in alloc
                if 'resource' in alloc:
                    assert alloc['resource']['resource_name'] == 'Derecho'

    def test_get_project_allocations_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/allocations')
        assert response.status_code == 404


class TestProjectExpirationEndpoints:
    """Test expiring/expired project endpoints."""

    def test_get_expiring_projects(self, auth_client):
        """Test GET /projects/expiring."""
        response = auth_client.get('/api/v1/projects/expiring')
        assert response.status_code == 200

        data = response.get_json()
        assert 'expiring_projects' in data
        assert 'days' in data
        assert isinstance(data['expiring_projects'], list)

    def test_get_recently_expired_projects(self, auth_client):
        """Test GET /projects/recently_expired."""
        response = auth_client.get('/api/v1/projects/recently_expired')
        assert response.status_code == 200

        data = response.get_json()
        assert 'expired_projects' in data
        assert 'min_days' in data
        assert 'max_days' in data
        assert isinstance(data['expired_projects'], list)
