# SAM Database Anonymization - Technical Guide

Complete guide to anonymizing the SAM database for development and testing purposes.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Scripts](#scripts)
4. [Configuration](#configuration)
5. [Anonymization Strategy](#anonymization-strategy)
6. [Workflow](#workflow)
7. [Username Preservation](#username-preservation)
8. [Collision Detection](#collision-detection)
9. [Verification](#verification)
10. [Troubleshooting](#troubleshooting)
11. [Technical Details](#technical-details)

---

## Overview

The anonymization process replaces sensitive personal and institutional information while preserving database structure, relationships, and statistical properties.

### What Gets Anonymized

| Data Type | Fields | Example Transformation |
|-----------|--------|----------------------|
| **Users** | username, first_name, middle_name, last_name, nickname, upid | David Nolan → Beth Singh |
| **Emails** | email_address | dnolan@ucar.edu → user_11b982dd@anon-ucar.edu |
| **Phones** | phone_number | 512-232-7933 → 555-452-2052 |
| **User Aliases** | username, orcid_id, access_global_id | 0000-0001-2345-6789 → 1051-0630-0110-5149 |
| **Institutions** | name, acronym, address, city, zip | U OF ALASKA → Desert Research Institute (DRI) |
| **Organizations** | name, acronym, description | Research Lab → Data Analysis Team (DAT) |
| **Projects** | title, abstract | "Prediction at..." → "Data Investigation {hash}" |
| **Contracts** | contract_number, title, url | AGS-0830068 → TST-9265403 |

### What Stays Unchanged

- All primary keys and foreign keys
- Project codes (SCSG0001, AOLA0001, etc.)
- Dates and timestamps
- State/province IDs
- Institution types
- Resource types
- Allocation and charging data
- All database relationships

---

## Prerequisites

### Required Software

```bash
# Python 3.8+
python3 --version

# Required packages
pip install sqlalchemy pymysql pyyaml
```

### Database Access

Local MySQL instance running:
```bash
# Check connection
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT COUNT(*) FROM users;"
```

### Database Backup

**Critical:** Always work on a copy, never on production.

```bash
# Create backup before anonymization
mysqldump -u root -h 127.0.0.1 -proot sam > sam_backup_$(date +%Y%m%d).sql

# Restore if needed
mysql -u root -h 127.0.0.1 -proot sam < sam_backup_20241115.sql
```

---

## Scripts

### `anonymize_sam_db.py` - Main Anonymization Script

Performs the actual database anonymization.

**Usage:**
```bash
# Preview changes (always do this first)
python3 anonymize_sam_db.py --config config.yaml --dry-run

# Execute anonymization
python3 anonymize_sam_db.py --config config.yaml

# Export mappings for debugging
python3 anonymize_sam_db.py --config config.yaml --export-mappings mappings.json

# Custom seed for different anonymized dataset
python3 anonymize_sam_db.py --config config.yaml --seed 12345
```

**Command-Line Arguments:**
- `--config PATH` - Load configuration from YAML file
- `--dry-run` - Preview changes without committing
- `--seed INT` - Random seed for deterministic anonymization (default: 42)
- `--export-mappings FILE` - Export transformation mappings to JSON
- `--preserve-usernames USER1 USER2 ...` - Preserve specific usernames
- `--connection STRING` - Custom database connection string

### `preview_anonymization.py` - Preview Transformations

Shows example transformations before running anonymization.

**Usage:**
```bash
python3 preview_anonymization.py
```

**Output:**
- Sample user name transformations
- Sample email anonymization
- Phone number format preservation
- Contract number patterns
- Institution name examples

### `verify_anonymization.py` - Verification Script

Validates that anonymization completed successfully.

**Usage:**
```bash
python3 verify_anonymization.py
```

**Checks:**
- Username patterns (should be `user_*` or preserved)
- Email domains (should be `anon-*` or preserved)
- Phone area codes (should be `555` for anonymized)
- Contract numbers (should be `TST-*` or `CONTRACT-*`)
- UPID ranges (should be >= 900000 or preserved)
- Referential integrity (all foreign keys valid)

### `run_anonymization_workflow.sh` - Guided Workflow

Interactive script that guides through the entire process.

**Usage:**
```bash
./run_anonymization_workflow.sh
```

**Steps:**
1. Confirms running on replica (safety check)
2. Shows preview transformations
3. Runs dry-run
4. Prompts for confirmation
5. Executes anonymization with mapping export
6. Runs verification
7. Reports success/warnings

---

## Configuration

### config.yaml Structure

```yaml
# Database connection settings
local:
  host: localhost
  port: 3306
  user: root
  password: root
  database: sam
  docker_container: local-sam-mysql

# Anonymization settings
anonymization:
  # Usernames to preserve (not anonymize)
  preserve_usernames:
    - benkirk
    - csgteam

  # Preserve related data for preserved users
  preserve_emails: true
  preserve_phones: true

  # Random seed for deterministic results
  seed: 42
```

### Environment Variables

For SSL connections to remote databases:

```bash
# .env file
SAM_DB_REQUIRE_SSL=true
```

---

## Anonymization Strategy

### Deterministic Hashing

All transformations use SHA-256 hashing with a seed:

```python
def _deterministic_hash(value: str, salt: str = '') -> str:
    combined = f"{value}:{salt}:{seed}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]
```

**Properties:**
- Same input + same seed → always same output
- Different seed → different anonymized data
- Reproducible: re-running with same seed produces identical results

### Consistency Rules

1. **User Consistency**: Same `user_id` → same fake name everywhere
2. **Email Matching**: Fake emails use fake usernames
   - Original: `dnolan@ucar.edu`
   - Fake username: `user_11b982dd`
   - Fake email: `user_11b982dd@anon-ucar.edu`

3. **Institution Consistency**: Same `institution_id` → same fake name
4. **Relationship Preservation**: All foreign keys remain valid

### Transformation Details

#### Names
- Pool: 50 first names, 50 last names
- Distribution: ~540 users per name (with 27K users)
- Middle names: 30% probability

#### Usernames
- Format: `user_{8-char hex hash}`
- Example: `dnolan` → `user_11b982dd`
- Collision detection with numeric suffix

#### UPIDs
- Range: 900000-999999 (100,000 possible values)
- Old range (90000-99999) caused guaranteed collisions
- Preserves original UPID for preserved users

#### Emails
- Pattern: `{fake_username}@anon-{original_domain}`
- Examples:
  - `user@ucar.edu` → `user_abc123@anon-ucar.edu`
  - `user@example.com` → `user_abc123@anon-example.com`

#### Phone Numbers
- US format: `555-XXX-XXXX` (555 is invalid area code)
- International: Country code preserved, rest randomized
- Format detection:
  - `XXX-XXX-XXXX` (US hyphen format)
  - `XXX.XXX.XXXX` (US dot format)
  - `+CC-XX-XXXXXXXX` (international)

#### Contract Numbers
- NSF pattern: `PREFIX-NNNNNN` → `TST-NNNNNN`
- Example: `AGS-0830068` → `TST-9265403`
- Non-standard: `CONTRACT-{hash}`

#### ORCID IDs
- Format preserved: `XXXX-XXXX-XXXX-XXXX`
- Example: `0000-0001-2345-6789` → `1051-0630-0110-5149`

---

## Workflow

### Recommended Process

```bash
# 1. Ensure database backup exists
mysqldump -u root -proot sam > backup.sql

# 2. Preview sample transformations
python3 preview_anonymization.py

# 3. Dry-run to see statistics (no changes)
python3 anonymize_sam_db.py --config config.yaml --dry-run

# Expected output:
# Users Anonymized: 27,202
# Users Preserved: 2
# Emails Anonymized: 46,610
# ...

# 4. Execute anonymization
python3 anonymize_sam_db.py --config config.yaml --export-mappings mappings.json

# You will be prompted:
# > Are you sure you want to continue? (yes/no):
# Type: yes

# 5. Verify results
python3 verify_anonymization.py

# Expected: "✓ SUCCESS: Database appears properly anonymized!"

# 6. Test with your applications
# Run integration tests, start app, verify functionality

# 7. If issues, restore backup
mysql -u root -proot sam < backup.sql
```

### Using the Workflow Script

```bash
./run_anonymization_workflow.sh
```

The script will:
1. Ask for confirmation (replica check)
2. Show preview
3. Run dry-run
4. Ask for confirmation again
5. Execute with mapping export
6. Run verification
7. Display final results

---

## Username Preservation

### Purpose

Preserve specific usernames and their associated data for:
- Unit testing with known accounts
- Development with your own account
- Debugging with reference data

### Configuration

**In config.yaml:**
```yaml
anonymization:
  preserve_usernames:
    - benkirk
    - csgteam
    - test_user_1
  preserve_emails: true
  preserve_phones: true
```

**Via command line:**
```bash
python3 anonymize_sam_db.py --preserve-usernames benkirk csgteam --dry-run
```

### What Gets Preserved

For users in `preserve_usernames` list:

**Always Preserved:**
- Username (exact match)
- First name, middle name, last name
- Nickname
- UPID (original value)

**Conditionally Preserved:**
- Email addresses (if `preserve_emails: true`)
- Phone numbers (if `preserve_phones: true`)

**Still Anonymized:**
- Project titles/abstracts (global change)
- Contract numbers/titles (global change)
- Institution names (global change)

### Example

**Before anonymization:**
```sql
SELECT username, first_name, last_name, upid
FROM users
WHERE username IN ('dnolan', 'benkirk');

username  first_name  last_name  upid
--------  ----------  ---------  -----
dnolan    David       Nolan      45678
benkirk   Benjamin    Kirk       35399
```

**After anonymization with `preserve_usernames: [benkirk]`:**
```sql
username       first_name  last_name  upid
-------------  ----------  ---------  ------
user_11b982dd  Beth        Singh      945678  ← Anonymized
benkirk        Benjamin    Kirk       35399   ← Preserved
```

### Verification

```bash
# Check preserved user data
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT u.username, u.first_name, u.last_name, u.upid, e.email_address
  FROM users u
  LEFT JOIN email_address e ON u.user_id = e.user_id AND e.is_primary = 1
  WHERE u.username IN ('benkirk', 'csgteam');
"
```

### Security Considerations

**Warning:** Preserved users contain **REAL DATA**.

Best practices:
- Only preserve users specifically needed for testing
- Document why each user is preserved (in config.yaml comments)
- Never distribute databases with sensitive preserved accounts
- Consider creating dedicated test accounts instead of preserving production users

---

## Collision Detection

### Problem

Database unique constraints can be violated when anonymizing due to hash collisions or limited fake data pools.

### Unique Constraints

```sql
-- Constraints that required collision detection
users.username          → username_uk (UNIQUE)
users.upid              → idx_users_upid (UNIQUE)
user_alias.username     → username (UNIQUE)
organization.acronym    → idx_organization_acronym (UNIQUE)
contract.contract_number→ contract_contract_number_uk (UNIQUE)
```

### Issues Fixed

#### 1. Organization Acronym Collisions (CRITICAL)

**Problem:**
- 397 organizations to anonymize
- Only 12 fake names in pool
- Result: ~33 organizations per acronym → guaranteed collisions

**Solution:**
- Numeric suffix on collision: `CSG`, `CSG1`, `CSG2`, ..., `CSG70`
- Tracks all used acronyms in `self.used_org_acronyms`

#### 2. UPID Collisions (CRITICAL)

**Problem:**
- 27,204 users need unique UPIDs
- Old range: 90000-99999 (only 10,000 values)
- Result: guaranteed collisions

**Solution:**
- Expanded range: 900000-999999 (100,000 values)
- Collision detection with increment
- Preserves original UPIDs for preserved users

#### 3. Contract Number Collisions (MEDIUM)

**Problem:**
- 2,163 contracts
- Hash-based 7-digit generation
- ~0.23% collision probability

**Solution:**
- Collision detection with retry
- Preserves `TST-NNNNNNN` format
- Increments on collision

#### 4. Username Collisions (LOW)

**Problem:**
- 27,204 users
- 8-char hex hash = 4.3 billion possible values
- Very low but non-zero risk

**Solution:**
- Numeric suffix on collision: `user_abc12345_1`
- Tracks all used usernames

### Implementation

**Initialization:**
```python
def _initialize_tracking_sets(self, session: Session):
    """Pre-load existing database values to avoid collisions."""
    # Load existing UPIDs
    result = session.execute(text("SELECT DISTINCT upid FROM users"))
    for row in result:
        self.used_upids.add(row[0])

    # Load existing usernames
    result = session.execute(text("SELECT DISTINCT username FROM users"))
    for row in result:
        self.used_usernames.add(row[0])

    # Load organization acronyms
    result = session.execute(text("SELECT DISTINCT acronym FROM organization"))
    for row in result:
        self.used_org_acronyms.add(row[0])

    # Load contract numbers
    result = session.execute(text("SELECT DISTINCT contract_number FROM contract"))
    for row in result:
        self.used_contract_numbers.add(row[0])
```

**Generation with collision detection:**
```python
def _get_fake_upid(self, user_id: int, original_upid: Optional[int]) -> int:
    """Generate unique UPID with collision avoidance."""
    if user_id in self.user_id_to_upid:
        return self.user_id_to_upid[user_id]

    # Generate base value
    base_hash = int(self._deterministic_hash(str(original_upid or user_id))[:8], 16)
    fake_upid = 900000 + (base_hash % 100000)

    # Resolve collisions
    attempts = 0
    while fake_upid in self.used_upids:
        fake_upid = 900000 + ((base_hash + attempts) % 100000)
        attempts += 1
        if attempts > 100000:
            raise ValueError("Exhausted UPID space")

    # Cache and track
    self.user_id_to_upid[user_id] = fake_upid
    self.used_upids.add(fake_upid)
    return fake_upid
```

### Verification

After anonymization, verify no duplicates:

```bash
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT
    COUNT(*) as total_users,
    COUNT(DISTINCT username) as unique_usernames,
    COUNT(DISTINCT upid) as unique_upids
  FROM users;
"
```

Expected: All counts should match (no duplicates).

```bash
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT
    COUNT(*) as total_orgs,
    COUNT(DISTINCT acronym) as unique_acronyms
  FROM organization;
"
```

Expected: Both counts = 397.

---

## Verification

### Automated Verification

```bash
python3 verify_anonymization.py
```

**Checks performed:**

1. **Username patterns**
   - Anonymized: `user_*` format
   - Preserved: Original usernames (from config)
   - Flags: Any other patterns

2. **Email domains**
   - Anonymized: `anon-*` pattern
   - Preserved: Original emails (if configured)
   - Flags: Potentially real domains

3. **Phone numbers**
   - Anonymized US: `555-XXX-XXXX`
   - Flags: Other area codes

4. **Contract numbers**
   - Anonymized: `TST-*` or `CONTRACT-*`
   - Flags: NSF-like patterns (AGS-, ACI-, etc.)

5. **UPID ranges**
   - Anonymized: >= 900000
   - Preserved: < 900000 (original values)
   - Flags: Unexpected ranges

6. **Referential integrity**
   - All foreign keys valid
   - No orphaned records

### Manual Verification

```bash
# Check sample users
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT user_id, username, first_name, last_name, upid
  FROM users
  LIMIT 10;
"

# Check emails
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT user_id, email_address
  FROM email_address
  LIMIT 10;
"

# Check institutions
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT institution_id, name, acronym
  FROM institution
  LIMIT 10;
"

# Verify relationships
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT u.username, e.email_address, p.projcode
  FROM users u
  JOIN email_address e ON u.user_id = e.user_id AND e.is_primary = 1
  JOIN account_user au ON u.user_id = au.user_id
  JOIN account a ON au.account_id = a.account_id
  JOIN project p ON a.project_id = p.project_id
  LIMIT 5;
"
```

### Expected Results

**Success output:**
```
[*] Checking username patterns...
  Sample of 100 usernames:
    Anonymized (user_*): 98
    Preserved:           2
  ✓ Usernames appear properly anonymized/preserved

[*] Checking email domains...
  Sample of 100 emails:
    Anonymized (anon-*): 100
  ✓ Email domains appear anonymized

[*] Checking phone number patterns...
  Sample of 100 US-format phone numbers:
    Using 555 (fake):    100
  ✓ Phone numbers appear anonymized

[*] Checking contract number patterns...
  Sample of 100 contract numbers:
    Anonymized (TST-*):  97
    Anonymized (CONTRACT-*): 3
  ✓ Contract numbers appear anonymized

[*] Checking UPID ranges...
  UPID range: 900000 to 999994
  Note: 1 preserved user with UPID < 900000 (expected)
  ✓ UPIDs appear properly anonymized

[*] Checking referential integrity...
  ✓ All foreign key relationships intact

✓ SUCCESS: Database appears properly anonymized!
```

### Common Warnings (Expected)

These warnings are normal and expected:

1. **"Some UPIDs may be real (< 90000)"**
   - This is correct for preserved users
   - Check: Should match number of preserved usernames

2. **"Found N users with first_name='David'"**
   - These are fake names from the randomization pool
   - With 27K users and 50 names, expect ~540 per name
   - This proves good hash distribution

---

## Troubleshooting

### Database Connection Issues

**Error: "Access denied"**

```bash
# Check MySQL is running
docker ps | grep mysql

# Test connection
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT 1;"

# Check credentials in config.yaml
```

**Error: "Can't connect to MySQL server"**

```bash
# Start MySQL container
./docker_start.sh

# Or manually
docker compose up -d
```

### Anonymization Errors

**Error: "Duplicate entry 'CSG' for key 'organization.idx_organization_acronym'"**

This was fixed in collision detection updates. If you see this:
- Ensure you're using the latest `anonymize_sam_db.py`
- The script should call `_initialize_tracking_sets()` before anonymization

**Error: "Table doesn't exist"**

```bash
# Verify database name
mysql -u root -h 127.0.0.1 -proot -e "SHOW DATABASES;"

# Check table exists
mysql -u root -h 127.0.0.1 -proot sam -e "SHOW TABLES LIKE 'users';"
```

### Verification Warnings

**Warning: "Some contract numbers may be real"**

Check contract pattern distribution:
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "
  SELECT
    CASE
      WHEN contract_number LIKE 'TST-%' THEN 'TST (anonymized)'
      WHEN contract_number LIKE 'CONTRACT-%' THEN 'CONTRACT (anonymized)'
      ELSE 'Other'
    END as pattern,
    COUNT(*) as count
  FROM contract
  GROUP BY pattern;
"
```

Expected: 100% should be TST or CONTRACT patterns.

### Restoration

If anonymization failed or needs to be re-run:

```bash
# 1. Drop current database
mysql -u root -h 127.0.0.1 -proot -e "DROP DATABASE sam;"

# 2. Recreate database
mysql -u root -h 127.0.0.1 -proot -e "CREATE DATABASE sam CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 3. Restore from backup
mysql -u root -h 127.0.0.1 -proot sam < sam_backup_20241115.sql

# 4. Re-run anonymization with different seed (optional)
python3 anonymize_sam_db.py --config config.yaml --seed 99999
```

---

## Technical Details

### Tables Processed

| Table | Records | Anonymized Fields |
|-------|---------|------------------|
| users | ~27,200 | username, first_name, middle_name, last_name, nickname, upid |
| email_address | ~46,600 | email_address |
| phone | ~15,700 | phone_number |
| user_alias | ~43 | username, orcid_id, access_global_id |
| institution | ~1,300 | name, acronym, address, city, zip |
| organization | ~400 | name, acronym, description |
| project | ~5,400 | title, abstract |
| contract | ~2,100 | contract_number, title, url |

### Processing Order

Order matters for consistency:

1. **Users** - Establishes fake names and usernames
2. **User Aliases** - Uses usernames from step 1
3. **Emails** - Uses usernames from step 1
4. **Phones** - Uses preserved user list from step 1
5. **Institutions** - Independent
6. **Organizations** - Independent
7. **Projects** - Independent
8. **Contracts** - Independent

### Performance

Typical execution times:

- Dry-run: ~0.5 seconds
- Full anonymization: ~5-10 seconds
- Verification: ~1-2 seconds
- Total workflow: ~15-20 seconds

Memory usage: Minimal (< 100MB)

### Fake Data Pools

| Pool | Size | Notes |
|------|------|-------|
| First names | 50 | Diverse, internationally representative |
| Last names | 50 | Diverse, internationally representative |
| Institution names | 24 | Generic research institution names |
| Organization names | 12 | Generic department/division names |

### Hash Function

```python
import hashlib

def _deterministic_hash(value: str, salt: str = '', seed: int = 42) -> str:
    """
    Generate deterministic hash from value.

    Args:
        value: Input string to hash
        salt: Optional salt for variation
        seed: Numeric seed for reproducibility

    Returns:
        12-character hex string
    """
    combined = f"{value}:{salt}:{seed}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]
```

Properties:
- SHA-256 cryptographic hash
- Truncated to 12 hex characters (48 bits)
- Deterministic: same input → same output
- Different seeds produce different results

### Phone Number Format Detection

```python
import re

# US format with hyphens
if re.match(r'^\d{3}-\d{3}-\d{4}$', phone):
    fake = f"555-{random_3_digits}-{random_4_digits}"

# US format with dots
elif re.match(r'^\d{3}\.\d{3}\.\d{4}$', phone):
    fake = f"555.{random_3_digits}.{random_4_digits}"

# International format (preserve country code)
elif re.match(r'^\+?\d+-\d+-\d+', phone):
    parts = phone.split('-')
    fake_parts = [parts[0]] + [randomize(p) for p in parts[1:]]
    fake = '-'.join(fake_parts)
```

### Limitations

1. **Activity data not anonymized** - Job history, allocations, charges remain
2. **Lookup tables unchanged** - resource_type, allocation_type, etc.
3. **URLs cleared** - Contract URLs set to NULL (not anonymized)
4. **Generic abstracts** - Project abstracts get generic text, not realistic content
5. **Tree structures preserved** - Organization hierarchies maintain relationships

---

## Statistics Example

Typical anonymization run output:

```
======================================================================
SAM Database Anonymization
======================================================================
Mode: LIVE (database will be modified)
Seed: 42
======================================================================

[*] Initializing collision tracking with existing database values...
  Loaded 27119 existing UPIDs
  Loaded 27204 existing usernames
  Loaded 397 existing organization acronyms
  Loaded 2163 existing contract numbers

[*] Anonymizing users table...
  Preserving 2 usernames: benkirk, csgteam
  ... processed 27204/27204 users (27202 anonymized, 2 preserved)
[✓] Anonymized 27202 users, preserved 2 users

[*] Anonymizing user_alias table...
[✓] Anonymized 43 user aliases

[*] Anonymizing email_address table...
[✓] Anonymized 46610 email addresses, preserved 1 emails

[*] Anonymizing phone table...
[✓] Anonymized 15756 phone numbers, preserved 1 phones

[*] Anonymizing institution table...
[✓] Anonymized 1347 institutions

[*] Anonymizing organization table...
[✓] Anonymized 397 organizations

[*] Anonymizing project table...
  ... processed 5408/5408 projects
[✓] Anonymized 5408 projects

[*] Anonymizing contract table...
[✓] Anonymized 2163 contracts

[*] Committing all changes...
[✓] All changes committed successfully

======================================================================
Anonymization Summary
======================================================================
Users Anonymized: 27,202
Users Preserved: 2
Emails Anonymized: 46,610
Emails Preserved: 1
Phones Anonymized: 15,756
Phones Preserved: 1
Institutions Anonymized: 1,347
Organizations Anonymized: 397
Projects Anonymized: 5,408
Contracts Anonymized: 2,163

Total Duration: 8.42 seconds
======================================================================
```

---

## Best Practices

1. **Always work on a replica** - Never run on production
2. **Always dry-run first** - Preview changes before committing
3. **Export mappings** - Keep JSON file for debugging
4. **Document your seed** - Record seed value for reproducibility
5. **Verify after anonymization** - Run verification script
6. **Test applications** - Ensure apps work with anonymized data
7. **Limit preserved users** - Only preserve what's necessary for testing
8. **Back up before anonymizing** - Keep original data safe

## References

- `anonymize_sam_db.py` - Main script source code
- `config.yaml` - Configuration file with comments
- `verify_anonymization.py` - Verification script
- Database schema documentation (if available)
