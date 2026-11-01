"""Tests for the navigation registry (webapp/utils/nav.py) and the navbar
dropdown / mobile offcanvas markup it drives.

The registry is the single source of truth for the desktop navbar section
dropdowns, the mobile offcanvas menu, and breadcrumbs. Visibility is
permission-driven per request; ``nav_locate`` maps endpoints to their
section/item for breadcrumb derivation.
"""

import pytest

from webapp.utils.nav import NAV_SECTIONS, nav_locate



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

    def test_status_events_item_is_last(self):
        """Events sits after the RBAC-gated status items, matching the tab
        strip in base_status.html (where it must stay rightmost)."""
        status = next(s for s in NAV_SECTIONS if s['key'] == 'status')
        assert status['items'][-1]['endpoint'] == 'status_dashboard.events'
        assert status['items'][-1]['label'] == 'Calendar & Events'

    def test_registry_endpoints_exist(self, app):
        """Every registry endpoint must be a real route — catches drift when
        blueprints are renamed."""
        for s in NAV_SECTIONS:
            assert s['endpoint'] in app.view_functions
            for i in s['items']:
                assert i['endpoint'] in app.view_functions, i['endpoint']

    def test_every_section_tab_is_in_the_nav(self):
        """A tab added to a section strip and not to the registry is a page the menu
        cannot reach (My Requests was one)."""
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[3] / 'src/webapp/templates/dashboards'
        strips = list(root.glob('*/base_*.html'))
        assert len(strips) >= 4
        missing = sorted(
            f'{f.name}: {ep}'
            for f in strips
            for ep in re.findall(r"'endpoint':\s*'([a-z_]+\.[a-z_]+)'", f.read_text())
            if nav_locate(ep)[1] is None)
        assert not missing, missing


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
        for href in ('/admin/configuration', '/admin/account-requests',
                     '/allocations/adjustments', '/admin/users-groups'):
            assert f'href="{href}"' in html
        # Split caret toggles render per section
        assert 'aria-label="Admin pages"' in html
        assert 'aria-label="System Status pages"' in html

    def test_fs_scans_entry_needs_permission_and_data(self, auth_client, monkeypatch):
        """The Filesystem Scans entry uses the same gate as the status tab
        strip: VIEW_ALL_FILESYSTEM_DATA AND a warmed scan collection. The
        test env has no warmed collections by default -> absent even for a
        full admin; present once the data side is satisfied."""
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'href="/status/filesystem-scans"' not in html

        monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                            lambda app=None: ['Campaign_Store'])
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'href="/status/filesystem-scans"' in html

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
        assert 'href="/admin/account-requests"' not in html, \
            'the Accounts queue is system-wide, never facility-scoped'

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

    def test_my_requests_is_in_the_menu_and_the_trail(self, auth_client):
        html = auth_client.get('/user/requests').get_data(as_text=True)
        assert 'aria-current="page">My Requests' in html
        assert html.count('href="/user/requests"') >= 3   # navbar, drawer, tab strip

    @pytest.mark.parametrize('path, parent, leaf', [
        ('/admin/htmx/notifications', 'Configuration', 'Notifications'),
        ('/admin/htmx/rate-limits', 'Configuration', 'Rate limiting'),
        ('/admin/htmx/tasks', 'Configuration', 'Scheduled tasks'),
        ('/admin/users/last-seen', 'Users &amp; Groups', 'Last seen'),
        ('/admin/organizations/mnemonics', 'Organizations', 'Mnemonic Codes'),
        ('/allocations/xras/configuration', 'XRAS', 'Configuration'),
    ])
    def test_sub_pages_name_their_parent(self, auth_client, path, parent, leaf):
        import re
        resp = auth_client.get(path)
        if resp.status_code == 404:
            pytest.skip(f'{path} not mounted here')
        trail = re.search(r'<nav aria-label="breadcrumb" class="sam-breadcrumbs">(.*?)</nav>',
                          resp.get_data(as_text=True), re.S).group(1)
        assert f'>{parent}</a>' in trail, trail
        assert f'aria-current="page">{leaf}<' in trail, trail

    def test_only_the_last_crumb_is_current(self, auth_client, active_project):
        import re
        html = auth_client.get(f'/admin/project/{active_project.projcode}/edit').get_data(as_text=True)
        trail = re.search(r'class="sam-breadcrumbs">(.*?)</nav>', html, re.S).group(1)
        assert trail.count('aria-current') == 1
        assert f'aria-current="page">Manage {active_project.projcode}<' in trail
        assert '>Projects</a>' in trail and 'Back to Dashboard' not in html

    def test_no_breadcrumbs_outside_registry(self, client):
        html = client.get('/auth/login').get_data(as_text=True)
        assert 'sam-breadcrumbs' not in html

    def test_no_back_link_remnants(self, auth_client):
        """The referrer-based .back-link machinery is fully retired."""
        html = auth_client.get('/user/accounts').get_data(as_text=True)
        assert 'back-link' not in html


class TestPublicEntryPoints:
    """The public forms are linked only where they serve a signed-out visitor."""

    def test_login_page_and_navbar_link_the_open_form(self, client):
        login = client.get('/auth/login').get_data(as_text=True)
        assert 'href="/register/"' in login and 'New to NCAR?' in login
        status = client.get('/status/derecho').get_data(as_text=True)
        assert 'id="getAccessDropdown"' in status
        assert status.count('href="/register/"') == 2      # navbar + drawer

    def test_nothing_when_the_form_bounces_to_login(self, app, client, monkeypatch):
        monkeypatch.setitem(app.config, 'ACCOUNT_REGISTRATION_LOGIN_REQUIRED', True)
        assert 'New to NCAR?' not in client.get('/auth/login').get_data(as_text=True)
        assert 'getAccessDropdown' not in client.get('/status/derecho').get_data(as_text=True)

    def test_request_link_needs_the_visitor_lever(self, app, monkeypatch):
        from types import SimpleNamespace
        import webapp.utils.xras as xras
        if 'allocation_request' not in app.blueprints:
            pytest.skip('allocation_request blueprint not mounted')
        with app.test_request_context('/auth/login'):
            from webapp.utils.nav import public_entry_points
            monkeypatch.setattr(xras, 'interactive_config',
                                lambda: SimpleNamespace(visitor_submit_configured=False))
            assert 'Request an allocation' not in [p[0] for p in public_entry_points()]
            monkeypatch.setattr(xras, 'interactive_config',
                                lambda: SimpleNamespace(visitor_submit_configured=True))
            assert 'Request an allocation' in [p[0] for p in public_entry_points()]

    def test_signed_in_users_see_no_get_access(self, auth_client):
        assert 'getAccessDropdown' not in auth_client.get('/user/accounts').get_data(as_text=True)
