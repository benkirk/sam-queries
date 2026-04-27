"""
Integration tests for System Status Dashboard.

Verifies that dashboard pages render correctly (status 200) using the new
query layer. Each test seeds minimal Derecho/Casper data into the per-worker
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

    def test_dashboard_index(self, auth_client, status_session):
        """Test GET /status/ returns 200."""
        seed_data(status_session)
        response = auth_client.get('/status/')
        assert response.status_code == 200
        assert b'System Status' in response.data

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
        """`/status/?hours=720` renders without crashing."""
        seed_data(status_session)
        response = auth_client.get('/status/?hours=720')
        assert response.status_code == 200
        assert b'System Status' in response.data

    def test_dashboard_forwards_hours_to_drill_down_links(self, auth_client, status_session):
        """When `hours` is set, queue/nodetype row-click URLs must carry it."""
        seed_data(status_session)
        response = auth_client.get('/status/?hours=720')
        assert response.status_code == 200
        # Drill-down URLs are emitted as window.location='...' onclick handlers.
        assert b'hours=720' in response.data, (
            'Expected hours=720 to appear in row-click URLs on the dashboard'
        )

    def test_dashboard_forwards_legacy_days_as_hours(self, auth_client, status_session):
        """`?days=30` (legacy) is normalized to hours=720 in row-click URLs."""
        seed_data(status_session)
        response = auth_client.get('/status/?days=30')
        assert response.status_code == 200
        assert b'hours=720' in response.data

    def test_dashboard_no_hours_means_no_hours_in_links(self, auth_client, status_session):
        """No `hours` param → drill-down URLs are clean (no `hours=` query string).

        Regression guard: ensures the param-absent path matches today's URLs
        bit-for-bit so users without the param see the original behavior.
        """
        seed_data(status_session)
        response = auth_client.get('/status/')
        assert response.status_code == 200
        # The dashboard renders many things; we only care that drill-down URLs
        # in queue/nodetype tables don't have hours= appended. Look at the
        # specific onclick URLs.
        assert b'queue-history/derecho' in response.data
        # The URL preceding the queue_name shouldn't carry an `hours=` param
        # in any of the row-click handlers when none was requested.
        # (A bare `hours=` somewhere else like a script comment would be a
        # false positive; restrict to the drill-down URL prefix.)
        assert b'queue-history/derecho/' in response.data
        # Permissive substring check — no row-click should contain hours=:
        for url_prefix in (b'queue-history/derecho/', b'queue-history/casper/',
                           b'nodetype-history/casper/'):
            # Find every occurrence of the prefix and verify the surrounding
            # 200 bytes don't include hours=
            idx = 0
            while True:
                pos = response.data.find(url_prefix, idx)
                if pos == -1:
                    break
                snippet = response.data[pos:pos + 200]
                assert b'hours=' not in snippet, (
                    f'Unexpected hours= near {url_prefix!r} in dashboard '
                    f'rendered with no params: {snippet!r}'
                )
                idx = pos + len(url_prefix)

    def test_queue_history_back_link_carries_hours(self, auth_client, status_session):
        """Detail-page back links forward `hours` to the dashboard so the
        user's range survives a back-then-forward cycle.
        """
        seed_data(status_session)
        response = auth_client.get('/status/queue-history/derecho/main?hours=720')
        assert response.status_code == 200
        assert b'/status/?hours=720' in response.data, (
            'Expected back-link to forward hours=720 to status_dashboard.index'
        )

    def test_nodetype_history_back_link_carries_hours(self, auth_client, status_session):
        """Same forwarding for nodetype detail page."""
        seed_data(status_session)
        response = auth_client.get('/status/nodetype-history/casper/cpu?hours=720')
        assert response.status_code == 200
        assert b'/status/?hours=720' in response.data
