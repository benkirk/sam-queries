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
if xzcat /backup.sql.xz | mysql --socket=/var/run/mysqld/mysqld.sock -uroot 2>&1; then
    msg "Restore completed successfully!"
else
    exitcode=$?
    msg "ERROR: Restore failed with exit code $exitcode"
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    exit 1
fi

msg "Shutting down temporary server..."
mysqladmin --socket=/var/run/mysqld/mysqld.sock -uroot shutdown
wait "$pid" 2>/dev/null || true

msg "Starting MySQL normally..."
exec docker-entrypoint.sh mysqld
