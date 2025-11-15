#!/usr/bin/env python3
"""
Verify SAM database anonymization was successful.

Checks for:
- Common real names/usernames
- Real email domains
- Suspicious patterns indicating incomplete anonymization
"""

import re
import sys
from pathlib import Path
from typing import List

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


class AnonymizationVerifier:
    """Verify database has been properly anonymized."""

    def __init__(self, connection_string: str, preserve_usernames: List[str] = None):
        self.engine = create_engine(connection_string)
        self.preserve_usernames = set(preserve_usernames or [])
        self.issues = []

    def check_common_names(self, session: Session) -> int:
        """Check for common real names that shouldn't exist after anonymization."""
        print("\n[*] Checking for common real names...")

        common_first_names = [
            'John', 'Jane', 'Michael', 'Sarah', 'David', 'Jennifer',
            'Robert', 'Mary', 'William', 'Patricia', 'James', 'Linda'
        ]

        common_last_names = [
            'Johnson', 'Williams', 'Jones', 'Brown', 'Davis', 'Miller',
            'Wilson', 'Moore', 'Taylor', 'Anderson', 'Thomas', 'Jackson'
        ]

        issues = 0
        for name in common_first_names:
            result = session.execute(text(
                f"SELECT COUNT(*) as cnt FROM users WHERE first_name = :name"
            ), {'name': name})
            count = result.fetchone()[0]
            if count > 10:  # Threshold for suspicion
                print(f"  ⚠️  Found {count} users with first_name='{name}'")
                issues += 1

        for name in common_last_names:
            result = session.execute(text(
                f"SELECT COUNT(*) as cnt FROM users WHERE last_name = :name"
            ), {'name': name})
            count = result.fetchone()[0]
            if count > 10:
                print(f"  ⚠️  Found {count} users with last_name='{name}'")
                issues += 1

        if issues == 0:
            print("  ✓ No common real names found (expected for anonymized data)")
        else:
            print(f"  ⚠️  Found {issues} potential issues with common names")

        return issues

    def check_email_domains(self, session: Session) -> int:
        """Check for real email domains that should have been anonymized."""
        print("\n[*] Checking email domains...")

        result = session.execute(text(
            "SELECT email_address FROM email_address LIMIT 100"
        ))
        emails = [row[0] for row in result.fetchall()]

        anon_count = sum(1 for e in emails if 'anon-' in e)
        real_count = len(emails) - anon_count

        print(f"  Sample of 100 emails:")
        print(f"    Anonymized (anon-*): {anon_count}")
        print(f"    Potentially real:    {real_count}")

        if anon_count > 90:
            print("  ✓ Email domains appear anonymized")
            return 0
        else:
            print("  ⚠️  Many emails may not be anonymized")
            # Show examples
            real_emails = [e for e in emails if 'anon-' not in e][:5]
            print(f"  Examples: {real_emails}")
            return 1

    def check_username_patterns(self, session: Session) -> int:
        """Check for username patterns indicating anonymization."""
        print("\n[*] Checking username patterns...")
        if self.preserve_usernames:
            print(f"  Note: {len(self.preserve_usernames)} preserved usernames: {', '.join(sorted(self.preserve_usernames))}")

        result = session.execute(text(
            "SELECT username FROM users LIMIT 100"
        ))
        usernames = [row[0] for row in result.fetchall()]

        user_pattern_count = sum(1 for u in usernames if u.startswith('user_'))
        preserved_count = sum(1 for u in usernames if u in self.preserve_usernames)
        other_count = len(usernames) - user_pattern_count - preserved_count

        print(f"  Sample of 100 usernames:")
        print(f"    Anonymized (user_*): {user_pattern_count}")
        if self.preserve_usernames:
            print(f"    Preserved:           {preserved_count}")
        print(f"    Other patterns:      {other_count}")

        # Account for preserved usernames in threshold
        expected_anonymized = user_pattern_count + preserved_count
        if expected_anonymized > 90:
            print("  ✓ Usernames appear properly anonymized/preserved")
            return 0
        else:
            print("  ⚠️  Many usernames may not be anonymized")
            others = [u for u in usernames if not u.startswith('user_') and u not in self.preserve_usernames][:5]
            if others:
                print(f"  Examples: {others}")
            return 1

    def check_phone_patterns(self, session: Session) -> int:
        """Check phone numbers use anonymized area codes."""
        print("\n[*] Checking phone number patterns...")

        result = session.execute(text(
            "SELECT phone_number FROM phone WHERE phone_number LIKE '___-___-____' LIMIT 100"
        ))
        phones = [row[0] for row in result.fetchall()]

        if not phones:
            print("  ℹ️  No US-format phone numbers found")
            return 0

        fake_555_count = sum(1 for p in phones if p.startswith('555-'))
        real_count = len(phones) - fake_555_count

        print(f"  Sample of {len(phones)} US-format phone numbers:")
        print(f"    Using 555 (fake):    {fake_555_count}")
        print(f"    Other area codes:    {real_count}")

        if fake_555_count > len(phones) * 0.9:
            print("  ✓ Phone numbers appear anonymized")
            return 0
        else:
            print("  ⚠️  Some phone numbers may be real")
            real_phones = [p for p in phones if not p.startswith('555-')][:5]
            print(f"  Examples: {real_phones}")
            return 1

    def check_contract_numbers(self, session: Session) -> int:
        """Check contract numbers use anonymized prefixes."""
        print("\n[*] Checking contract number patterns...")

        result = session.execute(text(
            "SELECT contract_number FROM contract LIMIT 100"
        ))
        contracts = [row[0] for row in result.fetchall()]

        if not contracts:
            print("  ℹ️  No contracts found")
            return 0

        tst_pattern_count = sum(1 for c in contracts if c.startswith('TST-'))
        nsf_pattern_count = sum(1 for c in contracts if re.match(r'^[A-Z]{3}-\d+', c) and not c.startswith('TST-'))

        print(f"  Sample of {len(contracts)} contract numbers:")
        print(f"    Anonymized (TST-*):  {tst_pattern_count}")
        print(f"    NSF-like patterns:   {nsf_pattern_count}")

        if tst_pattern_count > len(contracts) * 0.8:
            print("  ✓ Contract numbers appear anonymized")
            return 0
        else:
            print("  ⚠️  Some contract numbers may be real")
            nsf_contracts = [c for c in contracts if re.match(r'^[A-Z]{3}-\d+', c) and not c.startswith('TST-')][:5]
            if nsf_contracts:
                print(f"  Examples: {nsf_contracts}")
            return 1

    def check_upid_ranges(self, session: Session) -> int:
        """Check UPID values are in anonymized range."""
        print("\n[*] Checking UPID ranges...")

        result = session.execute(text(
            "SELECT MIN(upid) as min_upid, MAX(upid) as max_upid FROM users WHERE upid IS NOT NULL"
        ))
        row = result.fetchone()
        min_upid, max_upid = row[0], row[1]

        print(f"  UPID range: {min_upid} to {max_upid}")

        if min_upid >= 90000:
            print("  ✓ UPIDs appear anonymized (all >= 90000)")
            return 0
        else:
            print("  ⚠️  Some UPIDs may be real (< 90000)")
            result = session.execute(text(
                "SELECT COUNT(*) as cnt FROM users WHERE upid < 90000"
            ))
            count = result.fetchone()[0]
            print(f"  {count} users with UPID < 90000")
            return 1

    def check_referential_integrity(self, session: Session) -> int:
        """Verify foreign key relationships are intact."""
        print("\n[*] Checking referential integrity...")

        checks = [
            ("email_address", "user_id", "users", "user_id"),
            ("phone", "user_id", "users", "user_id"),
            ("project", "project_lead_user_id", "users", "user_id"),
            ("contract", "principal_investigator_user_id", "users", "user_id"),
        ]

        issues = 0
        for child_table, child_col, parent_table, parent_col in checks:
            result = session.execute(text(f"""
                SELECT COUNT(*) as orphans
                FROM {child_table}
                WHERE {child_col} IS NOT NULL
                AND {child_col} NOT IN (SELECT {parent_col} FROM {parent_table})
            """))
            orphans = result.fetchone()[0]

            if orphans > 0:
                print(f"  ⚠️  {child_table}.{child_col} has {orphans} orphaned records")
                issues += 1

        if issues == 0:
            print("  ✓ All foreign key relationships intact")

        return issues

    def verify_all(self) -> bool:
        """Run all verification checks."""
        print("=" * 80)
        print("SAM Database Anonymization Verification")
        print("=" * 80)

        total_issues = 0

        with Session(self.engine) as session:
            total_issues += self.check_username_patterns(session)
            total_issues += self.check_email_domains(session)
            total_issues += self.check_phone_patterns(session)
            total_issues += self.check_contract_numbers(session)
            total_issues += self.check_upid_ranges(session)
            total_issues += self.check_common_names(session)
            total_issues += self.check_referential_integrity(session)

        print("\n" + "=" * 80)
        if total_issues == 0:
            print("✓ SUCCESS: Database appears properly anonymized!")
            print("=" * 80)
            return True
        else:
            print(f"⚠️  WARNING: Found {total_issues} potential issues")
            print("Review the warnings above and investigate further.")
            print("=" * 80)
            return False


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Verify SAM database anonymization')
    parser.add_argument(
        '--connection',
        type=str,
        default='mysql+pymysql://root:root@127.0.0.1/sam',
        help='Database connection string (default: mysql+pymysql://root:root@127.0.0.1/sam)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to config.yaml file (optional, loads preserve_usernames)'
    )

    args = parser.parse_args()

    # Load preserve_usernames from config if provided
    preserve_usernames = []
    if args.config:
        try:
            with open(args.config, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'anonymization' in config:
                    preserve_usernames = config['anonymization'].get('preserve_usernames', [])
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
    else:
        # Try default config location
        default_config = Path(__file__).parent.parent / 'containers' / 'sam-sql-dev' / 'config.yaml'
        if default_config.exists():
            try:
                with open(default_config, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and 'anonymization' in config:
                        preserve_usernames = config['anonymization'].get('preserve_usernames', [])
            except:
                pass

    verifier = AnonymizationVerifier(args.connection, preserve_usernames=preserve_usernames)
    success = verifier.verify_all()

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
