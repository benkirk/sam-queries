"""
Integration tests for System Status Dashboard.

Verifies that dashboard pages render correctly (status 200) using the new query layer.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from system_status import (
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    QueueStatus, LoginNodeStatus
)


def seed_data(session):
    """Seed minimal data for dashboard tests."""
    now = datetime.now()

    # Seed Derecho status
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
        active_users=20
    )
    session.add(derecho)

    # Seed Derecho Queue
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
        gpus_pending=0
    )
    session.add(d_queue)

    # Seed Casper status
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
        active_users=15
    )
    session.add(casper)

    # Seed Casper Node Type
    c_nodetype = CasperNodeTypeStatus(
        timestamp=now,
        casper_status=casper,
        node_type='cpu',
        nodes_total=50,
        nodes_available=45,
        nodes_down=5,
        nodes_allocated=20,
        utilization_percent=40.0,
        memory_utilization_percent=30.0
    )
    session.add(c_nodetype)
    
    # Seed Casper Queue
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
        gpus_pending=0
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
        # Data already seeded by previous test or we can ensure it's there
        # but let's assume session persists or we re-seed if we clear in fixture
        # The fixture clears at start, so data from seed_data persists for the class unless cleared
        
        # Ensure data exists (idempotent seed or check)
        if status_session.query(CasperStatus).count() == 0:
            seed_data(status_session)

        response = auth_client.get('/status/nodetype-history/casper/cpu')
        assert response.status_code == 200
        assert b'Node Type History' in response.data
        assert b'cpu' in response.data

    def test_queue_history(self, auth_client, status_session):
        """Test GET /status/queue-history/derecho/main returns 200."""
        if status_session.query(DerechoStatus).count() == 0:
            seed_data(status_session)
            
        response = auth_client.get('/status/queue-history/derecho/main')
        assert response.status_code == 200
        assert b'main Queue History' in response.data
