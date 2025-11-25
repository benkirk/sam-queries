"""
Tests for System Status API endpoints.

Tests POST endpoints (data ingestion) and GET endpoints (retrieval)
for Derecho, Casper, JupyterHub, outages, and reservations.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'python'))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus, DerechoLoginNodeStatus, DerechoQueueStatus, DerechoFilesystemStatus,
    CasperStatus, CasperLoginNodeStatus, CasperNodeTypeStatus, CasperQueueStatus,
    JupyterHubStatus, SystemOutage, ResourceReservation
)


@pytest.fixture(scope='module')
def status_session():
    """Create a system_status database session for testing."""
    engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as session:
        # Clean up any existing test data
        session.query(DerechoLoginNodeStatus).delete()
        session.query(DerechoQueueStatus).delete()
        session.query(DerechoFilesystemStatus).delete()
        session.query(DerechoStatus).delete()
        session.query(CasperLoginNodeStatus).delete()
        session.query(CasperNodeTypeStatus).delete()
        session.query(CasperQueueStatus).delete()
        session.query(CasperStatus).delete()
        session.query(JupyterHubStatus).delete()
        session.query(SystemOutage).delete()
        session.query(ResourceReservation).delete()
        session.commit()
        yield session


# ============================================================================
# POST Endpoint Tests - Data Ingestion
# ============================================================================

class TestDerechoPost:
    """Tests for POST /api/v1/status/derecho endpoint."""

    def test_post_derecho_minimal(self, auth_client, status_session):
        """Test posting minimal Derecho status data."""
        data = {
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

        response = auth_client.post('/api/v1/status/derecho',
                                   json=data,
                                   content_type='application/json')

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'status_id' in json_data
        assert 'timestamp' in json_data

    def test_post_derecho_with_login_nodes(self, auth_client, status_session):
        """Test posting Derecho status with login nodes array."""
        data = {
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
                    'degraded': False,
                    'user_count': 10,
                    'load_1min': 2.5,
                    'load_5min': 2.8,
                    'load_15min': 3.0
                },
                {
                    'node_name': 'derecho5',
                    'node_type': 'gpu',
                    'available': True,
                    'degraded': False,
                    'user_count': 3,
                    'load_1min': 1.2,
                    'load_5min': 1.5,
                    'load_15min': 1.4
                }
            ]
        }

        response = auth_client.post('/api/v1/status/derecho',
                                   json=data,
                                   content_type='application/json')

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'login_node_ids' in json_data
        assert len(json_data['login_node_ids']) == 2

    def test_post_derecho_with_queues_and_filesystems(self, auth_client, status_session):
        """Test posting Derecho status with queues and filesystems."""
        data = {
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
            'queues': [
                {
                    'queue_name': 'main',
                    'running_jobs': 100,
                    'pending_jobs': 20,
                    'active_users': 30,
                    'cores_allocated': 8000,
                    'gpus_allocated': 0,
                    'nodes_allocated': 60
                }
            ],
            'filesystems': [
                {
                    'filesystem_name': 'glade',
                    'available': True,
                    'degraded': False,
                    'capacity_tb': 20000.0,
                    'used_tb': 16500.0,
                    'utilization_percent': 82.5
                }
            ]
        }

        response = auth_client.post('/api/v1/status/derecho',
                                   json=data,
                                   content_type='application/json')

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'queue_ids' in json_data
        assert 'filesystem_ids' in json_data
        assert len(json_data['queue_ids']) == 1
        assert len(json_data['filesystem_ids']) == 1


class TestCasperPost:
    """Tests for POST /api/v1/status/casper endpoint."""

    def test_post_casper_with_login_nodes(self, auth_client, status_session):
        """Test posting Casper status with login nodes array."""
        data = {
            'compute_nodes_total': 260,
            'compute_nodes_available': 185,
            'compute_nodes_down': 3,
            'cpu_utilization_percent': 68.5,
            'gpu_utilization_percent': 82.3,
            'memory_utilization_percent': 71.2,
            'running_jobs': 456,
            'pending_jobs': 89,
            'active_users': 92,
            'login_nodes': [
                {
                    'node_name': 'casper1',
                    'available': True,
                    'degraded': False,
                    'user_count': 39,
                    'load_1min': 3.2,
                    'load_5min': 3.4,
                    'load_15min': 3.6
                },
                {
                    'node_name': 'casper2',
                    'available': True,
                    'degraded': False,
                    'user_count': 39,
                    'load_1min': 3.1,
                    'load_5min': 3.3,
                    'load_15min': 3.5
                }
            ]
        }

        response = auth_client.post('/api/v1/status/casper',
                                   json=data,
                                   content_type='application/json')

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'login_node_ids' in json_data
        assert len(json_data['login_node_ids']) == 2


# ============================================================================
# GET Endpoint Tests - Data Retrieval
# ============================================================================

class TestDerechoGet:
    """Tests for GET /api/v1/status/derecho/latest endpoint."""

    @pytest.fixture(autouse=True)
    def setup_derecho_data(self, status_session):
        """Create test data for Derecho GET tests."""
        timestamp = datetime.now()

        # Create main status
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
        status_session.add(status)

        # Create login nodes
        for i in range(1, 3):
            node = DerechoLoginNodeStatus(
                timestamp=timestamp,
                node_name=f'derecho{i}',
                node_type='cpu',
                available=True,
                degraded=False,
                user_count=10 + i,
                load_1min=2.0 + i * 0.1,
                load_5min=2.5 + i * 0.1,
                load_15min=3.0 + i * 0.1
            )
            status_session.add(node)

        status_session.commit()

    def test_get_derecho_latest(self, auth_client):
        """Test retrieving latest Derecho status."""
        response = auth_client.get('/api/v1/status/derecho/latest')

        assert response.status_code == 200
        data = response.get_json()

        # Check main status fields
        assert data['cpu_nodes_total'] == 100
        assert data['running_jobs'] == 150
        assert 'timestamp' in data

        # Check login nodes array
        assert 'login_nodes' in data
        assert len(data['login_nodes']) == 2
        assert data['login_nodes'][0]['node_name'] == 'derecho1'
        assert data['login_nodes'][0]['node_type'] == 'cpu'
        assert data['login_nodes'][0]['user_count'] == 11

    def test_get_derecho_no_data(self, auth_client, status_session):
        """Test retrieving Derecho status when no data exists."""
        # Clear all data
        status_session.query(DerechoLoginNodeStatus).delete()
        status_session.query(DerechoStatus).delete()
        status_session.commit()

        response = auth_client.get('/api/v1/status/derecho/latest')

        assert response.status_code == 404
        data = response.get_json()
        assert 'message' in data


class TestCasperGet:
    """Tests for GET /api/v1/status/casper/latest endpoint."""

    @pytest.fixture(autouse=True)
    def setup_casper_data(self, status_session):
        """Create test data for Casper GET tests."""
        timestamp = datetime.now()

        # Create main status
        status = CasperStatus(
            timestamp=timestamp,
            compute_nodes_total=260,
            compute_nodes_available=185,
            compute_nodes_down=3,
            cpu_utilization_percent=68.5,
            gpu_utilization_percent=82.3,
            memory_utilization_percent=71.2,
            running_jobs=456,
            pending_jobs=89,
            active_users=92,
        )
        status_session.add(status)

        # Create login nodes
        for i in range(1, 3):
            node = CasperLoginNodeStatus(
                timestamp=timestamp,
                node_name=f'casper{i}',
                available=True,
                degraded=False,
                user_count=38 + i,
                load_1min=3.0 + i * 0.1,
                load_5min=3.2 + i * 0.1,
                load_15min=3.4 + i * 0.1
            )
            status_session.add(node)

        status_session.commit()

    def test_get_casper_latest(self, auth_client):
        """Test retrieving latest Casper status."""
        response = auth_client.get('/api/v1/status/casper/latest')

        assert response.status_code == 200
        data = response.get_json()

        # Check main status fields
        assert data['compute_nodes_total'] == 260
        assert data['running_jobs'] == 456

        # Check login nodes array
        assert 'login_nodes' in data
        assert len(data['login_nodes']) == 2
        assert data['login_nodes'][0]['node_name'] == 'casper1'
        assert data['login_nodes'][0]['user_count'] == 39
