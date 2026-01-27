#!/bin/bash
# This script runs in /docker-entrypoint-initdb.d/ during MySQL's first startup
# It's executed by the official MySQL entrypoint after database initialization

echo "[init-db.sh] Creating databases..."

# Create databases
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS sam;"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS system_status;"

echo "[init-db.sh] Setting up root user for remote access..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"

# Try to restore backup if available
if [ -f /backup.sql.xz ]; then
    echo "[init-db.sh] Checking backup file..."
    FIRST_BYTES=$(head -c 50 /backup.sql.xz 2>/dev/null || echo "")
    if echo "$FIRST_BYTES" | grep -q "version https://git-lfs"; then
        echo "[init-db.sh] Backup is Git LFS pointer - skipping restore"
    else
        echo "[init-db.sh] Restoring from backup..."
        if xzcat /backup.sql.xz 2>/dev/null | mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" 2>&1; then
            echo "[init-db.sh] ✓ Backup restored successfully"
        else
            echo "[init-db.sh] Backup restore failed (continuing with empty databases)"
        fi
    fi
else
    echo "[init-db.sh] No backup file found"
fi

# Count tables
SAM_TABLES=$(mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "0")
echo "[init-db.sh] Database 'sam' has $SAM_TABLES tables"

echo "[init-db.sh] ✓ Initialization complete"
