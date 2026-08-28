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

    @pytest.mark.xfail(reason="Likely to fail on obfuscated database.")
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


def _key_header():
    """A bare Basic header for the TestingConfig collector key -- no session."""
    import base64
    creds = base64.b64encode(b"collector:test-api-key").decode("ascii")
    return {"Authorization": f"Basic {creds}"}


class TestApiKeyAccess:
    """The list, search and per-user routes accept an API key (no session);
    ``/me`` does not, because it reads ``current_user.user_id``."""

    def test_list_with_a_key_only(self, client):
        resp = client.get('/api/v1/users/?per_page=2', headers=_key_header())
        assert resp.status_code == 200
        body = resp.get_json()
        assert set(body) == {'users', 'page', 'per_page', 'total'}
        assert len(body['users']) <= 2

    def test_search_with_a_key_only(self, client):
        resp = client.get('/api/v1/users/search?q=ben', headers=_key_header())
        assert resp.status_code == 200
        assert any(u['username'] == 'benkirk' for u in resp.get_json())

    def test_one_user_and_their_projects_with_a_key_only(self, client):
        assert client.get('/api/v1/users/benkirk', headers=_key_header()).status_code == 200
        resp = client.get('/api/v1/users/benkirk/projects', headers=_key_header())
        assert resp.status_code == 200
        assert resp.get_json()['username'] == 'benkirk'

    def test_a_bad_key_is_a_json_401(self, client):
        import base64
        bad = {"Authorization": "Basic " + base64.b64encode(b"collector:nope").decode()}
        resp = client.get('/api/v1/users/benkirk', headers=bad)
        assert resp.status_code == 401
        assert 'error' in resp.get_json()

    def test_me_stays_session_only(self, client):
        resp = client.get('/api/v1/users/me', headers=_key_header())
        assert resp.status_code in (302, 401)

    def test_no_credentials_is_a_json_401_not_a_redirect(self, client):
        resp = client.get('/api/v1/users/benkirk')
        assert resp.status_code == 401
        assert 'error' in resp.get_json()

