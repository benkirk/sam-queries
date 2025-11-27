"""
Tests for System Status marshmallow schemas.

Tests schema serialization, datetime handling, and nested relationships
for all status-related schemas.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'python'))

from system_status import (
    DerechoStatus, DerechoLoginNodeStatus, DerechoQueueStatus,
    CasperStatus, CasperLoginNodeStatus, CasperNodeTypeStatus, CasperQueueStatus,
    FilesystemStatus,
    JupyterHubStatus, SystemOutage, ResourceReservation
)
from webui.schemas.status import (
    DerechoStatusSchema, DerechoLoginNodeSchema, DerechoQueueSchema, FilesystemSchema,
    CasperStatusSchema, CasperLoginNodeSchema, CasperNodeTypeSchema, CasperQueueSchema,
    JupyterHubStatusSchema, SystemOutageSchema, ResourceReservationSchema
)


class TestDerechoSchemas:
    """Tests for Derecho-related schemas."""

    def test_derecho_status_schema(self):
        """Test DerechoStatusSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        status = DerechoStatus(
            timestamp=timestamp,
            cpu_nodes_total=100,
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
            cpu_utilization_percent=78.1,
            gpu_count_total=80,
            gpu_count_allocated=60,
            gpu_count_idle=20,
            gpu_utilization_percent=75.0,
            memory_total_gb=25600.0,
            memory_allocated_gb=20000.0,
            memory_utilization_percent=78.1,
            running_jobs=150,
            pending_jobs=30,
            active_users=50,
        )

        schema = DerechoStatusSchema()
        result = schema.dump(status)

        # Status ID should be None since we didn't set it (autoincrement)
        assert 'status_id' in result
        assert result['cpu_nodes_total'] == 100
        assert result['running_jobs'] == 150
        assert result['cpu_utilization_percent'] == 78.1
        # Datetime should be serialized to string
        assert isinstance(result['timestamp'], str)
        assert result['timestamp'] == '2025-01-25T14:30:00'

    def test_derecho_login_node_schema(self):
        """Test DerechoLoginNodeSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        node = DerechoLoginNodeStatus(
            login_node_id=1,
            timestamp=timestamp,
            node_name='derecho1',
            node_type='cpu',
            available=True,
            degraded=False,
            user_count=12,
            load_1min=2.3,
            load_5min=2.5,
            load_15min=2.8
        )

        schema = DerechoLoginNodeSchema()
        result = schema.dump(node)

        assert result['login_node_id'] == 1
        assert result['node_name'] == 'derecho1'
        assert result['node_type'] == 'cpu'
        assert result['available'] is True
        assert result['user_count'] == 12
        assert result['load_1min'] == 2.3

    def test_derecho_login_node_schema_many(self):
        """Test serializing multiple login nodes."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        nodes = [
            DerechoLoginNodeStatus(
                login_node_id=i,
                timestamp=timestamp,
                node_name=f'derecho{i}',
                node_type='cpu' if i <= 4 else 'gpu',
                available=True,
                degraded=False,
                user_count=10 + i,
                load_1min=2.0 + i * 0.1,
                load_5min=2.5 + i * 0.1,
                load_15min=3.0 + i * 0.1
            )
            for i in range(1, 9)
        ]

        schema = DerechoLoginNodeSchema(many=True)
        result = schema.dump(nodes)

        assert len(result) == 8
        assert result[0]['node_name'] == 'derecho1'
        assert result[0]['node_type'] == 'cpu'
        assert result[4]['node_name'] == 'derecho5'
        assert result[4]['node_type'] == 'gpu'

    def test_derecho_queue_schema(self):
        """Test DerechoQueueSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        queue = DerechoQueueStatus(
            queue_status_id=1,
            timestamp=timestamp,
            queue_name='main',
            running_jobs=100,
            pending_jobs=20,
            active_users=30,
            cores_allocated=8000,
            gpus_allocated=0,
            nodes_allocated=60
        )

        schema = DerechoQueueSchema()
        result = schema.dump(queue)

        assert result['queue_status_id'] == 1
        assert result['queue_name'] == 'main'
        assert result['running_jobs'] == 100

    def test_filesystem_schema(self):
        """Test FilesystemSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        fs = FilesystemStatus(
            fs_status_id=1,
            timestamp=timestamp,
            filesystem_name='glade',
            system_name='derecho',
            available=True,
            degraded=False,
            capacity_tb=20000.0,
            used_tb=16500.0,
            utilization_percent=82.5
        )

        schema = FilesystemSchema()
        result = schema.dump(fs)

        assert result['fs_status_id'] == 1
        assert result['filesystem_name'] == 'glade'
        assert result['system_name'] == 'derecho'
        assert result['available'] is True
        assert result['capacity_tb'] == 20000.0


class TestCasperSchemas:
    """Tests for Casper-related schemas."""

    def test_casper_status_schema(self):
        """Test CasperStatusSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        status = CasperStatus(
            timestamp=timestamp,
            # CPU nodes
            cpu_nodes_total=151,
            cpu_nodes_available=135,
            cpu_nodes_down=3,
            cpu_nodes_reserved=10,
            cpu_cores_total=9556,
            cpu_cores_allocated=3200,
            cpu_cores_idle=6356,
            cpu_utilization_percent=68.5,
            # GPU nodes
            gpu_nodes_total=22,
            gpu_nodes_available=17,
            gpu_nodes_down=2,
            gpu_nodes_reserved=3,
            gpu_count_total=102,
            gpu_count_allocated=60,
            gpu_count_idle=42,
            gpu_utilization_percent=82.3,
            # VIZ nodes
            viz_nodes_total=15,
            viz_nodes_available=15,
            viz_nodes_down=0,
            viz_nodes_reserved=0,
            viz_count_total=96,
            viz_count_allocated=4,
            viz_count_idle=92,
            viz_utilization_percent=4.2,
            # Memory
            memory_total_gb=112413.0,
            memory_allocated_gb=55826.0,
            memory_utilization_percent=71.2,
            # Jobs
            running_jobs=456,
            pending_jobs=89,
            active_users=92,
        )

        schema = CasperStatusSchema()
        result = schema.dump(status)

        assert 'status_id' in result
        assert result['cpu_nodes_total'] == 151
        assert result['gpu_nodes_total'] == 22
        assert result['viz_nodes_total'] == 15
        assert result['running_jobs'] == 456

    def test_casper_login_node_schema(self):
        """Test CasperLoginNodeSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        node = CasperLoginNodeStatus(
            login_node_id=1,
            timestamp=timestamp,
            node_name='casper1',
            available=True,
            degraded=False,
            user_count=39,
            load_1min=3.2,
            load_5min=3.4,
            load_15min=3.6
        )

        schema = CasperLoginNodeSchema()
        result = schema.dump(node)

        assert result['login_node_id'] == 1
        assert result['node_name'] == 'casper1'
        assert result['available'] is True
        assert result['user_count'] == 39

    def test_casper_node_type_schema(self):
        """Test CasperNodeTypeSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        node_type = CasperNodeTypeStatus(
            node_type_status_id=1,
            timestamp=timestamp,
            node_type='gpu-v100',
            nodes_total=64,
            nodes_available=42,
            nodes_down=2,
            nodes_allocated=20,
            cores_per_node=36,
            memory_gb_per_node=384,
            gpu_model='NVIDIA V100',
            gpus_per_node=4,
            utilization_percent=82.7
        )

        schema = CasperNodeTypeSchema()
        result = schema.dump(node_type)

        assert result['node_type_status_id'] == 1
        assert result['node_type'] == 'gpu-v100'
        assert result['nodes_total'] == 64


class TestJupyterHubSchema:
    """Tests for JupyterHub schema."""

    def test_jupyterhub_status_schema(self):
        """Test JupyterHubStatusSchema serialization."""
        timestamp = datetime(2025, 1, 25, 14, 30, 0)
        status = JupyterHubStatus(
            timestamp=timestamp,
            available=True,
            active_users=45,
            active_sessions=50,
            cpu_utilization_percent=62.3,
            memory_utilization_percent=58.9
        )

        schema = JupyterHubStatusSchema()
        result = schema.dump(status)

        assert 'status_id' in result
        assert result['available'] is True
        assert result['active_users'] == 45


class TestSupportSchemas:
    """Tests for outage and reservation schemas."""

    def test_system_outage_schema(self):
        """Test SystemOutageSchema serialization."""
        start_time = datetime(2025, 1, 25, 10, 0, 0)
        outage = SystemOutage(
            system_name='Derecho',
            component='GPU nodes',
            severity='major',
            status='monitoring',
            title='GPU node maintenance',
            description='GPU node maintenance in progress',
            start_time=start_time,
            end_time=None,
            estimated_resolution=None,
            created_at=start_time,
            updated_at=start_time
        )

        schema = SystemOutageSchema()
        result = schema.dump(outage)

        assert 'outage_id' in result
        assert result['system_name'] == 'Derecho'
        assert result['status'] == 'monitoring'
        assert result['severity'] == 'major'
        assert result['title'] == 'GPU node maintenance'

    def test_resource_reservation_schema(self):
        """Test ResourceReservationSchema serialization."""
        start_time = datetime(2025, 2, 1, 8, 0, 0)
        end_time = datetime(2025, 2, 1, 16, 0, 0)
        created_at = datetime(2025, 1, 25, 10, 0, 0)
        reservation = ResourceReservation(
            system_name='Derecho',
            reservation_name='MONTHLY_MAINTENANCE',
            description='Monthly system maintenance',
            start_time=start_time,
            end_time=end_time,
            node_count=50,
            partition='main',
            created_at=created_at,
            updated_at=None
        )

        schema = ResourceReservationSchema()
        result = schema.dump(reservation)

        assert 'reservation_id' in result
        assert result['system_name'] == 'Derecho'
        assert result['reservation_name'] == 'MONTHLY_MAINTENANCE'
        assert result['node_count'] == 50
