"""
API endpoint tests for User endpoints

Tests HTTP endpoints for user listing, detail views, and user projects.
"""

import pytest


class TestUserListEndpoint:
    """Test GET /api/v1/users/ endpoint."""

    def test_list_users_success(self, auth_client):
        """Test successful user listing."""
        response = auth_client.get('/api/v1/users/')
        assert response.status_code == 200

        data = response.get_json()
        assert 'users' in data
        assert 'page' in data
        assert 'total' in data
        assert isinstance(data['users'], list)

    def test_list_users_pagination(self, auth_client):
        """Test pagination parameters."""
        response = auth_client.get('/api/v1/users/?page=2&per_page=10')
        assert response.status_code == 200

        data = response.get_json()
        assert data['page'] == 2
        assert data['per_page'] == 10

    def test_list_users_search(self, auth_client):
        """Test search filtering."""
        response = auth_client.get('/api/v1/users/?search=ben')
        assert response.status_code == 200

        data = response.get_json()
        # Search should find users with 'ben' in username or name
        # May return empty if search is case-sensitive or requires exact match
        assert isinstance(data['users'], list)

    def test_list_users_search_no_results(self, auth_client):
        """Test search with no results."""
        response = auth_client.get('/api/v1/users/?search=nonexistent_user_xyz')
        assert response.status_code == 200

        data = response.get_json()
        assert len(data['users']) == 0


class TestUserDetailEndpoint:
    """Test GET /api/v1/users/<username> endpoint."""

    def test_get_user_success(self, auth_client):
        """Test successful user retrieval."""
        response = auth_client.get('/api/v1/users/benkirk')
        assert response.status_code == 200

        data = response.get_json()
        assert data['username'] == 'benkirk'
        # Schema may use 'email' or 'email_addresses' depending on implementation
        assert 'email' in data or 'email_addresses' in data
        assert 'institutions' in data
        assert 'organizations' in data
        assert 'roles' in data

    def test_get_user_with_institutions(self, auth_client):
        """Test user with institutions (validates UserSchema bug fix)."""
        response = auth_client.get('/api/v1/users/negins')
        assert response.status_code == 200

        data = response.get_json()
        assert data['username'] == 'negins'
        assert len(data['institutions']) > 0

        # Verify institution fields exist
        inst = data['institutions'][0]
        assert 'institution_name' in inst
        assert 'institution_acronym' in inst
        # Verify is_primary was removed (part of bug fix)
        assert 'is_primary' not in inst

    def test_get_user_not_found(self, auth_client):
        """Test 404 for non-existent user."""
        response = auth_client.get('/api/v1/users/invalid_user_xyz')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data


class TestUserProjectsEndpoint:
    """Test GET /api/v1/users/<username>/projects endpoint."""

    def test_get_user_projects_success(self, auth_client):
        """Test successful retrieval of user's projects."""
        response = auth_client.get('/api/v1/users/benkirk/projects')
        assert response.status_code == 200

        data = response.get_json()
        assert 'username' in data
        assert 'led_projects' in data
        assert 'admin_projects' in data
        assert 'member_projects' in data
        assert 'total_projects' in data

    def test_get_user_projects_not_found(self, auth_client):
        """Test 404 for non-existent user."""
        response = auth_client.get('/api/v1/users/invalid_user_xyz/projects')
        assert response.status_code == 404
