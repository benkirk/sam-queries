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
python_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    FilesystemStatus,
    JupyterHubStatus,
    LoginNodeStatus,
    QueueStatus,
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
            held_jobs=derecho_data['held_jobs'],
            active_users=derecho_data['active_users'],
        )
        print(f"   ✓ Derecho main status")

        # Derecho login nodes - add via relationship so FK is set automatically
        for node_data in derecho_data['login_nodes']:
            login_node = LoginNodeStatus(
                timestamp=timestamp,
                node_name=node_data['node_name'],
                node_type=node_data['node_type'],
                system_name='derecho',
                available=node_data['available'],
                degraded=node_data['degraded'],
                user_count=node_data.get('user_count'),
                load_1min=node_data.get('load_1min'),
                load_5min=node_data.get('load_5min'),
                load_15min=node_data.get('load_15min'),
            )
            derecho_status.login_nodes.append(login_node)  # FK set via relationship
            print(f"   ✓ Derecho login node: {node_data['node_name']} ({node_data['node_type']})")

        # Derecho queues - add via relationship so FK is set automatically
        for queue_data in derecho_data['queues']:
            queue_status = QueueStatus(
                timestamp=timestamp,
                queue_name=queue_data['queue_name'],
                system_name='derecho',
                running_jobs=queue_data['running_jobs'],
                pending_jobs=queue_data['pending_jobs'],
                held_jobs=queue_data['held_jobs'],
                active_users=queue_data['active_users'],
                cores_allocated=queue_data['cores_allocated'],
                gpus_allocated=queue_data['gpus_allocated'],
                nodes_allocated=queue_data['nodes_allocated'],
            )
            derecho_status.queues.append(queue_status)  # FK set via relationship
            print(f"   ✓ Derecho queue: {queue_data['queue_name']}")

        # Derecho filesystems - add via relationship so FK is set automatically
        for fs_data in derecho_data['filesystems']:
            fs_status = FilesystemStatus(
                timestamp=timestamp,
                filesystem_name=fs_data['filesystem_name'],
                system_name='derecho',
                available=fs_data['available'],
                degraded=fs_data['degraded'],
                capacity_tb=fs_data['capacity_tb'],
                used_tb=fs_data['used_tb'],
                utilization_percent=fs_data['utilization_percent'],
            )
            derecho_status.filesystems.append(fs_status)  # FK set via relationship
            print(f"   ✓ Derecho filesystem: {fs_data['filesystem_name']}")

        # Add derecho_status to session (cascades to all children)
        session.add(derecho_status)

        # Casper
        print("\n2. Ingesting Casper status...")
        casper_data = mock_data['casper']
        timestamp = parse_timestamp(casper_data['timestamp'])

        casper_status = CasperStatus(
            timestamp=timestamp,
            cpu_nodes_total=casper_data['cpu_nodes_total'],
            cpu_nodes_available=casper_data['cpu_nodes_available'],
            cpu_nodes_down=casper_data['cpu_nodes_down'],
            cpu_nodes_reserved=casper_data['cpu_nodes_reserved'],
            gpu_nodes_total=casper_data['gpu_nodes_total'],
            gpu_nodes_available=casper_data['gpu_nodes_available'],
            gpu_nodes_down=casper_data['gpu_nodes_down'],
            gpu_nodes_reserved=casper_data['gpu_nodes_reserved'],
            viz_nodes_total=casper_data['viz_nodes_total'],
            viz_nodes_available=casper_data['viz_nodes_available'],
            viz_nodes_down=casper_data['viz_nodes_down'],
            viz_nodes_reserved=casper_data['viz_nodes_reserved'],
            cpu_cores_total=casper_data['cpu_cores_total'],
            cpu_cores_allocated=casper_data['cpu_cores_allocated'],
            cpu_cores_idle=casper_data['cpu_cores_idle'],
            cpu_utilization_percent=casper_data['cpu_utilization_percent'],
            gpu_count_total=casper_data['gpu_count_total'],
            gpu_count_allocated=casper_data['gpu_count_allocated'],
            gpu_count_idle=casper_data['gpu_count_idle'],
            gpu_utilization_percent=casper_data['gpu_utilization_percent'],
            viz_count_total=casper_data['viz_count_total'],
            viz_count_allocated=casper_data['viz_count_allocated'],
            viz_count_idle=casper_data['viz_count_idle'],
            viz_utilization_percent=casper_data['viz_utilization_percent'],
            memory_total_gb=casper_data['memory_total_gb'],
            memory_allocated_gb=casper_data['memory_allocated_gb'],
            memory_utilization_percent=casper_data['memory_utilization_percent'],
            running_jobs=casper_data['running_jobs'],
            pending_jobs=casper_data['pending_jobs'],
            held_jobs=casper_data['held_jobs'],
            active_users=casper_data['active_users'],
        )
        print(f"   ✓ Casper main status")

        # Casper login nodes - add via relationship so FK is set automatically
        for node_data in casper_data['login_nodes']:
            login_node = LoginNodeStatus(
                timestamp=timestamp,
                node_name=node_data['node_name'],
                node_type='cpu',
                system_name='casper',
                available=node_data['available'],
                degraded=node_data['degraded'],
                user_count=node_data.get('user_count'),
                load_1min=node_data.get('load_1min'),
                load_5min=node_data.get('load_5min'),
                load_15min=node_data.get('load_15min'),
            )
            casper_status.login_nodes.append(login_node)  # FK set via relationship
            print(f"   ✓ Casper login node: {node_data['node_name']}")

        # Casper node types - add via relationship so FK is set automatically
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
            casper_status.node_types.append(nt_status)  # FK set via relationship
            print(f"   ✓ Casper node type: {nt_data['node_type']}")

        # Casper queues - add via relationship so FK is set automatically
        for queue_data in casper_data['queues']:
            queue_status = QueueStatus(
                timestamp=timestamp,
                queue_name=queue_data['queue_name'],
                system_name='casper',
                running_jobs=queue_data['running_jobs'],
                pending_jobs=queue_data['pending_jobs'],
                held_jobs=queue_data['held_jobs'],
                active_users=queue_data['active_users'],
                cores_allocated=queue_data['cores_allocated'],
                nodes_allocated=queue_data['nodes_allocated'],
            )
            casper_status.queues.append(queue_status)  # FK set via relationship
            print(f"   ✓ Casper queue: {queue_data['queue_name']}")

        # Casper filesystems - add via relationship so FK is set automatically
        if 'filesystems' in casper_data:
            for fs_data in casper_data['filesystems']:
                fs_status = FilesystemStatus(
                    timestamp=timestamp,
                    filesystem_name=fs_data['filesystem_name'],
                    system_name='casper',
                    available=fs_data['available'],
                    degraded=fs_data['degraded'],
                    capacity_tb=fs_data['capacity_tb'],
                    used_tb=fs_data['used_tb'],
                    utilization_percent=fs_data['utilization_percent'],
                )
                casper_status.filesystems.append(fs_status)  # FK set via relationship
                print(f"   ✓ Casper filesystem: {fs_data['filesystem_name']}")

        # Add casper_status to session (cascades to all children)
        session.add(casper_status)

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
        # Make reservations upcoming by offsetting to future dates
        # This ensures they appear in the dashboard (which filters for end_time >= now)
        from datetime import timedelta
        now = datetime.now()

        for idx, res_data in enumerate(mock_data['reservations']):
            # Parse original times to get time-of-day
            orig_start = parse_timestamp(res_data['start_time'])
            orig_end = parse_timestamp(res_data['end_time'])

            # Calculate duration
            duration = orig_end - orig_start

            # Offset to future: first reservation is tomorrow, second is next week
            days_offset = 1 if idx == 0 else 7
            new_start = now.replace(
                hour=orig_start.hour,
                minute=orig_start.minute,
                second=0,
                microsecond=0
            ) + timedelta(days=days_offset)
            new_end = new_start + duration

            reservation = ResourceReservation(
                system_name=res_data['system_name'],
                reservation_name=res_data['reservation_name'],
                description=res_data['description'],
                start_time=new_start,
                end_time=new_end,
                node_count=res_data['node_count'],
                partition=res_data['partition'],
            )
            session.add(reservation)
            print(f"   ✓ Reservation: {res_data['reservation_name']} ({new_start.strftime('%Y-%m-%d %H:%M')})")

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
