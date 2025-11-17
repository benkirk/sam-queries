#!/usr/bin/env python3
"""
Test script to verify username anonymization in summary and activity tables.

This script:
1. Samples usernames from the users table
2. Checks corresponding records in summary/activity tables
3. Verifies username consistency across all tables
"""

import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def test_username_consistency(connection_string: str):
    """
    Test that usernames are consistent between users table and summary/activity tables.
    """
    engine = create_engine(connection_string)

    print("=" * 70)
    print("Username Anonymization Consistency Test")
    print("=" * 70)

    with Session(engine) as session:
        # Get sample users
        print("\n[*] Fetching sample users...")
        result = session.execute(text(
            "SELECT user_id, username FROM users LIMIT 10"
        ))
        sample_users = result.fetchall()

        print(f"  Found {len(sample_users)} sample users")

        # Test each summary table
        summary_tables = [
            'comp_charge_summary',
            'dav_charge_summary',
            'disk_charge_summary',
            'archive_charge_summary',
            'hpc_charge_summary'
        ]

        print("\n[*] Testing charge summary tables...")
        for table_name in summary_tables:
            print(f"\n  Checking {table_name}:")

            for user_id, username in sample_users:
                # Check if username matches in summary table
                result = session.execute(text(
                    f"SELECT username, act_username FROM {table_name} "
                    f"WHERE user_id = :user_id LIMIT 5"
                ), {'user_id': user_id})

                records = result.fetchall()

                if records:
                    for rec_username, rec_act_username in records:
                        if rec_username != username:
                            print(f"    ✗ MISMATCH: user_id={user_id}, "
                                  f"users.username='{username}', "
                                  f"{table_name}.username='{rec_username}'")
                            return False
                        if rec_act_username and rec_act_username != username:
                            print(f"    ✗ MISMATCH: user_id={user_id}, "
                                  f"users.username='{username}', "
                                  f"{table_name}.act_username='{rec_act_username}'")
                            return False

            print(f"    ✓ {table_name} usernames consistent")

        # Test activity tables
        activity_tables = [
            ('hpc_activity', 'hpc_activity_id'),
            ('dav_activity', 'dav_activity_id'),
            ('disk_activity', 'disk_activity_id'),
            ('archive_activity', 'archive_activity_id')
        ]

        print("\n[*] Testing activity tables...")
        for table_name, id_column in activity_tables:
            print(f"\n  Checking {table_name}:")

            for user_id, username in sample_users:
                # Activity tables don't have user_id, so we check by username
                result = session.execute(text(
                    f"SELECT username FROM {table_name} "
                    f"WHERE username = :username LIMIT 5"
                ), {'username': username})

                records = result.fetchall()

                if records:
                    for (rec_username,) in records:
                        if rec_username != username:
                            print(f"    ✗ MISMATCH: expected username='{username}', "
                                  f"got '{rec_username}' in {table_name}")
                            return False

            print(f"    ✓ {table_name} usernames consistent")

        # Check for any remaining non-anonymized usernames
        print("\n[*] Checking for non-anonymized usernames...")

        # Get all usernames from users table
        result = session.execute(text("SELECT DISTINCT username FROM users"))
        all_usernames = {row[0] for row in result.fetchall()}

        # Check summary tables for usernames not in users table
        for table_name in summary_tables:
            result = session.execute(text(
                f"SELECT DISTINCT username FROM {table_name} WHERE username IS NOT NULL"
            ))
            summary_usernames = {row[0] for row in result.fetchall()}

            orphaned = summary_usernames - all_usernames
            if orphaned:
                print(f"  ⚠️  {table_name} has {len(orphaned)} usernames not in users table")
                print(f"     Examples: {list(orphaned)[:5]}")
            else:
                print(f"  ✓ {table_name} - all usernames exist in users table")

        # Check activity tables for usernames not in users table
        for table_name, _ in activity_tables:
            result = session.execute(text(
                f"SELECT DISTINCT username FROM {table_name} WHERE username IS NOT NULL"
            ))
            activity_usernames = {row[0] for row in result.fetchall()}

            orphaned = activity_usernames - all_usernames
            if orphaned:
                print(f"  ⚠️  {table_name} has {len(orphaned)} usernames not in users table")
                print(f"     Examples: {list(orphaned)[:5]}")
            else:
                print(f"  ✓ {table_name} - all usernames exist in users table")

    print("\n" + "=" * 70)
    print("✓ All username consistency tests passed!")
    print("=" * 70)
    return True


def main():
    connection_string = 'mysql+pymysql://root:root@127.0.0.1/sam'

    try:
        success = test_username_consistency(connection_string)
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
