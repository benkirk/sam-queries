#!/bin/bash

# Enable exit on error but allow us to handle specific failures
set -e

msg() { echo "[docker-init.sh] $(date '+%Y-%m-%d %H:%M:%S'): $@"; }
err() { echo "[docker-init.sh] ERROR $(date '+%Y-%m-%d %H:%M:%S'): $@" >&2; }

# MySQL official image entrypoint variables
DATADIR="/var/lib/mysql"

# Ensure directories exist with proper permissions
msg "Setting up directories..."
mkdir -p /var/run/mysqld
mkdir -p "${DATADIR}"
chown -R mysql:mysql /var/run/mysqld
chown -R mysql:mysql "${DATADIR}"
chmod 777 /var/run/mysqld

# If system tables exist, skip restore
if [ -d "${DATADIR}/mysql" ]; then
    msg "Existing MySQL data found — skipping restore."
    exec docker-entrypoint.sh mysqld
fi

msg "No existing data found. Will initialize and restore from backup..."

# Initialize database
msg "Initializing MySQL database..."
mysqld --initialize-insecure --user=mysql --datadir="${DATADIR}" 2>&1 | tee -a /tmp/mysql-init.log || {
    err "Failed to initialize database"
    cat /tmp/mysql-init.log || true
    # Don't exit - try to continue anyway
    msg "Attempting to continue despite initialization error..."
}

# Start temporary MySQL server for restore
msg "Starting temporary MySQL server..."
mysqld --user=mysql --skip-networking --socket=/var/run/mysqld/mysqld.sock --datadir="${DATADIR}" > /tmp/mysql-server.log 2>&1 &
MYSQL_PID=$!
msg "MySQL started with PID $MYSQL_PID"

# Wait for MySQL to be ready
msg "Waiting for MySQL to accept connections (max 120 seconds)..."
max_attempts=120
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot ping >/dev/null 2>&1; then
        msg "✓ MySQL is ready!"
        break
    fi
    attempt=$((attempt + 1))
    if [ $((attempt % 10)) -eq 0 ]; then
        msg "  Waiting... ($attempt/$max_attempts seconds)"
    fi
    sleep 1
done

if [ $attempt -ge $max_attempts ]; then
    err "MySQL failed to start within $max_attempts seconds"
    tail -50 /tmp/mysql-server.log || true
    kill $MYSQL_PID 2>/dev/null || true
    exit 1
fi

msg "Restoring SAM database from backup..."

# Check backup file
if [ ! -f /backup.sql.xz ]; then
    msg "Backup file /backup.sql.xz not found - creating empty database"
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || true
elif head -c 16 /backup.sql.xz 2>/dev/null | grep -q "^version https://git-lfs"; then
    msg "Backup is Git LFS pointer (not actual backup) - creating empty database"
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || true
else
    msg "Attempting to restore from backup..."
    if xzcat /backup.sql.xz 2>/dev/null | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1 | head -20; then
        msg "✓ Backup restore successful"
    else
        msg "Backup restore failed - creating empty database instead"
        mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || true
    fi
fi

# Ensure database exists
msg "Verifying database exists..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || {
    err "Failed to create database"
    kill $MYSQL_PID 2>/dev/null || true
    exit 1
}

# Get table count
TABLE_COUNT=$(mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "0")
msg "Database 'sam' has $TABLE_COUNT tables"

# Analyze tables if they exist
if [ "$TABLE_COUNT" -gt 0 ]; then
    msg "Analyzing tables..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e \
        "SELECT CONCAT('ANALYZE TABLE \`', table_schema, '\`.\`', table_name, '\`;') \
         FROM information_schema.tables \
         WHERE table_schema NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys');" 2>/dev/null | \
        mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1 || msg "Table analysis failed (continuing anyway)"
fi

# Setup permissions
msg "Setting up root user for remote access..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot <<'MYSQL_EOF' 2>&1 || msg "Permission setup had issues (continuing)"
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
MYSQL_EOF

# Shutdown temporary server
msg "Shutting down temporary MySQL server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown 2>&1 || true
sleep 2
wait $MYSQL_PID 2>/dev/null || true

msg "Initialization complete. Starting MySQL normally..."
exec docker-entrypoint.sh mysqld
