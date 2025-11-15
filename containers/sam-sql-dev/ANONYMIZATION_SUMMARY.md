# SAM Database Anonymization - Quick Reference

## 📋 Three Scripts

### 1. `anonymize_sam_db.py` - Main Anonymization Script
**Purpose**: Anonymize the SAM database replica

**Usage**:
```bash
# Preview changes (ALWAYS DO THIS FIRST)
python anonymize_sam_db.py --dry-run

# With config file (recommended - includes preserve_usernames)
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml --dry-run

# Execute anonymization
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml

# Preserve specific usernames (for testing)
python anonymize_sam_db.py --preserve-usernames benkirk csgteam --dry-run

# Export mappings for debugging
python anonymize_sam_db.py --export-mappings mappings.json
```

**NEW**: ✨ Preserve specific usernames for testing! See `ANONYMIZATION_PRESERVE_USERNAMES.md`

### 2. `preview_anonymization.py` - Preview Transformations
**Purpose**: See sample transformations before running anonymization

**Usage**:
```bash
python preview_anonymization.py
```

Shows examples of:
- User names: "David Nolan" → "Beth Singh"
- Usernames: "dnolan" → "user_11b982dd"
- Emails: "dnolan@ucar.edu" → "user_11b982dd@anon-ucar.edu"
- Phone numbers, contracts, institutions, etc.

### 3. `verify_anonymization.py` - Verify Success
**Purpose**: Confirm database is properly anonymized

**Usage**:
```bash
python verify_anonymization.py
```

Checks for:
- ✓ Anonymized username patterns (user_*)
- ✓ Anonymized email domains (anon-*)
- ✓ Fake phone area codes (555-)
- ✓ Fake contract numbers (TST-*)
- ✓ UPID offset (>= 90000)
- ✓ Referential integrity

## 🚀 Quick Start

```bash
# 0. Configure preserved usernames (optional)
# Edit containers/sam-sql-dev/config.yaml

# 1. Preview what will be anonymized
python preview_anonymization.py

# 2. Dry-run to see statistics
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml --dry-run

# 3. Execute anonymization
python anonymize_sam_db.py --config containers/sam-sql-dev/config.yaml

# 4. Verify it worked
python verify_anonymization.py
```

## ✅ What Gets Anonymized

| Data Type | Example Before | Example After | Pattern Preserved? |
|-----------|---------------|---------------|-------------------|
| **Names** | David Nolan | Beth Singh | ❌ Random |
| **Usernames** | dnolan | user_11b982dd | ❌ Hashed |
| **Emails** | dnolan@ucar.edu | user_11b982dd@anon-ucar.edu | ✅ Domain type |
| **Phones** | 512-232-7933 | 555-452-2052 | ✅ Format |
| **Contracts** | AGS-0830068 | TST-9265403 | ✅ NSF pattern |
| **Institutions** | U OF ALASKA | Desert Research Institute | ❌ Random |
| **Project Titles** | "Prediction at..." | "Data Investigation {hash}" | ❌ Generic |
| **UPIDs** | 35399 | 95973 | ✅ Numeric |
| **ORCIDs** | 0000-0001-2345-6789 | 1051-0630-0110-5149 | ✅ Format |

## 🔒 What Stays the Same

- ✅ Project codes (SCSG0001, AOLA0001, etc.)
- ✅ State/province IDs
- ✅ Institution types
- ✅ All primary/foreign keys
- ✅ Dates and timestamps
- ✅ Allocation/charging data
- ✅ Resource types
- ✅ All relationships

## 📊 Expected Results (Based on Current DB)

```
Users Anonymized: ~27,200
Emails Anonymized: ~46,600
Phones Anonymized: ~15,700
Institutions Anonymized: ~1,300
Organizations Anonymized: ~400
Projects Anonymized: ~5,400
Contracts Anonymized: ~2,100

Total Duration: ~5-10 seconds
```

## 🔑 Key Properties

### Deterministic Hashing
- Same input + same seed → same output (always)
- Re-running with seed=42 produces identical results
- Different seed → different anonymized data

### Consistency Guaranteed
- Same user_id → same fake name everywhere
- Emails match usernames: user_abc123@anon-ucar.edu
- Institution references consistent across all tables
- All foreign keys remain valid

### Safety Features
- 🔒 Dry-run mode (no commits)
- 🔒 Confirmation prompt for live execution
- 🔒 Transaction rollback on error
- 🔒 Full statistics report

## ⚠️ Important Notes

1. **ALWAYS run on a replica** - Never on production
2. **ALWAYS dry-run first** - Preview before committing
3. **Verify after** - Run verify_anonymization.py
4. **Document seed** - Record seed value for reproducibility

## 🔧 Workflow

```bash
# Step 1: Backup database
mysqldump -u root -proot sam > sam_backup.sql

# Step 2: Preview transformations
python preview_anonymization.py

# Step 3: Dry-run
python anonymize_sam_db.py --dry-run

# Step 4: Execute (you'll be prompted to confirm)
python anonymize_sam_db.py --export-mappings mappings.json

# Step 5: Verify
python verify_anonymization.py

# Step 6: Test with applications
# ... test your apps with anonymized data ...

# Step 7 (if needed): Restore original
mysql -u root -proot sam < sam_backup.sql
```

## 📖 Full Documentation

See `ANONYMIZATION_README.md` for comprehensive documentation including:
- Detailed transformation examples
- Technical implementation details
- Troubleshooting guide
- Advanced usage options
- Performance details

## 🎯 Success Criteria

After anonymization, `verify_anonymization.py` should report:

```
✓ SUCCESS: Database appears properly anonymized!
```

All checks should show:
- ✓ Usernames appear anonymized
- ✓ Email domains appear anonymized
- ✓ Phone numbers appear anonymized
- ✓ Contract numbers appear anonymized
- ✓ UPIDs appear anonymized
- ✓ All foreign key relationships intact

## 💡 Tips

- Use `--config` with config.yaml for persistent settings
- **Preserve test usernames** for unit testing (see ANONYMIZATION_PRESERVE_USERNAMES.md)
- Use `--seed` parameter for reproducible anonymization
- Export mappings with `--export-mappings` for debugging
- Check `mappings.json` to see exact transformations
- Verify referential integrity with test queries
- Test applications thoroughly with anonymized data

## 📚 Documentation Files

- **ANONYMIZATION_SUMMARY.md** (this file) - Quick reference
- **ANONYMIZATION_README.md** - Comprehensive guide
- **ANONYMIZATION_PRESERVE_USERNAMES.md** ✨ NEW - Username preservation guide
