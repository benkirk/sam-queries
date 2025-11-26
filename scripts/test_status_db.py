#!/usr/bin/env python3
"""
Test System Status Database Connection

Quick test to verify the database setup is working correctly.

Usage:
    python scripts/test_status_db.py
"""

import sys
from pathlib import Path
from datetime import datetime

# Add python directory to path
python_dir = Path(__file__).parent.parent / 'python'
sys.path.insert(0, str(python_dir))

from system_status import create_status_engine, get_session
from system_status.models import (
    DerechoStatus, CasperStatus, JupyterHubStatus,
    SystemOutage, ResourceReservation
)


def test_connection():
    """Test database connection and basic operations."""
    print("=" * 80)
    print("System Status Database Connection Test")
    print("=" * 80)

    # Test connection
    print("\n1. Testing connection...")
    try:
        engine, SessionLocal = create_status_engine()
        print("   ✓ Connection successful")
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False

    # Test session creation
    print("\n2. Testing session creation...")
    try:
        with get_session(SessionLocal) as session:
            print("   ✓ Session created successfully")

            # Test query (should return empty results initially)
            print("\n3. Testing queries...")
            derecho_count = session.query(DerechoStatus).count()
            casper_count = session.query(CasperStatus).count()
            jupyterhub_count = session.query(JupyterHubStatus).count()
            outage_count = session.query(SystemOutage).count()
            reservation_count = session.query(ResourceReservation).count()

            print(f"   ✓ Derecho status records: {derecho_count}")
            print(f"   ✓ Casper status records: {casper_count}")
            print(f"   ✓ JupyterHub status records: {jupyterhub_count}")
            print(f"   ✓ System outage records: {outage_count}")
            print(f"   ✓ Resource reservation records: {reservation_count}")

    except Exception as e:
        print(f"   ❌ Session test failed: {e}")
        return False

    print("\n" + "=" * 80)
    print("✓ All tests passed!")
    print("=" * 80)
    print("\nDatabase is ready for use.")
    print("Next: Ingest mock data with python scripts/ingest_mock_status.py")
    return True


if __name__ == '__main__':
    success = test_connection()
    sys.exit(0 if success else 1)
