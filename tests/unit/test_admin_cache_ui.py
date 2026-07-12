"""
Tests for the Admin > Configuration Caching-card "Clear…" HTMX route.

POST /admin/htmx/cache/clear re-renders just the caching card body with a
"cleared" summary alert. Gated on Permission.SYSTEM_ADMIN — one tier above
the read-only Configuration card (VIEW_SYSTEM_CONFIG).
"""

from unittest.mock import patch


class TestClearCacheRouteAuth:

    def test_unauthenticated_rejected(self, client):
        response = client.post('/admin/htmx/cache/clear')
        assert response.status_code in (302, 401)

    def test_non_admin_forbidden(self, non_admin_client):
        response = non_admin_client.post('/admin/htmx/cache/clear')
        assert response.status_code == 403

    def test_system_admin_allowed(self, auth_client):
        response = auth_client.post('/admin/htmx/cache/clear')
        assert response.status_code == 200


class TestClearCacheRouteBehavior:

    def test_renders_cleared_alert(self, auth_client):
        html = auth_client.post('/admin/htmx/cache/clear').get_data(as_text=True)
        assert 'Cleared:' in html

    def test_clear_all_calls_facade_with_none(self, auth_client):
        with patch('webapp.dashboards.admin.configuration_routes.caching.clear',
                   return_value={'flask': 0}) as mock_clear:
            resp = auth_client.post('/admin/htmx/cache/clear')
        assert resp.status_code == 200
        mock_clear.assert_called_once_with(None)

    def test_category_passed_through_to_facade(self, auth_client):
        with patch('webapp.dashboards.admin.configuration_routes.caching.clear',
                   return_value={'chart': 3}) as mock_clear:
            resp = auth_client.post('/admin/htmx/cache/clear?category=chart')
        assert resp.status_code == 200
        mock_clear.assert_called_once_with('chart')

    def test_bad_category_falls_back_to_clear_all(self, auth_client):
        with patch('webapp.dashboards.admin.configuration_routes.caching.clear',
                   return_value={'flask': 0}) as mock_clear:
            resp = auth_client.post('/admin/htmx/cache/clear?category=bogus')
        assert resp.status_code == 200
        mock_clear.assert_called_once_with(None)
