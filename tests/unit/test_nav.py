"""Tests for the navigation registry (webapp/utils/nav.py) and the navbar
dropdown / mobile offcanvas markup it drives.

The registry is the single source of truth for the desktop navbar section
dropdowns, the mobile offcanvas menu, and breadcrumbs. Visibility is
permission-driven per request; ``nav_locate`` maps endpoints to their
section/item for breadcrumb derivation.
"""
import pytest

from webapp.utils.nav import NAV_SECTIONS, nav_locate

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# nav_locate (pure — no request context needed)
# ---------------------------------------------------------------------------

class TestNavLocate:

    def test_page_endpoint_locates_section_and_item(self):
        section, item = nav_locate('status_dashboard.casper')
        assert section['key'] == 'status'
        assert item['label'] == 'Casper'

    def test_detail_endpoint_locates_section_only(self):
        """A route inside a known blueprint but not in the registry (e.g. an
        admin detail card) resolves the section with no item."""
        section, item = nav_locate('admin_dashboard.user_card')
        assert section['key'] == 'admin'
        assert item is None

    def test_unknown_endpoint(self):
        assert nav_locate('auth.login') == (None, None)
        assert nav_locate(None) == (None, None)

    def test_registry_endpoints_exist(self, app):
        """Every registry endpoint must be a real route — catches drift when
        blueprints are renamed."""
        for s in NAV_SECTIONS:
            assert s['endpoint'] in app.view_functions
            for i in s['items']:
                assert i['endpoint'] in app.view_functions, i['endpoint']


# ---------------------------------------------------------------------------
# Rendered navbar markup (visibility resolution end-to-end)
# ---------------------------------------------------------------------------

class TestNavbarDropdowns:

    def test_admin_sees_all_sections(self, auth_client):
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        for href in ('/user/accounts', '/status/derecho',
                     '/allocations/projects', '/admin/projects'):
            assert f'href="{href}"' in html
        # Dropdown pages, including the permission-gated ones
        for href in ('/admin/configuration', '/status/filesystem-scans',
                     '/allocations/adjustments', '/admin/users-groups'):
            assert f'href="{href}"' in html
        # Split caret toggles render per section
        assert 'aria-label="Admin pages"' in html
        assert 'aria-label="System Status pages"' in html

    def test_non_admin_sections_hidden(self, non_admin_client):
        html = non_admin_client.get('/user/accounts').get_data(as_text=True)
        assert 'aria-label="Allocations pages"' not in html
        assert 'aria-label="Admin pages"' not in html
        assert 'href="/admin/projects"' not in html
        # Permission-gated status page hidden too
        assert 'href="/status/filesystem-scans"' not in html
        # But public/status and own-user sections remain
        assert 'aria-label="System Status pages"' in html
        assert 'href="/user/info"' in html

    def test_scoped_admin_lacks_configuration_item(self, auth_client, monkeypatch):
        """Facility-scoped admin (no system VIEW_SYSTEM_CONFIG): the Admin
        dropdown renders without the Configuration entry."""
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
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'aria-label="Admin pages"' in html
        assert 'href="/admin/projects"' in html
        assert 'href="/admin/configuration"' not in html

    def test_mobile_offcanvas_markup_present(self, auth_client):
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'id="mobileNav"' in html
        assert 'data-bs-target="#mobileNav"' in html      # hamburger retargeted
        assert 'mobileNavSection-status' in html
        assert 'navbarNavMobile' not in html              # old dual-collapse gone


# ---------------------------------------------------------------------------
# Breadcrumbs (auto-derived from the registry; see also the status-history
# trail tests in tests/integration/test_status_dashboard.py)
# ---------------------------------------------------------------------------

class TestBreadcrumbs:

    def test_dashboard_page_auto_trail(self, auth_client):
        html = auth_client.get('/allocations/transactions').get_data(as_text=True)
        assert 'sam-breadcrumbs' in html
        # Section crumb links to the section default; current page is the
        # active (unlinked) crumb.
        assert 'aria-current="page">Transactions' in html

    def test_no_breadcrumbs_outside_registry(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        assert 'sam-breadcrumbs' not in html

    def test_no_back_link_remnants(self, auth_client):
        """The referrer-based .back-link machinery is fully retired."""
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'back-link' not in html
