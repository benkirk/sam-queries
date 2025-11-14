# Username Preservation Feature - Implementation Summary

## Overview

Added ability to preserve specific usernames (and their associated data) during database anonymization. This is useful for maintaining test accounts with known/predictable data for unit testing and development.

## Changes Made

### 1. Configuration (`containers/sam-sql-dev/config.yaml`)

Added new `anonymization` section:

```yaml
anonymization:
  preserve_usernames:
    - benkirk
    - csgteam
  preserve_emails: true
  preserve_phones: true
  seed: 42
```

### 2. Core Anonymization Script (`python/anonymize_sam_db.py`)

**New Parameters**:
- `preserve_usernames`: List of usernames to skip during anonymization
- `preserve_emails`: Whether to preserve emails for preserved users
- `preserve_phones`: Whether to preserve phone numbers for preserved users

**New Command-Line Arguments**:
- `--config PATH`: Load settings from config.yaml
- `--preserve-usernames USER1 USER2 ...`: Specify usernames via CLI

**Modified Methods**:
- `anonymize_users()`: Skips users in preserve_usernames list
- `anonymize_emails()`: Skips emails for preserved users if configured
- `anonymize_phones()`: Skips phone numbers for preserved users if configured

**New Statistics**:
- `users_preserved`: Count of preserved users
- `emails_preserved`: Count of preserved emails
- `phones_preserved`: Count of preserved phone numbers

### 3. Preview Script (`python/preview_anonymization.py`)

- Automatically loads `preserve_usernames` from config.yaml
- Shows preserved usernames in KEY PROPERTIES output
- Demonstrates which users will be kept as-is

### 4. Verification Script (`python/verify_anonymization.py`)

- Loads `preserve_usernames` from config.yaml or `--config` arg
- Accounts for preserved usernames when checking anonymization patterns
- Shows preserved username count in validation output

### 5. Documentation

Created **ANONYMIZATION_PRESERVE_USERNAMES.md**:
- Complete guide to username preservation feature
- Configuration examples (config.yaml and CLI)
- Use cases (unit testing, development, reference accounts)
- Security considerations
- Troubleshooting guide

Updated **ANONYMIZATION_SUMMARY.md**:
- Added config file examples to all commands
- Added "Preserve specific usernames" quick start section
- Referenced new documentation file

## Usage Examples

### Basic Usage with Config File

```bash
# 1. Edit config.yaml to add usernames
vim containers/sam-sql-dev/config.yaml

# 2. Run anonymization
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml --dry-run
```

### Command-Line Override

```bash
# Preserve specific users via CLI (overrides config)
python anonymize_sam_db.py --preserve-usernames benkirk testuser --dry-run
```

### Verification

```bash
# Verify with preserved usernames awareness
python verify_anonymization.py --config containers/sam-sql-dev/config.yaml
```

## Example Output

```
[*] Anonymizing users table...
  Preserving 2 usernames: benkirk, csgteam
  ... processed 27204/27204 users (27202 anonymized, 2 preserved)
[✓] Anonymized 27202 users, preserved 2 users

======================================================================
Anonymization Summary
======================================================================
Users Anonymized: 27,202
Users Preserved: 2                    ← NEW
Emails Anonymized: 46,610
Emails Preserved: 1                   ← NEW
Phones Anonymized: 15,756
Phones Preserved: 1                   ← NEW
...
```

## Database State After Anonymization

### Preserved User (benkirk)
```sql
SELECT * FROM users WHERE username = 'benkirk';
-- Returns: Original name, email, phone, UPID (ALL preserved)
```

### Anonymized User (dnolan)
```sql
SELECT * FROM users WHERE user_id = 22;  -- was 'dnolan'
-- Returns: Fake name, user_XXXXXXXX username, anonymized data
```

## Testing

Tested with:
- ✅ Config file loading
- ✅ Dry-run mode with preserved users
- ✅ Statistics tracking (preserved counts)
- ✅ Email preservation
- ✅ Phone preservation
- ✅ Verification with preserved usernames
- ✅ Preview with preserved usernames

Verified:
- ✅ 2 users preserved (benkirk, csgteam)
- ✅ 27,202 users anonymized
- ✅ 1 email preserved
- ✅ 1 phone preserved
- ✅ All foreign keys intact

## Benefits

1. **Unit Testing**: Maintain known test accounts with predictable data
2. **Development**: Keep your own account for testing workflows
3. **Debugging**: Have reference accounts for comparison
4. **Flexibility**: Configure via file or command line
5. **Safety**: Dry-run shows preserved users before execution

## Security Notes

⚠️ Preserved users contain **REAL DATA**:
- Only preserve users specifically needed for testing
- Document why each user is preserved
- Never distribute databases with sensitive preserved accounts

## Dependencies Added

- `pyyaml`: For loading config.yaml file

## Files Modified/Created

**Modified**:
- `containers/sam-sql-dev/config.yaml` - Added anonymization section
- `python/anonymize_sam_db.py` - Core preservation logic
- `python/preview_anonymization.py` - Config loading
- `python/verify_anonymization.py` - Preserved username awareness
- `python/ANONYMIZATION_SUMMARY.md` - Updated with new feature

**Created**:
- `python/ANONYMIZATION_PRESERVE_USERNAMES.md` - Feature documentation
- `python/PRESERVE_USERNAMES_FEATURE.md` - This file (implementation summary)

## Backward Compatibility

✅ Fully backward compatible:
- If no `preserve_usernames` specified → anonymizes everything (original behavior)
- If no config file → works as before
- Old command-line usage still works without changes

## Future Enhancements (Optional)

- [ ] Preserve entire projects (all members of specific projects)
- [ ] Preserve by role (e.g., all PIs)
- [ ] Preserve by institution
- [ ] Export preserved user list after anonymization

---

**Implementation Date**: 2024-11-14
**Status**: ✅ Complete and tested
