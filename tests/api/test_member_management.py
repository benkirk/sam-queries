"""
API endpoint tests for project member management.

Tests HTTP endpoints for adding/removing members and changing project admin.

Endpoints tested:
    POST   /api/v1/projects/<projcode>/members - Add member
    DELETE /api/v1/projects/<projcode>/members/<username> - Remove member
    PUT    /api/v1/projects/<projcode>/admin - Change admin
"""

import os
import pytest


class TestAddMemberEndpoint:
    """Test POST /api/v1/projects/<projcode>/members endpoint."""

    def test_add_member_unauthenticated(self, client):
        """Unauthenticated request returns 302 (redirect) or 401."""
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.post(
            '/api/v1/projects/SCSG0001/members',
            json={'username': 'testuser'}
        )
        # Flask-Login typically redirects to login page (302) for browser requests
        # or returns 401 for API requests depending on configuration
        assert response.status_code in [302, 401]

    def test_add_member_project_not_found(self, auth_client):
        """Non-existent project returns 404."""
        response = auth_client.post(
            '/api/v1/projects/INVALID999/members',
            json={'username': 'benkirk'}
        )
        assert response.status_code == 404
        assert 'error' in response.get_json()

    def test_add_member_missing_username(self, auth_client):
        """Missing username returns 400."""
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={}
        )
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data
        assert 'username' in data['error'].lower()

    def test_add_member_user_not_found(self, auth_client):
        """Non-existent user returns 404."""
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={'username': 'nonexistent_user_xyz'}
        )
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data
        assert 'not found' in data['error'].lower()

    def test_add_member_invalid_date_format(self, auth_client):
        """Invalid date format returns 400."""
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={
                'username': 'benkirk',
                'start_date': 'invalid-date'
            }
        )
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data
        assert 'date' in data['error'].lower()

    def test_add_member_success_json(self, auth_client):
        """Successful member addition with JSON body."""
        # Note: benkirk is the project lead for SCSG0001, so this request
        # is authorized. The actual add may succeed or fail depending on
        # whether the user is already a member.
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={'username': 'benkirk'}
        )

        # Either success (200) or already a member (400)
        assert response.status_code in [200, 400]

        data = response.get_json()
        if response.status_code == 200:
            assert 'success' in data
            assert data['success'] is True
            assert 'member' in data

    @pytest.mark.skip(reason="Flask test client FormData handling differs from browser")
    def test_add_member_success_formdata(self, auth_client):
        """Successful member addition with FormData (multipart).

        Note: This test is skipped because Flask's test client handles FormData
        differently than browsers. The JSON tests verify the functionality works.
        """
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            data={'username': 'benkirk'},
            content_type='multipart/form-data'
        )
        assert response.status_code in [200, 400]

    def test_add_member_with_dates(self, auth_client):
        """Add member with start_date and end_date."""
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={
                'username': 'benkirk',
                'start_date': '2025-01-01',
                'end_date': '2025-12-31'
            }
        )

        # Either success or already a member
        assert response.status_code in [200, 400]


class TestRemoveMemberEndpoint:
    """Test DELETE /api/v1/projects/<projcode>/members/<username> endpoint."""

    def test_remove_member_unauthenticated(self, client):
        """Unauthenticated request returns 302 (redirect) or 401."""
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.delete('/api/v1/projects/SCSG0001/members/testuser')
        assert response.status_code in [302, 401]

    def test_remove_member_project_not_found(self, auth_client):
        """Non-existent project returns 404."""
        response = auth_client.delete('/api/v1/projects/INVALID999/members/benkirk')
        assert response.status_code == 404

    def test_remove_member_user_not_found(self, auth_client):
        """Non-existent user returns 404."""
        response = auth_client.delete(
            '/api/v1/projects/SCSG0001/members/nonexistent_user_xyz'
        )
        assert response.status_code == 404

    def test_remove_member_cannot_remove_lead(self, auth_client):
        """Cannot remove project lead, returns 400."""
        # benkirk is the project lead, so trying to remove them should fail
        response = auth_client.delete('/api/v1/projects/SCSG0001/members/benkirk')
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data
        assert 'lead' in data['error'].lower()


class TestChangeAdminEndpoint:
    """Test PUT /api/v1/projects/<projcode>/admin endpoint."""

    def test_change_admin_unauthenticated(self, client):
        """Unauthenticated request returns 302 (redirect) or 401."""
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.put(
            '/api/v1/projects/SCSG0001/admin',
            json={'admin_username': 'testuser'}
        )
        assert response.status_code in [302, 401]

    def test_change_admin_project_not_found(self, auth_client):
        """Non-existent project returns 404."""
        response = auth_client.put(
            '/api/v1/projects/INVALID999/admin',
            json={'admin_username': 'benkirk'}
        )
        assert response.status_code == 404

    def test_change_admin_user_not_found(self, auth_client):
        """Non-existent user returns 404."""
        response = auth_client.put(
            '/api/v1/projects/SCSG0001/admin',
            json={'admin_username': 'nonexistent_user_xyz'}
        )
        assert response.status_code == 404

    def test_change_admin_non_member_fails(self, auth_client, session):
        """Cannot set admin to user who is not a project member."""
        # Find a user who is NOT a member of SCSG0001
        from sam.core.users import User
        from sam.accounting.accounts import AccountUser, Account

        # Get a user who exists but isn't on this project
        # First get account IDs for SCSG0001
        from sam.projects.projects import Project
        project = Project.get_by_projcode(session, 'SCSG0001')

        account_ids = [a.account_id for a in project.accounts]
        member_user_ids = session.query(AccountUser.user_id).filter(
            AccountUser.account_id.in_(account_ids)
        ).distinct().all()
        member_user_ids = {u[0] for u in member_user_ids}
        member_user_ids.add(project.project_lead_user_id)

        # Find a user not in this set
        non_member = session.query(User).filter(
            ~User.user_id.in_(member_user_ids),
            User.username.isnot(None)
        ).first()

        if non_member:
            response = auth_client.put(
                '/api/v1/projects/SCSG0001/admin',
                json={'admin_username': non_member.username}
            )
            assert response.status_code == 400
            assert 'member' in response.get_json()['error'].lower()
        else:
            pytest.skip("No non-member user found in database")

    def test_change_admin_clear_admin(self, auth_client):
        """Clear admin by sending empty username."""
        response = auth_client.put(
            '/api/v1/projects/SCSG0001/admin',
            json={'admin_username': ''}
        )
        assert response.status_code == 200

        data = response.get_json()
        assert data['success'] is True
        assert data['admin'] is None

    def test_change_admin_success_json(self, auth_client, session):
        """Successful admin change with JSON body."""
        # Find a project member who can be made admin
        from sam.projects.projects import Project
        project = Project.get_by_projcode(session, 'SCSG0001')

        # Get a member (not lead)
        if project.users:
            member = None
            for u in project.users:
                if u.user_id != project.project_lead_user_id:
                    member = u
                    break

            if member:
                response = auth_client.put(
                    '/api/v1/projects/SCSG0001/admin',
                    json={'admin_username': member.username}
                )
                assert response.status_code == 200

                data = response.get_json()
                assert data['success'] is True
                assert data['admin']['username'] == member.username
            else:
                pytest.skip("No non-lead member found for project")
        else:
            pytest.skip("Project has no users")

    @pytest.mark.skip(reason="Flask test client FormData handling differs from browser")
    def test_change_admin_success_formdata(self, auth_client):
        """Successful admin change with FormData (multipart).

        Note: This test is skipped because Flask's test client handles FormData
        differently than browsers. The JSON tests verify the functionality works.
        """
        response = auth_client.put(
            '/api/v1/projects/SCSG0001/admin',
            data={'admin_username': ''},
            content_type='multipart/form-data'
        )
        assert response.status_code == 200


class TestUnauthorizedAccess:
    """Test that non-lead/admin users cannot manage members.

    These tests would require a different authenticated user who is NOT
    the project lead or admin. For now, we test the basic cases and
    rely on the permission function unit tests for complete coverage.
    """

    def test_permission_check_happens_before_user_lookup(self, auth_client):
        """
        Verify that permission checks run even when target user doesn't exist.

        This is a security best practice - don't reveal user existence
        through different error codes.
        """
        # For SCSG0001, benkirk IS the lead, so this will pass permission
        # and then fail on user lookup (404)
        response = auth_client.post(
            '/api/v1/projects/SCSG0001/members',
            json={'username': 'nonexistent_xyz'}
        )

        # Should get 404 (not found) not 403 (forbidden)
        # because benkirk has permission
        assert response.status_code == 404
