#!/usr/bin/env python3
"""
System Status Database Setup Script

Creates the system_status database (if it does not exist) and all its tables
using SQLAlchemy ORM models. Works with both MySQL and PostgreSQL backends
(controlled by STATUS_DB_DRIVER env var).

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
python_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(python_dir))

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from sqlalchemy import create_engine, text
from system_status import StatusBase, create_status_engine


def ensure_database_exists():
    """
    Create the STATUS_DB database if it does not already exist.

    Connects to the appropriate admin database (postgres / no-db for MySQL),
    checks for existence, and issues CREATE DATABASE when needed.
    """
    driver   = os.getenv('STATUS_DB_DRIVER', 'mysql').lower()
    username = os.environ['STATUS_DB_USERNAME']
    password = os.environ['STATUS_DB_PASSWORD']
    server   = os.environ['STATUS_DB_SERVER']
    database = os.getenv('STATUS_DB_NAME', 'system_status')

    if driver in ('postgresql', 'postgres'):
        admin_url = f"postgresql+psycopg2://{username}:{password}@{server}/postgres"
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db"),
                {"db": database}
            ).fetchone()
            if row:
                print(f'  Database "{database}" already exists.')
            else:
                conn.execute(text(f'CREATE DATABASE "{database}"'))
                print(f'  Created database "{database}".')
        admin_engine.dispose()
    else:
        admin_url = f"mysql+pymysql://{username}:{password}@{server}"
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        print(f'  Database "{database}" ensured (MySQL).')
        admin_engine.dispose()


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

    # Ensure database exists before connecting
    print("\nChecking database exists...")
    try:
        ensure_database_exists()
    except Exception as e:
        print(f"\n❌ ERROR: Could not create/verify database: {e}")
        sys.exit(1)

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
