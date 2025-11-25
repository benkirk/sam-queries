#!/usr/bin/env python3
"""
Ingest Mock System Status Data

Loads mock data from tests/mock_data/status_mock_data.json and inserts
it into the system_status database using the ORM models directly.

This script bypasses the API and directly inserts via ORM for quick testing.
For API testing, use the API test suite.

Usage:
    python scripts/ingest_mock_status.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add python directory to path
python_dir = Path(__file__).parent.parent / 'python'
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus, DerechoQueueStatus, DerechoFilesystemStatus,
    DerechoLoginNodeStatus,
    CasperStatus, CasperNodeTypeStatus, CasperQueueStatus,
    CasperLoginNodeStatus,
    JupyterHubStatus,
    SystemOutage, ResourceReservation
)


def parse_timestamp(ts_str):
    """Parse ISO timestamp string to datetime."""
    return datetime.fromisoformat(ts_str)


def ingest_mock_data():
    """Load and ingest mock data from JSON file."""
    print("=" * 80)
    print("Ingesting Mock System Status Data")
    print("=" * 80)

    # Load mock data
    mock_data_file = Path(__file__).parent.parent / 'tests' / 'mock_data' / 'status_mock_data.json'
    print(f"\nLoading mock data from: {mock_data_file}")

    try:
        with open(mock_data_file, 'r') as f:
            mock_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ ERROR: Mock data file not found: {mock_data_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ ERROR: Invalid JSON in mock data file: {e}")
        sys.exit(1)

    print("✓ Mock data loaded successfully")

    # Connect to database
    print("\nConnecting to database...")
    try:
        engine, SessionLocal = create_status_engine()
    except Exception as e:
        print(f"❌ ERROR: Database connection failed: {e}")
        sys.exit(1)

    print("✓ Connected successfully")

    # Ingest data
    print("\nIngesting data...")
    with get_session(SessionLocal) as session:
        # Derecho
        print("\n1. Ingesting Derecho status...")
        derecho_data = mock_data['derecho']
        timestamp = parse_timestamp(derecho_data['timestamp'])

        derecho_status = DerechoStatus(
            timestamp=timestamp,
            cpu_nodes_total=derecho_data['cpu_nodes_total'],
            cpu_nodes_available=derecho_data['cpu_nodes_available'],
            cpu_nodes_down=derecho_data['cpu_nodes_down'],
            cpu_nodes_reserved=derecho_data['cpu_nodes_reserved'],
            gpu_nodes_total=derecho_data['gpu_nodes_total'],
            gpu_nodes_available=derecho_data['gpu_nodes_available'],
            gpu_nodes_down=derecho_data['gpu_nodes_down'],
            gpu_nodes_reserved=derecho_data['gpu_nodes_reserved'],
            cpu_cores_total=derecho_data['cpu_cores_total'],
            cpu_cores_allocated=derecho_data['cpu_cores_allocated'],
            cpu_cores_idle=derecho_data['cpu_cores_idle'],
            cpu_utilization_percent=derecho_data['cpu_utilization_percent'],
            gpu_count_total=derecho_data['gpu_count_total'],
            gpu_count_allocated=derecho_data['gpu_count_allocated'],
            gpu_count_idle=derecho_data['gpu_count_idle'],
            gpu_utilization_percent=derecho_data['gpu_utilization_percent'],
            memory_total_gb=derecho_data['memory_total_gb'],
            memory_allocated_gb=derecho_data['memory_allocated_gb'],
            memory_utilization_percent=derecho_data['memory_utilization_percent'],
            running_jobs=derecho_data['running_jobs'],
            pending_jobs=derecho_data['pending_jobs'],
            active_users=derecho_data['active_users'],
        )
        session.add(derecho_status)
        print(f"   ✓ Derecho main status")

        # Derecho login nodes
        for node_data in derecho_data['login_nodes']:
            login_node = DerechoLoginNodeStatus(
                timestamp=timestamp,
                node_name=node_data['node_name'],
                node_type=node_data['node_type'],
                available=node_data['available'],
                degraded=node_data['degraded'],
                user_count=node_data.get('user_count'),
                load_1min=node_data.get('load_1min'),
                load_5min=node_data.get('load_5min'),
                load_15min=node_data.get('load_15min'),
            )
            session.add(login_node)
            print(f"   ✓ Derecho login node: {node_data['node_name']} ({node_data['node_type']})")

        # Derecho queues
        for queue_data in derecho_data['queues']:
            queue_status = DerechoQueueStatus(
                timestamp=timestamp,
                queue_name=queue_data['queue_name'],
                running_jobs=queue_data['running_jobs'],
                pending_jobs=queue_data['pending_jobs'],
                active_users=queue_data['active_users'],
                cores_allocated=queue_data['cores_allocated'],
                gpus_allocated=queue_data['gpus_allocated'],
                nodes_allocated=queue_data['nodes_allocated'],
            )
            session.add(queue_status)
            print(f"   ✓ Derecho queue: {queue_data['queue_name']}")

        # Derecho filesystems
        for fs_data in derecho_data['filesystems']:
            fs_status = DerechoFilesystemStatus(
                timestamp=timestamp,
                filesystem_name=fs_data['filesystem_name'],
                available=fs_data['available'],
                degraded=fs_data['degraded'],
                capacity_tb=fs_data['capacity_tb'],
                used_tb=fs_data['used_tb'],
                utilization_percent=fs_data['utilization_percent'],
            )
            session.add(fs_status)
            print(f"   ✓ Derecho filesystem: {fs_data['filesystem_name']}")

        # Casper
        print("\n2. Ingesting Casper status...")
        casper_data = mock_data['casper']
        timestamp = parse_timestamp(casper_data['timestamp'])

        casper_status = CasperStatus(
            timestamp=timestamp,
            compute_nodes_total=casper_data['compute_nodes_total'],
            compute_nodes_available=casper_data['compute_nodes_available'],
            compute_nodes_down=casper_data['compute_nodes_down'],
            cpu_utilization_percent=casper_data['cpu_utilization_percent'],
            gpu_utilization_percent=casper_data['gpu_utilization_percent'],
            memory_utilization_percent=casper_data['memory_utilization_percent'],
            running_jobs=casper_data['running_jobs'],
            pending_jobs=casper_data['pending_jobs'],
            active_users=casper_data['active_users'],
        )
        session.add(casper_status)
        print(f"   ✓ Casper main status")

        # Casper login nodes
        for node_data in casper_data['login_nodes']:
            login_node = CasperLoginNodeStatus(
                timestamp=timestamp,
                node_name=node_data['node_name'],
                available=node_data['available'],
                degraded=node_data['degraded'],
                user_count=node_data.get('user_count'),
                load_1min=node_data.get('load_1min'),
                load_5min=node_data.get('load_5min'),
                load_15min=node_data.get('load_15min'),
            )
            session.add(login_node)
            print(f"   ✓ Casper login node: {node_data['node_name']}")

        # Casper node types
        for nt_data in casper_data['node_types']:
            nt_status = CasperNodeTypeStatus(
                timestamp=timestamp,
                node_type=nt_data['node_type'],
                nodes_total=nt_data['nodes_total'],
                nodes_available=nt_data['nodes_available'],
                nodes_down=nt_data['nodes_down'],
                nodes_allocated=nt_data['nodes_allocated'],
                cores_per_node=nt_data['cores_per_node'],
                memory_gb_per_node=nt_data['memory_gb_per_node'],
                gpu_model=nt_data['gpu_model'],
                gpus_per_node=nt_data['gpus_per_node'],
                utilization_percent=nt_data['utilization_percent'],
            )
            session.add(nt_status)
            print(f"   ✓ Casper node type: {nt_data['node_type']}")

        # Casper queues
        for queue_data in casper_data['queues']:
            queue_status = CasperQueueStatus(
                timestamp=timestamp,
                queue_name=queue_data['queue_name'],
                running_jobs=queue_data['running_jobs'],
                pending_jobs=queue_data['pending_jobs'],
                active_users=queue_data['active_users'],
                cores_allocated=queue_data['cores_allocated'],
                nodes_allocated=queue_data['nodes_allocated'],
            )
            session.add(queue_status)
            print(f"   ✓ Casper queue: {queue_data['queue_name']}")

        # JupyterHub
        print("\n3. Ingesting JupyterHub status...")
        jupyterhub_data = mock_data['jupyterhub']
        timestamp = parse_timestamp(jupyterhub_data['timestamp'])

        jupyterhub_status = JupyterHubStatus(
            timestamp=timestamp,
            available=jupyterhub_data['available'],
            active_users=jupyterhub_data['active_users'],
            active_sessions=jupyterhub_data['active_sessions'],
            cpu_utilization_percent=jupyterhub_data['cpu_utilization_percent'],
            memory_utilization_percent=jupyterhub_data['memory_utilization_percent'],
        )
        session.add(jupyterhub_status)
        print(f"   ✓ JupyterHub status")

        # Outages
        print("\n4. Ingesting outages...")
        for outage_data in mock_data['outages']:
            outage = SystemOutage(
                system_name=outage_data['system_name'],
                component=outage_data['component'],
                title=outage_data['title'],
                description=outage_data['description'],
                severity=outage_data['severity'],
                status=outage_data['status'],
                start_time=parse_timestamp(outage_data['start_time']),
                estimated_resolution=parse_timestamp(outage_data['estimated_resolution']) if outage_data.get('estimated_resolution') else None,
            )
            session.add(outage)
            print(f"   ✓ Outage: {outage_data['title']}")

        # Reservations
        print("\n5. Ingesting reservations...")
        for res_data in mock_data['reservations']:
            reservation = ResourceReservation(
                system_name=res_data['system_name'],
                reservation_name=res_data['reservation_name'],
                description=res_data['description'],
                start_time=parse_timestamp(res_data['start_time']),
                end_time=parse_timestamp(res_data['end_time']),
                node_count=res_data['node_count'],
                partition=res_data['partition'],
            )
            session.add(reservation)
            print(f"   ✓ Reservation: {res_data['reservation_name']}")

        # Commit all changes
        print("\nCommitting to database...")
        session.commit()

    print("\n" + "=" * 80)
    print("✓ Mock data ingested successfully!")
    print("=" * 80)
    print("\nNext steps:")
    print("  1. Query the data: python scripts/test_status_db.py")
    print("  2. Test API endpoints manually or via automated tests")
    print("  3. View in dashboard (once UI is implemented)")


if __name__ == '__main__':
    ingest_mock_data()
