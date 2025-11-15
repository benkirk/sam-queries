# Preserving Specific Usernames During Anonymization

## Overview

You can preserve specific usernames (and their associated data) during anonymization. This is useful for:
- **Unit testing**: Keep known test accounts with predictable data
- **Development**: Maintain your own account for testing workflows
- **Debugging**: Have reference accounts with real data patterns

## Configuration

### Method 1: Using config.yaml (Recommended)

Edit `containers/sam-sql-dev/config.yaml`:

```yaml
anonymization:
  # Usernames to preserve (for testing/development purposes)
  preserve_usernames:
    - benkirk
    - csgteam
    # Add more as needed

  # Whether to preserve related data
  preserve_emails: true     # keep real emails for preserved users
  preserve_phones: true     # keep real phone numbers for preserved users

  # Random seed for deterministic anonymization
  seed: 42
```

Then run anonymization:

```bash
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml --dry-run
```

### Method 2: Command-Line Arguments

```bash
# Preserve specific usernames via CLI
python anonymize_sam_db.py --preserve-usernames benkirk csgteam --dry-run

# Mix with config file (CLI overrides config)
python anonymize_sam_db.py --config config.yaml --preserve-usernames myuser --dry-run
```

## What Gets Preserved

When a username is in the `preserve_usernames` list:

### Always Preserved
✅ **Username**: Kept exactly as-is
✅ **Names**: first_name, middle_name, last_name, nickname (NOT anonymized)
✅ **UPID**: Original UPID value (NOT offset)

### Conditionally Preserved
✅ **Emails**: Kept if `preserve_emails: true` (default)
✅ **Phone Numbers**: Kept if `preserve_phones: true` (default)

### Still Anonymized (Global)
❌ **Projects**: Titles/abstracts still anonymized (affects all projects)
❌ **Contracts**: Contract numbers/titles still anonymized (global)
❌ **Institutions**: Institution names still anonymized (global)

## Example

### Before Anonymization

```sql
SELECT user_id, username, first_name, last_name
FROM users
WHERE username IN ('benkirk', 'dnolan');
```

```
user_id  username  first_name  last_name
-------  --------  ----------  ---------
22       dnolan    David       Nolan
23971    benkirk   Benjamin    Kirk
```

### After Anonymization (with benkirk preserved)

```yaml
# config.yaml
anonymization:
  preserve_usernames:
    - benkirk
```

```
user_id  username       first_name  last_name
-------  -------------  ----------  ---------
22       user_11b982dd  Beth        Singh        ← Anonymized
23971    benkirk        Benjamin    Kirk         ← Preserved
```

## Usage Examples

### Full Workflow with Preserved Users

```bash
# 1. Configure preserved usernames
# Edit containers/sam-sql-dev/config.yaml

# 2. Preview (shows preserved users)
python preview_anonymization.py

# 3. Dry-run (shows preservation statistics)
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml --dry-run

# 4. Execute anonymization
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml

# 5. Verify (checks preserved usernames)
python verify_anonymization.py --config containers/sam-sql-dev/config.yaml
```

### Output Example

```
[*] Anonymizing users table...
  Preserving 2 usernames: benkirk, csgteam
  ... processed 27204/27204 users (27202 anonymized, 2 preserved)
[✓] Anonymized 27202 users, preserved 2 users

[*] Anonymizing email_address table...
[✓] Anonymized 46610 email addresses, preserved 1 emails

[*] Anonymizing phone table...
[✓] Anonymized 15756 phone numbers, preserved 1 phones

======================================================================
Anonymization Summary
======================================================================
Users Anonymized: 27,202
Users Preserved: 2
Emails Anonymized: 46,610
Emails Preserved: 1
Phones Anonymized: 15,756
Phones Preserved: 1
...
```

## Verification

After anonymization, verify preserved users:

```bash
# Using config file
python verify_anonymization.py --config containers/sam-sql-dev/config.yaml

# Verification output shows preserved usernames
[*] Checking username patterns...
  Note: 2 preserved usernames: benkirk, csgteam
  Sample of 100 usernames:
    Anonymized (user_*): 98
    Preserved:           2
    Other patterns:      0
  ✓ Usernames appear properly anonymized/preserved
```

## Querying Preserved Users

```bash
# Check preserved user data
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT username, first_name, last_name, email_address
   FROM users u
   JOIN email_address e ON u.user_id = e.user_id
   WHERE username IN ('benkirk', 'csgteam');"
```

Expected output:
```
username  first_name  last_name  email_address
--------  ----------  ---------  ----------------
benkirk   Benjamin    Kirk       benkirk@ucar.edu     ← Real data preserved
csgteam   Benjamin    Kirk       csgteam@ucar.edu     ← Real data preserved
```

## Use Cases

### 1. Unit Testing

Preserve known accounts for automated tests:

```yaml
# config.yaml
anonymization:
  preserve_usernames:
    - test_user_1
    - test_user_2
    - test_admin
```

In your tests:
```python
def test_user_lookup():
    """Test with preserved user account."""
    user = User.get_by_username(session, 'test_user_1')
    assert user is not None
    # Real data preserved - predictable for testing
    assert user.first_name == "Test"
```

### 2. Development Database

Keep your own account for testing workflows:

```yaml
# config.yaml
anonymization:
  preserve_usernames:
    - your_username  # Your development account
```

Benefits:
- Login with your real credentials
- Test permissions with your real roles
- Debug with familiar data

### 3. Reference Accounts

Preserve a few diverse accounts for comparison:

```yaml
# config.yaml
anonymization:
  preserve_usernames:
    - pi_user          # Principal investigator
    - staff_user       # Staff account
    - student_user     # Student account
    - external_user    # External collaborator
```

## Important Notes

### Security Considerations

⚠️ **Preserved users contain REAL DATA**:
- Real names, emails, phone numbers
- Original UPID values
- Real project memberships

**Best practices**:
- Only preserve users specifically needed for testing
- Document why each user is preserved
- Never distribute anonymized databases with sensitive preserved accounts
- Consider creating dedicated test accounts instead of preserving production users

### Relationship Preservation

Preserved users maintain **all relationships**:
- ✅ Project memberships (project leads, admins, members)
- ✅ Contract associations (PIs, contract monitors)
- ✅ Institutional affiliations
- ✅ Email addresses and phone numbers

But related entities are still anonymized:
- ❌ Project titles/abstracts are still anonymized
- ❌ Institution names are still anonymized
- ❌ Contract titles are still anonymized

### Verification

Always verify preservation worked correctly:

```bash
# 1. Check username preserved
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT username FROM users WHERE username = 'benkirk';"

# Expected: benkirk (NOT user_XXXXXXXX)

# 2. Check email preserved
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT e.email_address FROM email_address e
   JOIN users u ON e.user_id = u.user_id
   WHERE u.username = 'benkirk';"

# Expected: benkirk@ucar.edu (NOT user_XXXXXXXX@anon-ucar.edu)
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `preserve_usernames` | list | `[]` | Usernames to preserve (not anonymize) |
| `preserve_emails` | bool | `true` | Keep emails for preserved users |
| `preserve_phones` | bool | `true` | Keep phone numbers for preserved users |
| `seed` | int | `42` | Random seed for deterministic anonymization |

## Disabling Email/Phone Preservation

You can anonymize emails/phones even for preserved users:

```yaml
# config.yaml
anonymization:
  preserve_usernames:
    - benkirk
  preserve_emails: false   # Anonymize emails even for benkirk
  preserve_phones: false   # Anonymize phones even for benkirk
```

Result:
```
Username: benkirk (preserved)
Name: Benjamin Kirk (preserved)
Email: user_25973098@anon-ucar.edu (anonymized)
Phone: 555-123-4567 (anonymized)
```

## Troubleshooting

### Issue: Preserved user still anonymized

**Check**:
1. Username spelling in config.yaml
2. Config file loaded correctly (`--config` flag)
3. Dry-run output shows "Preserving X usernames"

### Issue: Wrong number of preserved users

**Check**:
```bash
# Verify usernames exist in database
mysql -u root -h 127.0.0.1 -proot sam -e \
  "SELECT username FROM users WHERE username IN ('benkirk', 'csgteam');"
```

If user doesn't exist, they can't be preserved!

### Issue: Email anonymized but user preserved

**Check** `preserve_emails` setting:
```yaml
anonymization:
  preserve_usernames:
    - benkirk
  preserve_emails: true  # Must be true to preserve emails
```

## Summary

✅ **Preserve specific users**: Add to `preserve_usernames` list
✅ **Use config.yaml**: Recommended for persistent settings
✅ **Verify**: Always check preserved users after anonymization
✅ **Security**: Only preserve users needed for testing/development
✅ **Relationships**: Preserved users keep all associations

For more details, see:
- `ANONYMIZATION_README.md` - Comprehensive anonymization guide
- `ANONYMIZATION_SUMMARY.md` - Quick reference
