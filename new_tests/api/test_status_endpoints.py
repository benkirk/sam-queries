"""
Tests for System Status API endpoints with simplified schema-based POST.

Phase 4f port: refactored to use the `status_session` fixture (which
DELETEs all status tables on entry) instead of a per-test
`cleanup_database()` helper that opened its own engine. Both the POST
routes and the test fixture now route through Flask-SQLAlchemy's
`db.session` against the per-worker SQLite tempfile bound at
`SQLALCHEMY_BINDS['system_status']`.
"""

from datetime import datetime

import pytest

from system_status import (
    DerechoStatus,
    LoginNodeStatus,
    QueueStatus,
    CasperStatus,
    CasperNodeTypeStatus,
    FilesystemStatus,
)


pytestmark = pytest.mark.integration


# ============================================================================
# POST Endpoint Tests - Simplified with Nested Schema Loading
# ============================================================================

class TestDerechoPost:
    """Tests for POST /api/v1/status/derecho endpoint with nested loading."""

    def test_post_derecho_minimal(self, api_key_client, status_session):
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

        response = api_key_client.post(
            '/api/v1/status/derecho',
            json=data,
            content_type='application/json',
        )

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'status_id' in json_data
        assert 'timestamp' in json_data
        assert json_data['login_node_ids'] == []
        assert json_data['queue_ids'] == []
        assert json_data['filesystem_ids'] == []

    def test_post_derecho_with_nested_objects(self, api_key_client, status_session):
        """Test posting Derecho status with all nested object types."""
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
                    'available': True,
                    'user_count': 10,
                    'load_1min': 2.5,
                }
            ],
            'queues': [
                {
                    'queue_name': 'main',
                    'running_jobs': 100,
                    'pending_jobs': 20,
                }
            ],
            'filesystems': [
                {
                    'filesystem_name': 'glade',
                    'available': True,
                    'capacity_tb': 20000.0,
                }
            ]
        }

        response = api_key_client.post(
            '/api/v1/status/derecho',
            json=data,
            content_type='application/json',
        )

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert len(json_data['login_node_ids']) == 1
        assert len(json_data['queue_ids']) == 1
        assert len(json_data['filesystem_ids']) == 1

    def test_post_derecho_missing_required_field(self, api_key_client, status_session):
        """Test posting Derecho status with missing required field."""
        data = {
            'cpu_nodes_total': 100,
            # Missing many required fields
        }

        response = api_key_client.post(
            '/api/v1/status/derecho',
            json=data,
            content_type='application/json',
        )

        assert response.status_code == 500  # Schema validation error


class TestCasperPost:
    """Tests for POST /api/v1/status/casper endpoint with nested loading."""

    def test_post_casper_minimal(self, api_key_client, status_session):
        """Test posting minimal Casper status data."""
        data = {
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

        response = api_key_client.post(
            '/api/v1/status/casper',
            json=data,
            content_type='application/json',
        )

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert 'status_id' in json_data
        assert 'timestamp' in json_data

    def test_post_casper_with_nested_objects(self, api_key_client, status_session):
        """Test posting Casper status with all nested object types."""
        data = {
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
                {
                    'node_name': 'casper1',
                    'available': True,
                    'user_count': 39,
                }
            ],
            'node_types': [
                {
                    'node_type': 'gpu-v100',
                    'nodes_total': 64,
                    'nodes_available': 42,
                }
            ],
            'queues': [
                {
                    'queue_name': 'casper',
                    'running_jobs': 200,
                }
            ],
            'filesystems': [
                {
                    'filesystem_name': 'campaign',
                    'available': True,
                }
            ]
        }

        response = api_key_client.post(
            '/api/v1/status/casper',
            json=data,
            content_type='application/json',
        )

        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['success'] is True
        assert len(json_data['login_node_ids']) == 1
        assert len(json_data['node_type_ids']) == 1
        assert len(json_data['queue_ids']) == 1
        assert len(json_data['filesystem_ids']) == 1


# ============================================================================
# GET Endpoint Tests - Verify Nested Objects Are Retrieved
# ============================================================================

class TestDerechoGet:
    """Tests for GET /api/v1/status/derecho/latest endpoint."""

    def test_get_derecho_latest(self, api_key_client, status_session):
        """Test retrieving latest Derecho status includes nested objects."""
        # Seed via the API so the route's serialization path is exercised end-to-end
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
                    'available': True,
                    'user_count': 11,
                    'node_type': 'cpu',
                },
                {
                    'node_name': 'derecho2',
                    'available': True,
                    'user_count': 12,
                    'node_type': 'cpu',
                },
            ],
        }
        api_key_client.post('/api/v1/status/derecho', json=data)

        response = api_key_client.get('/api/v1/status/derecho/latest')

        assert response.status_code == 200
        data = response.get_json()

        assert data['cpu_nodes_total'] == 100
        assert data['running_jobs'] == 150
        assert 'timestamp' in data

        assert 'login_nodes' in data
        assert len(data['login_nodes']) == 2
        # Order is not guaranteed, find by name
        node1 = next((n for n in data['login_nodes'] if n['node_name'] == 'derecho1'), None)
        assert node1 is not None
        assert node1['user_count'] == 11

    def test_get_derecho_no_data(self, api_key_client, status_session):
        """Test retrieving Derecho status when no data exists."""
        # status_session fixture pre-cleans, so nothing has been seeded
        response = api_key_client.get('/api/v1/status/derecho/latest')

        assert response.status_code == 404
        data = response.get_json()
        assert 'message' in data


class TestCasperGet:
    """Tests for GET /api/v1/status/casper/latest endpoint."""

    def test_get_casper_latest(self, api_key_client, status_session):
        """Test retrieving latest Casper status includes nested objects."""
        data = {
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
                {
                    'node_name': 'casper1',
                    'available': True,
                    'user_count': 39,
                    'node_type': 'cpu',
                }
            ],
            'node_types': [
                {
                    'node_type': 'gpu-v100',
                    'nodes_total': 64,
                    'nodes_available': 42,
                }
            ]
        }
        api_key_client.post('/api/v1/status/casper', json=data)

        response = api_key_client.get('/api/v1/status/casper/latest')

        assert response.status_code == 200
        data = response.get_json()

        assert data['cpu_nodes_total'] == 151
        assert data['gpu_nodes_total'] == 22
        assert data['running_jobs'] == 456

        assert 'login_nodes' in data
        assert len(data['login_nodes']) == 1
        assert data['login_nodes'][0]['node_name'] == 'casper1'

        assert 'node_types' in data
        assert len(data['node_types']) == 1
        assert data['node_types'][0]['node_type'] == 'gpu-v100'
