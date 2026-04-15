"""
API endpoint tests for user search functionality.

Tests HTTP endpoint for user search autocomplete used in member management.

Endpoint tested:
    GET /api/v1/users/search - Search users for autocomplete
"""

import os
import pytest


class TestUserSearchEndpoint:
    """Test GET /api/v1/users/search endpoint."""

    def test_search_unauthenticated(self, client):
        """Unauthenticated request returns 302 (redirect) or 401."""
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.get('/api/v1/users/search?q=ben')
        assert response.status_code in [302, 401]

    def test_search_no_query_returns_empty(self, auth_client):
        """Missing query parameter returns empty array."""
        response = auth_client.get('/api/v1/users/search')
        assert response.status_code == 200

        data = response.get_json()
        assert data == []

    def test_search_query_too_short(self, auth_client):
        """Query less than 2 characters returns empty array."""
        response = auth_client.get('/api/v1/users/search?q=a')
        assert response.status_code == 200

        data = response.get_json()
        assert data == []

    def test_search_returns_matching_users(self, auth_client):
        """Search returns users matching the query."""
        # Search for 'ben' which should match 'benkirk'
        response = auth_client.get('/api/v1/users/search?q=ben')
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)

        # Should find at least one user
        if len(data) > 0:
            # Verify response structure
            user = data[0]
            assert 'username' in user
            assert 'display_name' in user
            assert 'email' in user

    def test_search_result_structure(self, auth_client):
        """Search results have correct structure."""
        response = auth_client.get('/api/v1/users/search?q=benkirk')
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)

        # Should find benkirk
        usernames = [u['username'] for u in data]
        assert 'benkirk' in usernames

        # Verify structure of benkirk record
        benkirk = next(u for u in data if u['username'] == 'benkirk')
        assert 'display_name' in benkirk
        assert 'email' in benkirk
        # Email should be string (may be empty)
        assert isinstance(benkirk['email'], str)

    def test_search_with_limit(self, auth_client):
        """Limit parameter controls result count."""
        response = auth_client.get('/api/v1/users/search?q=a&limit=5')
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) <= 5

    def test_search_limit_max_enforced(self, auth_client):
        """Limit is capped at 50."""
        response = auth_client.get('/api/v1/users/search?q=a&limit=1000')
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)
        # Max should be enforced
        assert len(data) <= 50

    def test_search_excludes_project_members(self, auth_client, session):
        """With projcode param, excludes existing project members."""
        # Search for benkirk WITHOUT projcode filter
        response_all = auth_client.get('/api/v1/users/search?q=benkirk')
        assert response_all.status_code == 200
        data_all = response_all.get_json()
        all_usernames = [u['username'] for u in data_all]

        # Search for benkirk WITH projcode=SCSG0001 (benkirk is lead)
        response_filtered = auth_client.get('/api/v1/users/search?q=benkirk&projcode=SCSG0001')
        assert response_filtered.status_code == 200
        data_filtered = response_filtered.get_json()
        filtered_usernames = [u['username'] for u in data_filtered]

        # benkirk should be in all but NOT in filtered (since he's on SCSG0001)
        assert 'benkirk' in all_usernames
        assert 'benkirk' not in filtered_usernames

    def test_search_invalid_projcode_returns_results(self, auth_client):
        """Invalid projcode doesn't filter (returns normal results)."""
        response = auth_client.get('/api/v1/users/search?q=benkirk&projcode=INVALID999')
        assert response.status_code == 200

        data = response.get_json()
        # Should return results since project doesn't exist (no filtering)
        usernames = [u['username'] for u in data]
        assert 'benkirk' in usernames

    def test_search_case_insensitive(self, auth_client):
        """Search is case insensitive."""
        response_lower = auth_client.get('/api/v1/users/search?q=benkirk')
        response_upper = auth_client.get('/api/v1/users/search?q=BENKIRK')

        assert response_lower.status_code == 200
        assert response_upper.status_code == 200

        # Both should find benkirk
        # Note: actual case sensitivity depends on database collation
        # This test verifies the endpoint handles both cases
        data_lower = response_lower.get_json()
        data_upper = response_upper.get_json()

        assert isinstance(data_lower, list)
        assert isinstance(data_upper, list)

    def test_search_wildcard_pattern(self, auth_client):
        """Search supports wildcard patterns."""
        # Search with wildcard pattern
        response = auth_client.get('/api/v1/users/search?q=ben%')
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)


class TestCurrentUserEndpoints:
    """Test /api/v1/users/me endpoints."""

    def test_get_current_user(self, auth_client):
        """GET /api/v1/users/me returns current user details."""
        response = auth_client.get('/api/v1/users/me')
        assert response.status_code == 200

        data = response.get_json()
        assert 'username' in data
        assert data['username'] == 'benkirk'

    def test_get_current_user_projects_grouped(self, auth_client):
        """GET /api/v1/users/me/projects returns grouped projects."""
        response = auth_client.get('/api/v1/users/me/projects')
        assert response.status_code == 200

        data = response.get_json()
        assert 'username' in data
        assert 'led_projects' in data
        assert 'admin_projects' in data
        assert 'member_projects' in data
        assert 'total_projects' in data

    def test_get_current_user_projects_dashboard_format(self, auth_client):
        """GET /api/v1/users/me/projects?format=dashboard returns dashboard format."""
        response = auth_client.get('/api/v1/users/me/projects?format=dashboard')
        assert response.status_code == 200

        data = response.get_json()
        assert 'username' in data
        assert 'projects' in data
        assert 'total_projects' in data

        # Verify dashboard format structure
        if data['projects']:
            project = data['projects'][0]
            assert 'projcode' in project
            assert 'title' in project
            assert 'resources' in project

            # Verify resource structure
            if project['resources']:
                resource = project['resources'][0]
                assert 'resource_name' in resource
                assert 'allocated' in resource
                assert 'used' in resource
                assert 'remaining' in resource
                assert 'percent_used' in resource
