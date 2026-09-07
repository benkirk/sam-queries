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


class TestProjectDetailsModalBody:
    """The quick-view modal opens from a job table, a chart segment or a
    tree node — places where the viewer has only ever seen the projcode.
    The modal header carries that code and nothing else (set by the
    route's ``setModalTitle`` HX-Trigger), so the body has to be what
    names the project."""

    def test_body_names_the_project(self, auth_client, active_project):
        from markupsafe import escape

        resp = auth_client.get(
            f'/user/project-details-modal/{active_project.projcode}')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)

        expected = (str(escape(active_project.title))
                    if active_project.title else 'Untitled Project')
        assert expected in body

    def test_header_trigger_still_carries_the_projcode(
        self, auth_client, active_project,
    ):
        """Code in the header, title in the body — a 255-char title would
        overflow the header, and the code alone doesn't identify anything."""
        resp = auth_client.get(
            f'/user/project-details-modal/{active_project.projcode}')
        assert active_project.projcode in resp.headers['HX-Trigger']


class TestUsersGroupsCardAutoLoad:
    """``?username=`` / ``?groupname=`` deep-links auto-load the matching card
    and flag it for a one-shot reveal — the same idiom as the projects page
    (TestProjectCardAutoLoad). Absent params leave both containers inert."""

    def test_username_auto_loads_and_reveals(self, auth_client):
        html = auth_client.get(
            '/admin/users-groups?username=benkirk').get_data(as_text=True)
        assert 'data-reveal-on-load' in html
        assert '/admin/user/benkirk' in html

    def test_groupname_auto_loads_and_reveals(self, auth_client):
        html = auth_client.get(
            '/admin/users-groups?groupname=csg').get_data(as_text=True)
        assert 'data-reveal-on-load' in html
        assert '/admin/group/csg' in html

    def test_flags_absent_without_params(self, auth_client):
        html = auth_client.get('/admin/users-groups').get_data(as_text=True)
        assert 'data-reveal-on-load' not in html


class TestSubTabDeepLinks:
    """Lower-level tabs are addressable via ?tab= / ?view= — the route stamps
    the active pane server-side (data-tab-url-param), and a bad value falls
    back to the default rather than 400ing (read_tab is lenient)."""

    def test_resources_tab_stamps_active_pane(self, auth_client):
        html = auth_client.get(
            '/admin/htmx/resources?active_only=1&tab=machines').get_data(as_text=True)
        assert 'data-tab-url-param="tab"' in html
        assert 'tab-pane fade show active" id="machines-pane"' in html

    def test_resources_default_and_bad_tab_are_resources(self, auth_client):
        for qs in ('', '&tab=bogus'):
            html = auth_client.get(
                f'/admin/htmx/resources?active_only=1{qs}').get_data(as_text=True)
            assert 'tab-pane fade show active" id="resources-pane"' in html

    def test_organizations_tab_stamps_active_pane(self, auth_client):
        html = auth_client.get(
            '/admin/htmx/organizations-card?active_only=1&tab=institutions'
        ).get_data(as_text=True)
        assert 'data-tab-url-param="tab"' in html
        assert 'tab-pane fade show active" id="institutions-pane"' in html

    def test_organizations_default_and_bad_tab_are_orgs(self, auth_client):
        for qs in ('', '&tab=bogus'):
            html = auth_client.get(
                f'/admin/htmx/organizations-card?active_only=1{qs}'
            ).get_data(as_text=True)
            assert 'tab-pane fade show active" id="orgs-pane"' in html

    def test_xras_view_selects_accounts_pane(self, auth_client):
        html = auth_client.get(
            '/allocations/xras?view=accounts').get_data(as_text=True)
        assert 'data-tab-url-param="view"' in html
        assert 'tab-pane fade show active" id="xras-pane-accounts"' in html

    def test_xras_default_and_bad_view_are_activity(self, auth_client):
        for qs in ('', '?view=bogus', '?view=remediations'):
            html = auth_client.get(
                f'/allocations/xras{qs}').get_data(as_text=True)
            assert 'tab-pane fade show active" id="xras-pane-activity"' in html

    def test_xras_card_deeplink_markers_present(self, auth_client):
        """Remediations and Logs are cards, not tabs — reached by the
        data-deeplink scroll handler rather than the tab channel."""
        html = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert 'data-deeplink="remediations"' in html   # benkirk has MANAGE_XRAS
        assert 'data-deeplink="logs"' in html
