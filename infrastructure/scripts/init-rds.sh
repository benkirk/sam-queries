#!/bin/bash
set -euo pipefail

# Restore obfuscated SAM backup to staging RDS instance.
# Run this ONCE after terraform apply to initialize the database.
#
# Usage:
#   cd infrastructure/staging
#   ../scripts/init-rds.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STAGING_DIR="$REPO_ROOT/infrastructure/staging"
BACKUP_FILE="$REPO_ROOT/containers/sam-sql-dev/backups/sam-obfuscated.sql.xz"

source "$REPO_ROOT/scripts/lib/prereqs.sh"

# --- Prerequisites ---
require_cmd mysql mysql-client mysql-client "MySQL client"
require_cmd xzcat xz xz-utils "xz decompressor"
require_cmd terraform terraform terraform "Terraform"

msg() { echo "[init-rds] $(date '+%H:%M:%S'): $*"; }
err() { echo "[init-rds] ERROR: $*" >&2; }

# Get RDS endpoint from Terraform outputs
msg "Reading Terraform outputs..."
cd "$STAGING_DIR"

RDS_HOST=$(terraform output -raw rds_address 2>/dev/null)
if [ -z "$RDS_HOST" ]; then
    err "Could not read rds_address from Terraform outputs."
    err "Make sure you've run 'terraform apply' first."
    exit 1
fi
msg "RDS endpoint: $RDS_HOST"

check_vpn "$RDS_HOST" 3306

# Read credentials from secrets.auto.tfvars
DB_USER=$(grep 'db_username' "$STAGING_DIR/secrets.auto.tfvars" | sed 's/.*= *"//' | sed 's/".*//')
DBPW=$(grep 'db_password' "$STAGING_DIR/secrets.auto.tfvars" | sed 's/.*= *"//' | sed 's/".*//')

if [ -z "$DB_USER" ] || [ -z "$DBPW" ]; then
    err "Could not read credentials from secrets.auto.tfvars"
    exit 1
fi

# Verify backup file exists
if [ ! -f "$BACKUP_FILE" ]; then
    err "Backup file not found: $BACKUP_FILE"
    err "Run 'git lfs pull' to download the backup."
    exit 1
fi

FILE_SIZE=$(stat -f%z "$BACKUP_FILE" 2>/dev/null || stat -c%s "$BACKUP_FILE" 2>/dev/null)
if [ "$FILE_SIZE" -lt 1000 ]; then
    err "Backup file appears to be a Git LFS pointer (${FILE_SIZE} bytes)."
    err "Run 'git lfs pull' to download the actual file."
    exit 1
fi

msg "Backup file: $BACKUP_FILE ($FILE_SIZE bytes)"

# Test MySQL auth
msg "Testing MySQL connection..."
if ! mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" -e "SELECT 1" 2>/dev/null; then
    err "Cannot authenticate to RDS. Check credentials in secrets.auto.tfvars."
    exit 1
fi
msg "Connection successful."

# Create databases
msg "Creating databases..."
mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" -e "CREATE DATABASE IF NOT EXISTS sam;"
mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" -e "CREATE DATABASE IF NOT EXISTS system_status;"

# Restore backup
msg "Restoring backup (this may take a few minutes)..."
xzcat "$BACKUP_FILE" | mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" 2>&1

# Verify
TABLE_COUNT=$(mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" --skip-column-names \
    -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null)
msg "Restore complete. SAM database has $TABLE_COUNT tables."

USER_COUNT=$(mysql -h "$RDS_HOST" -u "$DB_USER" -p"$DBPW" --skip-column-names \
    -e "SELECT COUNT(*) FROM sam.users;" 2>/dev/null || echo "0")
msg "Users table has $USER_COUNT rows."

msg "Done! RDS is ready for staging."
