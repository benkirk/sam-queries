"""
Integration tests for System Status Dashboard.

Verifies that dashboard pages render correctly (status 200) using the new
query layer. The dashboard is routable pages — /status/ 302-redirects to
/status/derecho, and each system (derecho, casper, jupyterhub, reservations,
filesystem-scans) has its own page sharing the outage banner + tab strip.
Each test seeds minimal Derecho/Casper data into the per-worker
SQLite tempfile via the `status_session` fixture, then issues authenticated
HTTP GETs through `auth_client`. Both the seed and the route's `db.session`
queries route through the same Flask-SQLAlchemy `system_status` bind, so the
seeded data is visible to the route handler.

Phase 4f port note: the legacy version called `session.commit()` after
seeding because it ran against a per-worker MySQL `system_status_test_*`
database. We use the same `commit()` semantics under the SQLite tempfile,
which gets DELETE-cleaned at the start of each test by the fixture.
"""
from datetime import datetime

import pytest

from system_status import (
    DerechoStatus,
    CasperStatus,
    CasperNodeTypeStatus,
    QueueStatus,
)


pytestmark = pytest.mark.integration


def seed_data(session):
    """Seed minimal data for dashboard tests.

    Mirrors the legacy `seed_data` helper. Creates one Derecho status with
    one queue, one Casper status with one node-type and one queue. Enough
    rows to satisfy the dashboard route templates without being verbose.
    """
    now = datetime.now()

    derecho = DerechoStatus(
        timestamp=now,
        cpu_nodes_total=100,
        cpu_nodes_available=90,
        cpu_nodes_down=10,
        cpu_nodes_reserved=0,
        gpu_nodes_total=10,
        gpu_nodes_available=8,
        gpu_nodes_down=2,
        gpu_nodes_reserved=0,
        cpu_cores_total=1000,
        cpu_cores_allocated=500,
        cpu_cores_idle=500,
        gpu_count_total=40,
        gpu_count_allocated=20,
        gpu_count_idle=20,
        memory_total_gb=1000.0,
        memory_allocated_gb=500.0,
        running_jobs=50,
        pending_jobs=10,
        active_users=20,
    )
    session.add(derecho)

    d_queue = QueueStatus(
        timestamp=now,
        derecho_status=derecho,
        system_name='derecho',
        queue_name='main',
        running_jobs=10,
        pending_jobs=5,
        held_jobs=1,
        active_users=5,
        cores_allocated=100,
        cores_pending=50,
        gpus_allocated=0,
        gpus_pending=0,
    )
    session.add(d_queue)

    casper = CasperStatus(
        timestamp=now,
        cpu_nodes_total=50,
        cpu_nodes_available=45,
        cpu_nodes_down=5,
        cpu_nodes_reserved=0,
        gpu_nodes_total=20,
        gpu_nodes_available=18,
        gpu_nodes_down=2,
        gpu_nodes_reserved=0,
        viz_nodes_total=5,
        viz_nodes_available=5,
        viz_nodes_down=0,
        viz_nodes_reserved=0,
        cpu_cores_total=500,
        cpu_cores_allocated=200,
        cpu_cores_idle=300,
        gpu_count_total=80,
        gpu_count_allocated=40,
        gpu_count_idle=40,
        viz_count_total=20,
        viz_count_allocated=10,
        viz_count_idle=10,
        memory_total_gb=500.0,
        memory_allocated_gb=200.0,
        running_jobs=30,
        pending_jobs=5,
        active_users=15,
    )
    session.add(casper)

    c_nodetype = CasperNodeTypeStatus(
        timestamp=now,
        casper_status=casper,
        node_type='cpu',
        nodes_total=50,
        nodes_available=45,
        nodes_down=5,
        nodes_allocated=20,
        utilization_percent=40.0,
        memory_utilization_percent=30.0,
    )
    session.add(c_nodetype)

    c_queue = QueueStatus(
        timestamp=now,
        casper_status=casper,
        system_name='casper',
        queue_name='casper',
        running_jobs=15,
        pending_jobs=2,
        held_jobs=0,
        active_users=8,
        cores_allocated=50,
        cores_pending=10,
        gpus_allocated=0,
        gpus_pending=0,
    )
    session.add(c_queue)

    session.commit()


class TestStatusDashboard:
    """Tests for the status dashboard views."""

    def test_index_redirects_to_derecho(self, auth_client):
        """GET /status/ 302-redirects to the default page (/status/derecho)."""
        response = auth_client.get('/status/')
        assert response.status_code == 302
        assert response.headers['Location'].endswith('/status/derecho')

    def test_index_redirect_preserves_hours(self, auth_client):
        """The bare-URL redirect forwards ?hours= to the Derecho page."""
        response = auth_client.get('/status/?hours=720')
        assert response.status_code == 302
        location = response.headers['Location']
        assert '/status/derecho' in location
        assert 'hours=720' in location

    def test_derecho_page(self, auth_client, status_session):
        """Test GET /status/derecho returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/derecho')
        assert response.status_code == 200
        assert b'System Status' in response.data
        assert b'Derecho' in response.data

    def test_casper_page(self, auth_client, status_session):
        """Test GET /status/casper returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/casper')
        assert response.status_code == 200
        assert b'System Status' in response.data
        assert b'Casper' in response.data

    def test_jupyterhub_page(self, auth_client, status_session):
        """Test GET /status/jupyterhub returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/jupyterhub')
        assert response.status_code == 200
        assert b'System Status' in response.data
        assert b'JupyterHub' in response.data

    # ------------------------------------------------------------------
    # "Filesystem Scans" — the tab-strip link (on every status page) is
    # gated on VIEW_ALL_FILESYSTEM_DATA AND a non-empty scan-capable
    # resource list (plugin on + warmed collections). The page itself
    # (/status/filesystem-scans) is @login_required +
    # @require_permission(VIEW_ALL_FILESYSTEM_DATA).
    # The plugin is off in tests, so scan_capable_resources() returns [] by
    # default; patch it to exercise the visible path.
    # ------------------------------------------------------------------

    def test_fs_scans_tab_shown_with_perm(self, auth_client, status_session, monkeypatch):
        """benkirk holds the perm → tab-strip link on the status pages."""
        seed_data(status_session)
        monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                            lambda app=None: ['Campaign_Store'])
        response = auth_client.get('/status/derecho')
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert '/status/filesystem-scans' in body
        assert 'Filesystem Scans' in body

    def test_fs_scans_page_renders_subtabs(self, auth_client, status_session, monkeypatch):
        """The page renders one subtab per configured resource."""
        seed_data(status_session)
        monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                            lambda app=None: ['Campaign_Store'])
        response = auth_client.get('/status/filesystem-scans')
        assert response.status_code == 200
        assert 'Campaign_Store' in response.get_data(as_text=True)   # subtab rendered

    def test_fs_scans_tab_hidden_when_no_resources(self, auth_client, status_session, monkeypatch):
        """No scan-capable resource (plugin off / unwarmed) → no tab even with perm."""
        seed_data(status_session)
        monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                            lambda app=None: [])
        response = auth_client.get('/status/derecho')
        assert response.status_code == 200
        assert '/status/filesystem-scans' not in response.get_data(as_text=True)

    def test_fs_scans_tab_hidden_without_perm(self, non_admin_client, status_session, monkeypatch):
        """A user lacking VIEW_ALL_FILESYSTEM_DATA never sees the tab."""
        seed_data(status_session)
        monkeypatch.setattr('webapp.disk_scans.service.scan_capable_resources',
                            lambda app=None: ['Campaign_Store'])
        response = non_admin_client.get('/status/derecho')
        assert response.status_code == 200
        assert '/status/filesystem-scans' not in response.get_data(as_text=True)

    def test_fs_scans_page_requires_login(self, client):
        """Anonymous GET of the page redirects to the login screen."""
        response = client.get('/status/filesystem-scans')
        assert response.status_code == 302
        assert '/auth/login' in response.headers.get('Location', '')

    def test_fs_scans_page_403_without_perm(self, non_admin_client):
        """Logged-in but lacking VIEW_ALL_FILESYSTEM_DATA → 403."""
        response = non_admin_client.get('/status/filesystem-scans')
        assert response.status_code == 403

    def test_nodetype_history(self, auth_client, status_session):
        """Test GET /status/nodetype-history/casper/cpu returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/nodetype-history/casper/cpu')
        assert response.status_code == 200
        assert b'Node Type History' in response.data
        assert b'cpu' in response.data

    def test_queue_history(self, auth_client, status_session):
        """Test GET /status/queue-history/derecho/main returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/queue-history/derecho/main')
        assert response.status_code == 200
        assert b'main Queue History' in response.data

    # ------------------------------------------------------------------
    # `hours` filter passthrough — sideways navigation between detail
    # pages should inherit the user's chosen time range via the dashboard.
    # ------------------------------------------------------------------

    def test_dashboard_accepts_hours_param(self, auth_client, status_session):
        """`?hours=720` renders without crashing on each system page."""
        seed_data(status_session)
        for page in ('/status/derecho', '/status/casper'):
            response = auth_client.get(f'{page}?hours=720')
            assert response.status_code == 200
            assert b'System Status' in response.data

    def test_dashboard_forwards_hours_to_drill_down_links(self, auth_client, status_session):
        """When `hours` is set, queue/nodetype row-click URLs must carry it.

        Assert on the full drill-down URLs (not a bare `hours=720`
        substring) — the chart card's time-range picker always emits an
        `?hours=720` link, which would make a loose check tautological.
        """
        seed_data(status_session)
        response = auth_client.get('/status/derecho?hours=720')
        assert response.status_code == 200
        # Drill-down URLs are emitted as row-click data-href handlers.
        assert b'/status/queue-history/derecho/main?hours=720' in response.data, (
            'Expected hours=720 to appear in row-click URLs on the Derecho page'
        )
        response = auth_client.get('/status/casper?hours=720')
        assert response.status_code == 200
        assert b'/status/queue-history/casper/casper?hours=720' in response.data
        assert b'/status/nodetype-history/casper/cpu?hours=720' in response.data

    def test_dashboard_forwards_legacy_days_as_hours(self, auth_client, status_session):
        """`?days=30` (legacy) is normalized to hours=720 in row-click URLs."""
        seed_data(status_session)
        response = auth_client.get('/status/derecho?days=30')
        assert response.status_code == 200
        assert b'/status/queue-history/derecho/main?hours=720' in response.data

    def test_dashboard_no_hours_means_no_hours_in_links(self, auth_client, status_session):
        """No `hours` param → drill-down URLs are clean (no `hours=` query string).

        Regression guard: ensures the param-absent path matches today's URLs
        bit-for-bit so users without the param see the original behavior.
        """
        seed_data(status_session)
        # Derecho queues render on /status/derecho; Casper queues and
        # node-types on /status/casper.
        pages = {
            '/status/derecho': (b'queue-history/derecho/',),
            '/status/casper': (b'queue-history/casper/', b'nodetype-history/casper/'),
        }
        for page, url_prefixes in pages.items():
            response = auth_client.get(page)
            assert response.status_code == 200
            # The pages render many things; we only care that drill-down URLs
            # in queue/nodetype tables don't have hours= appended. Look at the
            # specific row-click URLs.
            # (A bare `hours=` somewhere else like a script comment would be a
            # false positive; restrict to the drill-down URL prefix.)
            for url_prefix in url_prefixes:
                assert url_prefix in response.data
                # Find every occurrence of the prefix and verify the
                # surrounding 200 bytes don't include hours=
                idx = 0
                while True:
                    pos = response.data.find(url_prefix, idx)
                    if pos == -1:
                        break
                    snippet = response.data[pos:pos + 200]
                    assert b'hours=' not in snippet, (
                        f'Unexpected hours= near {url_prefix!r} on {page} '
                        f'rendered with no params: {snippet!r}'
                    )
                    idx = pos + len(url_prefix)

    def test_queue_history_breadcrumb_carries_hours(self, auth_client, status_session):
        """Detail-page breadcrumbs forward `hours` to the system page so the
        user's range survives a back-then-forward cycle.
        """
        seed_data(status_session)
        response = auth_client.get('/status/queue-history/derecho/main?hours=720')
        assert response.status_code == 200
        assert b'/status/derecho?hours=720' in response.data, (
            'Expected breadcrumb to forward hours=720 to status_dashboard.derecho'
        )

    def test_nodetype_history_breadcrumb_carries_hours(self, auth_client, status_session):
        """Same forwarding for nodetype detail page."""
        seed_data(status_session)
        response = auth_client.get('/status/nodetype-history/casper/cpu?hours=720')
        assert response.status_code == 200
        assert b'/status/casper?hours=720' in response.data
