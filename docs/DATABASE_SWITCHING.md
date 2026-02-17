# Switching Between Local and Production Databases

Guide for switching between local Docker database and production database.

## Quick Switch

### Switch to Production Database

```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh  # Reload environment
```

### Switch to Local Database

```bash
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh  # Reload environment
```

## Manual Configuration

Edit `.env` file directly:

### Use Local Database (Default)

```bash
SAM_DB_USERNAME=root
SAM_DB_SERVER=127.0.0.1
SAM_DB_PASSWORD=root
SAM_DB_REQUIRE_SSL=false
```

### Use Production Database

```bash
SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}
SAM_DB_SERVER=${PROD_SAM_DB_SERVER}
SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}
SAM_DB_REQUIRE_SSL=true
```

**Note:** Comment out the local settings when using production.

## Production Database Details

- **Server:** sam-sql.ucar.edu
- **Access:** Read-only
- **SSL:** Required
- **Credentials:** Contact CISL staff for production database credentials
- **See:** [CREDENTIALS.md](CREDENTIALS.md) for credential setup

## What Works with Production Database

### ✅ Works

- **CLI queries** (`sam-search`) - All read operations work perfectly
- **Python REPL** - All read queries work
- **ORM queries** - Read operations work
- **API GET endpoints** - Read operations work

### ⚠️ Limited

- **Webapp read operations** - Dashboard, viewing data works
- **Webapp search/filter** - Works

### ❌ Doesn't Work

- **Webapp CRUD operations** - Create/Update/Delete will fail (read-only)
- **Flask-Admin write operations** - Will show errors
- **Test suite CRUD tests** - Will be skipped (expected)

## Use Cases

### Use Production Database When:

- You need access to latest production data
- You're doing read-only analysis
- You're testing queries against real data
- You don't need to modify data
- Local database is not available

### Use Local Database When:

- You need to test CRUD operations
- You're developing features that modify data
- You want faster queries (local is faster)
- You're working offline
- You're running the full test suite

## Testing the Switch

After switching, test the connection:

```bash
# Reload environment
source etc/config_env.sh

# Test CLI
sam-search user benkirk

# Test Python
python3 -c "from sam.session import create_sam_engine; engine, _ = create_sam_engine(); print('✅ Connected!')"
```

## Troubleshooting

### Issue: "Access denied" with production

**Solutions:**
1. Check VPN connection (if required)
2. Verify credentials in `.env`
3. Ensure password is wrapped in single quotes if it has special characters
4. Check SSL requirement: `SAM_DB_REQUIRE_SSL=true`

### Issue: "Can't connect" with production

**Solutions:**
1. Check network connectivity: `ping sam-sql.ucar.edu`
2. Verify VPN is connected (if required)
3. Check firewall settings
4. Try from a different network

### Issue: Webapp errors with production

**Expected:** Webapp will show errors for write operations. This is normal for read-only access.

**Solution:** Use local database for webapp development, or disable write features in webapp.

## Security Notes

- **Production credentials are read-only** - Safe to use for queries
- **Never commit `.env` file** - It's gitignored
- **Rotate credentials** - If credentials are compromised, contact CISL staff
- **Use local database** - For development and testing when possible

## See Also

- [LOCAL_SETUP.md](LOCAL_SETUP.md) - Local database setup
- [README.md](../README.md) - Project overview
