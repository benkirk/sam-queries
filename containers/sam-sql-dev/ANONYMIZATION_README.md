# SAM Database Anonymization Guide

## Overview

The `anonymize_sam_db.py` script anonymizes a SAM database replica by replacing sensitive personal and institutional information while preserving:

- ✅ Referential integrity (all foreign keys remain valid)
- ✅ Statistical properties (distributions preserved)
- ✅ Data patterns (phone formats, email domains, contract numbers)
- ✅ Project codes (e.g., SCSG0001, AOLA0001)
- ✅ Geographic data (state_prov_id)
- ✅ Institution types (institution_type_id)
- ✅ Relationships and tree structures

## What Gets Anonymized

### Critical (Always Anonymized)
- **Users**: first_name, middle_name, last_name, username, nickname, upid
- **Emails**: email addresses (local part anonymized, domain pattern preserved)
- **Phones**: phone numbers (format preserved: US vs international)
- **User Aliases**: username, orcid_id, access_global_id
- **Contracts**: contract_number (pattern preserved), title, url
- **Projects**: title, abstract
- **Institutions**: name, acronym, address, city, zip
- **Organizations**: name, acronym, description

### Preserved (Never Modified)
- All primary keys and foreign keys
- Project codes (projcode: SCSG0001, etc.)
- State/province IDs (state_prov_id)
- Institution types (institution_type_id)
- Resource types, allocation data, charging data
- Dates, timestamps, flags
- Tree structures (tree_left, tree_right, parent_id)

## Anonymization Strategy

### Deterministic Hashing
All transformations use **deterministic hashing** with a seed value, ensuring:
- Same input always produces same output
- Re-running the script with same seed produces identical results
- Different seeds produce different anonymized data

### Consistency Rules
1. **User Consistency**: Same user_id → same fake name across all tables
2. **Email Matching**: Fake emails use fake usernames (user_abc123@anon-ucar.edu)
3. **Institution Consistency**: Same institution_id → same fake name everywhere
4. **Relationship Preservation**: All user/project/contract relationships intact

### Transformation Examples

#### Users
```
Original: David Nolan (dnolan)
Fake:     Beth Singh (user_11b982dd)
```

#### Emails
```
Original: dnolan@ucar.edu
Fake:     user_11b982dd@anon-ucar.edu
Pattern:  {fake_username}@anon-{original_domain}
```

#### Phone Numbers
```
US Format:
  512-232-7933 → 555-452-2052

International Format:
  86-10-68475440 → 86-44-54070744
```

#### Contract Numbers
```
NSF Format Preserved:
  AGS-0830068 → TST-9265403
  ACI-1063057 → TST-3601013
```

#### Institutions
```
UNIVERSITY OF ALASKA FAIRBANKS → Desert Research Institute (DRI)
```

#### Project Titles
```
"Towards Seamless High-Resolution Prediction..." → "Data Investigation a7dc5fc3"
```

#### ORCID IDs
```
0000-0001-2345-6789 → 1051-0630-0110-5149
```

## Usage

### Prerequisites
```bash
pip install sqlalchemy pymysql
```

### Preview Changes (Dry Run)
**Always run dry-run first** to see what will be changed:

```bash
python anonymize_sam_db.py --dry-run
```

This will:
- ✓ Show exactly what will be anonymized
- ✓ Display statistics (users, emails, phones, etc.)
- ✓ **NOT commit any changes** to the database

### Preview Sample Transformations
```bash
python preview_anonymization.py
```

Shows example transformations for specific users, emails, phone numbers, etc.

### Execute Anonymization
⚠️ **WARNING: This modifies the database permanently!**

```bash
python anonymize_sam_db.py
```

You will be prompted to confirm before changes are made.

### Export Anonymization Mappings
Save the anonymization mappings to a JSON file for debugging:

```bash
python anonymize_sam_db.py --export-mappings mappings.json
```

Mappings file contains:
- User ID → fake names
- User ID → fake usernames
- Institution ID → fake institution names
- Contract ID → fake contract numbers
- Anonymization statistics

### Change Random Seed
Use a different seed for different anonymized datasets:

```bash
python anonymize_sam_db.py --seed 12345
```

### Custom Database Connection
```bash
python anonymize_sam_db.py --connection "mysql+pymysql://user:pass@host/dbname"
```

## Workflow

### Recommended Steps

1. **Create a database backup/replica**
   ```bash
   mysqldump -u root -proot sam > sam_backup.sql
   ```

2. **Run dry-run to preview changes**
   ```bash
   python anonymize_sam_db.py --config config.yaml --dry-run
   ```

3. **Review sample transformations**
   ```bash
   python preview_anonymization.py
   ```

4. **Execute anonymization**
   ```bash
   python anonymize_sam_db.py --config config.yaml --export-mappings mappings.json
   ```

5. **Verify anonymized data**
   ```bash
   mysql -u root -h 127.0.0.1 -proot sam -e \
     "SELECT username, first_name, last_name FROM users LIMIT 10;"
   ```

6. **Test applications with anonymized data**

## Tables Processed

| Table | Records | Fields Anonymized |
|-------|---------|-------------------|
| users | ~27,000 | username, first_name, middle_name, last_name, nickname, upid |
| email_address | ~46,000 | email_address |
| phone | ~15,000 | phone_number |
| user_alias | ~43 | username, orcid_id, access_global_id |
| institution | ~1,300 | name, acronym, address, city, zip |
| organization | ~400 | name, acronym, description |
| project | ~5,400 | title, abstract |
| contract | ~2,100 | contract_number, title, url |

## Performance

- **Dry run**: ~0.5 seconds
- **Full anonymization**: ~5-10 seconds (depends on database size)
- **Memory usage**: Minimal (processes in batches)

## Safety Features

1. **Dry-run mode**: Preview all changes without committing
2. **Transaction safety**: All changes in a single transaction (rollback on error)
3. **Confirmation prompt**: Must type "yes" to confirm live execution
4. **Seed-based determinism**: Same seed = same results (reproducible)
5. **Statistics tracking**: Full report of what was changed

## Verification Queries

After anonymization, verify data:

```bash
# Check user data
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT user_id, username, first_name, last_name FROM users LIMIT 10;"

# Check emails
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT user_id, email_address FROM email_address LIMIT 10;"

# Check institutions
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT institution_id, name, acronym FROM institution LIMIT 10;"

# Check contracts
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT contract_id, contract_number, title FROM contract LIMIT 10;"

# Verify referential integrity
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT u.username, e.email_address
   FROM users u
   JOIN email_address e ON u.user_id = e.user_id
   LIMIT 5;"
```

## Troubleshooting

### Error: "Access denied"
- Check database credentials in connection string
- Ensure MySQL server is running on 127.0.0.1:3306

### Error: "Table doesn't exist"
- Verify you're connected to the correct database
- Check table names match your schema

### Anonymization seems incomplete
- Check if dry-run mode was used (no changes committed)
- Verify commit was successful (check output logs)

### Need to re-anonymize with different seed
```bash
# Restore from backup first
mysql -u root -proot sam < sam_backup.sql

# Run with new seed
python anonymize_sam_db.py --seed 99999
```

## Technical Details

### Deterministic Hash Function
```python
def _deterministic_hash(value: str, salt: str = '') -> str:
    combined = f"{value}:{salt}:{seed}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]
```

### Name Pool Sizes
- First names: 52 options
- Last names: 52 options
- Institution names: 24 options
- Organization names: 12 options

These pools ensure realistic variety while maintaining consistency.

### Phone Number Format Detection
- US format: `XXX-XXX-XXXX` or `XXX.XXX.XXXX`
- International: Preserves country code and structure
- Anonymized US numbers always start with `555` (invalid area code)

## Best Practices

1. ✅ **Always test on a replica** - Never run on production database
2. ✅ **Use dry-run first** - Preview before committing
3. ✅ **Export mappings** - Useful for debugging/verification
4. ✅ **Document your seed** - Record seed value for reproducibility
5. ✅ **Verify integrity** - Check foreign keys after anonymization
6. ✅ **Test applications** - Ensure apps work with anonymized data

## Limitations

- Does not anonymize activity/usage data (not PII)
- Does not modify lookup tables (resource_type, allocation_type, etc.)
- URLs in contracts are cleared (not anonymized)
- Abstracts get generic text (not realistic scientific content)

## Support

For issues or questions:
1. Check this README
2. Review dry-run output
3. Check database logs
4. Verify connection credentials

## Version History

- v1.0 (2024-11-14): Initial release
  - Supports 8 core tables
  - Deterministic hashing with seed
  - Dry-run mode
  - Mapping export
