#!/usr/bin/env python3
"""
System Status Database Setup Script

Creates all tables for the system_status database using SQLAlchemy ORM models.
This script should be run after creating the database with create_status_db.sql.

Usage:
    python scripts/setup_status_db.py [--drop]

Options:
    --drop, --drop-existing    Drop existing tables before creating (DANGEROUS!)
    -y, --yes                  Skip confirmation prompts
"""

import sys
import os
from pathlib import Path

# Add python directory to path
python_dir = Path(__file__).parent.parent / 'python'
sys.path.insert(0, str(python_dir))

from system_status import StatusBase, create_status_engine
from system_status.models import (
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    LoginNodeStatus,
    QueueStatus,
    FilesystemStatus,
    JupyterHubStatus,
    SystemOutage, ResourceReservation
)


def setup_database(drop_existing=False, yes=False):
    """
    Create all tables in the system_status database.

    Args:
        drop_existing: If True, drop all existing tables before creating (DANGEROUS!)
        yes: If True, skip confirmation prompts
    """
    print("=" * 80)
    print("System Status Database Setup")
    print("=" * 80)

    # Create engine
    print("\nConnecting to database...")
    try:
        engine, SessionLocal = create_status_engine()
    except Exception as e:
        print(f"\n❌ ERROR: Failed to connect to database: {e}")
        print("\nPlease ensure:")
        print("  1. The system_status database exists (run create_status_db.sql)")
        print("  2. Environment variables are set: STATUS_DB_USERNAME, STATUS_DB_PASSWORD, STATUS_DB_SERVER")
        print("  3. Database server is running and accessible")
        sys.exit(1)

    print("✓ Connected successfully")

    # Drop existing tables if requested
    if drop_existing:
        print("\n⚠️  WARNING: Dropping all existing tables...")
        if not yes:
            response = input("Are you sure? This will DELETE ALL DATA! (yes/no): ")
            if response.lower() != 'yes':
                print("Aborted.")
                sys.exit(0)

        print("Dropping tables...")
        StatusBase.metadata.drop_all(engine)
        print("✓ Tables dropped")

    # Create all tables
    print("\nCreating tables...")
    StatusBase.metadata.create_all(engine)
    print("✓ Tables created successfully")

    print("\n" + "=" * 80)
    print("Database setup complete!")
    print("=" * 80)
    print("\nNext steps:")
    print("  1. Test the setup: python scripts/test_status_db.py")
    print("  2. Ingest mock data: python scripts/ingest_mock_status.py")
    print("  3. Implement API endpoints (Phase 1B)")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Setup system_status database tables')
    parser.add_argument('--drop', '--drop-existing', dest='drop_existing', action='store_true',
                       help='Drop existing tables before creating (DANGEROUS!)')
    parser.add_argument('--yes', '-y', action='store_true',
                       help='Skip confirmation prompts')

    args = parser.parse_args()

    setup_database(drop_existing=args.drop_existing, yes=args.yes)
