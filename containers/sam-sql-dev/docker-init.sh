#!/bin/bash

#----------------------------------------------------------------------------
# Determine the directory containing this script, compatible with bash and zsh
if [ -n "${BASH_SOURCE[0]}" ]; then
    SCRIPT_PATH="${BASH_SOURCE[0]}"
fi
#----------------------------------------------------------------------------
set -e


msg() { echo "[${SCRIPT_PATH}]: ${@}"; }

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
mysqld --initialize-insecure --user=mysql --datadir="${DATADIR}"

msg "Starting temporary server for restore..."
mysqld --user=mysql --skip-networking --socket=/var/run/mysqld/mysqld.sock --datadir="${DATADIR}" &
pid="$!"

# Wait for server to be ready
msg "Waiting for server to accept connections..."
for i in {1..60}; do
    if mysqladmin ping --socket=/var/run/mysqld/mysqld.sock 2>/dev/null; then
        msg "Server is ready!"
        break
    fi
    sleep 1
    if [ $i -eq 60 ]; then
        msg "ERROR: Server failed to start within 60 seconds"
        kill "$pid" 2>/dev/null || true
        exit 1
    fi
done

msg "Restoring backup.sql.xz into fresh database..."

# Check if file is Git LFS pointer
if head -1 /backup.sql.xz 2>/dev/null | grep -q "version https://git-lfs.github.com"; then
    msg "WARNING: Backup file is a Git LFS pointer, not the actual backup!"
    msg "The actual backup file needs to be downloaded from Git LFS."
    msg "Creating empty 'sam' database for now..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1
    msg "Empty 'sam' database created."
    msg "To get the real backup: git lfs pull containers/sam-sql-dev/backups/sam-obfuscated.sql.xz"
elif xzcat /backup.sql.xz 2>/dev/null | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1; then
    msg "Restore completed successfully (from xz compressed backup)!"
elif mysql --socket=/var/run/mysqld/mysqld.sock -uroot < /backup.sql.xz 2>&1; then
    msg "Restore completed successfully (from plain SQL file)!"
else
    msg "WARNING: Backup file restore failed. Creating empty 'sam' database..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1
    msg "Empty 'sam' database created. You may need to restore data manually."
fi

# Check if restore actually worked by verifying database exists
if mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "USE sam;" 2>/dev/null; then
    msg "Database 'sam' verified successfully!"
    
    # Only analyze tables if database has tables (not empty)
    table_count=$(mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'sam';" 2>/dev/null || echo "0")
    
    if [ "$table_count" -gt 0 ]; then
        # Analyze tables to update statistics immediately after bulk insert
        # (mysqlcheck is not available in minimal images, so we generate SQL manually)
        msg "Updating table statistics (ANALYZE)..."
        mysql --socket=/var/run/mysqld/mysqld.sock -uroot --skip-column-names -e \
            "SELECT CONCAT('ANALYZE TABLE \`', table_schema, '\`.\`', table_name, '\`;') \
             FROM information_schema.tables \
             WHERE table_schema NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys');" \
            | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>/dev/null || true
    else
        msg "Database 'sam' exists but is empty (backup restore may have failed)"
    fi
else
    exitcode=$?
    msg "ERROR: Database 'sam' does not exist (exit code $exitcode)"
    msg "Attempting to create empty database as fallback..."
    mysql --socket=/var/run/mysqld/mysqld.sock -uroot -e "CREATE DATABASE IF NOT EXISTS sam;" 2>&1 || {
        msg "ERROR: Failed to create database"
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        exit 1
    }
    msg "Empty 'sam' database created (backup restore failed)"
fi

msg "Setting up MySQL user permissions for remote access..."
# Grant root access from any host (for Docker Desktop VM connections)
mysql --socket=/var/run/mysqld/mysqld.sock -uroot <<EOF
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF

msg "Shutting down temporary server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown
wait "$pid" 2>/dev/null || true

msg "Starting MySQL normally..."
exec docker-entrypoint.sh mysqld
