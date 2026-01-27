#!/bin/bash
# This script runs in /docker-entrypoint-initdb.d/ during MySQL's first startup
# MySQL is already running and accepting connections when this executes

set +e  # Don't exit on errors

echo "[init-db.sh] Starting database initialization..."

# Create databases (sam is already created by MYSQL_DATABASE env var)
echo "[init-db.sh] Creating system_status database..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE DATABASE IF NOT EXISTS system_status;" || echo "Warning: Failed to create system_status"

echo "[init-db.sh] Setting up root user for remote access..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';" || echo "Warning: Failed to create root@%"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;" || echo "Warning: Failed to grant privileges"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;" || echo "Warning: Failed to flush privileges"

# Try to restore backup if available and not a LFS pointer
if [ -f /backup.sql.xz ]; then
    echo "[init-db.sh] Found backup file, checking if it's a LFS pointer..."
    if head -c 20 /backup.sql.xz | grep -q "version https"; then
        echo "[init-db.sh] Backup is Git LFS pointer - skipping restore"
    else
        echo "[init-db.sh] Attempting backup restore (this may take a while)..."
        if command -v xzcat >/dev/null 2>&1; then
            xzcat /backup.sql.xz 2>/dev/null | mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" 2>&1 && echo "[init-db.sh] Backup restored!" || echo "[init-db.sh] Backup restore failed"
        else
            echo "[init-db.sh] xzcat not available - skipping restore"
        fi
    fi
else
    echo "[init-db.sh] No backup file found at /backup.sql.xz"
fi

echo "[init-db.sh] Initialization complete!"
exit 0
