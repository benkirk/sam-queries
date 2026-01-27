#!/bin/bash
# This script runs in /docker-entrypoint-initdb.d/ during MySQL's first startup
# MySQL is already running and accepting connections when this executes
#
# IMPORTANT: This file is SOURCED by the MySQL entrypoint (not executed).
# Do NOT use 'exit' - it will kill the parent entrypoint!
# Do NOT use 'set -e' - errors should be handled gracefully.

echo "[init-db.sh] Starting database initialization..."
echo "[init-db.sh] MYSQL_ROOT_PASSWORD is set: $([ -n "${MYSQL_ROOT_PASSWORD}" ] && echo 'yes' || echo 'no')"

# Create databases (sam is already created by MYSQL_DATABASE env var)
echo "[init-db.sh] Creating system_status database..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS system_status;" 2>&1 || echo "[init-db.sh] Warning: Failed to create system_status"

echo "[init-db.sh] Setting up root user for remote access..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';" 2>&1 || echo "[init-db.sh] Warning: Failed to create root@%"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;" 2>&1 || echo "[init-db.sh] Warning: Failed to grant privileges"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;" 2>&1 || echo "[init-db.sh] Warning: Failed to flush privileges"

# Verify databases exist
echo "[init-db.sh] Verifying databases..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW DATABASES;" 2>&1 || echo "[init-db.sh] Warning: Failed to show databases"

# Try to restore backup if available and not a LFS pointer
if [ -f /backup.sql.xz ]; then
    echo "[init-db.sh] Found backup file at /backup.sql.xz"
    BACKUP_SIZE=$(stat -c%s /backup.sql.xz 2>/dev/null || stat -f%z /backup.sql.xz 2>/dev/null || echo "unknown")
    echo "[init-db.sh] Backup file size: ${BACKUP_SIZE} bytes"
    
    FIRST_BYTES=$(head -c 50 /backup.sql.xz 2>/dev/null | cat -v || echo "")
    echo "[init-db.sh] First 50 bytes: ${FIRST_BYTES}"
    
    if echo "${FIRST_BYTES}" | grep -q "version https"; then
        echo "[init-db.sh] Backup is Git LFS pointer - skipping restore"
        echo "[init-db.sh] Run 'git lfs pull' to download the actual backup file"
    else
        echo "[init-db.sh] Backup appears to be actual data, checking for xzcat..."
        if command -v xzcat >/dev/null 2>&1; then
            echo "[init-db.sh] xzcat found, attempting backup restore (this may take a while)..."
            if xzcat /backup.sql.xz 2>&1 | mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" 2>&1; then
                echo "[init-db.sh] Backup restored successfully!"
            else
                echo "[init-db.sh] Backup restore failed (continuing with empty database)"
            fi
        else
            echo "[init-db.sh] xzcat not available - skipping restore"
            echo "[init-db.sh] Install xz-utils to enable backup restore"
        fi
    fi
else
    echo "[init-db.sh] No backup file found at /backup.sql.xz"
fi

# Show table counts
echo "[init-db.sh] Counting tables in databases..."
SAM_TABLES=$(mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "error")
SS_TABLES=$(mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'system_status';" 2>/dev/null || echo "error")
echo "[init-db.sh] Database 'sam' has ${SAM_TABLES} tables"
echo "[init-db.sh] Database 'system_status' has ${SS_TABLES} tables"

echo "[init-db.sh] Initialization complete!"
# NOTE: Do NOT use 'exit' here - this script is sourced, not executed
