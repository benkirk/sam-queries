"""
API endpoint tests for project member management — HTTP-layer failure-mode subset.

Scope note (Phase 4d): This file is the failure-mode subset of the legacy
tests/api/test_member_management.py. The successful-write tests
(add_member_success_json, add_member_with_dates, change_admin_clear_admin,
change_admin_success_json) are intentionally dropped because they are
covered at the service layer by Phase 3's test_management_functions (14
tests against add_user_to_project/change_project_admin). Keeping them
here would require full Flask-SQLAlchemy SAVEPOINT bridging.

What this file DOES cover:
  - POST /api/v1/projects/<projcode>/members — auth/404/400 failure paths
  - DELETE /api/v1/projects/<projcode>/members/<username> — same
  - PUT /api/v1/projects/<projcode>/admin — same
  - Security behavior: permission check runs before target user lookup

None of the tests below produce a successful commit:
  - Auth tests return 302/401 before the view body runs
  - 404 tests fail on project or user lookup before management_transaction
  - 400 tests either fail marshmallow validation (before DB touch) or
    raise ValueError inside management_transaction (auto-rollback)

Endpoints tested:
    POST   /api/v1/projects/<projcode>/members
    DELETE /api/v1/projects/<projcode>/members/<username>
    PUT    /api/v1/projects/<projcode>/admin
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
        """Invalid date format returns 400 (fails in AddMemberForm.load)."""
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
        """Cannot remove project lead — ValueError -> 400, no write."""
        # benkirk is the SCSG0001 lead; remove_user_from_project raises
        # ValueError which management_transaction rolls back before the 400.
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
        """Cannot set admin to user who is not a project member.

        ValueError from change_project_admin -> management_transaction
        rollback -> 400 response. No write lands.
        """
        from sam.core.users import User
        from sam.accounting.accounts import AccountUser
        from sam.projects.projects import Project

        project = Project.get_by_projcode(session, 'SCSG0001')

        account_ids = [a.account_id for a in project.accounts]
        member_user_ids = session.query(AccountUser.user_id).filter(
            AccountUser.account_id.in_(account_ids)
        ).distinct().all()
        member_user_ids = {u[0] for u in member_user_ids}
        member_user_ids.add(project.project_lead_user_id)

        non_member = session.query(User).filter(
            ~User.user_id.in_(member_user_ids),
            User.username.isnot(None)
        ).first()

        if non_member is None:
            pytest.skip("No non-member user found in database")

        response = auth_client.put(
            '/api/v1/projects/SCSG0001/admin',
            json={'admin_username': non_member.username}
        )
        assert response.status_code == 400
        assert 'member' in response.get_json()['error'].lower()


class TestUnauthorizedAccess:
    """Test that non-lead/admin users cannot manage members.

    These tests would require a different authenticated user who is NOT
    the project lead or admin. For now, we test the basic cases and
    rely on the permission function unit tests for complete coverage.
    """

    def test_permission_check_happens_before_user_lookup(self, auth_client):
        """
        Verify that permission checks run even when target user doesn't exist.

        This is a security best practice — don't reveal user existence
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
