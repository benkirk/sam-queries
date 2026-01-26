# Script Organization

Overview of script organization in the SAM Queries project.

## Organization Structure

```
sam-queries/
├── install.sh                  # Bootstrap script (clones repo)
├── install_local.sh            # Main: Install Python environment
├── setup_local_db.sh           # Main: Set up local database
├── test_database.sh            # Main: Test database connection
│
└── scripts/
    ├── README.md              # Scripts directory overview
    ├── setup/                 # Setup utilities
    │   ├── README.md         # Setup utilities documentation
    │   ├── switch_to_production_db.sh
    │   ├── switch_to_local_db.sh
    │   ├── check_docker.sh
    │   ├── fix_mysql_permissions.sh
    │   └── download_backup.sh
    │
    └── [Python scripts]       # System status database scripts
        ├── setup_status_db.py
        ├── test_status_db.py
        └── ...
```

## Script Categories

### Main Entry Points (Root)

**Purpose:** Primary setup scripts that users run first

- `install.sh` - Bootstrap/clone repository
- `install_local.sh` - Install Python environment
- `setup_local_db.sh` - Set up local database
- `test_database.sh` - Test installation

**Why in root:** Easy to discover, primary workflow entry points

### Setup Utilities (scripts/setup/)

**Purpose:** Troubleshooting and advanced configuration

- Database switching (production ↔ local)
- Docker diagnostics
- MySQL permission fixes
- Git LFS backup download

**Why in scripts/setup/:** Secondary utilities, not needed for basic setup

### System Status Scripts (scripts/)

**Purpose:** Python scripts for system_status database

- Database setup and testing
- Data cleanup and ingestion

**Why in scripts/:** Domain-specific utilities, not core setup

## Usage Patterns

### New Developer Setup

```bash
# Main workflow (root scripts)
./install_local.sh
./setup_local_db.sh
./test_database.sh
```

### Advanced Usage

```bash
# Utilities (scripts/setup/)
./scripts/setup/switch_to_production_db.sh
./scripts/setup/check_docker.sh
./scripts/setup/fix_mysql_permissions.sh
```

## Rationale

**Root scripts:** Essential, run frequently, easy to find  
**scripts/setup/:** Utilities, troubleshooting, advanced features  
**scripts/:** Domain-specific (system_status), Python-based

This organization:
- Keeps root directory clean
- Groups related utilities together
- Makes it clear which scripts are essential vs. optional
- Follows common project conventions

## See Also

- **[SCRIPTS.md](SCRIPTS.md)** - Complete script reference
- **[scripts/README.md](../scripts/README.md)** - Scripts directory overview
- **[scripts/setup/README.md](../scripts/setup/README.md)** - Setup utilities
