#!/bin/bash

# Enable verbose logging for debugging
set -x

msg() { echo "[docker-init.sh] $@"; }
err() { echo "[docker-init.sh] ERROR: $@" >&2; }

# MySQL official image entrypoint variables
DATADIR="/var/lib/mysql"

# If system tables exist, skip restore
if [ -d "${DATADIR}/mysql" ]; then
    msg "Existing MySQL data found — skipping restore."
    exec docker-entrypoint.sh mysqld
fi

msg "No existing data found. Will initialize and restore from backup..."

# Initialize using mysqld directly (not entrypoint)
msg "Initializing database..."
mysqld --initialize-insecure --user=mysql --datadir="${DATADIR}" 2>&1 | tee /tmp/init.log
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    err "Failed to initialize database"
    cat /tmp/init.log
    exit 1
fi
msg "✓ Database initialized successfully"

# Start temporary server for restore (run in background)
msg "Starting temporary MySQL server for restore..."
mysqld --user=mysql --skip-networking --socket=/var/run/mysqld/mysqld.sock --datadir="${DATADIR}" > /tmp/mysql.log 2>&1 &
pid=$!
msg "MySQL process started with PID $pid"

# Wait for server to be ready
msg "Waiting for MySQL to accept connections..."
waited=0
while [ $waited -lt 60 ]; do
    if mysqladmin ping --socket=/var/run/mysqld/mysqld.sock 2>/dev/null; then
        msg "✓ MySQL server is ready!"
        break
    fi
    msg "  Waiting... ($waited/60s)"
    sleep 1
    waited=$((waited + 1))
done

if [ $waited -eq 60 ]; then
    err "MySQL server failed to start within 60 seconds"
    cat /tmp/mysql.log
    kill $pid 2>/dev/null || true
    exit 1
fi

msg "Restoring backup.sql.xz into fresh database..."

# Check if backup file exists
if [ ! -f /backup.sql.xz ]; then
    msg "WARNING: Backup file /backup.sql.xz does not exist!"
    msg "Creating empty 'sam' database..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1
    msg "✓ Empty 'sam' database created"
else
    # Check if file is Git LFS pointer
    if head -1 /backup.sql.xz 2>/dev/null | grep -q "version https://git-lfs.github.com"; then
        msg "WARNING: Backup file is a Git LFS pointer, not the actual backup!"
        msg "Creating empty 'sam' database..."
        mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1
        msg "✓ Empty 'sam' database created (LFS pointer detected)"
    else
        # Try to restore from backup
        msg "Attempting to restore from xz compressed backup..."
        if xzcat /backup.sql.xz 2>/dev/null | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1; then
            msg "✓ Restore completed successfully (from xz compressed backup)!"
        else
            msg "WARNING: xz restore failed or file is not xz compressed"
            msg "Attempting to restore from plain SQL file..."
            if mysql --socket=/var/run/mysqld/mysqld.sock -uroot < /backup.sql.xz 2>&1; then
                msg "✓ Restore completed successfully (from plain SQL file)!"
            else
                msg "WARNING: Both restore methods failed. Creating empty 'sam' database..."
                mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1
                msg "✓ Empty 'sam' database created (restore failed)"
            fi
        fi
    fi
fi

# Verify database exists
msg "Verifying database 'sam' exists..."
if ! mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "USE sam;" 2>/dev/null; then
    err "Database 'sam' does not exist, creating it now..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || {
        err "Failed to create database"
        kill $pid 2>/dev/null || true
        exit 1
    }
fi
msg "✓ Database 'sam' verified"

# Only analyze tables if database has tables (not empty)
table_count=$(mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "0")

if [ "$table_count" -gt 0 ]; then
    msg "Database has $table_count tables, updating statistics..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e \
        "SELECT CONCAT('ANALYZE TABLE \`', table_schema, '\`.\`', table_name, '\`;') \
         FROM information_schema.tables \
         WHERE table_schema NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys');" \
        | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>/dev/null || true
    msg "✓ Table statistics updated"
else
    msg "Database 'sam' is empty (no tables found)"
fi

msg "Setting up MySQL user permissions for remote access..."
mysql --socket=/var/run/mysqld/mysqld.sock -uroot <<EOF 2>&1 || msg "WARNING: Permission setup had issues"
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF
msg "✓ Permissions configured"

msg "Shutting down temporary MySQL server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown 2>&1 || true
wait $pid 2>/dev/null || true
msg "✓ Temporary server shutdown"

msg "Starting MySQL normally with docker-entrypoint.sh..."
exec docker-entrypoint.sh mysqld
