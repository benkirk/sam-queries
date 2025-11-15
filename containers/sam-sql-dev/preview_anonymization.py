#!/usr/bin/env python3
"""
Preview anonymization transformations without modifying database.

Shows what data will look like after anonymization.
"""

import sys
from pathlib import Path
import yaml
from anonymize_sam_db import SAMAnonymizer


def preview_samples(preserve_usernames=None):
    """Preview anonymization on sample data."""
    preserve_usernames = preserve_usernames or []

    anonymizer = SAMAnonymizer(
        connection_string='mysql+pymysql://root:root@127.0.0.1/sam',
        seed=42,
        dry_run=True,
        preserve_usernames=preserve_usernames,
        preserve_emails=True,
        preserve_phones=True
    )

    print("=" * 80)
    print("ANONYMIZATION PREVIEW")
    print("=" * 80)

    # Sample user transformations
    print("\n[User Data Transformations]")
    print("-" * 80)

    # User ID 22 (dnolan)
    first, middle, last = anonymizer._get_fake_name(22)
    username = anonymizer._get_fake_username(22, "dnolan")
    print(f"User ID 22 (dnolan):")
    print(f"  Name: David Nolan → {first} {last}")
    print(f"  Username: dnolan → {username}")
    print(f"  Email: dnolan@ucar.edu → {anonymizer._get_fake_email('dnolan@ucar.edu', 22)}")

    # User ID 30 (jhurrell)
    first, middle, last = anonymizer._get_fake_name(30)
    username = anonymizer._get_fake_username(30, "jhurrell")
    print(f"\nUser ID 30 (jhurrell):")
    print(f"  Name: James Hurrell → {first} {last}")
    print(f"  Username: jhurrell → {username}")
    print(f"  Email: jhurrell@ucar.edu → {anonymizer._get_fake_email('jhurrell@ucar.edu', 30)}")

    # User ID 23971 (benkirk)
    first, middle, last = anonymizer._get_fake_name(23971)
    username = anonymizer._get_fake_username(23971, "benkirk")
    print(f"\nUser ID 23971 (benkirk):")
    print(f"  Name: Benjamin Kirk → {first} {last}")
    print(f"  Username: benkirk → {username}")
    print(f"  Email: benkirk@ucar.edu → {anonymizer._get_fake_email('benkirk@ucar.edu', 23971)}")

    # Phone number examples
    print("\n[Phone Number Transformations]")
    print("-" * 80)
    examples = [
        "512-232-7933",
        "307-766-2635",
        "86-10-68475440",
        "+1-303-497-1234"
    ]
    for phone in examples:
        fake = anonymizer._get_fake_phone(phone)
        print(f"  {phone} → {fake}")

    # Contract number examples
    print("\n[Contract Number Transformations]")
    print("-" * 80)
    contracts = [
        (1, "AGS-0830068"),
        (2, "ACI-1063057"),
        (3, "OCE-0934737")
    ]
    for contract_id, number in contracts:
        fake = anonymizer._get_fake_contract_number(contract_id, number)
        print(f"  {number} → {fake}")

    # Institution examples
    print("\n[Institution Transformations]")
    print("-" * 80)
    institutions = [
        (1, "UNIVERSITY OF ALASKA FAIRBANKS"),
        (2, "UNIVERSITY OF ALABAMA AT HUNTSVILLE"),
        (10, "AUSTRALIAN BUREAU OF METEOROLOGY")
    ]
    for inst_id, name in institutions:
        fake_name, fake_acronym = anonymizer._get_fake_institution(inst_id, name)
        print(f"  {name} → {fake_name} ({fake_acronym})")

    # Project title examples
    print("\n[Project Title Transformations]")
    print("-" * 80)
    titles = [
        "Towards Seamless High-Resolution Prediction at Intraseasonal and Longer Timescales",
        "Community Computational Platforms for Developing Three-Dimensional Models of Earth Structure",
        "Turbulence in the Heliosphere: The Role of Current Sheets and Magnetic Reconnection"
    ]
    for title in titles:
        fake = anonymizer._get_fake_text('title', title, max_length=255)
        print(f"  Original: {title[:60]}...")
        print(f"  Fake:     {fake}")
        print()

    # ORCID examples
    print("\n[ORCID ID Transformations]")
    print("-" * 80)
    orcids = [
        "0000-0001-2345-6789",
        "0000-0002-9876-5432"
    ]
    for orcid in orcids:
        fake = anonymizer._get_fake_orcid(orcid)
        print(f"  {orcid} → {fake}")

    print("\n" + "=" * 80)
    print("KEY PROPERTIES:")
    print("  ✓ Same user_id always generates same fake data (deterministic)")
    print("  ✓ Relationships preserved (emails match usernames)")
    print("  ✓ Patterns preserved (phone formats, contract prefixes)")
    print("  ✓ Project codes (SCSG0001, etc.) are NOT modified")
    print("  ✓ State/province IDs are NOT modified")
    print("  ✓ Institution types are NOT modified")
    if preserve_usernames:
        print(f"  ✓ Preserved usernames: {', '.join(preserve_usernames)} (kept as-is)")
    print("=" * 80)


if __name__ == '__main__':
    # Try to load preserve_usernames from config.yaml
    preserve_usernames = []
    config_path = Path(__file__).parent.parent / 'containers' / 'sam-sql-dev' / 'config.yaml'

    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'anonymization' in config:
                    preserve_usernames = config['anonymization'].get('preserve_usernames', [])
                    if preserve_usernames:
                        print(f"Loaded preserve_usernames from {config_path}: {preserve_usernames}")
                        print()
        except Exception as e:
            print(f"Warning: Could not load config: {e}\n")

    preview_samples(preserve_usernames=preserve_usernames)
