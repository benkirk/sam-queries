#!/bin/bash
set -e

# MySQL official image entrypoint variables
DATADIR="/var/lib/mysql"

# If system tables exist, skip restore
if [ -d "$DATADIR/mysql" ]; then
    echo "Existing MySQL data found — skipping restore."
    exec docker-entrypoint.sh mysqld
fi

echo "No existing data found. Will initialize and restore from backup..."

# Initialize using mysqld directly (not entrypoint)
echo "Initializing database..."
mysqld --initialize-insecure --user=mysql --datadir="$DATADIR"

echo "Starting temporary server for restore..."
mysqld --user=mysql --skip-networking --socket=/var/run/mysqld/mysqld.sock --datadir="$DATADIR" &
pid="$!"

# Wait for server to be ready
echo "Waiting for server to accept connections..."
for i in {1..60}; do
    if mysqladmin ping --socket=/var/run/mysqld/mysqld.sock 2>/dev/null; then
        echo "Server is ready!"
        break
    fi
    sleep 1
    if [ $i -eq 60 ]; then
        echo "ERROR: Server failed to start within 60 seconds"
        kill "$pid" 2>/dev/null || true
        exit 1
    fi
done

echo "Restoring backup.sql.xz into fresh database..."
if xzcat /backup.sql.xz | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1; then
    echo "Restore completed successfully!"
else
    exitcode=$?
    echo "ERROR: Restore failed with exit code $exitcode"
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    exit 1
fi

echo "Shutting down temporary server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown
wait "$pid" 2>/dev/null || true

echo "Starting MySQL normally..."
exec docker-entrypoint.sh mysqld
