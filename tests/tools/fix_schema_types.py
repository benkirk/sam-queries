#!/usr/bin/env python3
"""
Automatic Schema Type Fix Script

This script automatically fixes the type mismatches between ORM models
and the database schema based on SCHEMA_FIX_PLAN.md.

Changes:
1. NUMERIC → Float (for columns that use FLOAT in DB)
2. DateTime → Date (for activity_date columns in summary tables)
3. TIMESTAMP → DateTime (for deletion_time in geography)

Run this script from the project root:
    python tests/fix_schema_types.py --dry-run  # Preview changes
    python tests/fix_schema_types.py             # Apply changes
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Base directory
BASE_DIR = Path(__file__).parent.parent / 'python' / 'sam'

# File patterns to fix
FIXES = [
    # (file_path, [list of (old_pattern, new_pattern) tuples])

    # Accounting - NUMERIC to Float
    ('accounting/allocations.py', [
        (r"amount = Column\(Numeric\(15, 2\)", "amount = Column(Float"),
        (r"requested_amount = Column\(Numeric\(15, 2\)", "requested_amount = Column(Float"),
        (r"transaction_amount = Column\(Numeric\(15, 2\)", "transaction_amount = Column(Float"),
        (r"default_allocation_amount = Column\(Numeric\(15, 2\)", "default_allocation_amount = Column(Float"),
    ]),

    # Archive - NUMERIC to Float
    ('activity/archive.py', [
        (r"terabyte_year = Column\(Numeric\(22, 8\)", "terabyte_year = Column(Float"),
        (r"charge = Column\(Numeric\(22, 8\)", "charge = Column(Float"),
    ]),

    # Archive summaries - NUMERIC to Float, DateTime to Date
    ('summaries/archive_summaries.py', [
        (r"terabyte_years = Column\(Numeric\(22, 8\)", "terabyte_years = Column(Float"),
        (r"charges = Column\(Numeric\(22, 8\)", "charges = Column(Float"),
        (r"activity_date = Column\(DateTime", "activity_date = Column(Date"),
    ]),

    # Computational - NUMERIC to Float
    ('activity/computational.py', [
        (r"external_charge = Column\(Numeric\(15, 8\)", "external_charge = Column(Float"),
        (r"external_charge = Column\(Numeric\(22, 8\)", "external_charge = Column(Float"),
        (r"charge = Column\(Numeric\(22, 8\)", "charge = Column(Float"),
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
    ]),

    # Comp summaries - NUMERIC to Float, DateTime to Date
    ('summaries/comp_summaries.py', [
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
        (r"charges = Column\(Numeric\(22, 8\)", "charges = Column(Float"),
        (r"activity_date = Column\(DateTime", "activity_date = Column(Date"),
    ]),

    # DAV - NUMERIC to Float
    ('activity/dav.py', [
        (r"charge = Column\(Numeric\(22, 8\)", "charge = Column(Float"),
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
        (r"terabyte_year = Column\(Numeric\(22, 8\)", "terabyte_year = Column(Float"),
    ]),

    # DAV summaries - NUMERIC to Float, DateTime to Date
    ('summaries/dav_summaries.py', [
        (r"terabyte_years = Column\(Numeric\(22, 8\)", "terabyte_years = Column(Float"),
        (r"charges = Column\(Numeric\(22, 8\)", "charges = Column(Float"),
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
        (r"activity_date = Column\(DateTime", "activity_date = Column(Date"),
    ]),

    # Disk - NUMERIC to Float
    ('activity/disk.py', [
        (r"terabyte_year = Column\(Numeric\(22, 8\)", "terabyte_year = Column(Float"),
        (r"charge = Column\(Numeric\(22, 8\)", "charge = Column(Float"),
    ]),

    # Disk summaries - NUMERIC to Float, DateTime to Date
    ('summaries/disk_summaries.py', [
        (r"terabyte_years = Column\(Numeric\(22, 8\)", "terabyte_years = Column(Float"),
        (r"charges = Column\(Numeric\(22, 8\)", "charges = Column(Float"),
        (r"activity_date = Column\(DateTime", "activity_date = Column(Date"),
    ]),

    # HPC - NUMERIC to Float
    ('activity/hpc.py', [
        (r"external_charge = Column\(Numeric\(15, 8\)", "external_charge = Column(Float"),
        (r"charge = Column\(Numeric\(22, 8\)", "charge = Column(Float"),
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
    ]),

    # HPC summaries - NUMERIC to Float, DateTime to Date
    ('summaries/hpc_summaries.py', [
        (r"charges = Column\(Numeric\(22, 8\)", "charges = Column(Float"),
        (r"core_hours = Column\(Numeric\(22, 8\)", "core_hours = Column(Float"),
        (r"activity_date = Column\(DateTime", "activity_date = Column(Date"),
    ]),

    # Machines - NUMERIC to Float
    ('resources/machines.py', [
        (r"factor_value = Column\(Numeric\(15, 2\)", "factor_value = Column(Float"),
        (r"wall_clock_hours_limit = Column\(Numeric\(5, 2\)", "wall_clock_hours_limit = Column(Float"),
    ]),

    # Operational - NUMERIC to Float
    ('operational.py', [
        (r"time_limit_hours = Column\(Numeric\(5, 2\)", "time_limit_hours = Column(Float"),
    ]),

    # Geography - TIMESTAMP to DateTime
    ('geography.py', [
        (r"deletion_time = Column\(TIMESTAMP\)", "deletion_time = Column(DateTime)"),
        (r"modified_time = Column\(TIMESTAMP", "modified_time = Column(DateTime"),
    ]),
]


def check_imports(file_path: Path, content: str) -> Tuple[str, List[str]]:
    """Check if needed imports are present, add if needed."""
    issues = []

    # Check if we need Date import
    if 'Column(Date' in content:
        if 'from sqlalchemy import' in content and 'Date' not in content.split('from sqlalchemy import')[1].split('\n')[0]:
            # Need to add Date to imports
            old_import = re.search(r'from sqlalchemy import \([^)]+\)', content, re.DOTALL)
            if not old_import:
                old_import = re.search(r'from sqlalchemy import [^\n]+', content)

            if old_import:
                old_line = old_import.group(0)
                if 'Date' not in old_line:
                    # Add Date to import
                    if '(' in old_line:  # Multiline import
                        new_line = old_line.replace(')', ', Date)')
                    else:  # Single line import
                        new_line = old_line.rstrip() + ', Date'

                    content = content.replace(old_line, new_line)
                    issues.append(f"Added Date to imports")

    return content, issues


def apply_fixes(file_path: Path, fixes: List[Tuple[str, str]], dry_run: bool = False) -> Tuple[int, List[str]]:
    """Apply fixes to a file."""
    full_path = BASE_DIR / file_path

    if not full_path.exists():
        return 0, [f"File not found: {full_path}"]

    # Read file
    with open(full_path, 'r') as f:
        content = f.read()

    original_content = content
    changes = []
    change_count = 0

    # Apply each fix
    for old_pattern, new_pattern in fixes:
        matches = list(re.finditer(old_pattern, content))
        if matches:
            content = re.sub(old_pattern, new_pattern, content)
            change_count += len(matches)
            changes.append(f"  {len(matches)}x: {old_pattern[:50]}... → {new_pattern[:50]}...")

    if change_count > 0:
        # Check/fix imports
        content, import_issues = check_imports(full_path, content)
        changes.extend([f"  {issue}" for issue in import_issues])

        if not dry_run:
            # Write back
            with open(full_path, 'w') as f:
                f.write(content)

    return change_count, changes


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fix ORM schema type mismatches')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be changed without modifying files')
    args = parser.parse_args()

    print("=" * 80)
    print("SAM ORM Schema Type Fix Script")
    print("=" * 80)
    print()

    if args.dry_run:
        print("⚠️  DRY RUN MODE - No files will be modified")
        print()

    total_changes = 0
    files_modified = 0

    for file_path, fixes in FIXES:
        full_path = BASE_DIR / file_path

        if not full_path.exists():
            print(f"❌ SKIP: {file_path} (file not found)")
            continue

        change_count, changes = apply_fixes(Path(file_path), fixes, args.dry_run)

        if change_count > 0:
            files_modified += 1
            total_changes += change_count
            status = "WOULD FIX" if args.dry_run else "✅ FIXED"
            print(f"{status}: {file_path}")
            for change in changes:
                print(change)
            print()

    print("=" * 80)
    print(f"Summary:")
    print(f"  Files modified: {files_modified}")
    print(f"  Total changes: {total_changes}")

    if args.dry_run:
        print()
        print("Run without --dry-run to apply changes")
    else:
        print()
        print("✅ All fixes applied!")
        print()
        print("Next steps:")
        print("  1. Run: python tests/orm_inventory.py")
        print("  2. Run: python -m pytest tests/ -v")
        print("  3. Commit changes")

    print("=" * 80)

    return 0


if __name__ == '__main__':
    sys.exit(main())
