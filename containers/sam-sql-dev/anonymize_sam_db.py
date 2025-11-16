#!/usr/bin/env python3
"""
SAM Database Anonymizer

Anonymizes a SAM database replica by replacing sensitive personal and institutional
information while preserving referential integrity, statistical properties, and patterns.

Usage:
    python anonymize_sam_db.py --config config.yaml --dry-run  # Preview changes without modifying DB
    python anonymize_sam_db.py --config config.yaml            # Execute anonymization
    python anonymize_sam_db.py --config config.yaml --export-mappings mappings.json  # Save mappings
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Fake data pools for consistent anonymization
FIRST_NAMES = [
    'Alice', 'Bob', 'Carol', 'David', 'Emma', 'Frank', 'Grace', 'Henry',
    'Isabel', 'Jack', 'Kate', 'Liam', 'Maria', 'Nathan', 'Olivia', 'Paul',
    'Quinn', 'Rachel', 'Sam', 'Tara', 'Uma', 'Victor', 'Wendy', 'Xavier',
    'Yara', 'Zoe', 'Aaron', 'Beth', 'Chris', 'Diana', 'Ethan', 'Fiona',
    'George', 'Hannah', 'Ian', 'Julia', 'Kevin', 'Laura', 'Mark', 'Nina',
    'Oscar', 'Petra', 'Quentin', 'Rita', 'Steve', 'Tina', 'Ulysses', 'Vera',
    'Walter', 'Xena', 'Yasmin', 'Zachary'
]

LAST_NAMES = [
    'Anderson', 'Brown', 'Chen', 'Davis', 'Evans', 'Fisher', 'Garcia', 'Harris',
    'Ibrahim', 'Johnson', 'Kim', 'Lee', 'Martinez', 'Nguyen', 'O\'Brien', 'Patel',
    'Quinn', 'Rodriguez', 'Smith', 'Taylor', 'Upton', 'Valdez', 'Williams', 'Xu',
    'Young', 'Zhang', 'Allen', 'Baker', 'Carter', 'Diaz', 'Edwards', 'Foster',
    'Green', 'Hill', 'Ivanov', 'Jackson', 'Khan', 'Lopez', 'Miller', 'Nelson',
    'Olson', 'Perez', 'Qian', 'Roberts', 'Singh', 'Thompson', 'Ueda', 'Vasquez',
    'Wang', 'Xiao', 'Yamamoto', 'Zimmerman'
]

INSTITUTION_NAMES = [
    'Northern Technical University', 'Southern State University', 'Eastern Research Institute',
    'Western Science Academy', 'Central University', 'Metropolitan Institute of Technology',
    'Coastal University', 'Mountain State University', 'Prairie Research University',
    'Valley Technical Institute', 'Harbor University', 'Summit State College',
    'Riverside University', 'Lakeside Institute', 'Forest University',
    'Desert Research Institute', 'Ocean State University', 'Highland University',
    'Peninsula Technical College', 'Bay Area University', 'Canyon State University',
    'Mesa Research Institute', 'Delta University', 'Plains State University'
]

ORGANIZATION_NAMES = [
    'Research Division A', 'Research Division B', 'Computing Services Group',
    'Data Analysis Team', 'Systems Engineering Division', 'Scientific Applications Unit',
    'Technical Services Group', 'Research Computing Division', 'Advanced Computing Section',
    'Computational Science Group', 'Infrastructure Services Team', 'Operations Division'
]


class SAMAnonymizer:
    """Anonymize SAM database while preserving referential integrity."""

    def __init__(self, connection_string: str, seed: int = 42, dry_run: bool = False,
                 preserve_usernames: Optional[List[str]] = None,
                 preserve_emails: bool = True,
                 preserve_phones: bool = True):
        """
        Initialize anonymizer.

        Args:
            connection_string: SQLAlchemy database connection string
            seed: Random seed for deterministic anonymization
            dry_run: If True, preview changes without committing
            preserve_usernames: List of usernames to preserve (not anonymize)
            preserve_emails: Preserve emails for preserved users
            preserve_phones: Preserve phone numbers for preserved users
        """
        self.engine = create_engine(connection_string)
        self.seed = seed
        self.dry_run = dry_run
        self.preserve_usernames = set(preserve_usernames or [])
        self.preserve_emails = preserve_emails
        self.preserve_phones = preserve_phones
        random.seed(seed)

        # Mapping caches for consistency
        self.user_id_to_names: Dict[int, Tuple[str, str, str]] = {}  # (first, middle, last)
        self.user_id_to_username: Dict[int, str] = {}
        self.original_username_to_user_id: Dict[str, int] = {}  # Reverse mapping for activity tables
        self.user_id_to_upid: Dict[int, int] = {}  # Track UPID mappings to avoid collisions
        self.used_upids: Set[int] = set()  # Track all UPIDs in use
        self.used_usernames: Set[str] = set()  # Track all usernames to avoid collisions
        self.preserved_user_ids: Set[int] = set()  # User IDs that are preserved
        self.institution_id_to_name: Dict[int, Tuple[str, str]] = {}  # (name, acronym)
        self.organization_id_to_name: Dict[int, Tuple[str, str]] = {}  # (name, acronym)
        self.used_org_acronyms: Set[str] = set()  # Track organization acronyms to avoid collisions
        self.contract_id_to_number: Dict[int, str] = {}
        self.used_contract_numbers: Set[str] = set()  # Track contract numbers to avoid collisions

        # Statistics
        self.stats = {
            'users_anonymized': 0,
            'users_preserved': 0,
            'emails_anonymized': 0,
            'emails_preserved': 0,
            'phones_anonymized': 0,
            'phones_preserved': 0,
            'institutions_anonymized': 0,
            'organizations_anonymized': 0,
            'projects_anonymized': 0,
            'contracts_anonymized': 0,
        }

    def _deterministic_hash(self, value: str, salt: str = '') -> str:
        """Generate deterministic hash from value."""
        combined = f"{value}:{salt}:{self.seed}"
        return hashlib.sha256(combined.encode()).hexdigest()[:12]

    def _get_fake_name(self, user_id: int) -> Tuple[str, Optional[str], str]:
        """
        Generate consistent fake name for user_id.

        Returns:
            (first_name, middle_name, last_name)
        """
        if user_id not in self.user_id_to_names:
            hash_val = int(self._deterministic_hash(str(user_id)), 16)
            first = FIRST_NAMES[hash_val % len(FIRST_NAMES)]
            last = LAST_NAMES[(hash_val // len(FIRST_NAMES)) % len(LAST_NAMES)]
            # 30% chance of middle name
            middle = FIRST_NAMES[(hash_val // 1000) % len(FIRST_NAMES)] if hash_val % 10 < 3 else None
            self.user_id_to_names[user_id] = (first, middle, last)
        return self.user_id_to_names[user_id]

    def _get_fake_username(self, user_id: int, original_username: str) -> str:
        """
        Generate consistent fake username with collision avoidance.

        Format: user_{8-char hex hash}
        """
        if user_id in self.user_id_to_username:
            return self.user_id_to_username[user_id]

        # Generate base username
        hash_suffix = self._deterministic_hash(original_username, 'username')[:8]
        fake_username = f"user_{hash_suffix}"

        # Resolve collisions by appending counter
        counter = 1
        while fake_username in self.used_usernames:
            fake_username = f"user_{hash_suffix}_{counter}"
            counter += 1
            if counter > 1000:
                raise ValueError(f"Exhausted username space for user_id={user_id}")

        # Cache and track
        self.user_id_to_username[user_id] = fake_username
        self.used_usernames.add(fake_username)
        return fake_username

    def _get_fake_upid(self, user_id: int, original_upid: Optional[int]) -> int:
        """
        Generate unique fake UPID for user, avoiding collisions.

        Uses deterministic hash with collision detection and resolution.
        UPID range: 900000-999999 (100,000 possible values)
        """
        if user_id in self.user_id_to_upid:
            return self.user_id_to_upid[user_id]

        # Start with deterministic hash
        base_hash = int(self._deterministic_hash(str(original_upid or user_id))[:8], 16)
        fake_upid = 900000 + (base_hash % 100000)

        # Resolve collisions by incrementing
        attempts = 0
        while fake_upid in self.used_upids:
            fake_upid = 900000 + ((base_hash + attempts) % 100000)
            attempts += 1
            if attempts > 100000:
                raise ValueError("Exhausted UPID space - too many users!")

        # Cache the mapping
        self.user_id_to_upid[user_id] = fake_upid
        self.used_upids.add(fake_upid)
        return fake_upid

    def _get_fake_email(self, original_email: str, user_id: int) -> str:
        """
        Generate fake email preserving domain type.

        Examples:
            user@ucar.edu -> user_abc123@anon-ucar.edu
            user@example.com -> user_abc123@anon-example.com
        """
        if '@' not in original_email:
            return original_email  # Invalid email, leave as-is

        local, domain = original_email.split('@', 1)
        fake_username = self.user_id_to_username.get(user_id, f"user_{self._deterministic_hash(local)[:8]}")

        # Anonymize domain but preserve TLD
        domain_parts = domain.split('.')
        if len(domain_parts) >= 2:
            anonymized_domain = f"anon-{domain_parts[0]}.{'.'.join(domain_parts[1:])}"
        else:
            anonymized_domain = f"anon-{domain}"

        return f"{fake_username}@{anonymized_domain}"

    def _get_fake_phone(self, original_phone: str) -> str:
        """
        Generate fake phone number preserving format.

        Examples:
            512-232-7933 -> 555-XXX-XXXX (US format)
            86-10-68475440 -> 86-10-XXXXXXXX (international format preserved)
        """
        # Detect format patterns
        if re.match(r'^\d{3}-\d{3}-\d{4}$', original_phone):
            # US format: XXX-XXX-XXXX
            hash_val = int(self._deterministic_hash(original_phone)[:8], 16)
            return f"555-{hash_val % 900 + 100:03d}-{hash_val % 10000:04d}"
        elif re.match(r'^\d{3}\.\d{3}\.\d{4}$', original_phone):
            # US format: XXX.XXX.XXXX
            hash_val = int(self._deterministic_hash(original_phone)[:8], 16)
            return f"555.{hash_val % 900 + 100:03d}.{hash_val % 10000:04d}"
        elif re.match(r'^\+?\d+-\d+-\d+', original_phone):
            # International format with hyphens
            parts = original_phone.split('-')
            hash_val = int(self._deterministic_hash(original_phone)[:8], 16)
            # Keep country code, randomize rest
            if parts[0].startswith('+') or len(parts[0]) <= 2:
                fake_parts = [parts[0]] + [str(hash_val % (10 ** len(p))) for p in parts[1:]]
            else:
                fake_parts = [str(hash_val % (10 ** len(p))) for p in parts]
            return '-'.join(fake_parts)
        else:
            # Unknown format - just randomize digits
            hash_val = int(self._deterministic_hash(original_phone)[:8], 16)
            return str(hash_val % (10 ** min(len(original_phone), 10)))

    def _get_fake_institution(self, institution_id: int, original_name: str) -> Tuple[str, str]:
        """
        Generate fake institution name and acronym.

        Returns:
            (name, acronym)
        """
        if institution_id not in self.institution_id_to_name:
            hash_val = int(self._deterministic_hash(str(institution_id)), 16)
            fake_name = INSTITUTION_NAMES[hash_val % len(INSTITUTION_NAMES)]
            # Generate acronym from fake name
            words = fake_name.split()
            acronym = ''.join(w[0].upper() for w in words if w[0].isupper())
            self.institution_id_to_name[institution_id] = (fake_name, acronym)
        return self.institution_id_to_name[institution_id]

    def _get_fake_organization(self, org_id: int, original_name: str) -> Tuple[str, str]:
        """
        Generate fake organization name and acronym with collision avoidance.

        Due to limited pool size (12 names), uses numeric suffix for acronyms
        to ensure uniqueness when needed.

        Returns:
            (name, acronym)
        """
        if org_id in self.organization_id_to_name:
            return self.organization_id_to_name[org_id]

        hash_val = int(self._deterministic_hash(str(org_id)), 16)
        fake_name = ORGANIZATION_NAMES[hash_val % len(ORGANIZATION_NAMES)]

        # Generate base acronym from fake name
        words = fake_name.split()
        base_acronym = ''.join(w[0].upper() for w in words[:4])  # Max 4 letters

        # Handle collisions by appending numeric suffix
        acronym = base_acronym
        counter = 1
        while acronym in self.used_org_acronyms:
            acronym = f"{base_acronym}{counter}"
            counter += 1
            if counter > 9999:
                raise ValueError(f"Exhausted acronym space for org_id={org_id}")

        # Cache and track
        self.organization_id_to_name[org_id] = (fake_name, acronym)
        self.used_org_acronyms.add(acronym)
        return (fake_name, acronym)

    def _get_fake_contract_number(self, contract_id: int, original_number: str) -> str:
        """
        Generate fake contract number preserving NSF/grant pattern with collision avoidance.

        Examples:
            AGS-0830068 -> TST-1234567
            ACI-1063057 -> TST-9876543
        """
        if contract_id in self.contract_id_to_number:
            return self.contract_id_to_number[contract_id]

        # Detect pattern: PREFIX-NUMBERS
        match = re.match(r'^([A-Z]+)-(\d+)(.*)$', original_number)
        if match:
            prefix, numbers, suffix = match.groups()
            base_hash = int(self._deterministic_hash(original_number)[:8], 16)

            # Generate contract number with collision detection
            attempts = 0
            while True:
                hash_val = (base_hash + attempts) % (10 ** len(numbers))
                fake = f"TST-{hash_val:0{len(numbers)}d}{suffix}"

                if fake not in self.used_contract_numbers:
                    break

                attempts += 1
                if attempts > 100000:
                    raise ValueError(f"Exhausted contract number space for {original_number}")
        else:
            # Non-standard format - use hash-based with collision detection
            base_hash = self._deterministic_hash(original_number)[:8]
            fake = f"CONTRACT-{base_hash}"

            # Handle collisions
            counter = 1
            while fake in self.used_contract_numbers:
                fake = f"CONTRACT-{base_hash}-{counter}"
                counter += 1
                if counter > 10000:
                    raise ValueError(f"Exhausted contract number space for {original_number}")

        # Cache and track
        self.contract_id_to_number[contract_id] = fake
        self.used_contract_numbers.add(fake)
        return fake

    def _get_fake_orcid(self, original_orcid: Optional[str]) -> Optional[str]:
        """
        Generate fake ORCID ID preserving format.

        ORCID format: XXXX-XXXX-XXXX-XXXX (16 digits with hyphens)
        """
        if not original_orcid:
            return None

        hash_val = self._deterministic_hash(original_orcid, 'orcid')
        # Generate 16 numeric digits from hash
        numeric_hash = str(int(hash_val, 16))[:16].zfill(16)
        # Format as XXXX-XXXX-XXXX-XXXX
        return f"{numeric_hash[0:4]}-{numeric_hash[4:8]}-{numeric_hash[8:12]}-{numeric_hash[12:16]}"

    def _get_fake_text(self, text_type: str, original_text: str, max_length: int = 255) -> str:
        """
        Generate fake text for titles, abstracts, descriptions.

        Args:
            text_type: 'title', 'abstract', or 'description'
            original_text: Original text (for hash consistency)
            max_length: Maximum length to return
        """
        hash_suffix = self._deterministic_hash(original_text, text_type)[:8]

        if text_type == 'title':
            templates = [
                f"Research Project {hash_suffix}",
                f"Scientific Study {hash_suffix}",
                f"Computational Analysis {hash_suffix}",
                f"Data Investigation {hash_suffix}",
            ]
            hash_val = int(hash_suffix, 16)
            return templates[hash_val % len(templates)][:max_length]

        elif text_type == 'abstract':
            return (f"This is a research project focused on scientific investigation "
                   f"and computational analysis. Reference ID: {hash_suffix}")[:max_length]

        elif text_type == 'description':
            return f"Generic description for anonymized data. ID: {hash_suffix}"[:max_length]

        else:
            return f"Anonymized text {hash_suffix}"[:max_length]

    def _initialize_tracking_sets(self, session: Session):
        """
        Pre-populate tracking sets with existing database values.

        This prevents collisions when updating records one-by-one, since some
        records may still have their old/existing values when we try to update others.
        """
        print("[*] Initializing collision tracking with existing database values...")

        # Track existing UPIDs
        result = session.execute(text("SELECT DISTINCT upid FROM users WHERE upid IS NOT NULL"))
        for row in result:
            self.used_upids.add(row[0])
        print(f"  Loaded {len(self.used_upids)} existing UPIDs")

        # Track existing usernames
        result = session.execute(text("SELECT DISTINCT username FROM users WHERE username IS NOT NULL"))
        for row in result:
            self.used_usernames.add(row[0])
        print(f"  Loaded {len(self.used_usernames)} existing usernames")

        # Track existing organization acronyms
        result = session.execute(text("SELECT DISTINCT acronym FROM organization WHERE acronym IS NOT NULL"))
        for row in result:
            self.used_org_acronyms.add(row[0])
        print(f"  Loaded {len(self.used_org_acronyms)} existing organization acronyms")

        # Track existing contract numbers
        result = session.execute(text("SELECT DISTINCT contract_number FROM contract WHERE contract_number IS NOT NULL"))
        for row in result:
            self.used_contract_numbers.add(row[0])
        print(f"  Loaded {len(self.used_contract_numbers)} existing contract numbers")

    def anonymize_users(self, session: Session) -> int:
        """
        Anonymize users table.

        Anonymizes:
            - username, first_name, middle_name, last_name, nickname
            - upid (unique assignment in range 900000-999999)

        Preserves:
            - user_id, unix_uid, relationships, flags
            - Users in preserve_usernames list
        """
        print("\n[*] Anonymizing users table...")
        if self.preserve_usernames:
            print(f"  Preserving {len(self.preserve_usernames)} usernames: {', '.join(sorted(self.preserve_usernames))}")

        # Fetch all users
        result = session.execute(text(
            "SELECT user_id, username, first_name, middle_name, last_name, nickname, upid "
            "FROM users"
        ))
        users = result.fetchall()

        anonymized_count = 0
        preserved_count = 0

        for row in users:
            user_id, orig_username, orig_first, orig_middle, orig_last, orig_nickname, orig_upid = row

            # Build reverse mapping for activity tables
            if orig_username:
                self.original_username_to_user_id[orig_username] = user_id

            # Check if this user should be preserved
            if orig_username in self.preserve_usernames:
                self.preserved_user_ids.add(user_id)
                self.user_id_to_username[user_id] = orig_username
                self.used_usernames.add(orig_username)  # Track to avoid collisions
                # Track preserved UPID to avoid conflicts
                if orig_upid:
                    self.used_upids.add(orig_upid)
                    self.user_id_to_upid[user_id] = orig_upid
                preserved_count += 1
                continue  # Skip anonymization for this user

            # Generate fake data
            fake_first, fake_middle, fake_last = self._get_fake_name(user_id)
            fake_username = self._get_fake_username(user_id, orig_username)
            fake_nickname = None  # Clear nicknames
            fake_upid = self._get_fake_upid(user_id, orig_upid)

            # Update
            if not self.dry_run:
                session.execute(text(
                    "UPDATE users SET "
                    "username = :username, "
                    "first_name = :first_name, "
                    "middle_name = :middle_name, "
                    "last_name = :last_name, "
                    "nickname = :nickname, "
                    "upid = :upid "
                    "WHERE user_id = :user_id"
                ), {
                    'username': fake_username,
                    'first_name': fake_first,
                    'middle_name': fake_middle,
                    'last_name': fake_last,
                    'nickname': fake_nickname,
                    'upid': fake_upid,
                    'user_id': user_id
                })

            anonymized_count += 1
            if (anonymized_count + preserved_count) % 1000 == 0:
                print(f"  ... processed {anonymized_count + preserved_count}/{len(users)} users "
                      f"({anonymized_count} anonymized, {preserved_count} preserved)")
                if not self.dry_run:
                    session.commit()

        if not self.dry_run:
            session.commit()

        self.stats['users_anonymized'] = anonymized_count
        self.stats['users_preserved'] = preserved_count
        print(f"[✓] Anonymized {anonymized_count} users, preserved {preserved_count} users")
        return anonymized_count

    def anonymize_user_aliases(self, session: Session) -> int:
        """
        Anonymize user_alias table.

        Anonymizes:
            - username (match users table)
            - orcid_id
            - access_global_id (offset)
        """
        print("\n[*] Anonymizing user_alias table...")

        result = session.execute(text(
            "SELECT user_alias_id, user_id, username, orcid_id, access_global_id "
            "FROM user_alias"
        ))
        aliases = result.fetchall()

        count = 0
        for row in aliases:
            alias_id, user_id, orig_username, orig_orcid, orig_access_id = row

            # Use same username as users table
            fake_username = self.user_id_to_username.get(user_id)
            if not fake_username:
                fake_username = self._get_fake_username(user_id, orig_username)

            # Anonymize ORCID
            fake_orcid = self._get_fake_orcid(orig_orcid) if orig_orcid else None

            # Anonymize access_global_id (numeric offset)
            fake_access_id = None
            if orig_access_id:
                hash_val = int(self._deterministic_hash(str(orig_access_id))[:8], 16)
                fake_access_id = str(200000 + (hash_val % 100000))

            if not self.dry_run:
                session.execute(text(
                    "UPDATE user_alias SET "
                    "username = :username, "
                    "orcid_id = :orcid_id, "
                    "access_global_id = :access_global_id "
                    "WHERE user_alias_id = :alias_id"
                ), {
                    'username': fake_username,
                    'orcid_id': fake_orcid,
                    'access_global_id': fake_access_id,
                    'alias_id': alias_id
                })

            count += 1

        if not self.dry_run:
            session.commit()

        print(f"[✓] Anonymized {count} user aliases")
        return count

    def anonymize_emails(self, session: Session) -> int:
        """Anonymize email_address table, preserving emails for preserved users if configured."""
        print("\n[*] Anonymizing email_address table...")

        result = session.execute(text(
            "SELECT email_address_id, user_id, email_address FROM email_address"
        ))
        emails = result.fetchall()

        anonymized_count = 0
        preserved_count = 0

        for row in emails:
            email_id, user_id, orig_email = row

            # Skip anonymization for preserved users if configured
            if self.preserve_emails and user_id in self.preserved_user_ids:
                preserved_count += 1
                continue

            fake_email = self._get_fake_email(orig_email, user_id)

            if not self.dry_run:
                session.execute(text(
                    "UPDATE email_address SET email_address = :email "
                    "WHERE email_address_id = :email_id"
                ), {'email': fake_email, 'email_id': email_id})

            anonymized_count += 1

        if not self.dry_run:
            session.commit()

        self.stats['emails_anonymized'] = anonymized_count
        self.stats['emails_preserved'] = preserved_count
        print(f"[✓] Anonymized {anonymized_count} email addresses, preserved {preserved_count} emails")
        return anonymized_count

    def anonymize_phones(self, session: Session) -> int:
        """Anonymize phone table, preserving phones for preserved users if configured."""
        print("\n[*] Anonymizing phone table...")

        result = session.execute(text(
            "SELECT ext_phone_id, user_id, phone_number FROM phone"
        ))
        phones = result.fetchall()

        anonymized_count = 0
        preserved_count = 0

        for row in phones:
            phone_id, user_id, orig_phone = row

            # Skip anonymization for preserved users if configured
            if self.preserve_phones and user_id in self.preserved_user_ids:
                preserved_count += 1
                continue

            fake_phone = self._get_fake_phone(orig_phone)

            if not self.dry_run:
                session.execute(text(
                    "UPDATE phone SET phone_number = :phone "
                    "WHERE ext_phone_id = :phone_id"
                ), {'phone': fake_phone, 'phone_id': phone_id})

            anonymized_count += 1

        if not self.dry_run:
            session.commit()

        self.stats['phones_anonymized'] = anonymized_count
        self.stats['phones_preserved'] = preserved_count
        print(f"[✓] Anonymized {anonymized_count} phone numbers, preserved {preserved_count} phones")
        return anonymized_count

    def anonymize_institutions(self, session: Session) -> int:
        """
        Anonymize institution table.

        Anonymizes:
            - name, acronym, address, city, zip

        Preserves:
            - state_prov_id, institution_type_id
        """
        print("\n[*] Anonymizing institution table...")

        result = session.execute(text(
            "SELECT institution_id, name, acronym FROM institution"
        ))
        institutions = result.fetchall()

        count = 0
        for row in institutions:
            inst_id, orig_name, orig_acronym = row
            fake_name, fake_acronym = self._get_fake_institution(inst_id, orig_name)

            # Generic address data
            hash_val = int(self._deterministic_hash(str(inst_id))[:8], 16)
            fake_address = f"{hash_val % 9999 + 1} Main Street"
            fake_city = "Anytown"
            fake_zip = f"{hash_val % 90000 + 10000:05d}"

            if not self.dry_run:
                session.execute(text(
                    "UPDATE institution SET "
                    "name = :name, "
                    "acronym = :acronym, "
                    "address = :address, "
                    "city = :city, "
                    "zip = :zip "
                    "WHERE institution_id = :inst_id"
                ), {
                    'name': fake_name,
                    'acronym': fake_acronym,
                    'address': fake_address,
                    'city': fake_city,
                    'zip': fake_zip,
                    'inst_id': inst_id
                })

            count += 1

        if not self.dry_run:
            session.commit()

        self.stats['institutions_anonymized'] = count
        print(f"[✓] Anonymized {count} institutions")
        return count

    def anonymize_organizations(self, session: Session) -> int:
        """
        Anonymize organization table.

        Anonymizes:
            - name, description, acronym

        Preserves:
            - Tree structure (parent_org_id, tree_left, tree_right)
        """
        print("\n[*] Anonymizing organization table...")

        result = session.execute(text(
            "SELECT organization_id, name, acronym FROM organization"
        ))
        orgs = result.fetchall()

        count = 0
        for row in orgs:
            org_id, orig_name, orig_acronym = row
            fake_name, fake_acronym = self._get_fake_organization(org_id, orig_name)
            fake_description = self._get_fake_text('description', orig_name)

            if not self.dry_run:
                session.execute(text(
                    "UPDATE organization SET "
                    "name = :name, "
                    "acronym = :acronym, "
                    "description = :description "
                    "WHERE organization_id = :org_id"
                ), {
                    'name': fake_name,
                    'acronym': fake_acronym,
                    'description': fake_description,
                    'org_id': org_id
                })

            count += 1

        if not self.dry_run:
            session.commit()

        self.stats['organizations_anonymized'] = count
        print(f"[✓] Anonymized {count} organizations")
        return count

    def anonymize_projects(self, session: Session) -> int:
        """
        Anonymize project table.

        Anonymizes:
            - title, abstract

        Preserves:
            - projcode, relationships, dates, flags
        """
        print("\n[*] Anonymizing project table...")

        result = session.execute(text(
            "SELECT project_id, title, abstract FROM project"
        ))
        projects = result.fetchall()

        count = 0
        for row in projects:
            project_id, orig_title, orig_abstract = row

            fake_title = self._get_fake_text('title', orig_title or '', max_length=255)
            fake_abstract = self._get_fake_text('abstract', orig_abstract or '', max_length=5000)

            if not self.dry_run:
                session.execute(text(
                    "UPDATE project SET "
                    "title = :title, "
                    "abstract = :abstract "
                    "WHERE project_id = :project_id"
                ), {
                    'title': fake_title,
                    'abstract': fake_abstract,
                    'project_id': project_id
                })

            count += 1
            if count % 1000 == 0:
                print(f"  ... processed {count}/{len(projects)} projects")
                if not self.dry_run:
                    session.commit()

        if not self.dry_run:
            session.commit()

        self.stats['projects_anonymized'] = count
        print(f"[✓] Anonymized {count} projects")
        return count

    def anonymize_contracts(self, session: Session) -> int:
        """
        Anonymize contract table.

        Anonymizes:
            - contract_number (preserving pattern)
            - title
            - url

        Preserves:
            - Dates, relationships, nsf_program_id
        """
        print("\n[*] Anonymizing contract table...")

        result = session.execute(text(
            "SELECT contract_id, contract_number, title, url FROM contract"
        ))
        contracts = result.fetchall()

        count = 0
        for row in contracts:
            contract_id, orig_number, orig_title, orig_url = row

            fake_number = self._get_fake_contract_number(contract_id, orig_number)
            fake_title = self._get_fake_text('title', orig_title or '', max_length=255)
            fake_url = None  # Clear URLs

            if not self.dry_run:
                session.execute(text(
                    "UPDATE contract SET "
                    "contract_number = :number, "
                    "title = :title, "
                    "url = :url "
                    "WHERE contract_id = :contract_id"
                ), {
                    'number': fake_number,
                    'title': fake_title,
                    'url': fake_url,
                    'contract_id': contract_id
                })

            count += 1

        if not self.dry_run:
            session.commit()

        self.stats['contracts_anonymized'] = count
        print(f"[✓] Anonymized {count} contracts")
        return count

    def anonymize_charge_summary_table(self, session: Session, table_name: str, id_column: str) -> int:
        """
        Anonymize charge summary tables (comp_charge_summary, dav_charge_summary, etc.).

        These tables have:
            - user_id (used for mapping)
            - username (denormalized)
            - act_username (denormalized)

        Anonymizes username fields to match users table.
        """
        print(f"\n[*] Anonymizing {table_name} table...")

        # Get count first
        result = session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        total = result.scalar()
        print(f"  Found {total:,} records to process")

        # Fetch records with username fields
        result = session.execute(text(
            f"SELECT {id_column}, user_id, username, act_username FROM {table_name}"
        ))
        records = result.fetchall()

        count = 0
        batch_size = 10000

        for row in records:
            summary_id, user_id, orig_username, orig_act_username = row

            # Get anonymized username from user_id mapping
            fake_username = None
            fake_act_username = None

            if user_id:
                fake_username = self.user_id_to_username.get(user_id)
                fake_act_username = fake_username  # Use same anonymized username

            # Fallback: if no user_id, try reverse lookup from original username
            if not fake_username and orig_username:
                lookup_user_id = self.original_username_to_user_id.get(orig_username)
                if lookup_user_id:
                    fake_username = self.user_id_to_username.get(lookup_user_id)

            if not fake_act_username and orig_act_username:
                lookup_user_id = self.original_username_to_user_id.get(orig_act_username)
                if lookup_user_id:
                    fake_act_username = self.user_id_to_username.get(lookup_user_id)

            # Update if we have anonymized values
            if fake_username or fake_act_username:
                if not self.dry_run:
                    session.execute(text(
                        f"UPDATE {table_name} SET "
                        f"username = :username, "
                        f"act_username = :act_username "
                        f"WHERE {id_column} = :summary_id"
                    ), {
                        'username': fake_username or orig_username,
                        'act_username': fake_act_username or orig_act_username,
                        'summary_id': summary_id
                    })

            count += 1
            if count % batch_size == 0:
                print(f"  ... processed {count:,}/{total:,} records")
                if not self.dry_run:
                    session.commit()

        if not self.dry_run:
            session.commit()

        print(f"[✓] Anonymized {count:,} {table_name} records")
        return count

    def anonymize_activity_table(self, session: Session, table_name: str, id_column: str) -> int:
        """
        Anonymize activity tables (hpc_activity, dav_activity, disk_activity, archive_activity).

        These tables have:
            - username (denormalized, NO user_id column)

        Uses reverse mapping: original_username -> user_id -> anonymized_username
        """
        print(f"\n[*] Anonymizing {table_name} table...")

        # Get count first
        result = session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        total = result.scalar()
        print(f"  Found {total:,} records to process")

        # Fetch records with username field
        result = session.execute(text(
            f"SELECT {id_column}, username FROM {table_name}"
        ))
        records = result.fetchall()

        count = 0
        batch_size = 10000

        for row in records:
            activity_id, orig_username = row

            # Reverse lookup: original username -> user_id -> anonymized username
            fake_username = orig_username  # Default to original
            if orig_username:
                user_id = self.original_username_to_user_id.get(orig_username)
                if user_id:
                    fake_username = self.user_id_to_username.get(user_id, orig_username)

            # Update if username changed
            if fake_username != orig_username:
                if not self.dry_run:
                    session.execute(text(
                        f"UPDATE {table_name} SET username = :username "
                        f"WHERE {id_column} = :activity_id"
                    ), {
                        'username': fake_username,
                        'activity_id': activity_id
                    })

            count += 1
            if count % batch_size == 0:
                print(f"  ... processed {count:,}/{total:,} records")
                if not self.dry_run:
                    session.commit()

        if not self.dry_run:
            session.commit()

        print(f"[✓] Anonymized {count:,} {table_name} records")
        return count

    def anonymize_all(self) -> Dict[str, int]:
        """
        Execute full anonymization workflow.

        Returns:
            Dictionary of anonymization statistics
        """
        print("=" * 70)
        print("SAM Database Anonymization")
        print("=" * 70)
        print(f"Mode: {'DRY RUN (no changes will be committed)' if self.dry_run else 'LIVE (database will be modified)'}")
        print(f"Seed: {self.seed}")
        print("=" * 70)

        start_time = datetime.now()

        with Session(self.engine) as session:
            try:
                # Initialize tracking sets with existing database values
                # This prevents collisions when updating records one-by-one
                self._initialize_tracking_sets(session)

                # Order matters for consistency!
                self.anonymize_users(session)
                self.anonymize_user_aliases(session)
                self.anonymize_emails(session)
                self.anonymize_phones(session)
                self.anonymize_institutions(session)
                self.anonymize_organizations(session)
                self.anonymize_projects(session)
                self.anonymize_contracts(session)

                # Anonymize charge summary tables (have user_id + username + act_username)
                print("\n" + "=" * 70)
                print("Anonymizing Charge Summary Tables")
                print("=" * 70)
                self.anonymize_charge_summary_table(session, 'comp_charge_summary', 'charge_summary_id')
                self.anonymize_charge_summary_table(session, 'dav_charge_summary', 'dav_charge_summary_id')
                self.anonymize_charge_summary_table(session, 'disk_charge_summary', 'disk_charge_summary_id')
                self.anonymize_charge_summary_table(session, 'archive_charge_summary', 'archive_charge_summary_id')
                self.anonymize_charge_summary_table(session, 'hpc_charge_summary', 'hpc_charge_summary_id')

                # Anonymize activity tables (have username only)
                print("\n" + "=" * 70)
                print("Anonymizing Activity Tables")
                print("=" * 70)
                self.anonymize_activity_table(session, 'hpc_activity', 'hpc_activity_id')
                self.anonymize_activity_table(session, 'dav_activity', 'dav_activity_id')
                self.anonymize_activity_table(session, 'disk_activity', 'disk_activity_id')
                self.anonymize_activity_table(session, 'archive_activity', 'archive_activity_id')

                if not self.dry_run:
                    print("\n[*] Committing all changes...")
                    session.commit()
                    print("[✓] All changes committed successfully")
                else:
                    print("\n[!] DRY RUN - No changes committed")

            except Exception as e:
                if not self.dry_run:
                    session.rollback()
                print(f"\n[✗] ERROR: {e}")
                raise

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print("\n" + "=" * 70)
        print("Anonymization Summary")
        print("=" * 70)
        for key, value in self.stats.items():
            print(f"{key.replace('_', ' ').title()}: {value:,}")
        print(f"\nTotal Duration: {duration:.2f} seconds")
        print("=" * 70)

        return self.stats

    def export_mappings(self, filepath: str):
        """Export anonymization mappings to JSON file for debugging."""
        mappings = {
            'user_names': {k: list(v) for k, v in self.user_id_to_names.items()},
            'user_usernames': self.user_id_to_username,
            'user_upids': self.user_id_to_upid,
            'institutions': {k: list(v) for k, v in self.institution_id_to_name.items()},
            'organizations': {k: list(v) for k, v in self.organization_id_to_name.items()},
            'contracts': self.contract_id_to_number,
            'stats': self.stats,
            'seed': self.seed,
        }

        with open(filepath, 'w') as f:
            json.dump(mappings, f, indent=2)

        print(f"[✓] Mappings exported to {filepath}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Anonymize SAM database while preserving referential integrity',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying database'
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=42,
        help='Random seed for deterministic anonymization (default: 42)'
    )
    parser.add_argument(
        '--export-mappings',
        type=str,
        metavar='FILE',
        help='Export anonymization mappings to JSON file'
    )
    parser.add_argument(
        '--connection',
        type=str,
        default='mysql+pymysql://root:root@127.0.0.1/sam',
        help='Database connection string (default: mysql+pymysql://root:root@127.0.0.1/sam)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to config.yaml file (optional, loads anonymization settings)'
    )
    parser.add_argument(
        '--preserve-usernames',
        type=str,
        nargs='*',
        help='Usernames to preserve (not anonymize), space-separated'
    )

    args = parser.parse_args()

    # Load configuration from config.yaml if provided
    preserve_usernames = []
    preserve_emails = True
    preserve_phones = True
    seed = args.seed

    if args.config:
        try:
            with open(args.config, 'r') as f:
                config = yaml.safe_load(f)
                if config and 'anonymization' in config:
                    anon_config = config['anonymization']
                    preserve_usernames = anon_config.get('preserve_usernames', [])
                    preserve_emails = anon_config.get('preserve_emails', True)
                    preserve_phones = anon_config.get('preserve_phones', True)
                    seed = anon_config.get('seed', args.seed)
                    print(f"[*] Loaded anonymization config from {args.config}")
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")

    # Command-line preserve_usernames override config file
    if args.preserve_usernames is not None:
        preserve_usernames = args.preserve_usernames

    # Confirm if not dry-run
    if not args.dry_run:
        print("\n" + "!" * 70)
        print("WARNING: This will MODIFY the database permanently!")
        print("!" * 70)
        if preserve_usernames:
            print(f"Note: The following usernames will be PRESERVED (not anonymized):")
            print(f"  {', '.join(preserve_usernames)}")
        response = input("Are you sure you want to continue? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return 1

    # Run anonymization
    anonymizer = SAMAnonymizer(
        connection_string=args.connection,
        seed=seed,
        dry_run=args.dry_run,
        preserve_usernames=preserve_usernames,
        preserve_emails=preserve_emails,
        preserve_phones=preserve_phones
    )

    try:
        anonymizer.anonymize_all()

        if args.export_mappings:
            anonymizer.export_mappings(args.export_mappings)

        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
