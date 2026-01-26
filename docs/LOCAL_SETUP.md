# Local Development Setup Guide

Complete guide for setting up SAM Queries locally for development and testing.

## Prerequisites

- **Conda** (Miniconda or Anaconda) - [Installation Guide](https://docs.conda.io/en/latest/miniconda.html)
- **Docker Desktop** - [Download](https://www.docker.com/products/docker-desktop)
- **Git** (for cloning repository)
- **Git LFS** (for downloading database backup) - Install with `brew install git-lfs && git lfs install`

## Quick Start

### Step 1: Install Python Environment

```bash
cd sam-queries
./install_local.sh
```

This will:
- Check for Conda installation
- Create conda environment (`conda-env/`)
- Install all Python dependencies
- Set up environment activation

**Time:** 2-5 minutes

### Step 2: Set Up Local Database

```bash
./setup_local_db.sh
```

This will:
- Check Docker is running
- Download database backup from Git LFS (if needed)
- Start MySQL container
- Restore database from backup
- Wait for database to be healthy

**Time:** 5-10 minutes (first time, includes backup download and restore)

### Step 3: Test Installation

```bash
./test_database.sh
```

Or test manually:
```bash
source etc/config_env.sh
sam-search user --search "a%" | head -10
```

## Detailed Setup

### Python Environment Setup

The `install_local.sh` script handles Python environment setup:

1. **Checks for Conda**: Verifies Conda is installed and accessible
2. **Creates Environment**: Uses `make conda-env` to create isolated environment
3. **Installs Dependencies**: Installs all packages from `pyproject.toml`

**Manual steps** (if script doesn't work):
```bash
make conda-env
source etc/config_env.sh
```

### Database Setup

The `setup_local_db.sh` script handles database setup:

1. **Checks Docker**: Verifies Docker Desktop is running
2. **Downloads Backup**: Pulls actual backup file from Git LFS (if pointer file exists)
3. **Starts Container**: Creates fresh MySQL container
4. **Restores Database**: Automatically restores from compressed backup
5. **Waits for Health**: Monitors until database is ready

**Manual steps** (if script doesn't work):

```bash
# 1. Download backup (if Git LFS pointer)
git lfs pull containers/sam-sql-dev/backups/sam-obfuscated.sql.xz

# 2. Start database
docker compose up -d mysql

# 3. Wait for health check
docker compose ps mysql  # Should show "healthy"

# 4. Verify database
docker compose exec mysql mysql -uroot -proot -e "SHOW DATABASES;"
```

### Environment Configuration

The `.env` file is automatically created from `.env.example` with local database settings.

**Important:** Add your production credentials to `.env` (not `.env.example`). See [CREDENTIALS.md](CREDENTIALS.md) for details.

```bash
SAM_DB_USERNAME=root
SAM_DB_SERVER=127.0.0.1
SAM_DB_PASSWORD=root
```

**To use production database** (instead of local):

**Option 1: Use switch script (recommended)**
```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh
```

**Option 2: Manual edit**
1. Edit `.env` file
2. Comment out local settings
3. Uncomment production settings (already configured)

**See [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md) for complete guide.**

## Available Scripts

### Essential Scripts

- **`install_local.sh`** - Install Python environment and dependencies
- **`setup_local_db.sh`** - Complete database setup (download backup, start, restore)
- **`test_database.sh`** - Test database connection and CLI functionality

### Troubleshooting Scripts

- **`scripts/setup/fix_mysql_permissions.sh`** - Fix MySQL permissions for Docker Desktop connections
  - Use if you see: `Host '192.168.65.1' is not allowed to connect`
- **`scripts/setup/check_docker.sh`** - Diagnose Docker status and permissions
- **`scripts/setup/download_backup.sh`** - Download backup from Git LFS

## Common Issues & Solutions

### Issue: Docker Permission Denied

**Error:** `permission denied while trying to connect to the Docker daemon socket`

**Solutions:**
1. Ensure Docker Desktop is fully started (whale icon in menu bar)
2. Restart Docker Desktop
3. If using Cursor terminal, try regular Terminal.app instead
4. Run diagnostic: `./scripts/setup/check_docker.sh`

See [DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md) for detailed solutions.

### Issue: "Conda is not installed"

**Solution:**
1. Install Miniconda: https://docs.conda.io/en/latest/miniconda.html
2. Restart terminal or run: `source ~/.zshrc`
3. Run `./install_local.sh` again

### Issue: "Docker is not running"

**Solution:**
1. Start Docker Desktop application
2. Wait for whale icon in menu bar
3. Run `./setup_local_db.sh` again

### Issue: "Backup file is a Git LFS pointer"

**Solution:**
```bash
# Install Git LFS
brew install git-lfs
git lfs install

# Download backup
git lfs pull containers/sam-sql-dev/backups/sam-obfuscated.sql.xz

# Run setup again
./setup_local_db.sh
```

### Issue: "Host '192.168.65.1' is not allowed to connect"

**Solution:**
```bash
./scripts/setup/fix_mysql_permissions.sh
```

This grants MySQL access from Docker Desktop VM IP.

### Issue: "Unknown database 'sam'"

**Solution:**
The database restore may not have completed. Check:
```bash
docker compose logs mysql | tail -50
```

If restore failed, restart:
```bash
docker compose down mysql
docker volume rm sam-queries_samuel-mysql-data
./setup_local_db.sh
```

### Issue: Database restore takes too long

**Normal behavior:** Large backups (9.9MB compressed) can take 5-10 minutes to restore. The script waits up to 10 minutes. Check progress:
```bash
docker compose logs mysql -f
```

Look for: `"Restore completed successfully!"`

## Daily Usage

### Starting the Database

If database container is stopped:
```bash
docker compose up -d mysql
```

Check status:
```bash
docker compose ps mysql
```

### Activating Environment

Always activate the environment before using CLI or running tests:
```bash
source etc/config_env.sh
```

This:
- Activates conda environment
- Loads `.env` file
- Sets up PYTHONPATH

### Running Tests

```bash
# Fast tests (parallel, no coverage) - ~32 seconds
pytest tests/ --no-cov

# Full tests with coverage - ~97 seconds
pytest tests/
```

### Using CLI

```bash
# Activate environment first
source etc/config_env.sh

# Search users
sam-search user benkirk
sam-search user --search "ben%"

# Search projects
sam-search project SCSG0001

# Get help
sam-search --help
```

### Stopping Database

```bash
docker compose down mysql
```

To remove all data:
```bash
docker compose down mysql
docker volume rm sam-queries_samuel-mysql-data
```

## Database Details

### Local Database

- **Host:** 127.0.0.1
- **Port:** 3306
- **Database:** sam
- **Username:** root
- **Password:** root
- **Size:** ~10MB compressed backup, expands to ~50-100MB
- **Tables:** 104 tables
- **Sample Data:** ~27,000 users (anonymized/obfuscated)

### Container Information

- **Image:** mysql:9
- **Container Name:** samuel-mysql
- **Volume:** sam-queries_samuel-mysql-data
- **Health Check:** Verifies MySQL is ready AND `users` table exists

### Backup File

- **Location:** `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz`
- **Size:** ~9.9MB compressed
- **Format:** XZ compressed SQL dump
- **Storage:** Git LFS (large file storage)

## Next Steps

After successful setup:

1. **Configure Credentials** (if needed):
   - See [CREDENTIALS.md](CREDENTIALS.md) for production database, GitHub, AWS credentials
   - Add credentials to `.env` file (never commit to Git)

2. **Read Documentation:**
   - [README.md](../README.md) - Project overview
   - [CREDENTIALS.md](CREDENTIALS.md) - Credential configuration
   - [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md) - Switch between databases
   - [CONTRIBUTING.md](../CONTRIBUTING.md) - Development guide
   - [CLAUDE.md](../CLAUDE.md) - Technical reference

2. **Explore CLI:**
   ```bash
   sam-search --help
   sam-search user --help
   sam-search project --help
   ```

3. **Run Tests:**
   ```bash
   pytest tests/ --no-cov
   ```

4. **Start Development:**
   - Check out existing code in `src/`
   - Read test files in `tests/` for examples
   - Follow patterns in [CLAUDE.md](../CLAUDE.md)

## Getting Help

- **Setup Issues:** Check this guide and troubleshooting section
- **Database Issues:** Check Docker logs: `docker compose logs mysql`
- **Python Issues:** Check conda environment: `conda env list`
- **CLI Issues:** Run `sam-search --help` for usage

## Related Documentation

- **[SCRIPTS.md](SCRIPTS.md)** - Detailed script reference
- **[SETUP_SUMMARY.md](SETUP_SUMMARY.md)** - Quick reference
- **[README.md](../README.md)** - Project overview
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Development guide
- **[CLAUDE.md](../CLAUDE.md)** - Technical reference
