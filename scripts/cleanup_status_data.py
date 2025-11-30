#!/usr/bin/env python3
"""
Clean up old system status data.

Removes status snapshots older than the retention period (default: 7 days).
Intended to be run daily via cron.

Usage:
    python scripts/cleanup_status_data.py [--retention-days 7] [--dry-run]

Cron example:
    0 2 * * * /path/to/python scripts/cleanup_status_data.py >> /var/log/status_cleanup.log 2>&1
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse

# Add python directory to path
python_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    JupyterHubStatus,
    FilesystemStatus,
    QueueStatus,
    LoginNodeStatus,
    SystemOutage, ResourceReservation
)


def cleanup_old_data(retention_days=7, dry_run=False):
    """
    Delete status data older than retention period.

    Args:
        retention_days: Number of days to retain (default: 7)
        dry_run: If True, only count records without deleting
    """
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    print("=" * 80)
    print(f"System Status Data Cleanup - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print(f"\nRetention period: {retention_days} days")
    print(f"Cutoff date: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN (no deletions)' if dry_run else 'DELETE'}")
    print()

    # Connect to database
    try:
        engine, SessionLocal = create_status_engine()
    except Exception as e:
        print(f"❌ ERROR: Database connection failed: {e}")
        sys.exit(1)

    deletion_counts = {}

    with get_session(SessionLocal) as session:
        # Define tables to clean
        tables_to_clean = [
            (DerechoStatus, 'derecho_status'),
            (CasperStatus, 'casper_status'),
            (CasperNodeTypeStatus, 'casper_node_type_status'),
            (QueueStatus, 'queue_status'),
            (FilesystemStatus, 'filesystem_status'),
            (LoginNodeStatus, 'login_node_status'),
            (JupyterHubStatus, 'jupyterhub_status'),
        ]

        print("Checking status snapshot tables...")
        print("-" * 80)

        total_deleted = 0
        for model_class, table_name in tables_to_clean:
            # Count old records
            count = session.query(model_class).filter(
                model_class.timestamp < cutoff_date
            ).count()

            deletion_counts[table_name] = count

            if count > 0:
                print(f"{table_name:40s} {count:8,} records")

                if not dry_run:
                    # Delete old records
                    deleted = session.query(model_class).filter(
                        model_class.timestamp < cutoff_date
                    ).delete(synchronize_session=False)

                    if deleted != count:
                        print(f"  ⚠️  Warning: Expected {count}, deleted {deleted}")

                total_deleted += count
            else:
                print(f"{table_name:40s} {count:8,} records (none to delete)")

        # Handle outages and reservations (don't use timestamp field)
        print()
        print("Checking support tables...")
        print("-" * 80)

        # Clean resolved outages older than retention period
        resolved_outages = session.query(SystemOutage).filter(
            SystemOutage.status == 'resolved',
            SystemOutage.end_time < cutoff_date
        ).count()

        deletion_counts['system_outages'] = resolved_outages

        if resolved_outages > 0:
            print(f"{'system_outages (resolved)':40s} {resolved_outages:8,} records")

            if not dry_run:
                session.query(SystemOutage).filter(
                    SystemOutage.status == 'resolved',
                    SystemOutage.end_time < cutoff_date
                ).delete(synchronize_session=False)

            total_deleted += resolved_outages
        else:
            print(f"{'system_outages (resolved)':40s} {resolved_outages:8,} records (none to delete)")

        # Clean past reservations
        past_reservations = session.query(ResourceReservation).filter(
            ResourceReservation.end_time < cutoff_date
        ).count()

        deletion_counts['resource_reservations'] = past_reservations

        if past_reservations > 0:
            print(f"{'resource_reservations (past)':40s} {past_reservations:8,} records")

            if not dry_run:
                session.query(ResourceReservation).filter(
                    ResourceReservation.end_time < cutoff_date
                ).delete(synchronize_session=False)

            total_deleted += past_reservations
        else:
            print(f"{'resource_reservations (past)':40s} {past_reservations:8,} records (none to delete)")

        # Commit deletions
        if not dry_run and total_deleted > 0:
            print()
            print("Committing deletions...")
            session.commit()
            print("✓ Commit successful")

    # Summary
    print()
    print("=" * 80)
    if dry_run:
        print(f"DRY RUN complete - {total_deleted:,} records WOULD BE deleted")
    else:
        print(f"Cleanup complete - {total_deleted:,} records deleted")
    print("=" * 80)

    # Detailed breakdown
    if total_deleted > 0:
        print("\nDeletion breakdown:")
        for table_name, count in deletion_counts.items():
            if count > 0:
                print(f"  {table_name}: {count:,}")

    return total_deleted


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clean up old system status data')
    parser.add_argument('--retention-days', type=int, default=7,
                       help='Number of days to retain (default: 7)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Count records without deleting')

    args = parser.parse_args()

    try:
        deleted_count = cleanup_old_data(
            retention_days=args.retention_days,
            dry_run=args.dry_run
        )
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR: Cleanup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
