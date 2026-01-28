# Scripts Directory

Organized scripts for SAM Queries project setup, maintenance, and utilities.

## Directory Structure

```
scripts/
├── README.md                    # This file
├── setup/                       # Setup and utility scripts
│   ├── README.md               # Setup scripts documentation
│   ├── switch_to_production_db.sh
│   ├── switch_to_local_db.sh
│   ├── check_docker.sh
│   ├── fix_mysql_permissions.sh
│   └── download_backup.sh
├── setup_status_db.py          # System status database setup
├── test_status_db.py           # System status database testing
├── cleanup_status_data.py      # System status data cleanup
├── ingest_mock_status.py       # Mock status data ingestion
└── create_status_db.sql        # System status database creation SQL
```

## Script Categories

### Setup Scripts (`setup/`)

Utility scripts for database setup, switching, and troubleshooting:

- **Database Switching:** Switch between local and production databases
- **Troubleshooting:** Docker diagnostics, MySQL permissions fixes
- **Git LFS:** Download database backup files

See [setup/README.md](setup/README.md) for detailed documentation.

### System Status Scripts

Python scripts for managing the `system_status` database:

- `setup_status_db.py` - Create system status database tables
- `test_status_db.py` - Test system status database connection
- `cleanup_status_data.py` - Clean up old status snapshots
- `ingest_mock_status.py` - Ingest mock status data for testing
- `create_status_db.sql` - SQL script for database creation

## Main Entry Point Scripts

These scripts remain in the project root for easy access:

- `install.sh` - Bootstrap script (clones repo, tested in CI)
- `install_local.sh` - Main installation script (Python environment)
- `setup_local_db.sh` - Main database setup script
- `test_database.sh` - Main testing script

**Why in root:** These are primary workflow entry points, frequently used, and `install.sh` is designed for `curl | bash` execution.

## Usage

### Setup Utilities

```bash
# Switch databases
./scripts/setup/switch_to_production_db.sh
./scripts/setup/switch_to_local_db.sh

# Troubleshooting
./scripts/setup/check_docker.sh
./scripts/setup/fix_mysql_permissions.sh

# Git LFS
./scripts/setup/download_backup.sh
```

### System Status Scripts

```bash
# Setup system status database
python scripts/setup_status_db.py

# Test connection
python scripts/test_status_db.py

# Cleanup old data
python scripts/cleanup_status_data.py
```

## See Also

- **[setup/README.md](setup/README.md)** - Setup utility scripts
- **[../docs/SCRIPTS.md](../docs/SCRIPTS.md)** - Complete script reference
- **[../docs/LOCAL_SETUP.md](../docs/LOCAL_SETUP.md)** - Setup guide
