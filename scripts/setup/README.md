# Setup Utility Scripts

Utility scripts for troubleshooting and advanced setup tasks.

## Scripts

### Database Utilities

#### `switch_to_production_db.sh`
Switch `.env` configuration to use production database.

**Usage:**
```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh
```

**When to use:** When you need access to production data for read-only queries.

**See:** [../../docs/DATABASE_SWITCHING.md](../../docs/DATABASE_SWITCHING.md) for complete guide.

---

#### `switch_to_local_db.sh`
Switch `.env` configuration back to local database.

**Usage:**
```bash
./scripts/setup/switch_to_local_db.sh
source etc/config_env.sh
```

**When to use:** When switching back from production to local development.

---

### Troubleshooting Utilities

#### `check_docker.sh`
Diagnose Docker Desktop status and permissions.

**Usage:**
```bash
./scripts/setup/check_docker.sh
```

**When to use:** When you see Docker permission errors or connection issues.

**What it checks:**
- Docker Desktop process status
- Docker socket location and permissions
- Docker connection test
- docker compose availability

**See:** [../../docs/DOCKER_TROUBLESHOOTING.md](../../docs/DOCKER_TROUBLESHOOTING.md) for detailed solutions.

---

#### `fix_mysql_permissions.sh`
Fix MySQL permissions for Docker Desktop VM connections.

**Usage:**
```bash
./scripts/setup/fix_mysql_permissions.sh
```

**When to use:** When you see error: `Host '192.168.65.1' is not allowed to connect`

**What it does:**
- Grants root access from any host (including Docker Desktop VM)
- Handles both password and no-password scenarios
- Shows current user permissions

---

### Git LFS Utilities

#### `download_backup.sh`
Download database backup from Git LFS.

**Usage:**
```bash
./scripts/setup/download_backup.sh
```

**When to use:**
- When backup file is a Git LFS pointer
- After `git clone` without LFS
- When backup file is missing

**Note:** Usually not needed - `setup_local_db.sh` handles this automatically.

---

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `switch_to_production_db.sh` | Switch to production DB | Need production data |
| `switch_to_local_db.sh` | Switch to local DB | Back to development |
| `check_docker.sh` | Docker diagnostics | Docker issues |
| `fix_mysql_permissions.sh` | Fix MySQL permissions | Connection errors |
| `download_backup.sh` | Download backup | Backup file issues |

## Usage Examples

### Switch to Production Database

```bash
./scripts/setup/switch_to_production_db.sh
source etc/config_env.sh
sam-search user benkirk  # Test connection
```

### Troubleshoot Docker Issues

```bash
./scripts/setup/check_docker.sh  # Diagnose issue
# Follow recommendations from output
```

### Fix MySQL Permissions

```bash
./scripts/setup/fix_mysql_permissions.sh
source etc/config_env.sh
sam-search user --search "a%" | head -10  # Test connection
```

## See Also

- **[../../docs/SCRIPTS.md](../../docs/SCRIPTS.md)** - Complete script reference
- **[../../docs/LOCAL_SETUP.md](../../docs/LOCAL_SETUP.md)** - Local setup guide
- **[../../docs/DATABASE_SWITCHING.md](../../docs/DATABASE_SWITCHING.md)** - Database switching guide
- **[../../docs/DOCKER_TROUBLESHOOTING.md](../../docs/DOCKER_TROUBLESHOOTING.md)** - Docker troubleshooting
