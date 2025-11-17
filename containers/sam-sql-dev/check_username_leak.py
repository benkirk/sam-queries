#!/usr/bin/env python3
"""
Quick check for username anonymization leaks in summary/activity tables.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def check_leaks():
    engine = create_engine('mysql+pymysql://root:root@127.0.0.1/sam')

    print("=" * 70)
    print("Checking for Username Anonymization Leaks")
    print("=" * 70)

    with Session(engine) as session:
        # Get anonymized usernames from users table
        result = session.execute(text(
            "SELECT username FROM users WHERE username LIKE 'user_%' LIMIT 5"
        ))
        anon_usernames = [row[0] for row in result.fetchall()]

        print(f"\n[*] Sample anonymized usernames in 'users' table:")
        for username in anon_usernames:
            print(f"    {username}")

        # Check for non-anonymized usernames in summary tables
        print(f"\n[*] Checking for NON-anonymized usernames in summary tables:")

        result = session.execute(text(
            "SELECT DISTINCT username FROM comp_charge_summary "
            "WHERE username NOT LIKE 'user_%' AND username IS NOT NULL LIMIT 10"
        ))
        leaked_usernames = [row[0] for row in result.fetchall()]

        if leaked_usernames:
            print(f"    ✗ LEAK FOUND in comp_charge_summary!")
            print(f"    Found {len(leaked_usernames)} non-anonymized usernames:")
            for username in leaked_usernames:
                print(f"      - {username}")
        else:
            print(f"    ✓ No leaks in comp_charge_summary")

        # Check activity table
        result = session.execute(text(
            "SELECT DISTINCT username FROM hpc_activity "
            "WHERE username NOT LIKE 'user_%' AND username IS NOT NULL LIMIT 10"
        ))
        leaked_activity = [row[0] for row in result.fetchall()]

        if leaked_activity:
            print(f"\n    ✗ LEAK FOUND in hpc_activity!")
            print(f"    Found {len(leaked_activity)} non-anonymized usernames:")
            for username in leaked_activity:
                print(f"      - {username}")
        else:
            print(f"\n    ✓ No leaks in hpc_activity")

        # Count total leaks
        result = session.execute(text(
            "SELECT COUNT(DISTINCT username) FROM comp_charge_summary "
            "WHERE username NOT LIKE 'user_%' AND username NOT IN ('benkirk', 'csgteam')"
        ))
        total_leaked = result.scalar()

        print(f"\n" + "=" * 70)
        print(f"SUMMARY: {total_leaked} non-anonymized usernames found in comp_charge_summary")
        print("(excluding preserved users: benkirk, csgteam)")
        print("=" * 70)


if __name__ == '__main__':
    check_leaks()
