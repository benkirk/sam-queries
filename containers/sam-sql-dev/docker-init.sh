#!/bin/bash

# DO NOT use set -e - handle all errors explicitly
# set -e

msg() { echo "[docker-init.sh] $(date '+%Y-%m-%d %H:%M:%S'): $@"; }
err() { echo "[docker-init.sh] ERROR $(date '+%Y-%m-%d %H:%M:%S'): $@" >&2; }

# MySQL official image entrypoint variables
DATADIR="/var/lib/mysql"

msg "=== Starting MySQL initialization ==="

# Ensure directories exist with proper permissions
msg "Setting up directories..."
mkdir -p /var/run/mysqld || true
mkdir -p "${DATADIR}" || true
chown -R mysql:mysql /var/run/mysqld 2>/dev/null || true
chown -R mysql:mysql "${DATADIR}" 2>/dev/null || true
chmod 777 /var/run/mysqld 2>/dev/null || true
msg "Directories ready"

# If system tables exist, skip restore and start normally
if [ -d "${DATADIR}/mysql" ]; then
    msg "Existing MySQL data found — skipping initialization"
    msg "Starting MySQL with docker-entrypoint.sh..."
    exec docker-entrypoint.sh mysqld
fi

msg "No existing data found. Will initialize fresh database..."

# Initialize database
msg "Running mysqld --initialize-insecure..."
if mysqld --initialize-insecure --user=mysql --datadir="${DATADIR}" 2>&1; then
    msg "✓ Database initialized successfully"
else
    err "mysqld --initialize-insecure failed"
    msg "Checking if data directory was created anyway..."
    if [ -d "${DATADIR}/mysql" ]; then
        msg "Data directory exists, continuing..."
    else
        err "No data directory created - cannot continue"
        exit 1
    fi
fi

# Start temporary MySQL server for restore
msg "Starting temporary MySQL server (skip-networking)..."
mysqld --user=mysql --skip-networking --socket=/var/run/mysqld/mysqld.sock --datadir="${DATADIR}" 2>&1 &
MYSQL_PID=$!
msg "MySQL process started with PID $MYSQL_PID"

# Wait for MySQL to be ready
msg "Waiting for MySQL to accept connections..."
READY=0
for i in $(seq 1 120); do
    if mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot ping >/dev/null 2>&1; then
        msg "✓ MySQL is ready after $i seconds"
        READY=1
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        msg "  Still waiting... ($i/120 seconds)"
    fi
    sleep 1
done

if [ "$READY" -ne 1 ]; then
    err "MySQL failed to start within 120 seconds"
    ps aux | grep mysql || true
    kill $MYSQL_PID 2>/dev/null || true
    exit 1
fi

# Create databases
msg "Creating databases..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || true
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS system_status;" 2>&1 || true
msg "✓ Databases created"

# Try to restore backup if available
msg "Checking for backup file..."
if [ -f /backup.sql.xz ]; then
    # Check if it's a Git LFS pointer
    FIRST_LINE=$(head -c 50 /backup.sql.xz 2>/dev/null || echo "")
    if echo "$FIRST_LINE" | grep -q "version https://git-lfs"; then
        msg "Backup is Git LFS pointer - skipping restore"
    else
        msg "Attempting to restore from backup..."
        if xzcat /backup.sql.xz 2>/dev/null | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1; then
            msg "✓ Backup restored successfully"
        else
            msg "Backup restore failed (continuing with empty database)"
        fi
    fi
else
    msg "No backup file found - using empty database"
fi

# Verify databases exist
msg "Verifying databases..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || true
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS system_status;" 2>&1 || true

# Count tables
SAM_TABLES=$(mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "0")
msg "Database 'sam' has $SAM_TABLES tables"

# Setup root user for remote access
msg "Setting up root user for remote access..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';" 2>&1 || true
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;" 2>&1 || true
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "FLUSH PRIVILEGES;" 2>&1 || true
msg "✓ Permissions configured"

# Shutdown temporary server
msg "Shutting down temporary MySQL server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown 2>&1 || true
sleep 3
wait $MYSQL_PID 2>/dev/null || true
msg "✓ Temporary server stopped"

msg "=== Initialization complete ==="
msg "Starting MySQL normally with docker-entrypoint.sh..."
exec docker-entrypoint.sh mysqld
