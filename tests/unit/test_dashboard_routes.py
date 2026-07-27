"""Routing tests for the routable user/admin dashboard pages.

The user and admin dashboards were split from single tabbed pages into
routable pages: the bare section URLs (``/user/``, ``/admin/``) 302 to
their default page (``/user/accounts``, ``/admin/projects``), and each
former tab is a page of its own. These tests pin the redirect targets,
the auth/permission gates, and a render smoke for each page.
"""
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# User dashboard
# ---------------------------------------------------------------------------

class TestUserDashboardRouting:

    def test_bare_url_redirects_to_accounts(self, auth_client):
        resp = auth_client.get('/user/')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/user/accounts')

    def test_accounts_page_renders(self, auth_client):
        assert auth_client.get('/user/accounts').status_code == 200

    def test_info_page_renders(self, auth_client):
        assert auth_client.get('/user/info').status_code == 200


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

ADMIN_PAGES = [
    '/admin/projects',
    '/admin/projects/directories',
    '/admin/users-groups',
    '/admin/resources',
    '/admin/organizations',
    '/admin/facilities',
]


class TestAdminDashboardRouting:

    def test_bare_url_redirects_to_projects(self, auth_client):
        resp = auth_client.get('/admin/')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/admin/projects')

    def test_bare_url_redirect_preserves_projcode(self, auth_client):
        """?projcode= re-hydration back-links must survive the redirect."""
        resp = auth_client.get('/admin/?projcode=SCSG0001')
        assert resp.status_code == 302
        location = resp.headers['Location']
        assert '/admin/projects' in location
        assert 'projcode=SCSG0001' in location

    @pytest.mark.parametrize('url', ADMIN_PAGES)
    def test_pages_render_for_admin(self, auth_client, url):
        assert auth_client.get(url).status_code == 200

    @pytest.mark.parametrize('url', ['/admin/'] + ADMIN_PAGES)
    def test_pages_403_for_non_admin(self, non_admin_client, url):
        """require_permission_any_facility aborts 403 for authenticated
        users with no ACCESS_ADMIN_DASHBOARD grant anywhere."""
        assert non_admin_client.get(url).status_code == 403


class TestAdminConfigurationPage:
    """/admin/configuration adds @require_permission(VIEW_SYSTEM_CONFIG)
    on top of the shared ACCESS_ADMIN_DASHBOARD gate."""

    def test_renders_for_full_admin(self, auth_client):
        assert auth_client.get('/admin/configuration').status_code == 200

    def test_403_without_view_system_config(self, auth_client, monkeypatch):
        """A facility-scoped admin (ACCESS_ADMIN_DASHBOARD in one facility,
        no system VIEW_SYSTEM_CONFIG) reaches /admin/projects but not
        /admin/configuration. Re-casts the benkirk login the same way
        test_allocations_performance.py::TestAllocationsDashboardFacilityScope
        does — drop the blanket override, grant a sureshm-shaped scope."""
        from webapp.utils import rbac
        from webapp.utils.rbac import Permission

        monkeypatch.setattr(rbac, 'USER_PERMISSION_OVERRIDES', {})
        monkeypatch.setattr(rbac, 'GROUP_PERMISSIONS', {})
        monkeypatch.setattr(rbac, 'USER_FACILITY_PERMISSIONS', {
            'benkirk': {
                'WNA': {
                    Permission.ACCESS_ADMIN_DASHBOARD,
                    Permission.VIEW_PROJECTS,
                },
            },
        })
        assert auth_client.get('/admin/projects').status_code == 200
        assert auth_client.get('/admin/configuration').status_code == 403


class TestProjectCardAutoLoad:
    """``?projcode=`` back-links auto-load the card and flag it for a
    one-shot scroll into view (``data-reveal-on-load``, consumed by
    dashboard-init.js). Without the parameter the container stays inert —
    otherwise every in-place card reload would yank the page to its top."""

    def test_flag_present_with_projcode(self, auth_client, active_project):
        html = auth_client.get(
            f'/admin/projects?projcode={active_project.projcode}'
        ).get_data(as_text=True)
        assert 'data-reveal-on-load' in html

    def test_flag_absent_without_projcode(self, auth_client):
        html = auth_client.get('/admin/projects').get_data(as_text=True)
        assert 'data-reveal-on-load' not in html
