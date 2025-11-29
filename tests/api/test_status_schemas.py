"""
Tests for System Status marshmallow schemas with nested loading.

Tests schema serialization and deserialization with nested relationships.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'python'))

from system_status.schemas.status import (
    DerechoStatusSchema,
    CasperStatusSchema,
    LoginNodeSchema,
    QueueSchema,
    FilesystemSchema,
    CasperNodeTypeSchema,
    JupyterHubStatusSchema,
)


class TestDerechoSchemaLoading:
    """Tests for DerechoStatusSchema with nested object loading."""

    def test_derecho_schema_minimal(self):
        """Test loading minimal Derecho status without nested objects."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 100,
            'cpu_nodes_available': 80,
            'cpu_nodes_down': 5,
            'cpu_nodes_reserved': 15,
            'gpu_nodes_total': 10,
            'gpu_nodes_available': 8,
            'gpu_nodes_down': 0,
            'gpu_nodes_reserved': 2,
            'cpu_cores_total': 12800,
            'cpu_cores_allocated': 10000,
            'cpu_cores_idle': 2800,
            'gpu_count_total': 80,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 20,
            'memory_total_gb': 25600.0,
            'memory_allocated_gb': 20000.0,
            'running_jobs': 150,
            'pending_jobs': 30,
            'active_users': 50,
        }

        schema = DerechoStatusSchema()
        status = schema.load(data)

        assert status.cpu_nodes_total == 100
        assert status.running_jobs == 150
        assert status.timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.login_nodes == []
        assert status.queues == []
        assert status.filesystems == []

    def test_derecho_schema_with_login_nodes(self):
        """Test loading Derecho status with nested login nodes."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 100,
            'cpu_nodes_available': 80,
            'cpu_nodes_down': 5,
            'cpu_nodes_reserved': 15,
            'gpu_nodes_total': 10,
            'gpu_nodes_available': 8,
            'gpu_nodes_down': 0,
            'gpu_nodes_reserved': 2,
            'cpu_cores_total': 12800,
            'cpu_cores_allocated': 10000,
            'cpu_cores_idle': 2800,
            'gpu_count_total': 80,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 20,
            'memory_total_gb': 25600.0,
            'memory_allocated_gb': 20000.0,
            'running_jobs': 150,
            'pending_jobs': 30,
            'active_users': 50,
            'login_nodes': [
                {
                    'node_name': 'derecho1',
                    'node_type': 'cpu',
                    'available': True,
                    'user_count': 42,
                    'load_1min': 3.5,
                },
                {
                    'node_name': 'derecho5',
                    'node_type': 'gpu',
                    'available': True,
                    'user_count': 12,
                    'load_1min': 1.2,
                }
            ]
        }

        schema = DerechoStatusSchema()
        status = schema.load(data)

        assert len(status.login_nodes) == 2
        # Verify timestamp and system_name were injected
        assert status.login_nodes[0].timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.login_nodes[0].system_name == 'derecho'
        assert status.login_nodes[0].node_name == 'derecho1'
        assert status.login_nodes[1].node_name == 'derecho5'

    def test_derecho_schema_with_all_nested_objects(self):
        """Test loading Derecho status with all nested object types."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 100,
            'cpu_nodes_available': 80,
            'cpu_nodes_down': 5,
            'cpu_nodes_reserved': 15,
            'gpu_nodes_total': 10,
            'gpu_nodes_available': 8,
            'gpu_nodes_down': 0,
            'gpu_nodes_reserved': 2,
            'cpu_cores_total': 12800,
            'cpu_cores_allocated': 10000,
            'cpu_cores_idle': 2800,
            'gpu_count_total': 80,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 20,
            'memory_total_gb': 25600.0,
            'memory_allocated_gb': 20000.0,
            'running_jobs': 150,
            'pending_jobs': 30,
            'active_users': 50,
            'login_nodes': [
                {'node_name': 'derecho1', 'available': True}
            ],
            'queues': [
                {'queue_name': 'main', 'running_jobs': 100}
            ],
            'filesystems': [
                {'filesystem_name': 'glade', 'available': True}
            ]
        }

        schema = DerechoStatusSchema()
        status = schema.load(data)

        assert len(status.login_nodes) == 1
        assert len(status.queues) == 1
        assert len(status.filesystems) == 1

        # Verify all nested objects got timestamp and system_name
        assert status.queues[0].timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.queues[0].system_name == 'derecho'
        assert status.filesystems[0].timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.filesystems[0].system_name == 'derecho'


class TestCasperSchemaLoading:
    """Tests for CasperStatusSchema with nested object loading."""

    def test_casper_schema_minimal(self):
        """Test loading minimal Casper status without nested objects."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 151,
            'cpu_nodes_available': 135,
            'cpu_nodes_down': 3,
            'cpu_nodes_reserved': 10,
            'cpu_cores_total': 9556,
            'cpu_cores_allocated': 3200,
            'cpu_cores_idle': 6356,
            'gpu_nodes_total': 22,
            'gpu_nodes_available': 17,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 3,
            'gpu_count_total': 102,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 42,
            'viz_nodes_total': 15,
            'viz_nodes_available': 15,
            'viz_nodes_down': 0,
            'viz_nodes_reserved': 0,
            'viz_count_total': 96,
            'viz_count_allocated': 4,
            'viz_count_idle': 92,
            'memory_total_gb': 112413.0,
            'memory_allocated_gb': 55826.0,
            'running_jobs': 456,
            'pending_jobs': 89,
            'active_users': 92,
        }

        schema = CasperStatusSchema()
        status = schema.load(data)

        assert status.cpu_nodes_total == 151
        assert status.gpu_nodes_total == 22
        assert status.viz_nodes_total == 15
        assert status.running_jobs == 456

    def test_casper_schema_with_node_types(self):
        """Test loading Casper status with nested node types."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 151,
            'cpu_nodes_available': 135,
            'cpu_nodes_down': 3,
            'cpu_nodes_reserved': 10,
            'cpu_cores_total': 9556,
            'cpu_cores_allocated': 3200,
            'cpu_cores_idle': 6356,
            'gpu_nodes_total': 22,
            'gpu_nodes_available': 17,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 3,
            'gpu_count_total': 102,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 42,
            'viz_nodes_total': 15,
            'viz_nodes_available': 15,
            'viz_nodes_down': 0,
            'viz_nodes_reserved': 0,
            'viz_count_total': 96,
            'viz_count_allocated': 4,
            'viz_count_idle': 92,
            'memory_total_gb': 112413.0,
            'memory_allocated_gb': 55826.0,
            'running_jobs': 456,
            'pending_jobs': 89,
            'active_users': 92,
            'node_types': [
                {
                    'node_type': 'gpu-v100',
                    'nodes_total': 64,
                    'nodes_available': 42,
                    'cores_per_node': 36,
                    'gpu_model': 'V100',
                    'gpus_per_node': 4,
                },
                {
                    'node_type': 'viz-l40',
                    'nodes_total': 15,
                    'nodes_available': 15,
                    'cores_per_node': 16,
                    'gpu_model': 'L40',
                    'gpus_per_node': 8,
                }
            ]
        }

        schema = CasperStatusSchema()
        status = schema.load(data)

        assert len(status.node_types) == 2
        # Verify timestamp was injected
        assert status.node_types[0].timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.node_types[0].node_type == 'gpu-v100'
        assert status.node_types[1].node_type == 'viz-l40'

    def test_casper_schema_with_all_nested_objects(self):
        """Test loading Casper status with all nested object types."""
        data = {
            'timestamp': datetime(2025, 1, 25, 14, 30, 0),
            'cpu_nodes_total': 151,
            'cpu_nodes_available': 135,
            'cpu_nodes_down': 3,
            'cpu_nodes_reserved': 10,
            'cpu_cores_total': 9556,
            'cpu_cores_allocated': 3200,
            'cpu_cores_idle': 6356,
            'gpu_nodes_total': 22,
            'gpu_nodes_available': 17,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 3,
            'gpu_count_total': 102,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 42,
            'viz_nodes_total': 15,
            'viz_nodes_available': 15,
            'viz_nodes_down': 0,
            'viz_nodes_reserved': 0,
            'viz_count_total': 96,
            'viz_count_allocated': 4,
            'viz_count_idle': 92,
            'memory_total_gb': 112413.0,
            'memory_allocated_gb': 55826.0,
            'running_jobs': 456,
            'pending_jobs': 89,
            'active_users': 92,
            'login_nodes': [
                {'node_name': 'casper1', 'available': True}
            ],
            'node_types': [
                {'node_type': 'gpu-v100', 'nodes_total': 64, 'nodes_available': 42}
            ],
            'queues': [
                {'queue_name': 'casper', 'running_jobs': 200}
            ],
            'filesystems': [
                {'filesystem_name': 'glade', 'available': True}
            ]
        }

        schema = CasperStatusSchema()
        status = schema.load(data)

        assert len(status.login_nodes) == 1
        assert len(status.node_types) == 1
        assert len(status.queues) == 1
        assert len(status.filesystems) == 1

        # Verify all nested objects got timestamp and system_name (except node_types which only get timestamp)
        assert status.login_nodes[0].system_name == 'casper'
        assert status.node_types[0].timestamp == datetime(2025, 1, 25, 14, 30, 0)
        assert status.queues[0].system_name == 'casper'
        assert status.filesystems[0].system_name == 'casper'


class TestSchemaSerialization:
    """Tests for schema dumping (serialization)."""

    def test_derecho_dump_includes_nested_objects(self):
        """Test that dumping Derecho status includes nested objects."""
        from system_status import DerechoStatus, LoginNodeStatus

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
            gpu_count_total=80,
            gpu_count_allocated=60,
            gpu_count_idle=20,
            memory_total_gb=25600.0,
            memory_allocated_gb=20000.0,
            running_jobs=150,
            pending_jobs=30,
            active_users=50,
        )
        status.login_nodes = [
            LoginNodeStatus(
                timestamp=timestamp,
                node_name='derecho1',
                node_type='cpu',
                system_name='derecho',
                available=True,
            )
        ]

        schema = DerechoStatusSchema()
        result = schema.dump(status)

        assert 'login_nodes' in result
        assert len(result['login_nodes']) == 1
        assert result['login_nodes'][0]['node_name'] == 'derecho1'
        # Timestamp should be serialized to ISO format string
        assert isinstance(result['timestamp'], str)
