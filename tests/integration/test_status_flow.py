"""
Integration tests for System Status database operations.

Tests database persistence and query operations using direct session access
to ensure data integrity without Flask layer. Transaction rollback ensures
tests are non-destructive.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from system_status import (
    DerechoStatus,
    CasperStatus,
    LoginNodeStatus,
    QueueStatus
)


class TestDerechoIntegration:
    """Integration tests for Derecho status database operations."""

    def test_create_and_query_derecho_with_login_nodes(self, status_session):
        """Test creating Derecho status with login nodes and querying it back."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)

        # Create main Derecho status
        derecho = DerechoStatus(
            timestamp=timestamp,
            cpu_nodes_total=2488,
            cpu_nodes_available=1850,
            cpu_nodes_down=15,
            cpu_nodes_reserved=623,
            gpu_nodes_total=82,
            gpu_nodes_available=45,
            gpu_nodes_down=2,
            gpu_nodes_reserved=35,
            cpu_cores_total=321536,
            cpu_cores_allocated=245000,
            cpu_cores_idle=76536,
            cpu_utilization_percent=76.2,
            gpu_count_total=656,
            gpu_count_allocated=485,
            gpu_count_idle=171,
            gpu_utilization_percent=73.9,
            memory_total_gb=650000.0,
            memory_allocated_gb=495000.0,
            memory_utilization_percent=76.2,
            running_jobs=1245,
            pending_jobs=328,
            active_users=156
        )
        status_session.add(derecho)
        status_session.flush()  # Get the status_id

        # Create login nodes linked to derecho status
        login_nodes = [
            LoginNodeStatus(
                timestamp=timestamp,
                derecho_status_id=derecho.status_id,
                node_name='derecho1',
                node_type='cpu',
                system_name='derecho',
                available=True,
                degraded=False,
                user_count=12,
                load_1min=2.3,
                load_5min=2.5,
                load_15min=2.8
            ),
            LoginNodeStatus(
                timestamp=timestamp,
                derecho_status_id=derecho.status_id,
                node_name='derecho2',
                node_type='cpu',
                system_name='derecho',
                available=True,
                degraded=False,
                user_count=11,
                load_1min=2.1,
                load_5min=2.4,
                load_15min=2.7
            ),
            LoginNodeStatus(
                timestamp=timestamp,
                derecho_status_id=derecho.status_id,
                node_name='derecho5',
                node_type='gpu',
                system_name='derecho',
                available=True,
                degraded=False,
                user_count=3,
                load_1min=1.1,
                load_5min=1.3,
                load_15min=1.2
            )
        ]
        status_session.add_all(login_nodes)
        status_session.flush()

        # Query back the latest Derecho status
        latest = status_session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        # Verify main status matches
        assert latest is not None
        assert latest.cpu_nodes_total == 2488
        assert latest.running_jobs == 1245
        assert latest.cpu_utilization_percent == 76.2

        # Verify login nodes through relationship
        assert len(latest.login_nodes) == 3

        # Verify individual login node data
        cpu_nodes = [n for n in latest.login_nodes if n.node_type == 'cpu']
        gpu_nodes = [n for n in latest.login_nodes if n.node_type == 'gpu']

        assert len(cpu_nodes) == 2
        assert len(gpu_nodes) == 1

        derecho1 = next(n for n in cpu_nodes if n.node_name == 'derecho1')
        assert derecho1.user_count == 12
        assert derecho1.load_1min == 2.3
        assert derecho1.available is True

        derecho5 = gpu_nodes[0]
        assert derecho5.node_name == 'derecho5'
        assert derecho5.user_count == 3
        assert derecho5.load_1min == 1.1


class TestCasperIntegration:
    """Integration tests for Casper status database operations."""

    def test_create_and_query_casper_with_login_nodes(self, status_session):
        """Test creating Casper status with login nodes and querying it back."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)

        # Create main Casper status
        casper = CasperStatus(
            timestamp=timestamp,
            cpu_nodes_total=151,
            cpu_nodes_available=135,
            cpu_nodes_down=3,
            cpu_nodes_reserved=10,
            cpu_cores_total=9556,
            cpu_cores_allocated=3200,
            cpu_cores_idle=6356,
            cpu_utilization_percent=68.5,
            gpu_nodes_total=22,
            gpu_nodes_available=17,
            gpu_nodes_down=2,
            gpu_nodes_reserved=3,
            gpu_count_total=102,
            gpu_count_allocated=60,
            gpu_count_idle=42,
            gpu_utilization_percent=82.3,
            viz_nodes_total=15,
            viz_nodes_available=15,
            viz_nodes_down=0,
            viz_nodes_reserved=0,
            viz_count_total=96,
            viz_count_allocated=4,
            viz_count_idle=92,
            viz_utilization_percent=4.2,
            memory_total_gb=112413.0,
            memory_allocated_gb=55826.0,
            memory_utilization_percent=71.2,
            running_jobs=456,
            pending_jobs=89,
            active_users=92
        )
        status_session.add(casper)
        status_session.flush()

        # Create login nodes
        login_nodes = [
            LoginNodeStatus(
                timestamp=timestamp,
                casper_status_id=casper.status_id,
                node_name='casper1',
                node_type='cpu',
                system_name='casper',
                available=True,
                degraded=False,
                user_count=39,
                load_1min=3.2,
                load_5min=3.4,
                load_15min=3.6
            ),
            LoginNodeStatus(
                timestamp=timestamp,
                casper_status_id=casper.status_id,
                node_name='casper2',
                node_type='cpu',
                system_name='casper',
                available=True,
                degraded=False,
                user_count=39,
                load_1min=3.1,
                load_5min=3.3,
                load_15min=3.5
            )
        ]
        status_session.add_all(login_nodes)
        status_session.flush()

        # Query back latest Casper status
        latest = status_session.query(CasperStatus).order_by(
            CasperStatus.timestamp.desc()
        ).first()

        # Verify main status
        assert latest is not None
        assert latest.cpu_nodes_total == 151
        assert latest.gpu_nodes_total == 22
        assert latest.viz_nodes_total == 15
        assert latest.running_jobs == 456

        # Verify login nodes
        casper1 = next(n for n in latest.login_nodes if n.node_name == 'casper1')
        assert casper1.user_count == 39
        assert casper1.load_1min == 3.2


class TestMultipleSnapshots:
    """Test handling multiple status snapshots over time."""

    def test_latest_returns_most_recent(self, status_session):
        """Test that latest query returns most recent snapshot."""
        # Create three snapshots with different timestamps
        for i, hour in enumerate([10, 12, 14]):
            derecho = DerechoStatus(
                timestamp=datetime(2025, 1, 25, hour, 0, 0),
                cpu_nodes_total=100 + i,  # Different values to distinguish
                cpu_nodes_available=80,
                cpu_nodes_down=5,
                cpu_nodes_reserved=15,
                gpu_nodes_total=10,
                gpu_nodes_available=8,
                gpu_nodes_down=0,
                gpu_nodes_reserved=2,
                cpu_cores_total=12800,
                cpu_cores_allocated=10000,
                cpu_cores_idle=2800,
                gpu_count_total=80,
                gpu_count_allocated=60,
                gpu_count_idle=20,
                memory_total_gb=25600.0,
                memory_allocated_gb=20000.0,
                running_jobs=150 + i,  # Different values
                pending_jobs=30,
                active_users=50
            )
            status_session.add(derecho)

        status_session.flush()

        # Query for latest
        latest = status_session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        # Should get the 14:00 snapshot (i=2)
        assert latest.cpu_nodes_total == 102  # 100 + 2
        assert latest.running_jobs == 152     # 150 + 2
        assert latest.timestamp.hour == 14

        # Verify all three exist
        all_snapshots = status_session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.asc()
        ).all()
        assert len(all_snapshots) == 3
        assert all_snapshots[0].timestamp.hour == 10
        assert all_snapshots[1].timestamp.hour == 12
        assert all_snapshots[2].timestamp.hour == 14
