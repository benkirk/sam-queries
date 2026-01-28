# Setup Scripts Reference

Reference guide for all setup and utility scripts in the SAM Queries project.

## Script Organization

**Root Directory (Main Entry Points):**
- `install.sh` - Bootstrap script (clones repo, can be run via `curl | bash`)
- `install_local.sh` - Python environment installation
- `setup_local_db.sh` - Database setup
- `test_database.sh` - Connection testing

**scripts/setup/ (Utilities):**
- Database switching, troubleshooting, Git LFS utilities

**scripts/ (System Status):**
- Python scripts for system_status database management

See [scripts/README.md](../scripts/README.md) for directory structure.

**Rationale:** Root scripts are essential entry points used in primary workflows. Utility scripts are in `scripts/setup/` for organization.

## Essential Setup Scripts (Root)

### `install.sh`

**Purpose:** Bootstrap script for cloning and initializing the repository

**What it does:**
- Clones repository (if not already cloned)
- Checks for required tools (git, git-lfs, docker, docker compose)
- Sets up Git LFS
- Creates `.env` file from example
- Can be run via `curl | bash` for quick setup

**Usage:**
```bash
# Direct execution
./install.sh

# Or piped from web (bootstrap)
curl -sSL https://raw.githubusercontent.com/benkirk/sam-queries/main/install.sh | bash

# With options
./install.sh --dir /path/to/install --branch main
```

**When to use:**
- Initial repository setup from scratch
- Automated deployment scenarios
- When you want to clone repo to a specific location
- CI/CD workflows (tested in `.github/workflows/test-install.yaml`)

**Note:** This is different from `install_local.sh` - `install.sh` clones the repo, `install_local.sh` sets up the Python environment.

---

### `install_local.sh`

**Purpose:** Install Python environment and dependencies

**What it does:**
- Checks for Conda installation
- Creates conda environment (`conda-env/`)
- Installs all Python dependencies from `pyproject.toml`

**Usage:**
```bash
./install_local.sh
```

**When to use:**
- First-time setup (after cloning repo)
- After cloning repository
- When dependencies change

**Time:** 2-5 minutes

---

### `setup_local_db.sh`

**Purpose:** Complete local database setup

**What it does:**
- Checks Docker is running
- Downloads database backup from Git LFS (if needed)
- Stops/removes existing MySQL container and volume
- Starts fresh MySQL container
- Restores database from backup
- Waits for database to be healthy

**Usage:**
```bash
./setup_local_db.sh
```

**When to use:**
- First-time database setup
- After pulling updated backup file
- When database is corrupted and needs fresh restore

**Time:** 5-10 minutes (first time, includes download and restore)

**Note:** This will delete existing database data. Use with caution.

---

### `test_database.sh`

**Purpose:** Test database connection and functionality

**What it does:**
- Verifies `sam` database exists
- Checks table count
- Verifies `users` table has data
- Tests CLI connection
- Shows sample query output

**Usage:**
```bash
./test_database.sh
```

**When to use:**
- After setup to verify everything works
- When troubleshooting connection issues
- Before starting development work

**Output:** Shows detailed test results and sample CLI output

---

## Utility Scripts (scripts/setup/)

### `scripts/setup/switch_to_production_db.sh`

**Purpose:** Switch `.env` to use production database

**What it does:**
- Backs up current `.env` file
- Comments out local database settings
- Uncomments production database settings
- Shows current configuration

**Usage:**
```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh  # Reload environment
```

**When to use:** When you need access to production data for read-only queries.

---

### `scripts/setup/switch_to_local_db.sh`

**Purpose:** Switch `.env` back to local database

**What it does:**
- Backs up current `.env` file
- Comments out production database settings
- Uncomments local database settings
- Shows current configuration

**Usage:**
```bash
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh  # Reload environment
```

**When to use:** When switching back from production to local development.

---

### `scripts/setup/download_backup.sh`

**Purpose:** Download database backup from Git LFS

**What it does:**
- Checks if Git LFS is installed
- Downloads actual backup file (replaces LFS pointer)
- Verifies file was downloaded

**Usage:**
```bash
./scripts/setup/download_backup.sh
```

**When to use:**
- When backup file is a Git LFS pointer
- After `git clone` without LFS
- When backup file is missing

**Note:** Usually not needed - `setup_local_db.sh` handles this automatically

---

### `scripts/setup/fix_mysql_permissions.sh`

**Purpose:** Fix MySQL permissions for Docker Desktop connections

**What it does:**
- Grants root access from any host (including Docker Desktop VM)
- Handles both password and no-password scenarios
- Shows current user permissions

**Usage:**
```bash
./scripts/setup/fix_mysql_permissions.sh
```

**When to use:** When you see error: `Host '192.168.65.1' is not allowed to connect`

---

### `scripts/setup/check_docker.sh`

**Purpose:** Diagnose Docker Desktop status and permissions

**What it does:**
- Checks Docker Desktop process status
- Finds Docker socket location
- Tests Docker connection
- Verifies docker compose availability

**Usage:**
```bash
./scripts/setup/check_docker.sh
```

**When to use:** When troubleshooting Docker permission or connection issues.

---

## Script Workflow

### First-Time Setup (Bootstrap)

```bash
# Option 1: Bootstrap script (clones repo)
./install.sh

# Option 2: Manual setup (if repo already cloned)
./install_local.sh      # Install Python environment
./setup_local_db.sh     # Set up database
./test_database.sh      # Test installation
```

### Daily Development

```bash
# Activate environment (always needed)
source etc/config_env.sh

# Start database (if stopped)
docker compose up -d mysql

# Test connection (if needed)
./test_database.sh
```

### Troubleshooting

```bash
# Database connection issues
./scripts/setup/fix_mysql_permissions.sh

# Docker issues
./scripts/setup/check_docker.sh

# Database missing or corrupted
./setup_local_db.sh  # Will recreate from backup

# Verify setup
./test_database.sh
```

### Switching Databases

```bash
# Switch to production
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh

# Switch back to local
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh
```

## Script Dependencies

```
install.sh (root)
  └─> Requires: Git, Git LFS, Docker, docker compose
  └─> Clones: Repository to target directory
  └─> Creates: .env file

install_local.sh (root)
  └─> Requires: Conda, Make, Git
  └─> Creates: conda-env/, .env

setup_local_db.sh (root)
  └─> Requires: Docker, Git LFS (for backup)
  └─> Uses: containers/sam-sql-dev/backups/sam-obfuscated.sql.xz
  └─> Creates: MySQL container, database volume
  └─> May call: scripts/setup/download_backup.sh

test_database.sh (root)
  └─> Requires: Docker, conda environment
  └─> Uses: etc/config_env.sh, sam-search CLI

scripts/setup/switch_to_production_db.sh
  └─> Requires: .env file exists
  └─> Modifies: .env file

scripts/setup/switch_to_local_db.sh
  └─> Requires: .env file exists
  └─> Modifies: .env file

scripts/setup/fix_mysql_permissions.sh
  └─> Requires: Docker, MySQL container running
  └─> Modifies: MySQL user permissions

scripts/setup/check_docker.sh
  └─> Requires: Docker Desktop installed
  └─> Tests: Docker connection, socket permissions

scripts/setup/download_backup.sh
  └─> Requires: Git LFS installed
  └─> Downloads: containers/sam-sql-dev/backups/sam-obfuscated.sql.xz
```

## Environment Variables

Scripts use these environment variables (from `.env`):

- `SAM_DB_USERNAME` - Database username (default: root)
- `SAM_DB_SERVER` - Database host (default: 127.0.0.1)
- `SAM_DB_PASSWORD` - Database password (default: root)
- `SAM_DB_REQUIRE_SSL` - SSL requirement (default: false)

## Error Handling

All scripts include error checking:

- **Missing prerequisites:** Scripts check for required tools and provide installation instructions
- **Docker issues:** Scripts verify Docker is running before use
- **File checks:** Scripts verify backup files exist and are valid
- **Health checks:** Database scripts wait for healthy status before completing

## See Also

- [scripts/README.md](../scripts/README.md) - Scripts directory overview
- [scripts/setup/README.md](../scripts/setup/README.md) - Setup utilities documentation
- [LOCAL_SETUP.md](LOCAL_SETUP.md) - Complete local setup guide
- [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md) - Database switching guide
- [README.md](../README.md) - Project overview
- [CONTRIBUTING.md](../CONTRIBUTING.md) - Development guide
