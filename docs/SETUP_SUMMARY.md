# Local Setup Summary

Quick reference for local development setup.

## Three-Step Setup

```bash
# 1. Install Python environment
./install_local.sh

# 2. Set up local database
./setup_local_db.sh

# 3. Test installation
./test_database.sh
```

## Scripts Overview

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `install_local.sh` | Install Python environment | First-time setup |
| `setup_local_db.sh` | Set up local database | First-time setup, database issues |
| `test_database.sh` | Test connection | After setup, troubleshooting |
| `scripts/setup/switch_to_production_db.sh` | Switch to production DB | Need production data |
| `scripts/setup/switch_to_local_db.sh` | Switch back to local DB | Development, testing |
| `scripts/setup/fix_mysql_permissions.sh` | Fix MySQL permissions | Connection errors |
| `scripts/setup/download_backup.sh` | Download backup from Git LFS | Backup file issues |
| `scripts/setup/check_docker.sh` | Docker diagnostics | Docker issues |

## Quick Commands

```bash
# Activate environment (always needed)
source etc/config_env.sh

# Start database (if stopped)
docker compose up -d mysql

# Stop database
docker compose down mysql

# View database logs
docker compose logs mysql -f

# Run tests
pytest tests/ --no-cov

# Switch to production database
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh

# Switch back to local database
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh
```

## Database Switching

**Switch to Production:**
```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh
```

**Switch to Local:**
```bash
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh
```

**Note:** Production database is read-only. CLI and Python queries work, but webapp CRUD operations will fail.

## Documentation

- **[LOCAL_SETUP.md](LOCAL_SETUP.md)** - Complete setup guide with troubleshooting
- **[CREDENTIALS.md](CREDENTIALS.md)** - How to configure credentials securely
- **[DATABASE_SWITCHING.md](DATABASE_SWITCHING.md)** - Switching between databases
- **[SCRIPTS.md](SCRIPTS.md)** - Detailed script reference
- **[DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md)** - Docker issues
- **[WEBAPP_SETUP.md](WEBAPP_SETUP.md)** - Web application setup
- **[README.md](../README.md)** - Project overview
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Development guide
