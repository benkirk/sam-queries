"""
Integration tests for System Status API flow.

Tests complete POST → Database → GET flows to ensure data integrity
across the entire stack.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus,
    CasperStatus,
    LoginNodeStatus
)


@pytest.fixture(scope='module')
def status_session():
    """Create a system_status database session for testing."""
    engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as session:
        # Clean up any existing test data
        session.query(LoginNodeStatus).delete()
        session.query(DerechoStatus).delete()
        session.query(CasperStatus).delete()
        session.commit()
        yield session


class TestDerechoIntegration:
    """Integration tests for Derecho status flow."""

    def test_post_then_get_derecho_with_login_nodes(self, auth_client, status_session):
        """Test POST Derecho data then GET it back with login nodes."""
        # POST data
        post_data = {
            'timestamp': '2025-01-25T14:30:00',
            'cpu_nodes_total': 2488,
            'cpu_nodes_available': 1850,
            'cpu_nodes_down': 15,
            'cpu_nodes_reserved': 623,
            'gpu_nodes_total': 82,
            'gpu_nodes_available': 45,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 35,
            'cpu_cores_total': 321536,
            'cpu_cores_allocated': 245000,
            'cpu_cores_idle': 76536,
            'cpu_utilization_percent': 76.2,
            'gpu_count_total': 656,
            'gpu_count_allocated': 485,
            'gpu_count_idle': 171,
            'gpu_utilization_percent': 73.9,
            'memory_total_gb': 650000.0,
            'memory_allocated_gb': 495000.0,
            'memory_utilization_percent': 76.2,
            'running_jobs': 1245,
            'pending_jobs': 328,
            'active_users': 156,
            'login_nodes': [
                {
                    'node_name': 'derecho1',
                    'node_type': 'cpu',
                    'available': True,
                    'degraded': False,
                    'user_count': 12,
                    'load_1min': 2.3,
                    'load_5min': 2.5,
                    'load_15min': 2.8
                },
                {
                    'node_name': 'derecho2',
                    'node_type': 'cpu',
                    'available': True,
                    'degraded': False,
                    'user_count': 11,
                    'load_1min': 2.1,
                    'load_5min': 2.4,
                    'load_15min': 2.7
                },
                {
                    'node_name': 'derecho5',
                    'node_type': 'gpu',
                    'available': True,
                    'degraded': False,
                    'user_count': 3,
                    'load_1min': 1.1,
                    'load_5min': 1.3,
                    'load_15min': 1.2
                }
            ]
        }

        # POST
        post_response = auth_client.post('/api/v1/status/derecho',
                                        json=post_data,
                                        content_type='application/json')
        assert post_response.status_code == 201
        post_result = post_response.get_json()
        assert post_result['success'] is True
        assert len(post_result['login_node_ids']) == 3

        # GET
        get_response = auth_client.get('/api/v1/status/derecho/latest')
        assert get_response.status_code == 200
        get_result = get_response.get_json()

        # Verify main status matches
        assert get_result['cpu_nodes_total'] == 2488
        assert get_result['running_jobs'] == 1245
        assert get_result['cpu_utilization_percent'] == 76.2

        # Verify login nodes match
        assert 'login_nodes' in get_result
        assert len(get_result['login_nodes']) == 3

        # Verify individual login node data
        cpu_nodes = [n for n in get_result['login_nodes'] if n['node_type'] == 'cpu']
        gpu_nodes = [n for n in get_result['login_nodes'] if n['node_type'] == 'gpu']

        assert len(cpu_nodes) == 2
        assert len(gpu_nodes) == 1

        derecho1 = next(n for n in cpu_nodes if n['node_name'] == 'derecho1')
        assert derecho1['user_count'] == 12
        assert derecho1['load_1min'] == 2.3
        assert derecho1['available'] is True

        derecho5 = gpu_nodes[0]
        assert derecho5['node_name'] == 'derecho5'
        assert derecho5['user_count'] == 3
        assert derecho5['load_1min'] == 1.1

    def test_database_persistence(self, auth_client, status_session):
        """Verify data was persisted correctly in database."""
        # First, clear and post data
        status_session.query(LoginNodeStatus).delete()
        status_session.query(DerechoStatus).delete()
        status_session.commit()

        post_data = {
            'timestamp': '2025-01-25T14:30:00',
            'cpu_nodes_total': 2488,
            'cpu_nodes_available': 1850,
            'cpu_nodes_down': 15,
            'cpu_nodes_reserved': 623,
            'gpu_nodes_total': 82,
            'gpu_nodes_available': 45,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 35,
            'cpu_cores_total': 321536,
            'cpu_cores_allocated': 245000,
            'cpu_cores_idle': 76536,
            'cpu_utilization_percent': 76.2,
            'gpu_count_total': 656,
            'gpu_count_allocated': 485,
            'gpu_count_idle': 171,
            'gpu_utilization_percent': 73.9,
            'memory_total_gb': 650000.0,
            'memory_allocated_gb': 495000.0,
            'memory_utilization_percent': 76.2,
            'running_jobs': 1245,
            'pending_jobs': 328,
            'active_users': 156,
            'login_nodes': [
                {'node_name': 'derecho1', 'node_type': 'cpu', 'available': True, 'degraded': False},
                {'node_name': 'derecho2', 'node_type': 'cpu', 'available': True, 'degraded': False},
                {'node_name': 'derecho5', 'node_type': 'gpu', 'available': True, 'degraded': False}
            ]
        }

        response = auth_client.post('/api/v1/status/derecho',
                                   json=post_data,
                                   content_type='application/json')
        assert response.status_code == 201

        # Now query main status
        status = status_session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        assert status is not None
        assert status.cpu_nodes_total == 2488
        assert status.running_jobs == 1245

        # Query login nodes
        login_nodes = status_session.query(LoginNodeStatus).filter_by(
            timestamp=status.timestamp,
            system_name='derecho'
        ).all()

        assert len(login_nodes) == 3

        # Verify CPU nodes
        cpu_nodes = [n for n in login_nodes if n.node_type == 'cpu']
        assert len(cpu_nodes) == 2

        # Verify GPU nodes
        gpu_nodes = [n for n in login_nodes if n.node_type == 'gpu']
        assert len(gpu_nodes) == 1


class TestCasperIntegration:
    """Integration tests for Casper status flow."""

    def test_post_then_get_casper_with_login_nodes(self, auth_client, status_session):
        """Test POST Casper data then GET it back with login nodes."""
        # POST data
        post_data = {
            'timestamp': '2025-01-25T14:30:00',
            # CPU nodes
            'cpu_nodes_total': 151,
            'cpu_nodes_available': 135,
            'cpu_nodes_down': 3,
            'cpu_nodes_reserved': 10,
            'cpu_cores_total': 9556,
            'cpu_cores_allocated': 3200,
            'cpu_cores_idle': 6356,
            'cpu_utilization_percent': 68.5,
            # GPU nodes
            'gpu_nodes_total': 22,
            'gpu_nodes_available': 17,
            'gpu_nodes_down': 2,
            'gpu_nodes_reserved': 3,
            'gpu_count_total': 102,
            'gpu_count_allocated': 60,
            'gpu_count_idle': 42,
            'gpu_utilization_percent': 82.3,
            # VIZ nodes
            'viz_nodes_total': 15,
            'viz_nodes_available': 15,
            'viz_nodes_down': 0,
            'viz_nodes_reserved': 0,
            'viz_count_total': 96,
            'viz_count_allocated': 4,
            'viz_count_idle': 92,
            'viz_utilization_percent': 4.2,
            # Memory
            'memory_total_gb': 112413.0,
            'memory_allocated_gb': 55826.0,
            'memory_utilization_percent': 71.2,
            # Jobs
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

        # POST
        post_response = auth_client.post('/api/v1/status/casper',
                                        json=post_data,
                                        content_type='application/json')
        assert post_response.status_code == 201
        post_result = post_response.get_json()
        assert post_result['success'] is True
        assert len(post_result['login_node_ids']) == 2

        # GET
        get_response = auth_client.get('/api/v1/status/casper/latest')
        assert get_response.status_code == 200
        get_result = get_response.get_json()

        # Verify main status matches
        assert get_result['cpu_nodes_total'] == 151
        assert get_result['gpu_nodes_total'] == 22
        assert get_result['viz_nodes_total'] == 15
        assert get_result['running_jobs'] == 456

        # Verify login nodes match
        assert 'login_nodes' in get_result
        assert len(get_result['login_nodes']) == 2

        casper1 = next(n for n in get_result['login_nodes'] if n['node_name'] == 'casper1')
        assert casper1['user_count'] == 39
        assert casper1['load_1min'] == 3.2


class TestMultipleSnapshots:
    """Test handling multiple status snapshots over time."""

    def test_latest_returns_most_recent(self, auth_client, status_session):
        """Test that /latest endpoint returns most recent snapshot."""
        # Clear old data
        status_session.query(LoginNodeStatus).delete()
        status_session.query(DerechoStatus).delete()
        status_session.commit()

        # POST three snapshots with different timestamps
        for i, hour in enumerate([10, 12, 14]):
            data = {
                'timestamp': f'2025-01-25T{hour}:00:00',
                'cpu_nodes_total': 100 + i,  # Different values
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
                'running_jobs': 150 + i,  # Different values
                'pending_jobs': 30,
                'active_users': 50,
                'login_nodes': [
                    {
                        'node_name': 'derecho1',
                        'node_type': 'cpu',
                        'available': True,
                        'degraded': False,
                        'user_count': 10 + i,  # Different values
                        'load_1min': 2.0 + i * 0.5,  # Different values
                        'load_5min': 2.5,
                        'load_15min': 3.0
                    }
                ]
            }

            response = auth_client.post('/api/v1/status/derecho',
                                       json=data,
                                       content_type='application/json')
            assert response.status_code == 201

        # GET latest
        response = auth_client.get('/api/v1/status/derecho/latest')
        assert response.status_code == 200
        result = response.get_json()

        # Should be the 14:00 snapshot (i=2)
        assert result['cpu_nodes_total'] == 102
        assert result['running_jobs'] == 152
        assert result['login_nodes'][0]['user_count'] == 12
        assert result['login_nodes'][0]['load_1min'] == 3.0
