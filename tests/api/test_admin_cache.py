"""
API endpoint tests for the admin cache-refresh endpoint.

Tests POST /api/v1/admin/cache/refresh — the global cache-invalidation
entry point on top of the webapp.caching facade. Gated on
Permission.SYSTEM_ADMIN (session path); the Basic-auth token path is
exercised elsewhere and intentionally bypasses the permission gate.
"""

import pytest


class TestAdminCacheRefreshAuth:
    """Permission gating on the session path."""

    def test_unauthenticated_rejected(self, client):
        response = client.post('/api/v1/admin/cache/refresh')
        assert response.status_code in (302, 401)

    def test_non_admin_forbidden(self, non_admin_client):
        response = non_admin_client.post('/api/v1/admin/cache/refresh')
        assert response.status_code == 403

    def test_system_admin_allowed(self, auth_client):
        response = auth_client.post('/api/v1/admin/cache/refresh')
        assert response.status_code == 200


class TestAdminCacheRefreshBehavior:
    """Response shape + category scoping."""

    def test_clear_all_returns_status_ok(self, auth_client):
        data = auth_client.post('/api/v1/admin/cache/refresh').get_json()
        assert data['status'] == 'ok'
        assert isinstance(data['cleared'], dict)

    def test_clear_all_covers_every_category(self, auth_client):
        data = auth_client.post('/api/v1/admin/cache/refresh').get_json()
        assert set(data['cleared'].keys()) == {'flask', 'chart', 'usage',
                                               'scans', 'jobs'}

    @pytest.mark.parametrize('category',
                             ['flask', 'chart', 'usage', 'scans', 'jobs'])
    def test_single_category_scopes_the_clear(self, auth_client, category):
        data = auth_client.post(
            f'/api/v1/admin/cache/refresh?category={category}'
        ).get_json()
        assert data['status'] == 'ok'
        assert set(data['cleared'].keys()) == {category}

    def test_invalid_category_returns_400(self, auth_client):
        response = auth_client.post('/api/v1/admin/cache/refresh?category=bogus')
        assert response.status_code == 400
