# Anonymization Collision Fixes

## Summary
Fixed multiple potential collision issues in `anonymize_sam_db.py` where unique database constraints could be violated during anonymization.

## Issues Found and Fixed

### 🔴 CRITICAL: Organization Acronym Collisions
**Problem:**
- Database has unique constraint: `idx_organization_acronym`
- 397 organizations need unique acronyms
- Fake name pool: Only 12 items in `ORGANIZATION_NAMES`
- Result: **GUARANTEED COLLISIONS** (~33 orgs per acronym)

**Fix:**
- Added collision detection with numeric suffix
- Example: `DAT` → `DAT`, `DAT1`, `DAT2`, etc.
- Tracks all used acronyms in `self.used_org_acronyms`

### 🔴 CRITICAL: UPID Collisions (Already Reported)
**Problem:**
- Database has unique constraint: `idx_users_upid`
- 27,204 users need unique UPIDs
- Old range: 90000-99999 (only 10,000 values)
- Result: **GUARANTEED COLLISIONS** with hash-based generation

**Fix:**
- Expanded range: 900000-999999 (100,000 values)
- Added collision detection with increment strategy
- Tracks all used UPIDs in `self.used_upids`
- Preserves original UPIDs for preserved users

### 🟡 MEDIUM: Contract Number Collisions
**Problem:**
- Database has unique constraint: `contract_contract_number_uk`
- 2,163 contracts with unique numbers
- Most use pattern: `PREFIX-NNNNNNN` (7 digits)
- Hash-based generation has ~0.23% collision probability

**Fix:**
- Added collision detection with increment strategy
- Preserves original pattern format
- Tracks all used contract numbers in `self.used_contract_numbers`

### 🟢 LOW: Username Collisions
**Problem:**
- Database has unique constraint: `username_uk`
- 27,204 users need unique usernames
- 8-char hex hash provides 4.3 billion values
- Very low risk, but possible

**Fix:**
- Added collision detection with numeric suffix
- Example: `user_abc12345` → `user_abc12345_1` if collision
- Tracks all used usernames in `self.used_usernames`
- Preserves original usernames for preserved users (benkirk, csgteam)

## Database Schema Analysis

Unique constraints on anonymized fields:
```sql
users.username          → username_uk (UNIQUE)
users.upid              → idx_users_upid (UNIQUE)
user_alias.username     → username (UNIQUE)
user_alias.user_id      → user_id (UNIQUE)
contract.contract_number→ contract_contract_number_uk (UNIQUE)
organization.acronym    → idx_organization_acronym (UNIQUE)
```

## Record Counts
- users: 27,204
- user_alias: 43
- contract: 2,163
- organization: 397
- institution: 1,347

## Implementation Details

### Collision Detection Pattern
All collision-prone methods now follow this pattern:

```python
def _get_fake_field(self, id, original_value):
    # Check cache first
    if id in self.id_to_field:
        return self.id_to_field[id]

    # Generate base value deterministically
    base_value = generate_from_hash(original_value)

    # Resolve collisions
    counter = 1
    fake_value = base_value
    while fake_value in self.used_values:
        fake_value = f"{base_value}{counter}"  # or increment numeric part
        counter += 1
        if counter > MAX_ATTEMPTS:
            raise ValueError("Exhausted value space")

    # Cache and track
    self.id_to_field[id] = fake_value
    self.used_values.add(fake_value)
    return fake_value
```

### New Tracking Sets
- `self.used_upids: Set[int]` - Track UPIDs to avoid collisions
- `self.used_usernames: Set[str]` - Track usernames to avoid collisions
- `self.used_org_acronyms: Set[str]` - Track org acronyms to avoid collisions
- `self.used_contract_numbers: Set[str]` - Track contract numbers to avoid collisions

### Preserved User Handling
Preserved users (configured in `config.yaml`):
- Original username tracked in `used_usernames`
- Original UPID tracked in `used_upids`
- Prevents anonymized data from colliding with preserved data

## Testing

Run the anonymization workflow:
```bash
cd /Users/benkirk/codes/sam-queries/containers/sam-sql-dev
./run_anonymization_workflow.sh
```

The script will:
1. Preview transformations
2. Run dry-run (no database changes)
3. Execute anonymization with collision detection
4. Verify results

## Configuration

Edit `config.yaml` to preserve specific users:
```yaml
anonymization:
  preserve_usernames:
    - benkirk
    - csgteam
  preserve_emails: true
  preserve_phones: true
  seed: 42
```

## Risk Assessment After Fixes

| Field | Unique Constraint | Records | Risk Before | Risk After |
|-------|------------------|---------|-------------|------------|
| users.upid | ✅ | 27,204 | 🔴 CRITICAL | ✅ SAFE |
| users.username | ✅ | 27,204 | 🟡 LOW | ✅ SAFE |
| user_alias.username | ✅ | 43 | 🟢 VERY LOW | ✅ SAFE |
| organization.acronym | ✅ | 397 | 🔴 CRITICAL | ✅ SAFE |
| contract.contract_number | ✅ | 2,163 | 🟡 MEDIUM | ✅ SAFE |

## Verification

After anonymization, verify uniqueness:
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "
SELECT
    COUNT(*) as total,
    COUNT(DISTINCT username) as unique_usernames,
    COUNT(DISTINCT upid) as unique_upids
FROM users;
"
```

All counts should match (no duplicates).
